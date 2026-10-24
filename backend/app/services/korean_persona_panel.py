"""
한국인 가상 소비자 패널（Korean Persona Panel）

NVIDIA Nemotron-Personas-Korea（CC BY 4.0）표본에서 사업 브리프의 타깃 고객에
맞는 페르소나를 골라 OASIS Agent로 추가한다. 그래프에서 추출한 엔티티（경쟁사·
언론·기관 등）만으로는 고객 표본이 너무 작기 때문에, 실제 한국 인구 분포에 기반한
고객 패널로 수요 설문의 표본을 보강하는 것이 목적이다.

표본 파일은 scripts/fetch_korean_personas.py 로 받는다.
"""

import ast
import json
import os
import random
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .oasis_profile_generator import OasisAgentProfile
from .simulation_config_generator import AgentActivityConfig

logger = get_logger('mirofish.korean_persona_panel')

PANEL_ENTITY_TYPE = "KoreanConsumer"

DEFAULT_PERSONA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "korean_personas.jsonl"
)

# 시·도 표기 → 데이터셋 province 값（데이터셋은 "경상남" 처럼 줄여 쓴다）
_PROVINCE_ALIASES = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주",
    "대전": "대전", "울산": "울산", "세종": "세종", "경기": "경기", "강원": "강원",
    "충북": "충청북", "충청북": "충청북", "충남": "충청남", "충청남": "충청남",
    "전북": "전라북", "전라북": "전라북", "전남": "전라남", "전라남": "전라남",
    "경북": "경상북", "경상북": "경상북", "경남": "경상남", "경상남": "경상남",
    "제주": "제주",
}

TARGET_FILTER_PROMPT = """\
아래 사업 브리프의 타깃 고객을 한국 인구 페르소나 데이터에서 고르기 위한 필터로 바꿔라.
데이터 컬럼: age(19~99), sex("남자"/"여자"), province(서울·경기·부산 등 시·도), occupation(한국어 직업명),
persona(자기소개 문장), family_type, housing_type.
B2B라면 구매 의사결정자（대표·관리자·자영업자 등）가 고객이다.
JSON으로만 답하라:
{"age_min": 정수, "age_max": 정수, "sex": "남자"|"여자"|null, "provinces": ["서울", ...] (전국이면 []),
 "occupation_keywords": ["직업명에 들어갈 단어", ...], "lifestyle_keywords": ["페르소나 문장에 들어갈 단어", ...]}"""


_pool_lock = threading.Lock()
_pool_cache: Dict[str, List[Dict[str, Any]]] = {}


def persona_pool_path() -> str:
    return os.environ.get("KOREAN_PERSONA_PATH") or os.path.abspath(DEFAULT_PERSONA_PATH)


def load_persona_pool(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """표본 파일을 읽는다（프로세스 내 캐시）. 파일이 없으면 빈 리스트."""
    path = path or persona_pool_path()
    with _pool_lock:
        if path in _pool_cache:
            return _pool_cache[path]
        rows: List[Dict[str, Any]] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row.get("age"), int) and row.get("persona"):
                        rows.append(row)
        _pool_cache[path] = rows
        return rows


def normalize_target_filter(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}

    def _int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    age_min = max(19, min(99, _int(raw.get("age_min"), 19)))
    age_max = max(age_min, min(99, _int(raw.get("age_max"), 99)))
    sex = raw.get("sex") if raw.get("sex") in ("남자", "여자") else None
    provinces = []
    for province in raw.get("provinces") or []:
        text = str(province).strip()
        for alias, value in _PROVINCE_ALIASES.items():
            if text.startswith(alias) and value not in provinces:
                provinces.append(value)
                break

    def _keywords(value: Any) -> List[str]:
        return [str(k).strip() for k in (value or []) if str(k).strip()][:10]

    return {
        "age_min": age_min,
        "age_max": age_max,
        "sex": sex,
        "provinces": provinces,
        "occupation_keywords": _keywords(raw.get("occupation_keywords")),
        "lifestyle_keywords": _keywords(raw.get("lifestyle_keywords")),
    }


def _hard_match(row: Dict[str, Any], target: Dict[str, Any]) -> bool:
    if not (target["age_min"] <= row["age"] <= target["age_max"]):
        return False
    if target["sex"] and row.get("sex") != target["sex"]:
        return False
    if target["provinces"] and not any(
        (row.get("province") or "").startswith(p) for p in target["provinces"]
    ):
        return False
    return True


def _soft_score(row: Dict[str, Any], target: Dict[str, Any]) -> int:
    occupation = row.get("occupation") or ""
    text = " ".join(str(row.get(k) or "") for k in ("persona", "professional_persona", "family_type", "housing_type"))
    score = 2 * sum(1 for k in target["occupation_keywords"] if k in occupation)
    score += sum(1 for k in target["lifestyle_keywords"] if k in text)
    return score


