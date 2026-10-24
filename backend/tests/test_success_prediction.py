import json
import random

import pytest

from app.services.korean_persona_panel import (
    PANEL_ENTITY_TYPE,
    KoreanPersonaPanelBuilder,
    normalize_target_filter,
    persona_to_profile,
    select_personas,
)
from app.services.report_agent import ReportManager, ReportOutline
from app.services.success_evaluator import (
    classify_role_by_keyword,
    compute_scorecard,
    parse_survey_response,
    render_scorecard_markdown,
    verdict_for,
)
from app.utils.business_brief import (
    build_simulation_requirement,
    normalize_business_brief,
    render_business_brief,
)


# ── 사업 브리프 ──

def test_brief_normalizes_fields_and_price():
    brief = normalize_business_brief(json.dumps({
        "initiative_type": "BUSINESS",
        "applicant_type": "pre_founder",
        "name": "  반려견 수제간식 구독  ",
        "price": "29,900",
        "price_unit": "월",
        "unknown": "ignored",
    }))
    assert brief == {
        "initiative_type": "business",
        "applicant_type": "pre_founder",
        "name": "반려견 수제간식 구독",
        "price_unit": "월",
        "price": 29900.0,
        "currency": "KRW",
    }
    text = render_business_brief(brief)
    assert "유형: 신사업" in text
    assert "예비창업자" in text
    assert "29,900 KRW / 월" in text


@pytest.mark.parametrize("raw", [None, "", "   ", "{}", {"price": "free"}])
def test_brief_returns_none_without_usable_fields(raw):
    assert normalize_business_brief(raw) is None


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"])
def test_brief_rejects_non_object(raw):
    with pytest.raises(ValueError):
        normalize_business_brief(raw)


def test_requirement_defaults_when_only_brief_is_given():
    brief = normalize_business_brief({"name": "AI 세무비서"})
    requirement = build_simulation_requirement("", brief)
    assert requirement.startswith("AI 세무비서을(를) 시장에 내놓았을 때")
    assert "[사업 브리프]" in requirement
    assert build_simulation_requirement("질문", None) == "질문"


# ── 설문 응답 해석 ──

def test_parse_survey_response_extracts_json_from_chatty_text():
    raw = '네, 답변드릴게요.\n```json\n{"purchase_intent": "4", "willingness_to_pay": "25,000원", ' \
          '"recommend_score": 11, "stance": -3, "is_potential_customer": "예", "main_reason": "편리함"}\n```'
    parsed = parse_survey_response(raw)
    assert parsed["purchase_intent"] == 4
    assert parsed["willingness_to_pay"] == 25000.0
    assert parsed["recommend_score"] == 10
    assert parsed["stance"] == -2
    assert parsed["is_potential_customer"] is True
    assert parsed["main_reason"] == "편리함"


@pytest.mark.parametrize("raw", [None, "", "모르겠어요", '{"main_reason": "only text"}'])
def test_parse_survey_response_rejects_unusable_answers(raw):
    assert parse_survey_response(raw) is None


@pytest.mark.parametrize("entity_type,role", [
    ("KoreanConsumer", "customer"),
    ("CompetitorCompany", "competitor"),
    ("MediaOutlet", "media"),
    ("GovernmentAgency", "regulator"),
    ("RetailPartner", "partner"),
    ("Organization", "other"),
])
def test_role_keyword_fallback(entity_type, role):
    assert classify_role_by_keyword(entity_type) == role


# ── 스코어카드 ──

def _response(role, intent=None, wtp=None, recommend=None, stance=None, entity_type="Segment"):
    return {
        "agent_id": 0, "entity_name": "x", "entity_type": entity_type, "role": role,
        "purchase_intent": intent, "willingness_to_pay": wtp, "recommend_score": recommend,
        "stance": stance, "is_potential_customer": None, "main_reason": "", "main_concern": "",
    }


def test_scorecard_components_and_weighted_score():
    responses = [
        _response("customer", intent=5, wtp=30000, recommend=10, stance=2),
        _response("customer", intent=4, wtp=20000, recommend=8, stance=1),
        _response("customer", intent=1, wtp=0, recommend=2, stance=-2),
        _response("media", stance=1),
        _response("competitor", stance=-2),
    ]
    card = compute_scorecard(responses, price=25000, total_surveyed=6)
    components = {k: v["score"] for k, v in card["components"].items()}
    # 평균 의향 10/3 → (10/3-1)/4
    assert components["demand"] == pytest.approx(58.3, abs=0.1)
    assert components["price_fit"] == pytest.approx(33.3, abs=0.1)
    assert components["advocacy"] == 50.0          # NPS 0
    assert components["ecosystem"] == 75.0         # 언론 +1, 경쟁사는 제외
    expected = 0.4 * 58.333 + 0.2 * 33.333 + 0.2 * 50 + 0.2 * 75
    assert card["success_score"] == pytest.approx(expected, abs=0.1)
    metrics = card["metrics"]
    assert metrics["customer_respondents"] == 3
    assert metrics["response_rate"] == pytest.approx(83.3, abs=0.1)
    assert metrics["competitor_mean_stance"] == -2
    assert metrics["estimated_conversion_raw"] == pytest.approx(33.3, abs=0.1)   # (0.75+0.25+0)/3
    assert metrics["estimated_conversion_adjusted"] == pytest.approx(23.3, abs=0.1)


