import json
import shutil
import subprocess
import sys
from pathlib import Path

from app.knowledge_pack import validate_pack
from app.intake import load_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_knowledge_pack.py"


def _run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_knowledge_alt_validates_and_runtime_schema_loads():
    result = validate_pack(REPO_ROOT / "knowledge-alt")

    assert result.valid, [issue.as_dict() for issue in result.errors]
    assert load_schema(REPO_ROOT / "knowledge-alt") is not None


def test_validator_accepts_schema_less_coaching_pack(tmp_path):
    pack = tmp_path / "coaching"
    pack.mkdir()
    for name in ("_persona.md", "_tone.md", "_safety_protocol.md"):
        (pack / name).write_text("# coaching\n", encoding="utf-8")
    (pack / "lesson.md").write_text(
        "---\ntype: concept\n---\n# Lesson\n\nKnowledge.\n", encoding="utf-8"
    )

    result = validate_pack(pack)
    exercise = validate_pack(pack, exercise=True)

    assert result.valid
    assert exercise.valid
    assert exercise.exercise == {"ok": True, "mode": "coaching", "messages": 0, "unfilled": []}


def test_validator_scans_nested_documents_for_frontmatter(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(REPO_ROOT / "knowledge-alt", pack)
    nested = pack / "sub"
    nested.mkdir()
    (nested / "bad.md").write_text("본문만 있고 frontmatter 없음.\n", encoding="utf-8")

    result = validate_pack(pack)

    codes_by_path = {issue.path: issue.code for issue in result.errors}
    assert codes_by_path.get("sub/bad.md") == "DOC_FRONTMATTER_MISSING"


def test_validator_json_is_deterministic_and_relative():
    first = _run_validator("knowledge-alt", "--json")
    second = _run_validator("knowledge-alt", "--json")

    assert first.returncode == 0
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert payload["valid"] is True
    assert "/Volumes/" not in first.stdout


def test_validator_missing_pack_exits_2():
    result = _run_validator("missing-pack", "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["errors"][0]["code"] == "PACK_NOT_FOUND"


def test_validator_missing_required_file_exits_1(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(REPO_ROOT / "knowledge-alt", pack)
    (pack / "_tone.md").unlink()

    result = _run_validator(str(pack), "--json")

    assert result.returncode == 1
    errors = json.loads(result.stdout)["errors"]
    assert any(error["code"] == "PACK_REQUIRED_FILE_MISSING" and error["path"] == "_tone.md" for error in errors)


def test_validator_semantic_schema_errors_are_actionable(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(REPO_ROOT / "knowledge-alt", pack)
    schema = (pack / "_intake_schema.md").read_text(encoding="utf-8")
    schema = schema.replace("id: learner_level", "id: brew_goal", 1)
    schema = schema.replace("priority: 1", "priority: high", 1)
    schema = schema.replace(
        "ask: \"커피 추출을 처음 배우시는지",
        "when: \"missing=위기\"\n      ask: \"커피 추출을 처음 배우시는지",
        1,
    )
    (pack / "_intake_schema.md").write_text(schema, encoding="utf-8")

    result = _run_validator(str(pack), "--json")
    payload = json.loads(result.stdout)
    codes = {error["code"] for error in payload["errors"]}

    assert result.returncode == 1
    assert "SLOT_ID_DUPLICATE" in codes
    assert "SLOT_PRIORITY_TYPE" in codes
    assert "SLOT_CONDITION_UNKNOWN_SLOT" in codes


def test_validator_exercise_reaches_terminal_state():
    result = _run_validator("knowledge-alt", "--exercise", "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["exercise"]["ok"] is True
    assert payload["exercise"]["unfilled"] == []


def test_validator_exercise_reports_unfilled_slot(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(REPO_ROOT / "knowledge-alt", pack)
    scenario_path = pack / "_validation_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["messages"] = scenario["messages"][:-1]
    scenario_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_validator(str(pack), "--exercise", "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["errors"][0]["code"] == "EXERCISE_TERMINAL_STATE"
    assert "demo_boundary" in payload["errors"][0]["message"]
