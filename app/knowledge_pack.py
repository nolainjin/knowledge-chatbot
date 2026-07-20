from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

REQUIRED_FILES = (
    "_persona.md",
    "_tone.md",
    "_safety_protocol.md",
)
OPTIONAL_FILES = ("_intake_schema.md", "_validation_scenario.json", "_ui.json")
# 구조 팩(build_structured_pack.py) 네비게이션 파일 -- 검색 제외(`_` 프리픽스),
# validate 의 reserved-file 경고 대상에서 제외. _topic.md 는 주제 폴더마다,
# _00_INDEX.md 는 팩 루트에 하나. basename 로만 판정한다.
_STRUCTURED_RESERVED = ("_topic.md", "_00_INDEX.md")
RESERVED_PREFIX = "_"
_YAML_FENCE_RE = re.compile(r"```yaml\s*?\n(.*?)```", re.DOTALL)
_SAFE_WHEN_RE = re.compile(r"^[A-Za-z0-9_-]+=[^=\n]+$")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ValidationResult:
    pack: str
    valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    exercise: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "pack": self.pack,
            "valid": self.valid,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }
        if self.exercise is not None:
            payload["exercise"] = self.exercise
        return payload


class DuplicateKeyError(ValueError):
    pass


def _json_object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise DuplicateKeyError(key)
        data[key] = value
    return data


def _load_json_no_duplicates(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object_no_duplicates)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _yaml_block(text: str) -> str | None:
    match = _YAML_FENCE_RE.search(text)
    if match is None:
        return None
    return match.group(1)


def _as_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


def _as_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return value
    return None


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list_of_strings(value: object) -> bool:
    return isinstance(value, list) and all(_is_nonempty_str(item) for item in value)


def _validate_signal_shape(value: object) -> bool:
    if _list_of_strings(value):
        return True
    if not isinstance(value, dict):
        return False
    return all(_is_nonempty_str(key) and _list_of_strings(items) for key, items in value.items())


def _validate_frontmatter(path: Path, root: Path, errors: list[ValidationIssue]) -> None:
    rel = _relative(path, root)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(ValidationIssue("DOC_FRONTMATTER_MISSING", rel, "frontmatter가 없습니다."))
        return
    end = text.find("\n---", 4)
    if end == -1:
        errors.append(ValidationIssue("DOC_FRONTMATTER_UNCLOSED", rel, "frontmatter 종료 구분자가 없습니다."))
        return
    try:
        parsed = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        errors.append(ValidationIssue("DOC_FRONTMATTER_YAML", rel, f"frontmatter YAML 오류: {exc.__class__.__name__}"))
        return
    if parsed is not None and not isinstance(parsed, dict):
        errors.append(ValidationIssue("DOC_FRONTMATTER_MAPPING", rel, "frontmatter는 매핑이어야 합니다."))
    if "# " not in text[end + 4 :]:
        errors.append(ValidationIssue("DOC_TITLE_MISSING", rel, "본문 H1 제목이 없습니다."))


