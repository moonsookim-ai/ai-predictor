"""
성공 예측 평가기（Success Evaluator）

시뮬레이션이 끝난 뒤（환경이 아직 살아 있는 상태에서）모든 Agent에게
구조화된 설문（구매/도입 의향, 지불 의향 가격, 추천 의향, 입장）을 던지고,
응답을 집계해 신제품·신사업·신전략의 성공 스코어카드를 만든다.

점수 산식은 모두 이 파일의 순수 함수에 있다（compute_scorecard）.
가중치와 임계값은 실제 출시 결과로 보정되기 전까지는 경험적 기본값이다.
"""

import json
import math
import os
import re
import statistics
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from ..models.project import ProjectManager
from ..knowledge import load_korea_business_context
from ..utils.business_brief import format_price, initiative_label, render_business_brief
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction
from ..utils.logger import get_logger
from .korean_persona_panel import PANEL_ENTITY_TYPE
from .simulation_manager import SimulationManager
from .simulation_runner import SimulationRunner

logger = get_logger('mirofish.success_evaluator')

ROLES = ["customer", "competitor", "partner", "media", "regulator", "investor", "internal", "other"]

ROLE_LABELS = {
    "customer": "고객",
    "competitor": "경쟁사",
    "partner": "유통·파트너",
    "media": "언론·인플루언서",
    "regulator": "규제기관",
    "investor": "투자자",
    "internal": "내부 조직",
    "other": "기타",
}

# 엔티티 유형명 키워드 → 역할（LLM 분류 실패 시 폴백）
_ROLE_KEYWORDS = [
    ("competitor", ["competitor", "rival", "incumbent"]),
    ("media", ["media", "journalist", "reporter", "influencer", "blogger", "analyst", "press", "kol"]),
    ("regulator", ["regulator", "government", "agency", "ministry", "authority", "policy"]),
    ("investor", ["investor", "vc", "fund", "shareholder"]),
    ("partner", ["partner", "supplier", "distributor", "retailer", "channel", "vendor", "reseller", "platform"]),
    ("internal", ["employee", "staff", "internal", "executive", "salesteam"]),
    ("customer", ["customer", "consumer", "user", "buyer", "client", "shopper", "adopter", "student",
                  "parent", "patient", "member", "subscriber", "person", "smb", "enterprise"]),
]

# 구매의향 5점 척도 → 실제 구매 전환 가중치（top-box 보정 휴리스틱）
INTENT_CONVERSION_WEIGHTS = {5: 0.75, 4: 0.25, 3: 0.05, 2: 0.0, 1: 0.0}

# LLM 가상 응답자는 실제 소비자보다 낙관적으로 답하는 경향이 있어 추가로 할인한다
SYNTHETIC_OPTIMISM_DISCOUNT = 0.7

COMPONENT_WEIGHTS = {
    "demand": 0.40,
    "price_fit": 0.20,
    "advocacy": 0.20,
    "ecosystem": 0.20,
}

COMPONENT_LABELS = {
    "demand": "수요(구매·도입 의향)",
    "price_fit": "가격 적합도",
    "advocacy": "추천 의향(NPS)",
    "ecosystem": "생태계 지지(파트너·언론·규제 등)",
}

# (최소 점수, 판정 코드, 라벨)
VERDICT_THRESHOLDS = [
    (70, "go", "추진(Go)"),
    (55, "conditional_go", "조건부 추진"),
    (40, "pivot", "피벗 검토"),
    (0, "no_go", "중단 권고(No-Go)"),
]

SURVEY_BATCH_SIZE = 20
SURVEY_BATCH_TIMEOUT = 300.0
MAX_TEXT_SAMPLES = 60


# ───────────────────────── 순수 함수 ─────────────────────────

def classify_role_by_keyword(entity_type: str) -> str:
    name = re.sub(r"[^a-z]", "", (entity_type or "").lower())
    for role, keywords in _ROLE_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return role
    return "other"


