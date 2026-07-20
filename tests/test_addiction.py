from pathlib import Path

import pytest

from app import addiction, chat
from app.config import Settings
from app.intake import load_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")


def _settings(model: str = "fake") -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=KNOWLEDGE_DIR,
        model=model,
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def test_addiction_detection_requires_problem_context():
    assert addiction.assess("회식에서 술 한잔 마셨어요") is None
    assert addiction.assess("친구와 게임을 했어요") is None


@pytest.mark.parametrize(
    ("message", "kind", "severity"),
    [
        ("도박 빚이 생겼고 통제가 안 돼요", "도박", "고위험"),
        ("매일 술을 못 끊고 있어요", "알코올", "고위험"),
        ("매일 소주를 마셔야 잠이 오고 끊으려 하면 손이 떨려요", "알코올", "고위험"),
        ("스마트폰 과의존인지 걱정돼요", "인터넷·스마트폰·게임", "평가 필요"),
        ("약을 너무 많이 먹었고 의식을 잃었어요", "마약·약물", "응급"),
    ],
)
def test_addiction_assessment_classifies_type_and_severity(message, kind, severity):
    assessment = addiction.assess(message)

    assert assessment is not None
    assert assessment.kind == kind
    assert assessment.severity == severity


@pytest.mark.parametrize(
    ("assessment", "expected"),
    [
        (addiction.AddictionAssessment("도박", "평가 필요"), "1336"),
        (addiction.AddictionAssessment("마약·약물", "고위험"), "1342"),
        (addiction.AddictionAssessment("인터넷·스마트폰·게임", "평가 필요"), "1599-0075"),
        (addiction.AddictionAssessment("알코올", "고위험"), "중독관리통합지원센터"),
        (addiction.AddictionAssessment("마약·약물", "응급"), "119"),
    ],
)
def test_addiction_reply_uses_verified_contact_for_type_and_severity(assessment, expected):
    reply = addiction.build_reply(assessment)

    assert expected in reply
    assert addiction.CENTER_DIRECTORY_URL in reply
    assert "진단" not in reply


def test_addiction_route_skips_llm_and_completes_specialist_handoff(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fail_if_called(**_kwargs):
        raise AssertionError("중독 전문기관 경로에서는 LLM을 호출하면 안 됩니다")

    monkeypatch.setattr(chat.llm, "ask", fail_if_called)
    result = chat.handle_message(
        "addiction-alcohol-high",
        "매일 술을 못 끊어서 생활이 무너지고 있어요",
        _settings("codex-cli"),
        participant_id="person-addiction-alcohol",
    )

    slots = chat._sessions["addiction-alcohol-high"].slots
    assert slots == {
        "track": "중독",
        "addiction_type": "알코올",
        "addiction_severity": "고위험",
        "addiction_referral": "전문기관 정보 제공",
        "chief_complaint": "매일 술을 못 끊어서 생활이 무너지고 있어요",
    }
    assert result["intake"]["unfilled"] == []
    assert "일반 상담을 이어가기보다" in result["reply"]
    assert "중독관리통합지원센터" in result["reply"]


def test_addiction_followup_keeps_higher_observed_severity(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    session_id = "addiction-severity-monotonic"

    first = chat.handle_message(session_id, "도박 빚이 생겼고 통제가 안 돼요", _settings())
    followup = chat.handle_message(session_id, "응급 신호는 없어요", _settings())

    assert chat._sessions[session_id].slots["addiction_severity"] == "고위험"
    assert "1336" in followup["reply"]
    assert followup["reply"] != first["reply"]
    assert "급한 신호가 생기면" not in followup["reply"]
    assert followup["intake"]["unfilled"] == []

def test_addiction_followup_changes_question_instead_of_repeating(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    session_id = "addiction-no-repeat"

    first = chat.handle_message(
        session_id,
        "중독 문제로 도움받을 전문기관을 찾고 있어요",
        _settings(),
    )
    second = chat.handle_message(session_id, "응급 신호는 없어요", _settings())

    assert "의료 도움이 필요한 신호가 있나요?" in first["reply"]
    assert "의료 도움이 필요한 신호가 있나요?" not in second["reply"]
    assert "어느 문제에 가장 가까운가요?" in second["reply"]
    assert first["reply"] != second["reply"]


def test_crisis_signal_has_priority_over_addiction_route(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = chat.handle_message(
        "addiction-with-suicide-crisis",
        "술을 못 끊어서 죽고 싶다는 생각이 들어요",
        _settings(),
    )

    assert chat._sessions["addiction-with-suicide-crisis"].slots["track"] == "위기"
    assert "자살예방상담전화 109" in result["reply"]
    assert "중독관리통합지원센터" not in result["reply"]


def test_addiction_track_disables_general_counseling_slots():
    schema = load_schema(KNOWLEDGE_DIR)
    assert schema is not None

    active_ids = {
        slot.id
        for slot in schema.active_slots(
            {
                "track": "중독",
                "chief_complaint": "도박 문제",
                "addiction_type": "도박",
                "addiction_severity": "평가 필요",
                "addiction_referral": "전문기관 정보 제공",
            }
        )
    }

    assert {"coping", "support", "expectation"}.isdisjoint(active_ids)
    assert {"addiction_type", "addiction_severity", "addiction_referral"} <= active_ids