def select_personas(
    pool: List[Dict[str, Any]],
    target: Dict[str, Any],
    size: int,
    rng: random.Random,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    타깃 필터에 맞는 페르소나를 고른다. 후보가 부족하면 조건을 단계적으로 완화한다.
    키워드가 맞는 후보를 절반까지 우선 뽑고, 나머지는 다양성을 위해 무작위로 뽑는다.

    Returns:
        (선택된 행, 완화한 조건 목록)
    """
    relaxations = [
        ("provinces", lambda t: {**t, "provinces": []}),
        ("sex", lambda t: {**t, "sex": None}),
        ("age", lambda t: {**t, "age_min": max(19, t["age_min"] - 10), "age_max": min(99, t["age_max"] + 10)}),
        ("all", lambda t: {**t, "age_min": 19, "age_max": 99, "sex": None, "provinces": []}),
    ]
    relaxed: List[str] = []
    candidates = [row for row in pool if _hard_match(row, target)]
    for name, relax in relaxations:
        if len(candidates) >= size:
            break
        target = relax(target)
        relaxed.append(name)
        candidates = [row for row in pool if _hard_match(row, target)]

    if len(candidates) <= size:
        return list(candidates), relaxed

    scored = [row for row in candidates if _soft_score(row, target) > 0]
    scored.sort(key=lambda row: _soft_score(row, target), reverse=True)
    chosen = scored[: size // 2]
    chosen_ids = {id(row) for row in chosen}
    remaining = [row for row in candidates if id(row) not in chosen_ids]
    chosen += rng.sample(remaining, size - len(chosen))
    return chosen, relaxed


def _persona_name(row: Dict[str, Any]) -> str:
    match = re.match(r"\s*([가-힣]{2,4})\s*씨", row.get("persona") or "")
    return match.group(1) if match else f"소비자{(row.get('uuid') or '')[:4]}"


def _parse_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except (ValueError, SyntaxError):
            pass
    return []


def persona_to_profile(row: Dict[str, Any], user_id: int) -> OasisAgentProfile:
    name = _persona_name(row)
    region = (row.get("district") or row.get("province") or "").replace("-", " ")
    persona_parts = [
        row.get("persona"),
        row.get("professional_persona"),
        row.get("family_persona"),
        row.get("cultural_background"),
        row.get("career_goals_and_ambitions"),
        f"거주: {region}, {row.get('housing_type', '')} · 가구: {row.get('family_type', '')} · "
        f"학력: {row.get('education_level', '')} · 혼인: {row.get('marital_status', '')}",
        "당신은 대한민국에 사는 실제 소비자처럼 생각하고 말한다. 자신의 소득·생활 형편과 습관에 비추어 "
        "새로운 제품·서비스를 현실적으로 판단하며, 필요 없거나 비싸면 솔직하게 거절한다.",
    ]
    return OasisAgentProfile(
        user_id=user_id,
        user_name=f"kr_consumer_{user_id}",
        name=name,
        bio=f"{row.get('age')}세 {row.get('sex', '')} · {row.get('occupation', '')} · {region}",
        persona="\n".join(str(p) for p in persona_parts if p),
        age=row.get("age"),
        gender={"남자": "male", "여자": "female"}.get(row.get("sex"), "other"),
        country="대한민국",
        profession=row.get("occupation"),
        interested_topics=_parse_list(row.get("hobbies_and_interests_list"))[:5],
        source_entity_uuid=f"kr_persona_{row.get('uuid', user_id)}",
        source_entity_type=PANEL_ENTITY_TYPE,
    )


def persona_activity_config(row: Dict[str, Any], agent_id: int, name: str, rng: random.Random) -> AgentActivityConfig:
    age = row.get("age") or 40
    if age >= 65:
        active_hours = list(range(6, 21))
    elif age < 30:
        active_hours = [7, 8, 12, 13] + list(range(18, 24)) + [0]
    else:
        active_hours = [7, 8, 12, 13] + list(range(18, 24))
    return AgentActivityConfig(
        agent_id=agent_id,
        entity_uuid=f"kr_persona_{row.get('uuid', agent_id)}",
        entity_name=name,
        entity_type=PANEL_ENTITY_TYPE,
        activity_level=round(rng.uniform(0.25, 0.6), 2),
        posts_per_hour=0.3,
        comments_per_hour=1.0,
        active_hours=active_hours,
        response_delay_min=10,
        response_delay_max=120,
        sentiment_bias=0.0,
        stance="neutral",
        influence_weight=1.0,
    )


class KoreanPersonaPanelBuilder:
    """브리프의 타깃 고객에 맞춘 한국인 소비자 패널 Agent를 만든다."""

    def __init__(self, llm_client: Optional[LLMClient] = None, seed: Optional[int] = None):
        self._llm = llm_client
        self.rng = random.Random(seed)

    def _target_filter(self, target_text: str) -> Dict[str, Any]:
        try:
            llm = self._llm or LLMClient()
            raw = llm.chat_json(
                messages=[
                    {"role": "system", "content": TARGET_FILTER_PROMPT},
                    {"role": "user", "content": target_text},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
        except Exception:
            logger.warning("타깃 고객 필터 생성 실패, 전체 인구에서 표본을 뽑습니다", exc_info=True)
            raw = {}
        return normalize_target_filter(raw)

    def build(
        self,
        target_text: str,
        start_id: int,
        size: int,
        pool: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[OasisAgentProfile], List[AgentActivityConfig], Dict[str, Any]]:
        pool = load_persona_pool() if pool is None else pool
        if not pool or size <= 0:
            return [], [], {"enabled": False, "reason": "persona pool not available" if not pool else "size is 0"}

        target = self._target_filter(target_text)
        rows, relaxed = select_personas(pool, target, size, self.rng)

        profiles, configs = [], []
        for offset, row in enumerate(rows):
            profile = persona_to_profile(row, start_id + offset)
            profiles.append(profile)
            configs.append(persona_activity_config(row, start_id + offset, profile.name, self.rng))

        meta = {
            "enabled": True,
            "source": "nvidia/Nemotron-Personas-Korea (CC BY 4.0)",
            "pool_size": len(pool),
            "panel_size": len(rows),
            "target_filter": target,
            "relaxed_conditions": relaxed,
        }
        logger.info("한국인 소비자 패널 %d명 생성 (완화 조건: %s)", len(rows), relaxed or "없음")
        return profiles, configs, meta