def _validate_schema(root: Path, errors: list[ValidationIssue]) -> dict[str, object] | None:
    schema_path = root / "_intake_schema.md"
    text = schema_path.read_text(encoding="utf-8")
    block = _yaml_block(text)
    if block is None:
        errors.append(ValidationIssue("SCHEMA_YAML_FENCE_MISSING", "_intake_schema.md", "```yaml fenced block이 없습니다."))
        return None
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        errors.append(ValidationIssue("SCHEMA_YAML_PARSE", "_intake_schema.md", f"YAML 파싱 오류: {exc.__class__.__name__}"))
        return None
    parsed_map = _as_mapping(parsed)
    if parsed_map is None:
        errors.append(ValidationIssue("SCHEMA_ROOT_MAPPING", "schema", "스키마 루트는 매핑이어야 합니다."))
        return None
    schema = _as_mapping(parsed_map.get("intake_schema"))
    if schema is None:
        errors.append(ValidationIssue("SCHEMA_ROOT_KEY", "schema.intake_schema", "intake_schema 매핑이 필요합니다."))
        return None

    if not _is_nonempty_str(schema.get("version")):
        errors.append(ValidationIssue("SCHEMA_VERSION", "schema.version", "version은 비어있지 않은 문자열이어야 합니다."))
    if not _is_nonempty_str(schema.get("opening_question")):
        errors.append(ValidationIssue("SCHEMA_OPENING_QUESTION", "schema.opening_question", "opening_question이 필요합니다."))

    slots = _as_list(schema.get("slots"))
    if not slots:
        errors.append(ValidationIssue("SCHEMA_SLOTS", "schema.slots", "slots는 비어있지 않은 목록이어야 합니다."))
        return schema

    ids: list[str] = []
    values_by_id: dict[str, set[str]] = {}
    slot_maps: list[dict[str, object]] = []
    for index, raw in enumerate(slots):
        path = f"schema.slots[{index}]"
        slot = _as_mapping(raw)
        if slot is None:
            errors.append(ValidationIssue("SLOT_MAPPING", path, "slot은 매핑이어야 합니다."))
            continue
        slot_maps.append(slot)
        slot_id = slot.get("id")
        if not _is_nonempty_str(slot_id):
            errors.append(ValidationIssue("SLOT_ID", f"{path}.id", "slot id는 비어있지 않은 문자열이어야 합니다."))
            continue
        slot_id_text = str(slot_id)
        if slot_id_text in ids:
            errors.append(ValidationIssue("SLOT_ID_DUPLICATE", f"{path}.id", f"중복 slot id: {slot_id_text}"))
        ids.append(slot_id_text)
        if not _is_nonempty_str(slot.get("label")):
            errors.append(ValidationIssue("SLOT_LABEL", f"{path}.label", "label은 문자열이어야 합니다."))
        if "required" in slot and not isinstance(slot["required"], bool):
            errors.append(ValidationIssue("SLOT_REQUIRED_TYPE", f"{path}.required", "required는 boolean이어야 합니다."))
        if "red_flag" in slot and not isinstance(slot["red_flag"], bool):
            errors.append(ValidationIssue("SLOT_RED_FLAG_TYPE", f"{path}.red_flag", "red_flag는 boolean이어야 합니다."))
        if "priority" in slot and not isinstance(slot["priority"], int):
            errors.append(ValidationIssue("SLOT_PRIORITY_TYPE", f"{path}.priority", "priority는 integer여야 합니다."))
        if "values" in slot:
            if not _list_of_strings(slot["values"]):
                errors.append(ValidationIssue("SLOT_VALUES_TYPE", f"{path}.values", "values는 문자열 목록이어야 합니다."))
            else:
                values_by_id[slot_id_text] = set(cast(list[str], slot["values"]))
        if "signals" in slot and not _validate_signal_shape(slot["signals"]):
            errors.append(ValidationIssue("SLOT_SIGNALS_TYPE", f"{path}.signals", "signals는 문자열 목록 또는 값별 문자열 목록이어야 합니다."))
        for key in ("allow_override_values", "reject_signals", "reject_unless_signals"):
            if key in slot and not _list_of_strings(slot[key]):
                errors.append(ValidationIssue("SLOT_LIST_TYPE", f"{path}.{key}", f"{key}는 문자열 목록이어야 합니다."))
        if "override_signals" in slot and not _validate_signal_shape(slot["override_signals"]):
            errors.append(ValidationIssue("SLOT_OVERRIDE_SIGNALS_TYPE", f"{path}.override_signals", "override_signals는 값별 문자열 목록이어야 합니다."))
        if "ask" in slot and not isinstance(slot["ask"], str):
            errors.append(ValidationIssue("SLOT_ASK_TYPE", f"{path}.ask", "ask는 문자열이어야 합니다."))
        if "capture" in slot and slot["capture"] != "full_message":
            errors.append(ValidationIssue("SLOT_CAPTURE_VALUE", f"{path}.capture", "capture는 full_message만 허용합니다."))

    known_ids = set(ids)
    for index, slot in enumerate(slot_maps):
        path = f"schema.slots[{index}]"
        for key in ("when", "unless"):
            condition = slot.get(key)
            if condition is None:
                continue
            if not isinstance(condition, str) or not _SAFE_WHEN_RE.fullmatch(condition):
                errors.append(ValidationIssue("SLOT_CONDITION_SYNTAX", f"{path}.{key}", "조건은 slot=value 형식이어야 합니다."))
                continue
            ref_id, ref_value = condition.split("=", 1)
            if ref_id not in known_ids:
                errors.append(ValidationIssue("SLOT_CONDITION_UNKNOWN_SLOT", f"{path}.{key}", f"알 수 없는 slot 참조: {ref_id}"))
            allowed_values = values_by_id.get(ref_id)
            if allowed_values is not None and ref_value not in allowed_values:
                errors.append(ValidationIssue("SLOT_CONDITION_UNKNOWN_VALUE", f"{path}.{key}", f"허용되지 않은 값 참조: {ref_value}"))

    return schema


