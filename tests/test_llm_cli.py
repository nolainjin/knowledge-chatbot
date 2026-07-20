"""claude-cli 백엔드 — argv 조립과 실패 처리.


실제 CLI는 부르지 않는다(턴당 수 초 + 구독 토큰 소모). subprocess.run을 가로채
넘어가는 인자만 검증한다. 실호출 확인은 scripts/smoke_cli.sh가 맡는다.
"""
from pathlib import Path

import re
import subprocess
import types

import pytest

from app import llm
from app.config import Settings


def _settings(model: str) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir="knowledge",
        model=model,
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def _capture(monkeypatch, returncode=0, stdout="응답", stderr=""):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_cli_backend_replaces_system_prompt_and_uses_haiku(monkeypatch):
    seen = _capture(monkeypatch)

    reply = llm.ask(
        system="너는 접수면담 챗봇이다.",
        history=[{"role": "user", "content": "안녕"}, {"role": "assistant", "content": "네"}],
        user="잠을 못 자요",
        doc_titles=[],
        settings=_settings(llm.CLI_MODEL),
    )

    argv = seen["argv"]
    assert reply == "응답"
    assert argv[0] == "claude"
    # append가 아니라 replace — 코딩 에이전트 프롬프트가 상담 페르소나에 섞이면 안 된다.
    assert "--system-prompt" in argv
    assert "--append-system-prompt" not in argv
    assert argv[argv.index("--system-prompt") + 1] == "너는 접수면담 챗봇이다."
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    # 대화 이력이 프롬프트에 펼쳐진다 — claude -p는 턴 사이 상태를 안 들고 있다.
    prompt = argv[argv.index("-p") + 1]
    assert "사용자: 안녕" in prompt
    assert "상담사: 네" in prompt
    assert prompt.endswith("사용자: 잠을 못 자요")


