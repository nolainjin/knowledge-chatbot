"""Phase 1(스키마 파서 + 슬롯 모델) 검증.

load_schema의 None 폴백 계약(부재·YAML 오류·필수 키 누락)과 Schema의
조건부 활성(when)·우선순위 정렬·레드플래그 최상단 정렬을 확인한다.
"""

from app.intake import load_schema

_VALID_SCHEMA_MD = """# 접수 슬롯 스키마

기계 파싱용 YAML 블록:

```yaml
intake_schema:
  version: "1"
  opening_question: "오늘 무엇을 도와드릴까요?"
  slots:
    - id: track
      label: 상담 트랙
      required: true
      priority: 0
      values: [개인상담, 커플상담]
      signals:
        개인상담: [혼자, 개인]
        커플상담: [연인, 부부]
    - id: visit_reason
      label: 방문 사유
      required: true
      priority: 1
      signals: [이유, 계기]
    - id: crisis_signal
      label: 위기 신호
      required: false
      priority: 2
      red_flag: true
      signals: [자해, 위험]
    - id: couple_duration
      label: 교제 기간
      required: false
      priority: 3
      when: "track=커플상담"
      signals: [만난지, 사귄지]
```
"""

_MALFORMED_YAML_MD = """# 접수 슬롯 스키마

```yaml
intake_schema:
  version: [1
  opening_question: "질문"
```
"""

_MISSING_REQUIRED_KEY_MD = """# 접수 슬롯 스키마

```yaml
intake_schema:
  version: "1"
  opening_question: "질문"
```
"""

_NO_YAML_FENCE_MD = """# 접수 슬롯 스키마

이 문서는 산문만 있고 기계 파싱용 YAML 블록이 없다.
"""


def _write_schema(tmp_path, content: str):
    (tmp_path / "_intake_schema.md").write_text(content, encoding="utf-8")


def test_load_schema_returns_none_when_file_absent(tmp_path):
    assert load_schema(tmp_path) is None


def test_load_schema_returns_none_on_malformed_yaml(tmp_path):
    _write_schema(tmp_path, _MALFORMED_YAML_MD)
    assert load_schema(tmp_path) is None


def test_load_schema_returns_none_when_required_key_missing(tmp_path):
    _write_schema(tmp_path, _MISSING_REQUIRED_KEY_MD)
    assert load_schema(tmp_path) is None


def test_load_schema_returns_none_when_yaml_fence_missing(tmp_path):
    _write_schema(tmp_path, _NO_YAML_FENCE_MD)
    assert load_schema(tmp_path) is None


def test_load_schema_parses_valid_schema(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    assert schema is not None
    assert schema.version == "1"
    assert schema.opening_question == "오늘 무엇을 도와드릴까요?"
    assert [slot.id for slot in schema.slots] == [
        "track",
        "visit_reason",
        "crisis_signal",
        "couple_duration",
    ]


def test_active_slots_excludes_unmet_conditional_slot(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    active_ids = [slot.id for slot in schema.active_slots({})]

    assert "couple_duration" not in active_ids
    assert active_ids == ["track", "visit_reason", "crisis_signal"]


def test_active_slots_includes_conditional_slot_when_condition_met(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    active_ids = [slot.id for slot in schema.active_slots({"track": "커플상담"})]

    assert "couple_duration" in active_ids


def test_unfilled_by_priority_sorts_by_priority_ascending(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    ordered_ids = [slot.id for slot in schema.unfilled_by_priority({}, red_flag_first_ids=())]

    assert ordered_ids == ["track", "visit_reason", "crisis_signal"]


def test_unfilled_by_priority_puts_red_flag_detections_first(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    ordered_ids = [
        slot.id
        for slot in schema.unfilled_by_priority({}, red_flag_first_ids={"crisis_signal"})
    ]

    assert ordered_ids == ["crisis_signal", "track", "visit_reason"]


def test_unfilled_by_priority_excludes_already_filled_slots(tmp_path):
    _write_schema(tmp_path, _VALID_SCHEMA_MD)
    schema = load_schema(tmp_path)

    ordered_ids = [
        slot.id
        for slot in schema.unfilled_by_priority({"track": "개인상담"}, red_flag_first_ids=())
    ]

    assert "track" not in ordered_ids
    assert ordered_ids == ["visit_reason", "crisis_signal"]
