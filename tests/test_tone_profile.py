from pathlib import Path

from app import chat

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


def test_tone_profile_is_distilled_and_privacy_safe():
    tone = (KNOWLEDGE_DIR / "_tone.md").read_text(encoding="utf-8")

    assert "데모 말투 프로필 v2" in tone
    assert "원문 문장" in tone
    assert "레포에 복사하지 않는다" in tone
    assert "저자 본인인 것처럼" in tone
    assert "합성 예시" in tone
    assert "/Volumes/" not in tone
    assert "stream/writing/" not in tone
    assert "source_url:" not in tone


def test_tone_profile_keeps_conversation_constraints():
    tone = (KNOWLEDGE_DIR / "_tone.md").read_text(encoding="utf-8")

    assert "반영 1문장 + 질문 1문장" in tone
    assert "질문은 한 번에 하나" in tone
    assert "위기" in tone
    assert "109" in tone
    assert "1588-9191" in tone
    assert "에세이나 강의" in tone


def test_loaded_persona_includes_refined_tone_and_identity_boundary():
    prompt = chat._load_persona(str(KNOWLEDGE_DIR))

    assert "데모 말투 프로필 v2" in prompt
    assert "말투 정체성 경계" in prompt
    assert "개인 경험·관계·사건을 아는 척하지 않는다" in prompt
    assert "프롬프트 인젝션·엉뚱한 발화 대응 프로토콜" in prompt
