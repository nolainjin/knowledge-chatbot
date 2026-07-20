from pathlib import Path
import types

from app.config import Settings
from scripts import persona_eval


REPO_ROOT = Path(__file__).resolve().parent.parent


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=str(REPO_ROOT / "knowledge"),
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=10**9,
    )


def test_usage_limit_detection_matches_claude_cli_message():
    exc = RuntimeError("claude CLI 실패(3회 시도): rc=1 stdout=\"You've hit your session limit\"")

    assert persona_eval._is_usage_limit_error(exc) is True


def test_scripted_patient_advances_without_model_call():
    transcript = []
    first = persona_eval._scripted_patient("crisis-hidden", transcript)
    transcript.append({"role": "patient", "text": first})
    transcript.append({"role": "bot", "text": "언제부터 그랬나요?"})

    second = persona_eval._scripted_patient("crisis-hidden", transcript)

    assert "잠" in first
    assert "한두 달" in second

def test_codex_patient_uses_output_last_message(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        output_path = Path(argv[argv.index("-o") + 1])
        output_path.write_text('"요즘 잠을 못 자서 왔어요."', encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="log noise", stderr="")

    monkeypatch.setattr(persona_eval.subprocess, "run", fake_run)

    reply = persona_eval._ask_patient_codex("불면으로 온 내담자", [], "gpt-test")

    assert reply == "요즘 잠을 못 자서 왔어요."
    assert seen["argv"][:2] == ["codex", "exec"]
    assert "--ignore-rules" in seen["argv"]
    assert seen["argv"][seen["argv"].index("-m") + 1] == "gpt-test"
    assert seen["kwargs"]["stdin"] is persona_eval.subprocess.DEVNULL



def test_run_one_scripted_fake_catches_hidden_crisis(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    persona = next(p for p in persona_eval.PERSONAS if p["id"] == "crisis-hidden")

    row = persona_eval.run_one(persona, 0, _settings(), "scripted", "unused")

    assert row["error"] is None
    assert row["usage_limited"] is False
    assert row["actual_track"] == "위기"
    assert row["track_match"] is True
