"""F2 (MED-1): 인용 게이트가 provenance 아닌 membership만 검사하던 구멍.

코칭모드에서 (A) `근거:` 줄이 아예 없는 답이 무검증 통과하던 것을 닫는다:
문서가 주입됐는데 실모드 답에 유효 인용이 0개면 고정거부. fake 모드와
정상 인용(F1 통과) 답은 통과해야 한다.
"""

from pathlib import Path

from app import chat, knowledge
from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SRL_PACK = str(REPO_ROOT / "data" / "knowledge-srl-pack")

# 실팩(data/)은 로컬 전용이라 공개 배포엔 없다 — 없으면 이 모듈 전체를 skip.
if not Path(SRL_PACK).is_dir():
    import pytest

    pytest.skip("로컬 SRL 팩(data/knowledge-srl-pack) 필요", allow_module_level=True)

_QUERY = "메타인지란 무엇이고 왜 중요한가요?"


def _settings(model: str) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=SRL_PACK,
        model=model,
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def test_real_reply_without_any_citation_is_refused(monkeypatch):
    """코칭모드 + 문서 주입됐는데 유효 인용 0개(근거 줄 없음) → 고정거부."""
    monkeypatch.setattr(
        chat.llm, "ask", lambda **_kwargs: "메타인지는 자기 사고를 점검하는 능력입니다."
    )
    chat._sessions.pop("cite-missing", None)

    result = chat.handle_message("cite-missing", _QUERY, _settings("claude-cli"))

    assert result["reply"] == chat._NO_GROUNDING_REPLY


def test_real_reply_with_valid_citation_passes(monkeypatch):
    """유효 인용(주입 문서 실경로)을 달면 통과 — F1 통과한 정상 grounded 답."""
    docs = knowledge.search(_QUERY, knowledge.load_documents(SRL_PACK))
    assert docs, "테스트 전제: 이 질문은 근거를 찾아야 한다"
    real_path = docs[0].rel_path

    monkeypatch.setattr(
        chat.llm,
        "ask",
        lambda **_kwargs: f"메타인지는 자기 사고를 점검하는 능력입니다.\n근거: {real_path}",
    )
    chat._sessions.pop("cite-valid", None)

    result = chat.handle_message("cite-valid", _QUERY, _settings("claude-cli"))

    assert result["reply"] != chat._NO_GROUNDING_REPLY
    assert real_path in result["reply"]


def test_fake_mode_reply_without_md_citation_is_not_refused():
    """fake 데모 요약은 .md 인용 형식이 아니어도 거부되면 안 된다(F2가 실모드 한정)."""
    chat._sessions.pop("cite-fake", None)

    result = chat.handle_message("cite-fake", _QUERY, _settings("fake"))

    assert result["reply"] != chat._NO_GROUNDING_REPLY
