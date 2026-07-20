"""Phase 6: grounded-mode 술어 단일화 + 0건 고정 거부 게이트.

MODEL=fake로 판정하면 위양성이다 -- app/llm.py의 _fake_reply가 0건에도 이미
"[fake] 관련 문서를 찾지 못했습니다."를 반환해, 게이트가 없어도 거부처럼
보인다. 따라서 "무엇을 답했나"가 아니라 llm.ask 자체의 monkeypatch 호출
카운트로 "부르지 않았나"를 단언한다(GM6).
"""

import shutil
from pathlib import Path

from app import chat
from app.chat import is_grounded_mode
from app.config import Settings
from app.intake import load_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_WIKI_DIR = str(REPO_ROOT / "knowledge-wiki")
KNOWLEDGE_MATH_DIR = str(REPO_ROOT / "knowledge-wiki")
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")  # 접수 팩 -- 게이트 면제(RED03)


def _settings(knowledge_dir: str) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=knowledge_dir,
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def _count_llm_ask_calls(monkeypatch):
    """llm.ask를 원본 동작은 그대로 두고 호출 횟수만 세는 wrapper로 바꾼다."""
    calls: list[int] = []
    original = chat.llm.ask

    def counting_ask(**kwargs):
        calls.append(1)
        return original(**kwargs)

    monkeypatch.setattr(chat.llm, "ask", counting_ask)
    return calls


def test_is_grounded_mode_is_positive_file_detection():
    # 코칭 팩(스키마 파일 없음) = grounded(코칭) 모드, 접수 팩(파일 있음) = 아님.
    assert is_grounded_mode(KNOWLEDGE_WIKI_DIR) is True
    assert is_grounded_mode(KNOWLEDGE_MATH_DIR) is True
    assert is_grounded_mode(KNOWLEDGE_DIR) is False


def test_coaching_pack_zero_hit_query_does_not_call_llm_ask(monkeypatch):
    """GM1 -- 코칭 팩 + 검색 0건이면 llm.ask 호출 카운트는 0이어야 한다."""
    calls = _count_llm_ask_calls(monkeypatch)
    chat._sessions.pop("gate-zero-hit", None)

    result = chat.handle_message(
        "gate-zero-hit", "파이썬 데코레이터가 뭔지 설명해줘", _settings(KNOWLEDGE_WIKI_DIR)
    )

    assert len(calls) == 0
    assert result["reply"] == chat._NO_GROUNDING_REPLY
    assert "intake" not in result


def test_coaching_pack_with_hit_still_calls_llm_ask(monkeypatch):
    """검색이 1건 이상이면 기존대로 llm.ask가 호출된다 -- 게이트 과발화 없음."""
    calls = _count_llm_ask_calls(monkeypatch)
    chat._sessions.pop("gate-hit", None)

    chat.handle_message(
        "gate-hit", "문서 근거와 해석은 어떻게 구분하나요?", _settings(KNOWLEDGE_WIKI_DIR)
    )

    assert len(calls) >= 1


def test_intake_pack_zero_hit_query_still_calls_llm_ask(monkeypatch):
    """RED03 -- 게이트는 코칭 모드에만 적용된다. 접수 팩은 0건이어도 상담 흐름이 이어진다."""
    calls = _count_llm_ask_calls(monkeypatch)
    chat._sessions.pop("gate-intake-zero-hit", None)

    result = chat.handle_message("gate-intake-zero-hit", "안녕하세요", _settings(KNOWLEDGE_DIR))

    assert len(calls) >= 1
    assert "intake" in result


def test_malformed_schema_pack_not_silently_flipped_to_grounded(tmp_path):
    """스키마 파일은 있는데 깨진 팩은 여전히 접수 팩 취급 -- 게이트 증폭(E28) 차단."""
    dst = tmp_path / "broken"
    shutil.copytree(REPO_ROOT / "knowledge", dst)
    (dst / "_intake_schema.md").write_text(
        "```yaml\n: : not valid yaml : :\n```", encoding="utf-8"
    )

    assert load_schema(dst) is None
    assert is_grounded_mode(dst) is False
