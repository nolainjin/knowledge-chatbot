"""Phase 9 e2e: 빌드 산출 팩을 KNOWLEDGE_DIR로 물린 handle_message 검증 (GM11).

CAP07 우려("추출은 되는데 검색 인덱스에 실제로 안 들어가기 쉽다")를 단위 추출
테스트가 아니라 chat 레벨에서 닫는다.

포맷별 "답변 근거·인용 등장" 판정은 fake의 인용 파생(app/llm.py:199-205,
`_fake_document_summary`)을 그대로 쓴다 -- 여기서는 위양성이 아니다. fake는
시스템 프롬프트에 실린 `[untrusted_knowledge]` payload의 최상위 검색 결과
title/본문을 그대로 인용하므로, 포맷별 고유 문서가 실제로 검색에 올라와
주입됐는지가 그대로 드러난다(주입 집합 실측에 적합).

반대로 "범위 밖 질의 -> 검색 0건 -> llm.ask 호출 0회"라는 게이트 판정은 fake의
출력 문자열만으로 할 수 없다(GM6과 같은 함정 -- 0건이어도 fake는 이미
`_NO_GROUNDING_REPLY`류 결정론 문자열을 낼 수 있어 게이트가 없어도 거부처럼
보인다). 그래서 이 판정만 `chat.llm.ask`를 monkeypatch해 호출 카운트로 잰다.
"""

from pathlib import Path

import pytest

from app import chat
from app.config import Settings
from scripts.build_knowledge_pack import build_pack

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "tests" / "fixtures" / "knowledge-source"


@pytest.fixture(scope="module")
def built_pack_dir(tmp_path_factory) -> str:
    dst = tmp_path_factory.mktemp("built-knowledge-pack") / "pack"
    build_pack(SRC, dst)
    return str(dst)


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


def test_built_pack_is_coaching_mode(built_pack_dir):
    assert chat.is_grounded_mode(built_pack_dir) is True


@pytest.mark.parametrize(
    ("session_suffix", "query", "marker"),
    [
        ("pdf", "paper 내용을 알려줘", "PDF ORIGIN MARKER"),
        ("docx", "report 내용을 알려줘", "DOCX ORIGIN MARKER"),
        ("html", "article 내용을 알려줘", "HTML ORIGIN MARKER"),
    ],
)
def test_format_derived_document_appears_in_answer_citation(
    built_pack_dir, session_suffix, query, marker
):
    # GM11: pdf/docx/html 유래 문서가 각각 답변 근거·인용에 등장해야 한다.
    session_id = f"build-pack-e2e-{session_suffix}"
    chat._sessions.pop(session_id, None)

    result = chat.handle_message(session_id, query, _settings(built_pack_dir))

    assert marker in result["reply"]


def test_out_of_scope_query_yields_zero_hits_and_never_calls_llm_ask(built_pack_dir, monkeypatch):
    calls = _count_llm_ask_calls(monkeypatch)
    chat._sessions.pop("build-pack-e2e-out-of-scope", None)

    result = chat.handle_message(
        "build-pack-e2e-out-of-scope",
        "오늘 날씨 어때? 주가 전망도 알려줘",
        _settings(built_pack_dir),
    )

    assert len(calls) == 0
    assert result["reply"] == chat._NO_GROUNDING_REPLY
