"""Phase 7: CAP05(인용 표시) + CAP06(코드 대조) 회귀.

MODEL=fake로는 이 기능을 검증할 수 없다 -- `app/llm.py`의 `_fake_reply`/
`_fake_document_summary`가 인용을 주입 집합(docs) 그 자체에서 파생하므로
fake는 구조적으로 집합 밖 문서명을 낼 수 없다(GM5 노트). 따라서
`chat.llm.ask`를 직접 monkeypatch해 날조 응답을 주입하고 코드 대조가
응답 전체를 차단하는지 단언한다.
"""

from pathlib import Path

from app import chat, knowledge
from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_WIKI_DIR = str(REPO_ROOT / "knowledge-wiki")
KNOWLEDGE_INTAKE_DIR = str(REPO_ROOT / "knowledge")  # 접수 팩 -- 대조 면제(코칭 모드 한정)


def _settings(knowledge_dir: str, model: str) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=knowledge_dir,
        model=model,
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def test_fabricated_citation_blocks_entire_reply(monkeypatch):
    """집합 밖 문서명을 인용하면 응답 전체가 고정 거부로 바뀐다(부분 제거 아님)."""
    monkeypatch.setattr(
        chat.llm,
        "ask",
        lambda **_kwargs: "근거: 존재하지-않는-문서.md 에 따르면 답은 42입니다.",
    )
    chat._sessions.pop("citation-fabricated", None)

    result = chat.handle_message(
        "citation-fabricated",
        "위키 질문은 어떻게 시작하나요?",
        _settings(KNOWLEDGE_WIKI_DIR, "claude-cli"),
    )

    assert result["reply"] == chat._NO_GROUNDING_REPLY
    assert "42" not in result["reply"]
    assert "존재하지-않는-문서" not in result["reply"]


def test_legitimate_citation_of_injected_doc_passes_through(monkeypatch):
    """주입된 문서의 실제 path를 그대로 인용하면 차단되지 않는다(과잉 차단 방지)."""
    message = "위키 질문은 어떻게 시작하나요?"
    docs = knowledge.search(message, knowledge.load_documents(KNOWLEDGE_WIKI_DIR))
    assert docs, "테스트 전제: 이 질문은 최소 1건의 문서를 찾아야 한다"
    real_path = docs[0].rel_path

    monkeypatch.setattr(
        chat.llm,
        "ask",
        lambda **_kwargs: f"위키 질문은 첫 안내문으로 시작합니다.\n근거: {real_path}",
    )
    chat._sessions.pop("citation-legit", None)

    result = chat.handle_message(
        "citation-legit", message, _settings(KNOWLEDGE_WIKI_DIR, "claude-cli")
    )

    assert result["reply"] != chat._NO_GROUNDING_REPLY
    assert real_path in result["reply"]


def test_nfc_citation_of_nfd_korean_path_is_not_refused(monkeypatch, tmp_path):
    """macOS 실팩 실측(2026-07-18): 한글 rel_path는 파일시스템에서 NFD인데 모델은
    NFC로 재출력한다 -- 정규화 없는 바이트 대조는 정상 인용을 날조로 오판해
    전량 고정 거부했다. 대조는 NFC 정규화로 해야 한다."""
    import unicodedata

    stem_nfd = unicodedata.normalize("NFD", "학습노트")
    stem_nfc = unicodedata.normalize("NFC", "학습노트")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / f"{stem_nfd}.md").write_text(
        "# 학습노트\n\n피드백은 자기조절학습을 돕는다.\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        chat.llm,
        "ask",
        lambda **_kwargs: f"피드백은 자기조절학습을 돕습니다.\n근거: {stem_nfc}.md",
    )
    chat._sessions.pop("citation-nfc", None)

    result = chat.handle_message(
        "citation-nfc", "피드백의 역할은?", _settings(str(pack), "claude-cli")
    )

    assert result["reply"] != chat._NO_GROUNDING_REPLY


def test_citation_crosscheck_is_skipped_outside_coaching_mode(monkeypatch):
    """RED03 -- 접수 팩(스키마 있음)은 코칭 전용 인용 대조 대상이 아니다."""
    monkeypatch.setattr(
        chat.llm,
        "ask",
        lambda **_kwargs: "근거: 존재하지-않는-문서.md 라고 적어도 접수 흐름은 이어진다.\n```slots\n{}\n```",
    )
    chat._sessions.pop("citation-intake-exempt", None)

    result = chat.handle_message(
        "citation-intake-exempt",
        "우울한 기분이 계속돼요.",
        _settings(KNOWLEDGE_INTAKE_DIR, "codex-cli"),
    )

    assert result["reply"] != chat._NO_GROUNDING_REPLY
    assert "존재하지-않는-문서" in result["reply"]