def _to_int_in_range(value: Any, low: int, high: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(\.\d+)?", str(value))
        if not match:
            return None
        number = float(match.group(0))
    if not math.isfinite(number):
        return None
    return int(min(high, max(low, round(number))))


def _to_amount(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        match = re.search(r"\d[\d,]*(\.\d+)?", str(value))
        if not match:
            return None
        amount = float(match.group(0).replace(",", ""))
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start:index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = cleaned.find("{", start + 1)
    return None


def parse_survey_response(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Agent의 설문 응답（JSON 문자열 또는 dict）을 정규화한다.
    핵심 문항이 하나도 없으면 None.
    """
    if isinstance(raw, dict):
        data = raw
    else:
        data = _extract_json_object(str(raw or ""))
    if not data:
        return None

    parsed = {
        "purchase_intent": _to_int_in_range(data.get("purchase_intent"), 1, 5),
        "willingness_to_pay": _to_amount(data.get("willingness_to_pay")),
        "recommend_score": _to_int_in_range(data.get("recommend_score"), 0, 10),
        "stance": _to_int_in_range(data.get("stance"), -2, 2),
        "is_potential_customer": data.get("is_potential_customer"),
        "main_reason": str(data.get("main_reason") or "").strip()[:500],
        "main_concern": str(data.get("main_concern") or "").strip()[:500],
    }
    if isinstance(parsed["is_potential_customer"], str):
        parsed["is_potential_customer"] = parsed["is_potential_customer"].strip().lower() in {
            "true", "yes", "1", "예", "네"
        }
    elif not isinstance(parsed["is_potential_customer"], bool):
        parsed["is_potential_customer"] = None

    if all(parsed[key] is None for key in ("purchase_intent", "recommend_score", "stance")):
        return None
    return parsed


def _mean(values: List[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def _nps(scores: List[int]) -> Optional[float]:
    if not scores:
        return None
    promoters = sum(1 for score in scores if score >= 9)
    detractors = sum(1 for score in scores if score <= 6)
    return 100.0 * (promoters - detractors) / len(scores)


def verdict_for(score: float) -> Dict[str, str]:
    for threshold, code, label in VERDICT_THRESHOLDS:
        if score >= threshold:
            return {"code": code, "label": label}
    return {"code": "no_go", "label": VERDICT_THRESHOLDS[-1][2]}


def _confidence(customer_count: int, parse_rate: float) -> Dict[str, str]:
    if customer_count >= 30 and parse_rate >= 0.8:
        return {"level": "high", "label": "높음"}
    if customer_count >= 10 and parse_rate >= 0.6:
        return {"level": "medium", "label": "보통"}
    return {"level": "low", "label": "낮음"}


def compute_scorecard(
    responses: List[Dict[str, Any]],
    price: Optional[float] = None,
    total_surveyed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    설문 응답을 집계해 스코어카드를 만든다.

    Args:
        responses: [{"agent_id", "entity_name", "entity_type", "role", ...parse_survey_response 필드}]
        price: 브리프의 가격（지불 의향과 비교）
        total_surveyed: 설문을 보낸 Agent 수（응답률 계산용）
    """
    total_surveyed = total_surveyed if total_surveyed is not None else len(responses)

    customers = [r for r in responses if r.get("role") == "customer"]
    customer_basis = "role"
    if not customers:
        customers = [r for r in responses if r.get("is_potential_customer") is True]
        customer_basis = "self_reported"
    if not customers:
        customers = list(responses)
        customer_basis = "all_respondents"
    customer_ids = {id(r) for r in customers}
    stakeholders = [
        r for r in responses
        if id(r) not in customer_ids and r.get("role") != "competitor"
    ]
    competitors = [r for r in responses if r.get("role") == "competitor"]

    intents = [r["purchase_intent"] for r in customers if r.get("purchase_intent") is not None]
    recommend = [r["recommend_score"] for r in customers if r.get("recommend_score") is not None]
    wtps = [r["willingness_to_pay"] for r in customers if r.get("willingness_to_pay") is not None]
    stakeholder_stances = [r["stance"] for r in stakeholders if r.get("stance") is not None]
    competitor_stances = [r["stance"] for r in competitors if r.get("stance") is not None]

    mean_intent = _mean(intents)
    nps = _nps(recommend)
    stakeholder_stance = _mean(stakeholder_stances)

    price_fit_share = None
    if price is not None and price > 0 and wtps:
        price_fit_share = sum(1 for wtp in wtps if wtp >= price) / len(wtps)

    components: Dict[str, Optional[float]] = {
        "demand": None if mean_intent is None else 100.0 * (mean_intent - 1) / 4,
        "price_fit": None if price_fit_share is None else 100.0 * price_fit_share,
        "advocacy": None if nps is None else (nps + 100.0) / 2,
        "ecosystem": None if stakeholder_stance is None else 100.0 * (stakeholder_stance + 2) / 4,
    }

    available = {key: value for key, value in components.items() if value is not None}
    if available:
        weight_sum = sum(COMPONENT_WEIGHTS[key] for key in available)
        success_score = sum(COMPONENT_WEIGHTS[key] * value for key, value in available.items()) / weight_sum
    else:
        success_score = None

    raw_conversion = None
    adjusted_conversion = None
    if intents:
        raw_conversion = sum(INTENT_CONVERSION_WEIGHTS[i] for i in intents) / len(intents)
        adjusted_conversion = raw_conversion * SYNTHETIC_OPTIMISM_DISCOUNT

    segments: Dict[str, Dict[str, Any]] = {}
    for response in responses:
        key = response.get("entity_type") or "Unknown"
        segment = segments.setdefault(key, {
            "entity_type": key,
            "role": response.get("role", "other"),
            "role_label": ROLE_LABELS.get(response.get("role", "other"), "기타"),
            "_intents": [], "_stances": [], "_recommend": [], "count": 0,
        })
        segment["count"] += 1
        if response.get("purchase_intent") is not None:
            segment["_intents"].append(response["purchase_intent"])
        if response.get("stance") is not None:
            segment["_stances"].append(response["stance"])
        if response.get("recommend_score") is not None:
            segment["_recommend"].append(response["recommend_score"])

    segment_list = []
    for segment in segments.values():
        intents_seg = segment.pop("_intents")
        stances_seg = segment.pop("_stances")
        recommend_seg = segment.pop("_recommend")
        segment["mean_purchase_intent"] = _round(_mean(intents_seg), 2)
        segment["top2box_share"] = (
            _round(100.0 * sum(1 for i in intents_seg if i >= 4) / len(intents_seg))
            if intents_seg else None
        )
        segment["mean_stance"] = _round(_mean(stances_seg), 2)
        segment["nps"] = _round(_nps(recommend_seg))
        segment_list.append(segment)
    segment_list.sort(key=lambda s: (s["role"] != "customer", -s["count"]))

    parse_rate = (len(responses) / total_surveyed) if total_surveyed else 0.0

    return {
        "success_score": _round(success_score),
        "verdict": verdict_for(success_score) if success_score is not None else None,
        "confidence": _confidence(len(customers), parse_rate),
        "components": {
            key: {
                "label": COMPONENT_LABELS[key],
                "score": _round(value),
                "weight": COMPONENT_WEIGHTS[key],
            }
            for key, value in components.items()
        },
        "metrics": {
            "respondents": len(responses),
            "surveyed": total_surveyed,
            "response_rate": _round(100.0 * parse_rate),
            "customer_respondents": len(customers),
            "customer_basis": customer_basis,
            "mean_purchase_intent": _round(mean_intent, 2),
            "top2box_share": (
                _round(100.0 * sum(1 for i in intents if i >= 4) / len(intents)) if intents else None
            ),
            "nps": _round(nps),
            "median_willingness_to_pay": _round(statistics.median(wtps), 0) if wtps else None,
            "price": price,
            "price_fit_share": _round(None if price_fit_share is None else 100.0 * price_fit_share),
            "stakeholder_mean_stance": _round(stakeholder_stance, 2),
            "competitor_mean_stance": _round(_mean(competitor_stances), 2),
            "estimated_conversion_raw": _round(None if raw_conversion is None else 100.0 * raw_conversion),
            "estimated_conversion_adjusted": _round(
                None if adjusted_conversion is None else 100.0 * adjusted_conversion
            ),
        },
        "segments": segment_list,
        "method": {
            # JSON 저장 후에도 같은 모양이 되도록 키를 문자열로 둔다
            "intent_conversion_weights": {str(k): v for k, v in INTENT_CONVERSION_WEIGHTS.items()},
            "synthetic_optimism_discount": SYNTHETIC_OPTIMISM_DISCOUNT,
            "component_weights": COMPONENT_WEIGHTS,
            "verdict_thresholds": [
                {"min_score": threshold, "code": code, "label": label}
                for threshold, code, label in VERDICT_THRESHOLDS
            ],
        },
    }


def render_scorecard_markdown(evaluation: Dict[str, Any]) -> str:
    """스코어카드를 보고서 머리에 붙일 Markdown으로 만든다."""
    scorecard = evaluation.get("scorecard") or {}
    metrics = scorecard.get("metrics") or {}
    insights = evaluation.get("insights") or {}

    def fmt(value: Any, suffix: str = "") -> str:
        if value is None:
            return "-"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{value:,}{suffix}" if isinstance(value, (int, float)) else f"{value}{suffix}"

    score = scorecard.get("success_score")
    verdict = (scorecard.get("verdict") or {}).get("label", "-")
    confidence = (scorecard.get("confidence") or {}).get("label", "-")

    lines = [
        "## 성공 예측 스코어카드",
        "",
        f"> **성공 점수 {fmt(score)} / 100 · 판정: {verdict} · 신뢰도: {confidence}**",
        "",
        "| 지표 | 점수 | 가중치 |",
        "|---|---|---|",
    ]
    for component in (scorecard.get("components") or {}).values():
        lines.append(
            f"| {component['label']} | {fmt(component['score'])} | {int(component['weight'] * 100)}% |"
        )

    lines += [
        "",
        "| 핵심 수치 | 값 |",
        "|---|---|",
        f"| 설문 응답 Agent | {fmt(metrics.get('respondents'))} / {fmt(metrics.get('surveyed'))} |",
        f"| 고객 응답자 | {fmt(metrics.get('customer_respondents'))} |",
        f"| 평균 구매·도입 의향 (1~5) | {fmt(metrics.get('mean_purchase_intent'))} |",
        f"| Top-2 Box (4~5점 비율) | {fmt(metrics.get('top2box_share'), '%')} |",
        f"| NPS | {fmt(metrics.get('nps'))} |",
        f"| 지불 의향 가격 중앙값 | {fmt(metrics.get('median_willingness_to_pay'))} |",
        f"| 제시 가격 이상 지불 의향 비율 | {fmt(metrics.get('price_fit_share'), '%')} |",
        f"| 추정 구매 전환율 (보정 후) | {fmt(metrics.get('estimated_conversion_adjusted'), '%')} |",
    ]

    for title, key in (
        ("성공 요인", "success_drivers"),
        ("핵심 리스크", "key_risks"),
        ("권고 사항", "recommendations"),
        ("활용 가능한 정부 지원사업 (최신 공고 확인 필요)", "support_programs"),
        ("출시 전 규제·인허가 점검", "regulatory_checks"),
    ):
        items = insights.get(key) or []
        if items:
            lines += ["", f"**{title}**", ""]
            lines += [f"- {item}" for item in items]

    lines += [
        "",
        "> 이 점수는 LLM 가상 이해관계자 설문을 집계한 상대 지표입니다. "
        "가중치·임계값은 실제 출시 결과로 보정되지 않은 기본값이므로 의사결정의 참고 자료로만 사용하세요.",
    ]
    return "\n".join(lines)


# ───────────────────────── 서비스 ─────────────────────────

SURVEY_PROMPT_TEMPLATE = """\
지금부터 짧은 설문에 답해 주세요. 당신이 지금까지 시뮬레이션에서 보고 겪은 내용과 당신의 정체성·입장에 근거해 솔직하게 답하세요.

{brief}

다른 설명 없이 아래 JSON 하나만 출력하세요:
{{
  "is_potential_customer": true 또는 false (당신이 이 {label}을(를) 직접 구매·도입·이용할 수 있는 대상인가),
  "purchase_intent": 1~5 정수 (5=반드시 구매·도입, 4=아마 구매, 3=모르겠음, 2=아마 안 함, 1=절대 안 함. 고객이 아니면 지지 정도로 답함),
  "willingness_to_pay": 지불할 의향이 있는 최대 금액 숫자 ({currency} 기준, 해당 없으면 null),
  "recommend_score": 0~10 정수 (주변에 추천할 가능성),
  "stance": -2~2 정수 (이 {label}의 성공에 대한 당신의 입장: -2 매우 부정 ~ 2 매우 긍정),
  "main_reason": "가장 큰 이유 한 문장",
  "main_concern": "가장 큰 우려 한 문장"
}}"""

ROLE_SYSTEM_PROMPT = """\
You classify stakeholder entity types for a market simulation of a new product / business / strategy.
Map every given entity type to exactly one role from:
customer, competitor, partner, media, regulator, investor, internal, other.
"customer" means it could buy, adopt or use the initiative (consumers, users, client companies).
Return JSON: {"roles": {"<EntityType>": "<role>", ...}}"""

INSIGHT_SYSTEM_PROMPT = """\
당신은 신제품·신사업 성공 가능성을 평가하는 전략 컨설턴트입니다.
가상 이해관계자 설문 결과（점수와 응답자의 이유·우려）를 근거로 핵심만 정리하세요.
근거 없는 낙관을 피하고, 응답에 실제로 나온 내용에 기반하세요.
독자는 한국의 기업과 예비 창업자입니다. 아래 [한국 시장·정책 참고자료]를 활용해
추진 주체의 단계에 맞는 정부 지원사업과 출시 전 점검할 한국 규제를 구체적으로 짚으세요.
JSON으로만 답하세요:
{"success_drivers": ["..."], "key_risks": ["..."], "recommendations": ["..."],
 "support_programs": ["지원사업명 — 왜 적합한지"], "regulatory_checks": ["규제·인허가 — 점검 내용"],
 "summary": "한두 문장 총평"}
각 목록은 3개 이하로 작성하세요."""


class SuccessEvaluator:
    """시뮬레이션 Agent 설문으로 성공 스코어카드를 만든다."""

    RESULT_FILENAME = "success_evaluation.json"

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self._llm = llm_client

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    @classmethod
    def _result_path(cls, simulation_id: str) -> str:
        return os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id, cls.RESULT_FILENAME)

    @classmethod
    def load(cls, simulation_id: str) -> Optional[Dict[str, Any]]:
        path = cls._result_path(simulation_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception("성공 평가 결과를 읽지 못했습니다: %s", simulation_id)
            return None

    @classmethod
    def save(cls, simulation_id: str, evaluation: Dict[str, Any]) -> None:
        path = cls._result_path(simulation_id)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(evaluation, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def _load_agents(self, simulation_id: str) -> List[Dict[str, Any]]:
        config_path = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"시뮬레이션 설정이 없습니다: {simulation_id}")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        agents = [
            {
                "agent_id": agent.get("agent_id"),
                "entity_name": agent.get("entity_name", ""),
                "entity_type": agent.get("entity_type", "Unknown"),
            }
            for agent in config.get("agent_configs", [])
            if agent.get("agent_id") is not None
        ]
        if not agents:
            raise ValueError(f"시뮬레이션에 Agent가 없습니다: {simulation_id}")
        return agents

    def _classify_roles(self, entity_types: List[str], brief_text: str) -> Dict[str, str]:
        roles = {entity_type: classify_role_by_keyword(entity_type) for entity_type in entity_types}
        llm_types = [entity_type for entity_type in entity_types if entity_type != PANEL_ENTITY_TYPE]
        if not llm_types:
            return roles
        try:
            result = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": ROLE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"{brief_text}\n\nEntity types: {json.dumps(llm_types)}"},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
            for entity_type, role in (result.get("roles") or {}).items():
                if entity_type in llm_types and role in ROLES:
                    roles[entity_type] = role
        except Exception:
            logger.warning("엔티티 역할 분류 LLM 호출 실패, 키워드 규칙으로 대체합니다", exc_info=True)
        return roles

    def _run_survey(
        self,
        simulation_id: str,
        agents: List[Dict[str, Any]],
        prompt: str,
        platform: str,
        progress_callback: Optional[Callable[[int, str], None]],
    ) -> Dict[int, Any]:
        answers: Dict[int, Any] = {}
        total = len(agents)
        for start in range(0, total, SURVEY_BATCH_SIZE):
            batch = agents[start:start + SURVEY_BATCH_SIZE]
            result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=[{"agent_id": a["agent_id"], "prompt": prompt} for a in batch],
                platform=platform,
                timeout=SURVEY_BATCH_TIMEOUT,
            )
            if not result.get("success"):
                logger.warning("설문 배치 실패 (%s~): %s", start, result.get("error"))
            else:
                for item in (result.get("result") or {}).get("results", {}).values():
                    agent_id = item.get("agent_id")
                    if agent_id is not None and item.get("response") is not None:
                        answers[agent_id] = item["response"]
            if progress_callback:
                done = min(start + SURVEY_BATCH_SIZE, total)
                progress_callback(10 + int(70 * done / total), f"가상 이해관계자 설문 {done}/{total}")
        return answers

    def _summarize(self, scorecard: Dict[str, Any], responses: List[Dict[str, Any]], brief_text: str) -> Dict[str, Any]:
        reasons = [
            f"[{ROLE_LABELS.get(r['role'], '기타')}/{r['entity_type']}] 의향 {r.get('purchase_intent')}: {r['main_reason']}"
            for r in responses if r.get("main_reason")
        ][:MAX_TEXT_SAMPLES]
        concerns = [
            f"[{ROLE_LABELS.get(r['role'], '기타')}/{r['entity_type']}] {r['main_concern']}"
            for r in responses if r.get("main_concern")
        ][:MAX_TEXT_SAMPLES]
        user_prompt = (
            f"{brief_text}\n\n[스코어카드]\n"
            f"{json.dumps({k: scorecard[k] for k in ('success_score', 'verdict', 'components', 'metrics')}, ensure_ascii=False)}\n\n"
            f"[응답자의 이유]\n" + "\n".join(reasons) + "\n\n[응답자의 우려]\n" + "\n".join(concerns)
            + f"\n\n[한국 시장·정책 참고자료]\n{load_korea_business_context()}"
        )
        try:
            result = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": f"{INSIGHT_SYSTEM_PROMPT}\n\n{get_language_instruction()}"},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
            )
        except Exception:
            logger.warning("성공 평가 인사이트 요약 실패", exc_info=True)
            return {}
        return {
            key: [str(item) for item in (result.get(key) or [])][:3]
            for key in ("success_drivers", "key_risks", "recommendations", "support_programs", "regulatory_checks")
        } | {"summary": str(result.get("summary") or "")}

    def evaluate(
        self,
        simulation_id: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        설문을 실행하고 스코어카드를 저장한다.

        Raises:
            ValueError: 시뮬레이션이 없거나 환경이 종료되어 설문할 수 없는 경우
        """
        state = SimulationManager().get_simulation(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션이 없습니다: {simulation_id}")
        if not SimulationRunner.check_env_alive(simulation_id):
            raise ValueError("시뮬레이션 환경이 종료되어 설문을 진행할 수 없습니다")

        project = ProjectManager.get_project(state.project_id)
        brief = (project.business_brief if project else None) or {}
        requirement = (project.simulation_requirement if project else "") or ""
        brief_text = render_business_brief(brief) or f"[예측 대상]\n{requirement}"

        agents = self._load_agents(simulation_id)
        if progress_callback:
            progress_callback(5, "이해관계자 역할 분류 중")
        roles = self._classify_roles(sorted({a["entity_type"] for a in agents}), brief_text)

        prompt = SURVEY_PROMPT_TEMPLATE.format(
            brief=brief_text,
            label=initiative_label(brief),
            currency=brief.get("currency", "KRW"),
        )
        answers = self._run_survey(
            simulation_id, agents, prompt, state.get_default_platform(), progress_callback
        )

        responses = []
        for agent in agents:
            parsed = parse_survey_response(answers.get(agent["agent_id"]))
            if parsed is None:
                continue
            responses.append({**agent, "role": roles.get(agent["entity_type"], "other"), **parsed})
        if not responses:
            raise ValueError("설문 응답을 하나도 해석하지 못했습니다")

        scorecard = compute_scorecard(responses, price=brief.get("price"), total_surveyed=len(agents))
        if progress_callback:
            progress_callback(85, "설문 결과 요약 중")
        insights = self._summarize(scorecard, responses, brief_text)

        evaluation = {
            "simulation_id": simulation_id,
            "created_at": datetime.now().isoformat(),
            "business_brief": brief,
            "price_display": format_price(brief) if brief else None,
            "roles": roles,
            "scorecard": scorecard,
            "insights": insights,
            "responses": responses,
        }
        self.save(simulation_id, evaluation)
        if progress_callback:
            progress_callback(100, "성공 예측 평가 완료")
        return evaluation
