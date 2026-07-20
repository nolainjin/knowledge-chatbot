from pathlib import Path

from fastapi.testclient import TestClient

from app import chat
from app.config import Settings
from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")

FAKE_SETTINGS = Settings(
    anthropic_api_key="",
    knowledge_dir=KNOWLEDGE_DIR,
    model="fake",
    trust_proxy_hops=0,
    daily_request_cap=500,
)

client = TestClient(app)


def test_handle_message_schema_mode_returns_intake_question():
    result = chat.handle_message("session-basic", "라포 형성 방법이 궁금해요", FAKE_SETTINGS)
    assert result["limit_reached"] is False
    assert result["turn"] == 1
    assert "오늘 상담을 받으러 오신 가장 큰 이유" in result["reply"]
    assert "intake" in result


def test_handle_message_with_no_matching_docs_still_runs_intake_flow():
    result = chat.handle_message("session-no-match", "zzz qqq 없는 단어", FAKE_SETTINGS)
    assert result["limit_reached"] is False
    assert "오늘 상담을 받으러 오신 가장 큰 이유" in result["reply"]


def test_11th_message_is_rejected():
    session_id = "session-cap"
    for i in range(chat.MAX_TURNS):
        result = chat.handle_message(session_id, f"질문 {i}", FAKE_SETTINGS)
        assert result["limit_reached"] is False
        assert result["turn"] == i + 1

    eleventh = chat.handle_message(session_id, "열한번째 질문", FAKE_SETTINGS)
    assert eleventh["limit_reached"] is True
    assert eleventh["turn"] == chat.MAX_TURNS


def test_api_chat_endpoint_happy_path(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    monkeypatch.setenv("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "api-basic",
            "participant_id": "person-api-basic",
            "message": "라포 형성 방법이 궁금해요",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn"] == 1
    assert body["limit_reached"] is False
    assert "reply" in body


def test_api_chat_rejects_empty_message(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    response = client.post("/api/chat", json={"session_id": "api-empty", "message": ""})
    assert response.status_code == 400


def test_api_chat_rejects_non_string_message(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    response = client.post("/api/chat", json={"session_id": "api-bad-type", "message": 12345})
    assert response.status_code == 400


def test_api_chat_rejects_message_over_2000_chars(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    response = client.post(
        "/api/chat", json={"session_id": "api-too-long", "message": "가" * 2001}
    )
    assert response.status_code == 400


def test_api_chat_rejects_missing_session_id(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 400


def test_api_chat_rejects_bad_participant_id(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    response = client.post(
        "/api/chat",
        json={"session_id": "api-bad-person", "participant_id": "../bad", "message": "hello"},
    )
    assert response.status_code == 400


def test_real_model_reply_uses_deterministic_slot_state(monkeypatch):
    """실응답 모드에서도 질문 반복 방지는 모델 출력이 아니라 슬롯 엔진이 맡는다."""
    settings = Settings(
        anthropic_api_key="",
        knowledge_dir=KNOWLEDGE_DIR,
        model="codex-cli",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )

    def fake_ask(**_kwargs):
        return "말씀하신 상태가 꽤 오래 이어져서 버거우셨겠어요. 한 가지만 더 확인할게요.\n```slots\n{}\n```"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    session_id = "real-deterministic-slots"
    first = chat.handle_message(session_id, "우울한 기분이 계속돼요.", settings)
    assert first["reply"].startswith("말씀하신 상태")
    assert first["intake"]["unfilled"][0]["id"] == "symptom_context"

    second = chat.handle_message(
        session_id, "긴장이 계속되는것부터가 시작인데, 회사에 가기 싫어요..", settings
    )
    assert second["intake"]["unfilled"][0]["id"] == "coping"

    slots = chat._sessions[session_id].slots
    assert slots["chief_complaint"] == "우울한 기분이 계속돼요."
    assert slots["symptom_context"] == "긴장이 계속되는것부터가 시작인데, 회사에 가기 싫어요.."