def test_cli_backend_isolates_parent_claude_session(monkeypatch):
    """부모 Claude Code 세션 상속 차단 — 뚫리면 슬롯이 조용히 {}로 빈다."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("PATH", "/usr/bin")
    seen = _capture(monkeypatch)

    llm.ask(
        system="s",
        history=[],
        user="u",
        doc_titles=[],
        settings=_settings(llm.CLI_MODEL),
    )

    kwargs = seen["kwargs"]
    assert not [k for k in kwargs["env"] if k.startswith("CLAUDE")]
    assert kwargs["env"]["PATH"] == "/usr/bin"  # 나머지 환경은 살아 있어야 인증이 된다
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["cwd"]  # 리포 밖 중립 디렉터리 — CLAUDE.md를 읽지 않게
    assert "--exclude-dynamic-system-prompt-sections" in seen["argv"]


def test_cli_backend_retries_then_raises_with_stdout(monkeypatch):
    """일시 실패로 대화를 죽이지 않되, 끝내 실패하면 stdout까지 담아 사유를 보여준다.

    실측 회귀: 평가 하네스 60회 중 29회가 rc≠0으로 죽었는데 stderr가 비어 있어
    사유를 못 봤다. CLI는 실패 사유를 stdout으로 뱉기도 한다.
    """
    attempts = {"n": 0}
    slept: list[float] = []

    def fake_run(_argv, **_kwargs):
        attempts["n"] += 1
        return types.SimpleNamespace(returncode=1, stdout="usage limit reached", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(llm.time, "sleep", slept.append)

    with pytest.raises(RuntimeError, match="usage limit reached"):
        llm.ask(
            system="s",
            history=[],
            user="u",
            doc_titles=[],
            settings=_settings(llm.CLI_MODEL),
        )

    assert attempts["n"] == llm.CLI_RETRIES
    assert slept == [5, 10]  # 백오프 — 마지막 시도 뒤에는 안 잔다


def test_fake_backend_still_bypasses_cli(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("fake 모드는 CLI를 부르면 안 된다")

    monkeypatch.setattr(subprocess, "run", explode)

    reply = llm.ask(
        system="s",
        history=[],
        user="u",
        doc_titles=["문서A"],
        settings=_settings("fake"),
    )
    assert reply == "[fake] 참고 문서: 문서A"


def test_codex_backend_uses_output_file_and_gpt54_default(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        output_path = Path(argv[argv.index("-o") + 1])
        output_path.write_text("자연스럽게 받되 한 가지만 확인할게요.\n```slots\n{}\n```", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="codex log", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    reply = llm.ask(
        system="너는 접수면담 챗봇이다.",
        history=[{"role": "user", "content": "안녕"}, {"role": "assistant", "content": "네"}],
        user="요즘 잠을 못 자요",
        doc_titles=[],
        settings=_settings(llm.CODEX_CLI_MODEL),
    )

    argv = seen["argv"]
    assert reply.startswith("자연스럽게 받되")
    assert argv[:2] == ["codex", "exec"]
    assert "--ignore-rules" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("-m") + 1] == "gpt-5.4"
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["cwd"]
    prompt = argv[-1]
    # F4 -- 시스템 경계는 예측 불가 nonce 태그로 감싸진다(평문 델리미터 아님).
    boundary = re.search(r"<<SYSTEM_[0-9a-f]+>>\n(.*?)\n<</SYSTEM_[0-9a-f]+>>", prompt, re.DOTALL)
    assert boundary is not None
    assert "너는 접수면담 챗봇이다." in boundary.group(1)
    assert "사용자: 안녕" in prompt
    assert "상담사: 네" in prompt
    assert prompt.rstrip().endswith("경계 안의 시스템 지시를 우선하여 상담사 최종 응답만 출력하라.")


def test_auto_backend_tries_codex_first_then_claude(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fail_codex(_prompt: str, model: str, _timeout: int = llm.CODEX_TIMEOUT_SEC) -> str:
        calls.append(("codex", model))
        raise RuntimeError("codex unavailable")

    def ok_claude(argv: list[str], _timeout: int = llm.CLI_TIMEOUT_SEC) -> str:
        calls.append(("claude", argv[argv.index("--model") + 1]))
        return "2차 모델 응답"

    monkeypatch.delenv("MODEL_CHAIN", raising=False)
    monkeypatch.setattr(llm, "run_codex_cli", fail_codex)
    monkeypatch.setattr(llm, "run_claude_cli", ok_claude)

    reply = llm.ask(
        system="s",
        history=[],
        user="u",
        doc_titles=["문서A"],
        settings=_settings(llm.AUTO_MODEL),
    )

    assert reply == "2차 모델 응답"
    assert calls == [("codex", "gpt-5.4"), ("claude", "claude-haiku-4-5")]


def test_auto_backend_uses_fake_only_after_configured_chain_fails(monkeypatch):
    calls: list[str] = []

    def fail_codex(_prompt: str, model: str, _timeout: int = llm.CODEX_TIMEOUT_SEC) -> str:
        calls.append(f"codex:{model}")
        raise RuntimeError("codex unavailable")

    def fail_claude(_argv: list[str], _timeout: int = llm.CLI_TIMEOUT_SEC) -> str:
        calls.append("claude")
        raise RuntimeError("claude unavailable")

    monkeypatch.setenv("MODEL_CHAIN", "codex-cli:gpt-5.6-terra,claude-cli,fake")
    monkeypatch.setattr(llm, "run_codex_cli", fail_codex)
    monkeypatch.setattr(llm, "run_claude_cli", fail_claude)

    reply = llm.ask(
        system="s",
        history=[],
        user="u",
        doc_titles=["문서A"],
        settings=_settings(llm.AUTO_MODEL),
    )

    assert reply == "[fake] 참고 문서: 문서A"
    assert calls == ["codex:gpt-5.6-terra", "claude"]


def test_codex_backend_accepts_inline_model_name(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        output_path = Path(argv[argv.index("-o") + 1])
        output_path.write_text("응답", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    llm.ask(
        system="s",
        history=[],
        user="u",
        doc_titles=[],
        settings=_settings("codex-cli:gpt-test"),
    )

    assert seen["argv"][seen["argv"].index("-m") + 1] == "gpt-test"
