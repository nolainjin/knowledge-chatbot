"""CAP06("지식 데이터만 교체하면 다른 분야 챗봇으로 전환") 실증 테스트.

코드(app/chat.py, app/knowledge.py)는 그대로 두고 Settings.knowledge_dir 값만
knowledge -> knowledge-alt 로 바꿔 같은 질문을 handle_message에 두 번 흘려보낸다.
상담 지식셋과 knowledge-alt starter pack은 모두 _intake_schema.md 때문에
접수 흐름으로 동작하고, 별도 fixture가 기존 fake RAG 스텁 폴백을 보존한다.
"""

from pathlib import Path

from app import chat
from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = str(REPO_ROOT / "knowledge")
KNOWLEDGE_ALT_DIR = str(REPO_ROOT / "knowledge-alt")
KNOWLEDGE_FALLBACK_DIR = str(REPO_ROOT / "tests" / "fixtures" / "knowledge-fallback")

# 두 지식셋 모두의 본문에 등장하는 공통 단어 -- 상담 지식셋은 접수면담 모드로
# 질문을 이어가고, knowledge-alt/커피는 fake RAG 문서 제목을 인용해야 한다.
QUESTION = "방법 알려줘"


def _settings(knowledge_dir: str) -> Settings:
    return Settings(
        anthropic_api_key="",
        knowledge_dir=knowledge_dir,
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )


def test_knowledge_dir_swap_changes_cited_docs_without_code_change():
    base_result = chat.handle_message("swap-e2e-base", QUESTION, _settings(KNOWLEDGE_DIR))
    alt_result = chat.handle_message("swap-e2e-alt", QUESTION, _settings(KNOWLEDGE_ALT_DIR))
    fallback_result = chat.handle_message(
        "swap-e2e-fallback",
        "원두 보관법 알려줘",
        _settings(KNOWLEDGE_FALLBACK_DIR),
    )

    # 기본 지식셋(상담) 구동 -> 스키마가 활성화되어 접수면담 응답과 intake 상태가 나온다.
    assert "오늘 상담" in base_result["reply"]
    assert "intake" in base_result
    assert "원두 보관법" not in base_result["reply"]

    assert "커피" in alt_result["reply"] or "학습" in alt_result["reply"]
    assert "intake" in alt_result

    assert "원두 보관법" in fallback_result["reply"]
    assert "intake" not in fallback_result
