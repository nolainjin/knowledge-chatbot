"""인용 게이트 — 날조(집합 밖 경로)만 거부하고, '인용 없음'은 거부하지 않는다.

2026-07-20 실측 회귀: 문서가 주입됐는데 인용이 없다고 일괄 거부(구 F2)했더니,
"이 자료엔 그 하위주제가 없지만 관련해선 …"라는 **정직하고 유용한 근거 응답**이
통째로 "관련 내용을 찾지 못했습니다"로 대체됐다. 무관 질의는 관련성 바닥
(knowledge.search 0건)이 이미 막으므로, 인용 유무로 거부하지 않는다. 날조 방어는
'집합 밖 인용 경로' 검사(test_citation.py)와 검색 바닥이 담당한다.
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


def test_real_reply_without_citation_is_not_refused(monkeypatch):
    """회귀 방지: 문서가 주입된 정직한 무인용 답은 거부되지 않고 그대로 전달된다.
    (질의는 관련성 바닥을 통과해 문서를 얻었으므로 무관 질의가 아니다.)"""
    honest = "메타인지의 신경 기질은 이 자료에 없지만, 자료는 메타인지의 개념과 학습 역할을 다룹니다."
    monkeypatch.setattr(chat.llm, "ask", lambda **_kwargs: honest)
    chat._sessions.pop("cite-missing", None)

    result = chat.handle_message("cite-missing", _QUERY, _settings("claude-cli"))

    assert result["reply"] == honest
    assert result["reply"] != chat._NO_GROUNDING_REPLY


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