def test_scorecard_renormalizes_missing_components_and_falls_back_to_all_respondents():
    responses = [_response("other", intent=5), _response("other", intent=5)]
    card = compute_scorecard(responses)
    assert card["metrics"]["customer_basis"] == "all_respondents"
    assert card["components"]["price_fit"]["score"] is None
    assert card["success_score"] == 100.0
    assert card["verdict"]["code"] == "go"
    assert card["confidence"]["level"] == "low"


def test_scorecard_without_any_signal_has_no_score():
    card = compute_scorecard([_response("customer")])
    assert card["success_score"] is None
    assert card["verdict"] is None


@pytest.mark.parametrize("score,code", [(85, "go"), (70, "go"), (60, "conditional_go"), (45, "pivot"), (10, "no_go")])
def test_verdict_thresholds(score, code):
    assert verdict_for(score)["code"] == code


def test_scorecard_markdown_includes_score_and_korean_sections():
    card = compute_scorecard([_response("customer", intent=4, recommend=9)])
    markdown = render_scorecard_markdown({
        "scorecard": card,
        "insights": {"support_programs": ["예비창업패키지 — 사업자 등록 전"], "key_risks": []},
    })
    assert markdown.startswith("## 성공 예측 스코어카드")
    assert "성공 점수 83.3 / 100" in markdown  # (0.4*75 + 0.2*100) / 0.6
    assert "예비창업패키지" in markdown
    assert "핵심 리스크" not in markdown


def test_report_assembly_inserts_preamble_after_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "_get_report_folder", classmethod(lambda cls, rid: str(tmp_path)))
    monkeypatch.setattr(ReportManager, "_get_report_markdown_path", classmethod(lambda cls, rid: str(tmp_path / "full.md")))
    monkeypatch.setattr(ReportManager, "get_generated_sections", classmethod(
        lambda cls, rid: [{"content": "## 시장 반응\n\n본문\n\n"}]
    ))
    outline = ReportOutline(title="제목", summary="요약", sections=[])
    markdown = ReportManager.assemble_full_report("r1", outline, preamble="## 성공 예측 스코어카드\n\n표")
    assert markdown.index("> 요약") < markdown.index("## 성공 예측 스코어카드") < markdown.index("시장 반응")
    # 머리말은 후처리 뒤에 붙으므로 제목 정리（## → 굵은 글씨）를 겪지 않는다
    assert "## 성공 예측 스코어카드" in markdown and "## 시장 반응" not in markdown


# ── 한국인 페르소나 패널 ──

def _persona(i, age, sex="여자", province="서울", occupation="회사원"):
    return {
        "uuid": f"u{i}", "persona": f"김민지 씨는 {age}세 {occupation}입니다.", "age": age, "sex": sex,
        "province": province, "district": f"{province}-중구", "occupation": occupation,
        "hobbies_and_interests_list": "['요가', '카페 탐방']", "family_type": "혼자 거주",
        "housing_type": "아파트", "education_level": "4년제 대학교", "marital_status": "미혼",
    }


def test_target_filter_normalization_maps_province_aliases_and_clamps_age():
    target = normalize_target_filter({
        "age_min": 10, "age_max": "200", "sex": "female", "provinces": ["경남", "서울특별시", "Mars"],
        "occupation_keywords": ["개발자", ""],
    })
    assert target["age_min"] == 19 and target["age_max"] == 99
    assert target["sex"] is None
    assert target["provinces"] == ["경상남", "서울"]
    assert target["occupation_keywords"] == ["개발자"]


def test_select_personas_prefers_matches_and_relaxes_when_short():
    pool = [_persona(i, 25 + i % 10) for i in range(20)]
    pool += [_persona(100 + i, 30, occupation="소프트웨어 개발자") for i in range(4)]
    pool += [_persona(200 + i, 70, sex="남자", province="부산") for i in range(10)]
    target = normalize_target_filter({"age_min": 20, "age_max": 39, "sex": "여자", "provinces": ["서울"],
                                      "occupation_keywords": ["개발자"]})
    chosen, relaxed = select_personas(pool, target, 10, random.Random(0))
    assert len(chosen) == 10 and relaxed == []
    assert sum("개발자" in row["occupation"] for row in chosen) == 4
    assert all(row["province"] == "서울" for row in chosen)

    chosen, relaxed = select_personas(pool, target, 30, random.Random(0))
    assert len(chosen) == 30
    assert relaxed[0] == "provinces"


