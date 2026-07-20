"""Phase 4(실모드 단일 호출 추출 + 신뢰 경계 검증) 검증.

실모드에서 LLM 응답에 섞인 ```slots fenced JSON을 분리해 session.slots에
병합하는 경로와, LLM 출력이 신뢰 경계 밖이라는 전제 하에 스키마 활성 슬롯
화이트리스트·문자열 강제·길이 상한·기채움 보호(허용된 안전 승격 예외)를 함께 검증한다.
fake 모드(extract_fake, 결정론)와는 독립된 경로 — Phase 3 회귀는
test_slot_flow.py가 맡는다.
"""

import json
from datetime import date
from pathlib import Path

from app import chat
from app.config import Settings
from app.intake import extract_real, load_schema

_TEST_SCHEMA_MD = """# 접수 슬롯 스키마

기계 파싱용 YAML 블록:

```yaml
intake_schema:
  version: "1"
  opening_question: "오늘은 어떤 이야기를 나누고 싶으세요?"
  slots:
    - id: reason
      label: 방문사유
      required: true
      priority: 0
    - id: crisis_plan
      label: 위기계획
      required: false
      priority: 1
```
"""


def _settings(knowledge_dir) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=str(knowledge_dir),
        model="claude-real-test",  # "fake"가 아니면 실모드 경로
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def _write_schema(tmp_path):
    # Phase 3: load_documents가 0 문서 폴더를 이제 예외로 처리하므로, 스키마만
    # 있는 지식 폴더 fixture에도 검색 대상 문서를 하나 곁들인다(ripple).
    (tmp_path / "_intake_schema.md").write_text(_TEST_SCHEMA_MD, encoding="utf-8")
    (tmp_path / "dummy-doc.md").write_text("# 더미 문서\n\n테스트용 본문입니다.\n", encoding="utf-8")


# --- extract_real 단위 테스트: fenced 분리 + 파싱 실패 스킵 ---------------------


