"""Phase 3(fake 슬롯 루프 통합) 검증.

Phase 1(파서)·Phase 2(스키마)를 handle_message 대화 루프에 배선한 결과를
확인한다: 시스템 프롬프트 슬롯 섹션 주입, fake 모드 다중 슬롯 결정론 추출,
조건부 활성 배선, 레드플래그 우선 정렬, 기채움 슬롯 보호, schema-less fixture
경로의 기존 동작 유지.
"""

from pathlib import Path

from app import chat
from app.config import Settings
from app.intake import load_schema

_TEST_SCHEMA_MD = """# 접수 슬롯 스키마

기계 파싱용 YAML 블록:

```yaml
intake_schema:
  version: "1"
  opening_question: "오늘은 어떤 이야기를 나누고 싶으세요?"
  slots:
    - id: track
      label: 상담 트랙
      required: true
      priority: 0
      values: [개인, 위기]
      signals:
        개인: [혼자]
        위기: [자해, 죽고 싶]
    - id: reason
      label: 방문사유
      required: true
      priority: 1
      signals: [이유, 계기]
    - id: crisis_plan
      label: 위기계획
      required: false
      priority: 2
      when: "track=위기"
      red_flag: true
      signals: [계획, 방법]
```
"""

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_FALLBACK_DIR = str(REPO_ROOT / "tests" / "fixtures" / "knowledge-fallback")


def _settings(knowledge_dir) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=str(knowledge_dir),
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def _write_schema(tmp_path):
    # Phase 3: load_documents가 0 문서 폴더를 이제 예외로 처리하므로, 스키마만
    # 있는 지식 폴더 fixture에도 검색 대상 문서를 하나 곁들인다(ripple).
    (tmp_path / "_intake_schema.md").write_text(_TEST_SCHEMA_MD, encoding="utf-8")
    (tmp_path / "dummy-doc.md").write_text("# 더미 문서\n\n테스트용 본문입니다.\n", encoding="utf-8")


def test_first_turn_system_prompt_includes_opening_question(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message("slot-cap12", "안녕하세요", _settings(tmp_path))

    schema = load_schema(tmp_path)
    assert schema.opening_question in captured["system"]


def test_track_fill_activates_conditional_crisis_slot(tmp_path):
    _write_schema(tmp_path)

    chat.handle_message("slot-cap02", "자해 생각이 자꾸 들어요", _settings(tmp_path))

    session = chat._sessions["slot-cap02"]
    assert session.slots["track"] == "위기"

    schema = load_schema(tmp_path)
    active_ids = [slot.id for slot in schema.active_slots(session.slots)]
    assert "crisis_plan" in active_ids


def test_single_utterance_fills_two_slots_at_once(tmp_path):
    _write_schema(tmp_path)

    result = chat.handle_message("slot-cap04", "혼자 왔는데 이유가 있어서요", _settings(tmp_path))

    session = chat._sessions["slot-cap04"]
    assert session.slots == {"track": "개인", "reason": "이유"}
    assert "상담 트랙=개인" in result["reply"]
    assert "방문사유=이유" in result["reply"]


def test_red_flag_signal_reorders_unfilled_slots_to_top(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    # "자해"로 트랙=위기가 이번 턴에 채워지며 crisis_plan이 막 활성화되고,
    # 동시에 "계획"이 crisis_plan의 red_flag signal에 걸린다 — priority(2)가
    # reason(priority 1)보다 낮은데도 레드플래그 정렬로 먼저 나와야 한다.
    chat.handle_message("slot-cap05", "자해할 계획이에요", _settings(tmp_path))

    system = captured["system"]
    section_start = system.index("미충족 슬롯")
    assert system.index("위기계획", section_start) < system.index("방문사유", section_start)


def test_already_filled_slot_is_not_overwritten(tmp_path):
    _write_schema(tmp_path)
    session_id = "slot-cap-noflip"

    chat.handle_message(session_id, "혼자 왔어요", _settings(tmp_path))
    session = chat._sessions[session_id]
    assert session.slots["track"] == "개인"

    result = chat.handle_message(session_id, "사실 자해 생각도 있어요", _settings(tmp_path))

    assert session.slots["track"] == "개인"
    assert "채움:" not in result["reply"]  # 이미 채워진 track은 이번 턴 신규 채움이 아니다


def test_schema_less_fixture_keeps_existing_behavior():
    result = chat.handle_message(
        "slot-no-schema",
        "원두 보관법 알려줘",
        _settings(KNOWLEDGE_FALLBACK_DIR),
    )

    assert "채움:" not in result["reply"]
    assert "다음 질문:" not in result["reply"]