def validate_pack(pack_dir: str | Path, exercise: bool = False) -> ValidationResult:
    root = Path(pack_dir)
    pack_name = root.name or "."
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    exercise_payload: dict[str, object] | None = None

    if not root.exists() or not root.is_dir():
        return ValidationResult(
            pack=pack_name,
            valid=False,
            errors=[ValidationIssue("PACK_NOT_FOUND", "pack", "pack_dir가 존재하는 디렉토리가 아닙니다.")],
            warnings=[],
        )
    root = root.resolve()

    for required in REQUIRED_FILES:
        path = root / required
        if not path.is_file():
            errors.append(ValidationIssue("PACK_REQUIRED_FILE_MISSING", required, f"{required} 파일이 필요합니다."))

    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            errors.append(ValidationIssue("PACK_SYMLINK_FORBIDDEN", _relative(path, root), "pack 안의 symlink는 허용하지 않습니다."))
            continue
        if path.is_file() and not _is_inside(path, root):
            errors.append(ValidationIssue("PACK_PATH_ESCAPE", _relative(path, root), "pack root 밖 파일은 허용하지 않습니다."))
        if (
            path.is_file()
            and path.name.startswith(RESERVED_PREFIX)
            and path.name not in REQUIRED_FILES + OPTIONAL_FILES + _STRUCTURED_RESERVED
        ):
            warnings.append(ValidationIssue("PACK_UNKNOWN_RESERVED_FILE", _relative(path, root), "알 수 없는 예약 파일입니다.", "warning"))

    if (root / "_intake_schema.md").is_file():
        _validate_schema(root, errors)

    docs = [path for path in sorted(root.rglob("*.md")) if not path.name.startswith(RESERVED_PREFIX)]
    if not docs:
        errors.append(ValidationIssue("PACK_DOCUMENTS_MISSING", "documents", "최소 1개 이상의 지식 문서가 필요합니다."))
    for doc in docs:
        _validate_frontmatter(doc, root, errors)

    scenario_path = root / "_validation_scenario.json"
    if scenario_path.is_file():
        try:
            scenario = _load_json_no_duplicates(scenario_path)
        except DuplicateKeyError as exc:
            errors.append(ValidationIssue("SCENARIO_DUPLICATE_KEY", "_validation_scenario.json", f"중복 JSON key: {exc}"))
            scenario = None
        except json.JSONDecodeError as exc:
            errors.append(ValidationIssue("SCENARIO_JSON_PARSE", "_validation_scenario.json", f"JSON 파싱 오류: {exc.msg}"))
            scenario = None
        if scenario is not None and not isinstance(scenario, dict):
            errors.append(ValidationIssue("SCENARIO_MAPPING", "_validation_scenario.json", "scenario는 JSON object여야 합니다."))
        elif isinstance(scenario, dict):
            messages = scenario.get("messages")
            if not _list_of_strings(messages):
                errors.append(ValidationIssue("SCENARIO_MESSAGES", "scenario.messages", "messages는 비어있지 않은 문자열 목록이어야 합니다."))

    if exercise and not errors:
        from app import chat
        from app.knowledge_pack_exercise import exercise_pack

        exercise_payload = exercise_pack(root, not chat.is_grounded_mode(root))
        if exercise_payload.get("ok") is not True:
            errors.append(
                ValidationIssue(
                    "EXERCISE_TERMINAL_STATE",
                    str(exercise_payload.get("path", "exercise")),
                    str(exercise_payload.get("message", "fake conversation did not reach terminal state")),
                )
            )

    return ValidationResult(pack=pack_name, valid=not errors, errors=errors, warnings=warnings, exercise=exercise_payload)
