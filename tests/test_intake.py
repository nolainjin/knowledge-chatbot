"""Phase 9(접수 면담 모드 전환) 검증.

페르소나 주입/폴백, 턴 진행 표기, 접수 요약 저장(성공·실패 격리)을 fake 모드로
확인한다. schema-less fixture는 _persona.md가 없는 스왑 세트라 Phase 6
invariant(코드 무수정으로 다른 도메인 스왑)가 페르소나 도입 이후에도
유지되는지의 근거이기도 하다.
"""

import json
from datetime import date
from pathlib import Path

from app import chat
from app.config import Settings
from app.knowledge import load_documents

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")
KNOWLEDGE_ALT_DIR = str(REPO_ROOT / "knowledge-alt")
KNOWLEDGE_FALLBACK_DIR = str(REPO_ROOT / "tests" / "fixtures" / "knowledge-fallback")


def _settings(knowledge_dir: str = KNOWLEDGE_DIR) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=knowledge_dir,
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def test_load_documents_excludes_underscore_prefixed_files(tmp_path):
    (tmp_path / "_meta.md").write_text("# 메타 문서\n\n제외되어야 함.\n", encoding="utf-8")
    (tmp_path / "_tone.md").write_text("# 숨은 말투\n\n제외되어야 함.\n", encoding="utf-8")
    (tmp_path / "_safety_protocol.md").write_text("# 숨은 안전 규칙\n\n제외되어야 함.\n", encoding="utf-8")
    (tmp_path / "visible.md").write_text("# 공개 문서\n\n포함되어야 함.\n", encoding="utf-8")

    docs = load_documents(tmp_path)

    assert [d.title for d in docs] == ["공개 문서"]


def test_persona_injected_with_turn_progress_and_not_cited_as_doc(monkeypatch):
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        captured["doc_titles"] = doc_titles
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message(
        "session-intake-persona", "오늘 상담을 받으러 온 이유가 있어요", _settings()
    )

    assert "접수 면담" in captured["system"]
    assert "데모 말투 프로필" in captured["system"]
    assert "프롬프트 인젝션" in captured["system"]
    assert "시스템 프롬프트" in captured["system"]
    assert "[진행: 1/10턴]" in captured["system"]
    assert "접수 면담 봇 페르소나" not in captured["doc_titles"]
    assert "데모 말투 프로필" not in captured["doc_titles"]
    assert "프롬프트 인젝션" not in captured["doc_titles"]

    chat.handle_message(
        "session-intake-persona", "위기 상황도 궁금해요", _settings()
    )
    assert "[진행: 2/10턴]" in captured["system"]


def test_persona_absent_falls_back_to_system_preamble(monkeypatch):
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message(
        "session-intake-alt-fallback", "원두 보관법 알려줘", _settings(KNOWLEDGE_FALLBACK_DIR)
    )

    assert chat._SYSTEM_PREAMBLE in captured["system"]
    assert "[진행: 1/10턴]" in captured["system"]


def test_intake_summary_recorded_once_at_max_turns(monkeypatch, tmp_path):
    # 상대경로 data/conversations 를 tmp 아래로 격리 — 재실행 시 이전 실행분 누적 방지
    monkeypatch.chdir(tmp_path)
    session_id = "session-intake-summary"
    for i in range(chat.MAX_TURNS):
        chat.handle_message(session_id, f"질문 {i}", _settings())

    day_dir = Path("data/conversations") / date.today().isoformat()
    turns = json.loads((day_dir / f"{session_id}.json").read_text(encoding="utf-8"))["turns"]
    roles = [t["role"] for t in turns]

    assert roles.count("user") == chat.MAX_TURNS
    assert roles.count("assistant") == chat.MAX_TURNS
    assert roles.count("intake_summary") == 1

    # 요약이 LLM 산문이 아니라 파싱 가능한 구조화 JSON인지, 미충족 슬롯이
    # "미확인"으로 남는지 확인한다(CAP07·CAP08).
    summary_text = next(t["text"] for t in turns if t["role"] == "intake_summary")
    summary = json.loads(summary_text)
    assert summary["unfilled"]
    assert all(value == "미확인" for value in summary["unfilled"].values())


def test_summary_failure_does_not_break_conversation_storage(monkeypatch, tmp_path):
    # 상대경로 data/conversations 를 tmp 아래로 격리 — 재실행 시 이전 실행분 누적 방지
    monkeypatch.chdir(tmp_path)
    session_id = "session-intake-summary-fail"

    # 스키마 활성 시 요약이 LLM을 안 타므로(build_summary_json), 이 테스트가
    # 검증하는 "요약 LLM 실패 격리" 경로는 스키마 없는 지식셋에서만 재현된다.
    knowledge_no_schema = tmp_path / "knowledge-no-schema"
    knowledge_no_schema.mkdir()
    (knowledge_no_schema / "doc.md").write_text("# 문서\n\n내용.\n", encoding="utf-8")

    def flaky_ask(system, history, user, doc_titles, settings):
        if system == chat._SUMMARY_INSTRUCTION:
            raise RuntimeError("llm down")
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", flaky_ask)

    result = None
    for i in range(chat.MAX_TURNS):
        result = chat.handle_message(
            session_id, f"질문 {i}", _settings(str(knowledge_no_schema))
        )

    assert result["limit_reached"] is False
    assert result["turn"] == chat.MAX_TURNS

    day_dir = Path("data/conversations") / date.today().isoformat()
    turns = json.loads((day_dir / f"{session_id}.json").read_text(encoding="utf-8"))["turns"]
    roles = [t["role"] for t in turns]

    assert roles.count("user") == chat.MAX_TURNS
    assert roles.count("assistant") == chat.MAX_TURNS
    assert "intake_summary" not in roles