def test_persona_profile_and_panel_builder(tmp_path):
    profile = persona_to_profile(_persona(1, 28), user_id=7)
    assert profile.user_id == 7 and profile.name == "김민지"
    assert profile.gender == "female" and profile.country == "대한민국"
    assert profile.interested_topics == ["요가", "카페 탐방"]
    assert profile.source_entity_type == PANEL_ENTITY_TYPE

    class FakeLLM:
        def chat_json(self, **kwargs):
            return {"age_min": 20, "age_max": 35, "sex": "여자", "provinces": []}

    pool = [_persona(i, 20 + i) for i in range(40)]
    profiles, configs, meta = KoreanPersonaPanelBuilder(llm_client=FakeLLM(), seed=1).build(
        "타깃: 20~30대 여성", start_id=5, size=8, pool=pool
    )
    assert [p.user_id for p in profiles] == list(range(5, 13))
    assert [c.agent_id for c in configs] == list(range(5, 13))
    assert all(20 <= p.age <= 35 for p in profiles)
    assert meta["panel_size"] == 8 and meta["enabled"] is True

    _, _, meta = KoreanPersonaPanelBuilder(llm_client=FakeLLM()).build("x", start_id=0, size=8, pool=[])
    assert meta["enabled"] is False


def test_business_report_guidance_appends_korea_context():
    from app.services.report_agent import BUSINESS_REPORT_GUIDANCE, _business_report_guidance

    guidance = _business_report_guidance()
    assert guidance.startswith(BUSINESS_REPORT_GUIDANCE)
    assert "한국 시장·정책 참고자료" in guidance
    assert "예비창업패키지" in guidance


def test_success_evaluator_end_to_end_with_fake_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services import success_evaluator as module

    sim_dir = tmp_path / "sim_1"
    sim_dir.mkdir()
    (sim_dir / "simulation_config.json").write_text(json.dumps({"agent_configs": [
        {"agent_id": 0, "entity_name": "경쟁사A", "entity_type": "CompetitorCompany"},
        {"agent_id": 1, "entity_name": "김민지", "entity_type": PANEL_ENTITY_TYPE},
        {"agent_id": 2, "entity_name": "이서준", "entity_type": PANEL_ENTITY_TYPE},
        {"agent_id": 3, "entity_name": "펫뉴스", "entity_type": "MediaOutlet"},
    ]}), encoding="utf-8")

    monkeypatch.setattr(module.SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(module.SimulationRunner, "check_env_alive", classmethod(lambda cls, sid: True))
    state = SimpleNamespace(project_id="p1", get_default_platform=lambda: "reddit")
    monkeypatch.setattr(module.SimulationManager, "get_simulation", lambda self, sid: state)
    project = SimpleNamespace(
        business_brief=normalize_business_brief({"name": "수제간식 구독", "price": 30000}),
        simulation_requirement="",
    )
    monkeypatch.setattr(module.ProjectManager, "get_project", classmethod(lambda cls, pid: project))

    answers = {
        0: {"purchase_intent": 1, "recommend_score": 0, "stance": -2},
        1: '{"purchase_intent": 5, "willingness_to_pay": 35000, "recommend_score": 9, "stance": 2, "main_reason": "편해요"}',
        2: '{"purchase_intent": 3, "willingness_to_pay": 20000, "recommend_score": 6, "stance": 0}',
        3: "응답 불가",
    }
    batches = []

    def fake_batch(cls, simulation_id, interviews, platform=None, timeout=None):
        batches.append((platform, [i["agent_id"] for i in interviews]))
        return {"success": True, "result": {"results": {
            f"reddit_{i['agent_id']}": {"agent_id": i["agent_id"], "response": answers[i["agent_id"]]}
            for i in interviews
        }}}

    monkeypatch.setattr(module.SimulationRunner, "interview_agents_batch", classmethod(fake_batch))

    class FakeLLM:
        def chat_json(self, messages, **kwargs):
            if "Entity types" in messages[1]["content"]:
                # 패널 유형은 LLM 분류 대상이 아니다
                assert PANEL_ENTITY_TYPE not in messages[1]["content"]
                return {"roles": {"CompetitorCompany": "competitor", "MediaOutlet": "media"}}
            return {"success_drivers": ["편의성"], "key_risks": ["가격"], "recommendations": [],
                    "support_programs": ["예비창업패키지"], "regulatory_checks": [], "summary": "조건부"}

    evaluation = module.SuccessEvaluator(llm_client=FakeLLM()).evaluate("sim_1")

    assert batches == [("reddit", [0, 1, 2, 3])]
    card = evaluation["scorecard"]
    assert card["metrics"]["respondents"] == 3 and card["metrics"]["surveyed"] == 4
    assert card["metrics"]["customer_respondents"] == 2
    assert card["metrics"]["price_fit_share"] == 50.0
    assert card["components"]["ecosystem"]["score"] is None  # 언론 응답은 해석 불가
    assert evaluation["insights"]["support_programs"] == ["예비창업패키지"]
    assert module.SuccessEvaluator.load("sim_1")["scorecard"] == card
