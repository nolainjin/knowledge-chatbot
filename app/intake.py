"""접수 슬롯 스키마 파서 + 슬롯 모델.

`_intake_schema.md`(지식 디렉토리 예약 파일)를 읽어 슬롯 모델로 바꾸는
도메인 무관 엔진. 스키마는 마크다운 산문 + 기계 파싱용 YAML 블록 1개로
선언한다(결정 D01). 파일 부재·YAML 블록 추출 실패·파싱 오류·필수 키
누락 등 어떤 실패 경로도 예외를 밖으로 새지 않고 None으로 수렴한다 —
스키마 오류 하나가 대화 전체를 죽이는 사고를 막기 위해서다(FP1, CAP09).

원칙: 상담 등 도메인 문구는 이 모듈에 일절 넣지 않는다 — 전부 스키마
데이터가 소유한다.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SCHEMA_FILENAME = "_intake_schema.md"
_YAML_FENCE_RE = re.compile(r"```yaml\s*?\n(.*?)```", re.DOTALL)
_SLOTS_FENCE_RE = re.compile(r"```slots\s*?\n(.*?)```", re.DOTALL)
_MAX_SLOT_VALUE_LEN = 200


@dataclass
class Slot:
    id: str
    label: str
    required: bool = False
    priority: int = 0
    red_flag: bool = False
    when: str | None = None
    unless: str | None = None
    values: list | None = None
    allow_override_values: list | None = None
    override_signals: dict | None = None
    reject_signals: list | None = None
    reject_unless_signals: list | None = None
    signals: dict | list | None = None
    ask: str | None = None
    capture: str | None = None

    def is_active(self, filled: dict) -> bool:
        """``when`` 조건은 일치할 때, ``unless`` 조건은 불일치할 때 활성화한다."""
        if self.when is not None:
            cond_id, _, cond_value = self.when.partition("=")
            if filled.get(cond_id) != cond_value:
                return False
        if self.unless is not None:
            cond_id, _, cond_value = self.unless.partition("=")
            if filled.get(cond_id) == cond_value:
                return False
        return True


@dataclass
class Schema:
    version: str
    opening_question: str
    slots: list[Slot]
    # 화면 고정 문구(제목·인사말·칩 등)도 스키마 데이터가 소유한다 — 모듈
    # 상단 원칙의 UI 연장. 비어 있으면 static/의 기본(상담) 문구를 쓴다.
    ui: dict = field(default_factory=dict)

    def active_slots(self, filled: dict) -> list[Slot]:
        return [slot for slot in self.slots if slot.is_active(filled)]

    def unfilled_by_priority(self, filled: dict, red_flag_first_ids) -> list[Slot]:
        """활성이면서 미충족인 슬롯을 정렬한다.

        red_flag_first_ids(이번 턴에 레드플래그 신호가 감지된 슬롯 id 집합)를
        최상단에 두고, 나머지는 priority 오름차순.
        """
        red_flag_ids = set(red_flag_first_ids or ())
        unfilled = [slot for slot in self.active_slots(filled) if slot.id not in filled]
        return sorted(unfilled, key=lambda slot: (slot.id not in red_flag_ids, slot.priority))


def _match_signal(message: str, signals) -> str | None:
    """message에서 signals 부분문자열 매칭 결과를 반환한다. 없으면 None.

    signals가 dict(값 -> 부분문자열 목록)이면 매칭된 값(키)을 반환하고,
    list(부분문자열 목록)이면 매칭된 부분문자열 자체를 반환한다.
    """
    if isinstance(signals, dict):
        for value, substrings in signals.items():
            if any(sub in message for sub in substrings):
                return value
        return None
    for sub in signals:
        if sub in message:
            return sub
    return None


def _fake_slot_value(message: str, matched_value: str, slot: Slot) -> str:
    """fake 모드 슬롯 값 생성. capture=full_message면 발화 전체를 보존한다."""
    if slot.capture == "full_message":
        return " ".join(message.split())[:_MAX_SLOT_VALUE_LEN]
    return matched_value


def _is_rejected_by_signal_guard(message: str | None, slot: Slot) -> bool:
    """slot-level negative signal guard.

    Some slots are semantically narrower than their keyword hints. Example:
    a current crisis-plan slot may use "약" as a signal, but "예전에 약을
    먹으려고 한 적" is past attempt history, not current means. The schema
    owns these domain distinctions through reject_signals/reject_unless_signals.
    """
    if message is None or not slot.reject_signals:
        return False
    if not any(word in message for word in slot.reject_signals):
        return False
    if slot.reject_unless_signals and any(
        word in message for word in slot.reject_unless_signals
    ):
        return False
    return True


def _can_override(
    slot: Slot, current_value: str, new_value: str, message: str | None = None
) -> bool:
    """스키마가 허용한 값으로만 기존 슬롯 값을 갱신한다(예: 위기 승격).

    override_signals가 선언된 값은 그 좁은 신호 부분문자열이 실제 메시지에 있어야
    갱신을 허용한다. 관계 승격을 남편/아내 같은 정체성 명사 언급만으로 허용하면,
    이미 확정된 트랙이 스쳐 지나가는 배우자 언급 한 번에 덮어써진다(실측:
    emo-insomnia 페르소나 — "잠"으로 정서 확정 후 "아내한테는 피곤하다고만 말했다"는
    지나가는 언급에 관계로 덮어써짐). message가 없는 경로(extract_real — LLM 자체
    판단 출력이라 원문 메시지에 접근하지 못함)에서는 override_signals가 선언된
    값을 안전하게 거부한다.
    """
    if slot.allow_override_values is None or new_value not in slot.allow_override_values:
        return False
    if current_value == new_value:
        return False
    restricted = slot.override_signals and slot.override_signals.get(new_value)
    if restricted:
        return message is not None and any(word in message for word in restricted)
    return True


def extract_fake(message: str, schema: Schema, filled: dict) -> dict[str, str]:
    """fake 모드 전용 결정론적 슬롯 추출.

    활성 상태이면서 아직 채워지지 않은 슬롯을 대상으로 signals 부분문자열 매칭을
    시도한다. 이미 채워진 슬롯은 기본적으로 덮어쓰지 않지만, 스키마가
    allow_override_values로 허용한 값(예: 위기 승격)은 갱신한다. 이 함수는
    filled를 변경하지 않고 이번 발화로 새로 채워진 슬롯만 담은 dict를 반환한다.
    """
    new_fills: dict[str, str] = {}
    for slot in schema.active_slots(filled):
        if slot.signals is None:
            continue
        matched_value = _match_signal(message, slot.signals)
        if matched_value is None:
            continue
        if _is_rejected_by_signal_guard(message, slot):
            continue
        if slot.id in filled and not _can_override(slot, filled[slot.id], matched_value, message):
            continue
        new_fills[slot.id] = _fake_slot_value(message, matched_value, slot)
    return new_fills


def extract_classification(message: str, schema: Schema, filled: dict) -> dict[str, str]:
    """닫힌 값 집합(values)을 선언한 분류 슬롯만 신호어로 결정론 판정한다. 실모드용.

    track을 모델 재량에 맡기면 같은 발화에도 채웠다 말았다 한다(실측). track이 비면
    when 분기 슬롯(symptom_context / crisis_*)이 통째로 안 켜지고, 위기 트랙을 놓치는
    건 안전 실패다. 자유서술 슬롯(chief_complaint 등)은 여전히 모델이 의미 요약한다.
    """
    classify_ids = {slot.id for slot in schema.slots if slot.values}
    return {
        slot_id: value
        for slot_id, value in extract_fake(message, schema, filled).items()
        if slot_id in classify_ids
    }


def detect_red_flags(message: str, schema: Schema, filled: dict) -> set[str]:
    """이번 발화가 red_flag 슬롯의 signals에 걸리면 그 슬롯 id 집합을 반환한다.

    채움 여부와 무관하게 감지한다 — 결과는 unfilled_by_priority의 우선 정렬
    신호로만 쓰인다(이미 채워진 슬롯은 unfilled_by_priority가 알아서 제외).
    """
    hits = set()
    for slot in schema.active_slots(filled):
        if not slot.red_flag or slot.signals is None:
            continue
        if _match_signal(message, slot.signals) is not None:
            hits.add(slot.id)
    return hits


def extract_real(
    reply: str,
    schema: Schema,
    filled: dict,
    message: str | None = None,
) -> tuple[str, dict[str, str]]:
    """실모드 LLM 응답에서 ```slots fenced JSON 블록을 분리해 신뢰 경계로 거른다.

    LLM 출력은 신뢰 경계 밖이다 — fenced 블록 분리 실패나 JSON 파싱 실패는
    그 턴의 추출을 스킵한다(원문 그대로, 빈 dict 반환. 다음 턴에 만회하므로
    파싱 실패의 영향은 그 턴 한정 — FP19 방지). 파싱에 성공해도 각 항목을
    4중 필터로 거른다: 스키마 활성 슬롯 id 화이트리스트에 없으면 폐기,
    문자열이 아니면 폐기, 200자를 넘으면 폐기, 이미 채워진 슬롯이면 폐기한다.
    단 스키마가 allow_override_values로 명시한 안전 승격 값은 허용한다. 통과분만
    반환하고, reply에서는 슬롯 JSON 블록을 제거해 사용자·history·storage에는 절대 노출하지 않는다.
    """
    match = _SLOTS_FENCE_RE.search(reply)
    if match is None:
        return reply, {}

    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return reply, {}

    if not isinstance(parsed, dict):
        return reply, {}

    active_slots = {slot.id: slot for slot in schema.active_slots(filled)}
    accepted: dict[str, str] = {}
    for slot_id, value in parsed.items():
        slot = active_slots.get(slot_id)
        if slot is None:
            continue
        if not isinstance(value, str):
            continue
        if len(value) > _MAX_SLOT_VALUE_LEN:
            continue
        # 닫힌 값 집합을 선언한 슬롯은 그 안의 값만 받는다. track이 대표적 —
        # when 분기(`track=위기`)가 이 값과 정확히 맞물리므로, 모델이 지어낸
        # 자유 문자열("work-related stress")을 받으면 조건부 슬롯이 통째로 안 켜진다.
        if slot.values and value not in slot.values:
            continue
        if _is_rejected_by_signal_guard(message, slot):
            continue
        if slot_id in filled and not _can_override(slot, filled[slot_id], value):
            continue
        accepted[slot_id] = value

    clean_reply = (reply[: match.start()] + reply[match.end() :]).rstrip()
    return clean_reply, accepted


def build_summary_json(schema: Schema, filled: dict) -> dict:
    """스키마 활성 시 세션 슬롯 상태만으로 구조화 접수 요약을 만든다(LLM 무호출).

    채워진 슬롯은 이미 세션 상태에 있으므로 LLM을 부를 이유가 없다 —
    결정론 생성이라 fake 모드에서도 동일하게 돈다. 활성인데 못 채운 슬롯은
    "미확인"으로 남긴다(CAP07). red_flags는 별도 감지 이력 없이 채워진
    red_flag 슬롯에서 파생한다 — 신호는 감지됐지만 끝내 못 채운 레드플래그
    슬롯은 unfilled의 미확인으로 표기되어 정보 손실이 없다.
    """
    active = schema.active_slots(filled)
    unfilled = [slot for slot in active if slot.id not in filled]
    return {
        "track": filled.get("track", "미확인"),
        "slots": dict(filled),
        "unfilled": {slot.id: "미확인" for slot in unfilled},
        "red_flags": [slot.id for slot in active if slot.red_flag and slot.id in filled],
    }


def _parse_slot(raw) -> Slot:
    if not isinstance(raw, dict):
        raise TypeError("slot 항목은 매핑이어야 한다")
    return Slot(
        id=raw["id"],
        label=raw["label"],
        required=bool(raw.get("required", False)),
        priority=int(raw.get("priority", 0)),
        red_flag=bool(raw.get("red_flag", False)),
        when=raw.get("when"),
        unless=raw.get("unless"),
        values=raw.get("values"),
        allow_override_values=(
            raw.get("allow_override_values")
            if isinstance(raw.get("allow_override_values"), list)
            else None
        ),
        override_signals=(
            raw.get("override_signals") if isinstance(raw.get("override_signals"), dict) else None
        ),
        reject_signals=(
            raw.get("reject_signals") if isinstance(raw.get("reject_signals"), list) else None
        ),
        reject_unless_signals=(
            raw.get("reject_unless_signals")
            if isinstance(raw.get("reject_unless_signals"), list)
            else None
        ),
        signals=raw.get("signals"),
        ask=raw.get("ask"),
        capture=raw.get("capture") if isinstance(raw.get("capture"), str) else None,
    )


def load_schema(knowledge_dir) -> Schema | None:
    """<knowledge_dir>/_intake_schema.md 를 읽어 Schema로 바꾼다.

    부재 · fenced 블록 추출 실패 · YAML 파싱 오류 · 필수 키 누락 중 어느
    경로를 타든 예외 없이 None을 반환한다(형식 오류 = 폴백, 결정 D01).
    """
    schema_path = Path(knowledge_dir) / _SCHEMA_FILENAME
    if not schema_path.is_file():
        return None

    text = schema_path.read_text(encoding="utf-8")
    match = _YAML_FENCE_RE.search(text)
    if match is None:
        return None

    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None

    if not isinstance(parsed, dict):
        return None
    data = parsed.get("intake_schema")
    if not isinstance(data, dict):
        return None

    version = data.get("version")
    opening_question = data.get("opening_question")
    slots_raw = data.get("slots")
    if not version or not opening_question or not isinstance(slots_raw, list) or not slots_raw:
        return None

    try:
        slots = [_parse_slot(raw) for raw in slots_raw]
    except (KeyError, TypeError, ValueError):
        return None

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}

    return Schema(version=version, opening_question=opening_question, slots=slots, ui=ui)
