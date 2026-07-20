"""F7/F8/F9 (web hardening): 본문 크기 상한 + 보안 헤더 + 에러 경로 노출 차단."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


# --- F7: 요청 본문 크기 상한 (파싱 전 413) --------------------------------------


def test_oversized_body_is_rejected_with_413():
    # 64KB를 크게 넘는 본문은 파싱 전에 413으로 거부된다(2000자 핸들러 검사 이전).
    response = client.post(
        "/api/chat", json={"session_id": "big", "message": "a" * 70000}
    )
    assert response.status_code == 413


def test_normal_body_is_not_rejected(monkeypatch):
    monkeypatch.setenv("MODEL", "fake")
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-wiki")
    response = client.post(
        "/api/chat", json={"session_id": "small", "message": "자기조절학습이란 무엇인가요?"}
    )
    assert response.status_code != 413


# --- F8: 보안 헤더 -------------------------------------------------------------


def test_security_headers_present(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge")
    response = client.get("/api/config")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"


# --- F9: 에러가 내부 경로를 노출하지 않는다 ------------------------------------


def test_knowledge_source_error_does_not_leak_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))  # 빈 폴더 → KnowledgeSourceError
    monkeypatch.setenv("MODEL", "fake")
    response = client.post(
        "/api/chat", json={"session_id": "f9-leak", "message": "안녕하세요"}
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail  # 일반 메시지는 있다
    assert str(tmp_path) not in detail  # 내부 경로는 없다
