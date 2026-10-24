"""
사업 브리프（Business Brief）

신제품 / 신사업 / 신전략의 성공 예측에 필요한 구조화 입력을 정규화하고,
LLM 프롬프트에 넣을 수 있는 텍스트로 변환한다.
"""

import json
import math
from typing import Any, Dict, Optional

INITIATIVE_TYPES = {
    "product": "신제품",
    "business": "신사업",
    "strategy": "신전략",
}

APPLICANT_TYPES = {
    "pre_founder": "예비창업자(사업자 등록 전)",
    "early_startup": "초기 창업기업(업력 3년 이내)",
    "growth_startup": "도약기 창업기업(업력 3~7년)",
    "sme": "중소기업",
    "large_company": "중견·대기업",
}

# 자유 텍스트 필드: (키, 라벨, 최대 길이)
_TEXT_FIELDS = [
    ("name", "이름", 200),
    ("industry", "업종·분야", 200),
    ("founder_profile", "창업자·추진 조직", 1000),
    ("description", "설명", 4000),
    ("target_customer", "타깃 고객", 2000),
    ("value_proposition", "핵심 가치 제안", 2000),
    ("competitors", "경쟁사·대체재", 2000),
    ("channels", "출시·유통 채널", 1000),
    ("market", "시장·지역", 500),
    ("success_criteria", "성공 기준(KPI)", 2000),
    ("price_unit", "가격 단위", 50),
]


def _parse_price(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        price = float(value)
    else:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        try:
            price = float(text)
        except ValueError:
            return None
    if not math.isfinite(price) or price < 0:
        return None
    return price


def normalize_business_brief(raw: Any) -> Optional[Dict[str, Any]]:
    """
    브리프를 정규화한다. JSON 문자열 또는 dict를 받는다.
    유효한 필드가 하나도 없으면 None을 반환한다.

    Raises:
        ValueError: JSON 형식이 아니거나 객체가 아닌 경우
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("business_brief must be a JSON object") from exc
    if not isinstance(raw, dict):
        raise ValueError("business_brief must be a JSON object")

    brief: Dict[str, Any] = {}

    initiative_type = str(raw.get("initiative_type") or "").strip().lower()
    if initiative_type in INITIATIVE_TYPES:
        brief["initiative_type"] = initiative_type

    applicant_type = str(raw.get("applicant_type") or "").strip().lower()
    if applicant_type in APPLICANT_TYPES:
        brief["applicant_type"] = applicant_type

    for key, _label, max_len in _TEXT_FIELDS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            brief[key] = text[:max_len]

    price = _parse_price(raw.get("price"))
    if price is not None:
        brief["price"] = price
        currency = str(raw.get("currency") or "KRW").strip().upper()[:8]
        brief["currency"] = currency or "KRW"

    if not brief:
        return None
    brief.setdefault("initiative_type", "product")
    return brief


def format_price(brief: Dict[str, Any]) -> Optional[str]:
    price = brief.get("price")
    if price is None:
        return None
    amount = f"{price:,.0f}" if float(price).is_integer() else f"{price:,.2f}"
    unit = brief.get("price_unit")
    text = f"{amount} {brief.get('currency', 'KRW')}"
    return f"{text} / {unit}" if unit else text


def initiative_label(brief: Optional[Dict[str, Any]]) -> str:
    if not brief:
        return INITIATIVE_TYPES["product"]
    return INITIATIVE_TYPES.get(brief.get("initiative_type", "product"), "신제품")


def render_business_brief(brief: Optional[Dict[str, Any]]) -> str:
    """브리프를 LLM 프롬프트용 텍스트로 변환한다."""
    if not brief:
        return ""
    lines = [f"[사업 브리프] 유형: {initiative_label(brief)}"]
    if brief.get("applicant_type"):
        lines.append(f"- 추진 주체: {APPLICANT_TYPES[brief['applicant_type']]}")
    for key, label, _max_len in _TEXT_FIELDS:
        if key == "price_unit":
            continue
        if brief.get(key):
            lines.append(f"- {label}: {brief[key]}")
    price_text = format_price(brief)
    if price_text:
        lines.append(f"- 가격: {price_text}")
    return "\n".join(lines)


def default_requirement(brief: Dict[str, Any]) -> str:
    name = brief.get("name") or f"이 {initiative_label(brief)}"
    return (
        f"{name}을(를) 시장에 내놓았을 때 타깃 고객, 경쟁사, 유통·파트너, 언론 등 "
        f"이해관계자가 어떻게 반응할지 시뮬레이션하고, 성공 가능성과 핵심 리스크를 예측하라."
    )


def build_simulation_requirement(requirement: str, brief: Optional[Dict[str, Any]]) -> str:
    """사용자 질문과 브리프를 합쳐 시뮬레이션 요구사항 텍스트를 만든다."""
    requirement = (requirement or "").strip()
    if not brief:
        return requirement
    if not requirement:
        requirement = default_requirement(brief)
    return f"{requirement}\n\n{render_business_brief(brief)}"
