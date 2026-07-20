import os
import tempfile
from pathlib import Path
from typing import cast

from app import chat, knowledge_pack
from app.config import Settings


def exercise_pack(root: Path, intake_mode: bool) -> dict[str, object]:
    scenario_path = root / "_validation_scenario.json"
    if not scenario_path.is_file():
        if intake_mode:
            return {"ok": False, "path": "_validation_scenario.json", "message": "intake pack에는 scenario가 필요합니다."}
        return {"ok": True, "mode": "coaching", "messages": 0, "unfilled": []}

    scenario = knowledge_pack._load_json_no_duplicates(scenario_path)
    if not isinstance(scenario, dict):
        return {"ok": False, "path": "scenario", "message": "scenario는 JSON object여야 합니다."}
    messages_obj = scenario.get("messages")
    if not isinstance(messages_obj, list) or not messages_obj or not all(isinstance(item, str) for item in messages_obj):
        return {"ok": False, "path": "scenario.messages", "message": "messages는 비어있지 않은 문자열 목록이어야 합니다."}
    messages = cast(list[str], messages_obj)
    session_id = str(scenario.get("session_id") or "knowledge-pack-validator")
    expect_unfilled_empty = scenario.get("expect_unfilled_empty", True) is True

    old_cwd = Path.cwd()
    old_model = os.environ.get("MODEL")
    old_knowledge_dir = os.environ.get("KNOWLEDGE_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="lmwiki-pack-exercise-") as temp_dir:
            os.chdir(temp_dir)
            os.environ["MODEL"] = "fake"
            os.environ["KNOWLEDGE_DIR"] = str(root)
            chat._sessions.pop(session_id, None)
            settings = Settings(
                anthropic_api_key="",
                knowledge_dir=str(root),
                model="fake",
                trust_proxy_hops=0,
                daily_request_cap=500,
            )
            last: dict[str, object] = {}
            for index, message in enumerate(messages):
                last = chat.handle_message(session_id, message, settings=settings)
                if intake_mode and not isinstance(last.get("intake"), dict):
                    return {"ok": False, "path": f"messages[{index}]", "message": "응답에 intake 상태가 없습니다."}
            if not intake_mode:
                return {"ok": True, "mode": "coaching", "messages": len(messages), "unfilled": []}
            intake_state = last.get("intake")
            unfilled = intake_state.get("unfilled") if isinstance(intake_state, dict) else None
            if expect_unfilled_empty and unfilled:
                first = unfilled[0] if isinstance(unfilled, list) and unfilled else {}
                slot_id = first.get("id") if isinstance(first, dict) else "unknown"
                return {
                    "ok": False,
                    "path": f"messages[{len(messages) - 1}]",
                    "message": f"터미널 상태가 아닙니다. 첫 미충족 slot: {slot_id}",
                    "unfilled": unfilled,
                }
            return {"ok": True, "messages": len(messages), "unfilled": unfilled or []}
    finally:
        os.chdir(old_cwd)
        if old_model is None:
            os.environ.pop("MODEL", None)
        else:
            os.environ["MODEL"] = old_model
        if old_knowledge_dir is None:
            os.environ.pop("KNOWLEDGE_DIR", None)
        else:
            os.environ["KNOWLEDGE_DIR"] = old_knowledge_dir
