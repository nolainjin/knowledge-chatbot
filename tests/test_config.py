import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import app


@pytest.fixture
def client():
    """TestClient 피크스처."""
    return TestClient(app)


def test_from_env_without_knowledge_dir_raises_config_error(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_config_endpoint_without_knowledge_dir_returns_explicit_500(client, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    response = client.get("/api/config")
    assert response.status_code == 500
    assert "KNOWLEDGE_DIR" in response.json()["detail"]


def test_config_with_schema(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge")
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "intake", "intake_schema": True, "ui": {}}


def test_config_with_starter_pack_schema(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-alt")
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "intake", "intake_schema": True, "ui": {}}


def test_config_without_schema_fixture(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "tests/fixtures/knowledge-fallback")
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "coaching", "intake_schema": False, "ui": {}}


def test_config_with_math_pack_exposes_coaching_mode(client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-wiki")
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data == {"mode": "coaching", "intake_schema": False, "ui": {}}
