from pathlib import Path

from fastapi.testclient import TestClient

from app import chat
from app.config import Settings
from app.knowledge import load_documents, search
from app.knowledge_pack import validate_pack
from app.main import app


PACK = Path(__file__).parents[1] / "knowledge-wiki"


def test_wiki_pack_is_schema_less_and_searchable() -> None:
    result = validate_pack(PACK, exercise=True)
    documents = load_documents(PACK)

    assert result.valid is True
    assert (PACK / "_intake_schema.md").exists() is False
    assert documents
    assert search("문서 근거를 확인하고 싶어요", documents)


def test_default_settings_select_wiki_pack(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-wiki")

    settings = Settings.from_env()

    assert settings.knowledge_dir == "knowledge-wiki"


def test_default_config_exposes_wiki_coaching_mode(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-wiki")

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"mode": "coaching", "intake_schema": False, "ui": {}}


def test_fake_reply_uses_wiki_grounding_label() -> None:
    settings = Settings(
        anthropic_api_key="",
        knowledge_dir=str(PACK),
        model="fake",
        trust_proxy_hops=0,
        daily_request_cap=500,
    )
    chat._sessions.pop("wiki-label", None)

    result = chat.handle_message("wiki-label", "문서 근거와 해석은 어떻게 구분하나요?", settings)

    assert result["reply"].startswith("[fake] 위키 근거:")


def test_coaching_config_exposes_pack_ui_json(monkeypatch, tmp_path) -> None:
    # 코칭 팩 선택 파일 _ui.json(시작 질문 chips)이 /api/config ui로 노출된다.
    (tmp_path / "doc.md").write_text("# 문서\n\n내용\n", encoding="utf-8")
    (tmp_path / "_ui.json").write_text(
        '{"greeting": "환영", "chips": [{"title": "주제", "send": "질문?"}]}', encoding="utf-8"
    )
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "coaching"
    assert body["ui"]["greeting"] == "환영"
    assert body["ui"]["chips"][0]["send"] == "질문?"


def test_coaching_config_broken_ui_json_fails_loudly(monkeypatch, tmp_path) -> None:
    # 깨진 _ui.json을 조용히 빈 값으로 뭉개면 시작 질문이 소리 없이 사라진다 — 500으로 세운다.
    (tmp_path / "doc.md").write_text("# 문서\n\n내용\n", encoding="utf-8")
    (tmp_path / "_ui.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    response = TestClient(app).get("/api/config")

    assert response.status_code == 500
    assert "_ui.json" in response.json()["detail"]