def test_extract_real_parses_fenced_json_and_strips_from_reply(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '방문 이유를 확인했습니다.\n```slots\n{"reason": "가족 갈등"}\n```'

    clean_reply, accepted = extract_real(raw, schema, {})

    assert accepted == {"reason": "가족 갈등"}
    assert "```slots" not in clean_reply
    assert "가족 갈등" not in clean_reply
    assert clean_reply.startswith("방문 이유를 확인했습니다.")


def test_extract_real_skips_when_no_fenced_block(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = "슬롯 블록 없이 그냥 응답만 왔습니다."

    clean_reply, accepted = extract_real(raw, schema, {})

    assert clean_reply == raw
    assert accepted == {}


def test_extract_real_skips_on_broken_json(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답 텍스트\n```slots\n{"reason": "가족 갈등"\n```'  # 닫는 중괄호 누락

    clean_reply, accepted = extract_real(raw, schema, {})

    assert clean_reply == raw  # 파싱 실패 → 원문 그대로, 추출 스킵
    assert accepted == {}


def test_extract_real_skips_when_json_is_not_an_object(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답 텍스트\n```slots\n["reason", "가족 갈등"]\n```'

    clean_reply, accepted = extract_real(raw, schema, {})

    assert clean_reply == raw
    assert accepted == {}


# --- extract_real 신뢰 경계 필터 4종 --------------------------------------------


def test_extract_real_discards_key_outside_active_whitelist(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답\n```slots\n{"reason": "가족 갈등", "not_a_real_slot": "무언가"}\n```'

    _, accepted = extract_real(raw, schema, {})

    assert accepted == {"reason": "가족 갈등"}


def test_extract_real_discards_non_string_value(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답\n```slots\n{"reason": 123, "crisis_plan": ["방법1", "방법2"]}\n```'

    _, accepted = extract_real(raw, schema, {})

    assert accepted == {}


def test_extract_real_discards_over_length_value(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답\n```slots\n{"reason": "' + ("가" * 201) + '"}\n```'

    _, accepted = extract_real(raw, schema, {})

    assert accepted == {}


def test_extract_real_accepts_value_at_length_limit(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    value = "가" * 200
    raw = '응답\n```slots\n{"reason": "' + value + '"}\n```'

    _, accepted = extract_real(raw, schema, {})

    assert accepted == {"reason": value}


def test_extract_real_does_not_overwrite_already_filled_slot(tmp_path):
    _write_schema(tmp_path)
    schema = load_schema(tmp_path)
    raw = '응답\n```slots\n{"reason": "새 이유"}\n```'

    _, accepted = extract_real(raw, schema, {"reason": "기존 이유"})

    assert accepted == {}


def test_extract_real_allows_declared_override_value(tmp_path):
    schema_md = """# 접수 슬롯 스키마

```yaml
intake_schema:
  version: "1"
  opening_question: "무슨 일인가요?"
  slots:
    - id: track
      label: 상담 트랙
      required: true
      priority: 0
      values: [정서, 위기]
      allow_override_values: [위기]
      signals:
        위기: [자해]
        정서: [우울]
```
"""
    (tmp_path / "_intake_schema.md").write_text(schema_md, encoding="utf-8")
    schema = load_schema(tmp_path)
    raw = '응답\n```slots\n{"track": "위기"}\n```'

    _, accepted = extract_real(raw, schema, {"track": "정서"})

    assert accepted == {"track": "위기"}


# --- handle_message 배선: 실모드 단일 호출 통합(D02) ----------------------------


def test_handle_message_real_mode_merges_extracted_slots(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    raw = '알겠습니다.\n```slots\n{"reason": "가족 갈등"}\n```'

    def fake_ask(system, history, user, doc_titles, settings):
        return raw

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    result = chat.handle_message("real-cap01", "가족 문제로 왔어요", _settings(tmp_path))
    session = chat._sessions["real-cap01"]

    assert session.slots == {"reason": "가족 갈등"}
    assert "```slots" not in result["reply"]
    assert "가족 갈등" not in result["reply"]


def test_handle_message_real_mode_parse_failure_keeps_reply_unchanged(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    raw = "슬롯 블록 없는 평범한 응답입니다."

    def fake_ask(system, history, user, doc_titles, settings):
        return raw

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    result = chat.handle_message("real-cap02", "가족 문제로 왔어요", _settings(tmp_path))
    session = chat._sessions["real-cap02"]

    assert session.slots == {}
    assert result["reply"] == raw  # 추출 스킵 — 응답 원문 그대로 유지


def test_handle_message_real_mode_injects_extraction_instruction(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        return "응답만 왔습니다."

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message("real-cap03", "가족 문제로 왔어요", _settings(tmp_path))

    assert "```slots" in captured["system"]


def test_fake_mode_system_prompt_has_no_extraction_instruction(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    captured = {}

    def fake_ask(system, history, user, doc_titles, settings):
        captured["system"] = system
        return "[fake] 응답"

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    fake_settings = Settings(
        anthropic_api_key="",
        knowledge_dir=str(tmp_path),
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )
    chat.handle_message("fake-cap01", "가족 문제로 왔어요", fake_settings)

    assert "```slots" not in captured["system"]


# --- history·storage에 슬롯 JSON 제거본 저장 ------------------------------------


def test_handle_message_real_mode_history_stores_clean_reply(tmp_path, monkeypatch):
    _write_schema(tmp_path)
    raw = '알겠습니다.\n```slots\n{"reason": "가족 갈등"}\n```'

    def fake_ask(system, history, user, doc_titles, settings):
        return raw

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message("real-cap04", "가족 문제로 왔어요", _settings(tmp_path))
    session = chat._sessions["real-cap04"]

    assistant_entries = [h for h in session.history if h["role"] == "assistant"]
    assert "```slots" not in assistant_entries[-1]["content"]
    assert "가족 갈등" not in assistant_entries[-1]["content"]


def test_handle_message_real_mode_storage_file_has_clean_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_schema(tmp_path)
    raw = '알겠습니다.\n```slots\n{"reason": "가족 갈등"}\n```'

    def fake_ask(system, history, user, doc_titles, settings):
        return raw

    monkeypatch.setattr(chat.llm, "ask", fake_ask)

    chat.handle_message("real-cap05", "가족 문제로 왔어요", _settings(tmp_path))

    day_dir = Path("data/conversations") / date.today().isoformat()
    turns = json.loads((day_dir / "real-cap05.json").read_text(encoding="utf-8"))["turns"]
    assistant_turn = next(t for t in turns if t["role"] == "assistant")

    assert "```slots" not in assistant_turn["text"]
    assert "가족 갈등" not in assistant_turn["text"]


def test_extract_real_rejects_value_outside_declared_set():
    """닫힌 값 집합 밖의 값은 폐기한다.

    실측 회귀: 모델이 track에 "work-related stress"를 넣었다. 통과시키면
    when 분기(`track=위기`)가 어긋나 조건부 슬롯이 통째로 안 켜진다.
    """
    from app.intake import Schema, Slot

    schema = Schema(
        version="1",
        opening_question="q",
        slots=[Slot(id="track", label="상담 트랙", values=["위기", "관계", "정서"])],
    )

    _, fills = extract_real('답변\n```slots\n{"track": "work-related stress"}\n```', schema, {})
    assert fills == {}

    _, fills = extract_real('답변\n```slots\n{"track": "정서"}\n```', schema, {})
    assert fills == {"track": "정서"}


def test_crisis_signal_beats_relationship_signal_in_same_message():
    """실측 회귀: 자살사고 내담자가 관계 트랙으로 샜다.

    "사는 게 의미가 없다"(위기 사전에 없던 완곡 표현) + "아내가"(관계 사전) 조합에서
    first-match가 관계를 집었다. 위기 사전을 넓혀 위기가 먼저 걸리게 한다.
    """
    from app.intake import extract_classification

    schema = load_schema("knowledge")
    msg = "회사 잘리고 나서 사는 게 의미가 없다는 생각이 들어요. 아내가 가보라고 해서 왔습니다."
    assert extract_classification(msg, schema, {})["track"] == "위기"


def test_spousal_bereavement_tracks_emotional_not_relationship():
    """'배우자와 사별'은 관계 갈등이 아니라 애도·정서 호소다."""
    from app.intake import extract_classification

    schema = load_schema("knowledge")
    msg = "작년에 배우자와 사별했고 아직도 너무 슬퍼요."

    assert extract_classification(msg, schema, {})["track"] == "정서"


def test_spousal_support_mention_tracks_emotional_not_relationship():
    from app.intake import extract_classification

    schema = load_schema("knowledge")
    msg = "요즘 우울해서 남편에게 도움을 요청했습니다."

    assert extract_classification(msg, schema, {})["track"] == "정서"


def test_model_can_escalate_track_to_crisis_over_existing_value():
    """사전에 없는 표현이라도 모델이 위험을 읽으면 위기로 승격할 수 있어야 한다."""
    from app.intake import Schema, Slot

    schema = Schema(
        version="1",
        opening_question="q",
        slots=[Slot(id="track", label="상담 트랙",
                    values=["위기", "관계", "정서"],
                    allow_override_values=["위기", "관계"])],
    )

    _, fills = extract_real('답변\n```slots\n{"track": "위기"}\n```', schema, {"track": "관계"})
    assert fills == {"track": "위기"}


def test_relationship_mention_does_not_override_established_emotional_track():
    """실측 회귀: 정서로 확정된 track이 나중에 배우자 언급 한 번에 관계로 덮어써졌다.

    emo-insomnia 페르소나 — "잠"으로 정서 확정 후, 지지체계를 묻는 질문에 답하며
    "아내"를 언급했더니 track이 관계로 바뀌었다. 관계 승격 자체는 유지하되
    override_signals.관계에 구체 갈등 신호를 요구해서, 단순 정체성 명사 언급은
    기존 정서 트랙을 덮지 못하게 한다.
    """
    from app.intake import extract_classification

    schema = load_schema("knowledge")
    first = "두 달 전부터 잠을 제대로 못 자고 있어요. 낮에는 계속 피곤하고요."
    assert extract_classification(first, schema, {})["track"] == "정서"

    later = "아내한테는 그냥 요즘 좀 피곤하다 정도로만 얘기했어요."
    fills = extract_classification(later, schema, {"track": "정서"})
    assert "track" not in fills  # 관계 신호가 있어도 정서를 덮어쓰지 않는다


def test_crisis_signal_still_overrides_established_relationship_track():
    """위기 승격은 여전히 살아 있어야 한다 — 관계 제거가 안전 경로까지 막으면 안 된다."""
    from app.intake import extract_classification

    schema = load_schema("knowledge")
    fills = extract_classification("이제 사는 게 의미가 없어요.", schema, {"track": "관계"})
    assert fills["track"] == "위기"

def test_reject_signals_block_past_attempt_as_current_plan():
    """과거 시도 이력 표현은 현재 계획·수단 슬롯을 채우면 안 된다."""
    from app.intake import extract_fake

    schema = load_schema("knowledge")
    filled = {"track": "위기", "chief_complaint": "죽고 싶다는 생각이 들어요"}
    msg = "예전에 약을 많이 먹으려고 한 적이 있는데 용기가 안 났어요."

    fills = extract_fake(msg, schema, filled)

    assert "crisis_plan_means" not in fills
    assert fills["crisis_attempt_history"] == msg


def test_reject_signals_allow_explicit_current_plan_answer():
    """과거 이력과 현재 수단이 함께 나오면 현재 계획·수단은 보존한다."""
    from app.intake import extract_fake

    schema = load_schema("knowledge")
    filled = {"track": "위기", "chief_complaint": "죽고 싶다는 생각이 들어요"}
    msg = "예전에 자해한 적도 있고, 지금은 약을 모아둔 게 있어요."

    fills = extract_fake(msg, schema, filled)

    assert fills["crisis_plan_means"] == msg
    assert fills["crisis_attempt_history"] == msg


def test_extract_real_applies_reject_signals_with_source_message():
    """실모드 LLM이 잘못 채운 슬롯도 원 발화 guard로 폐기한다."""
    from app.intake import extract_real

    schema = load_schema("knowledge")
    raw = (
        "과거 이력을 확인했습니다.\n"
        "```slots\n"
        '{"crisis_plan_means": "예전에 약을 먹으려고 한 적", '
        '"crisis_attempt_history": "예전에 약을 먹으려고 한 적"}\n'
        "```"
    )
    filled = {"track": "위기", "chief_complaint": "죽고 싶다는 생각이 들어요"}
    msg = "예전에 약을 많이 먹으려고 한 적이 있는데 용기가 안 났어요."

    _, fills = extract_real(raw, schema, filled, msg)

    assert "crisis_plan_means" not in fills
    assert fills["crisis_attempt_history"] == "예전에 약을 먹으려고 한 적"
