import pytest
from starlette.requests import Request

from app import ratelimit
from app import main as main_module


def make_request(client_host: str = "1.2.3.4", xff: str | None = None) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "client": (client_host, 12345),
        "headers": headers,
    }
    return Request(scope)


# --- client_ip: XFF 스푸핑 방어 -------------------------------------------------


def test_client_ip_hops_zero_ignores_xff():
    request = make_request(client_host="1.2.3.4", xff="9.9.9.9")
    assert ratelimit.client_ip(request, trust_proxy_hops=0) == "1.2.3.4"


def test_client_ip_hops_one_takes_rightmost():
    # 왼쪽 "9.9.9.9"는 클라이언트가 자유 조작 가능한 스푸핑 시도 — 신뢰하지 않는다.
    request = make_request(xff="9.9.9.9, 5.5.5.5")
    assert ratelimit.client_ip(request, trust_proxy_hops=1) == "5.5.5.5"


def test_client_ip_hops_two_takes_second_from_right():
    request = make_request(xff="9.9.9.9, 2.2.2.2, 3.3.3.3")
    assert ratelimit.client_ip(request, trust_proxy_hops=2) == "2.2.2.2"


def test_client_ip_falls_back_to_socket_when_xff_missing():
    request = make_request(client_host="1.2.3.4", xff=None)
    assert ratelimit.client_ip(request, trust_proxy_hops=1) == "1.2.3.4"


# --- RateLimiter: 세션 윈도우 + 일일 캡 -----------------------------------------


def test_sixth_new_session_in_window_is_blocked(tmp_path):
    limiter = ratelimit.RateLimiter(path=str(tmp_path / "ratelimit.json"))
    for i in range(5):
        limiter.check("1.2.3.4", f"session-{i}", daily_cap=500)

    with pytest.raises(ratelimit.RateLimitExceeded):
        limiter.check("1.2.3.4", "session-6th", daily_cap=500)


def test_existing_session_followups_are_not_counted(tmp_path):
    limiter = ratelimit.RateLimiter(path=str(tmp_path / "ratelimit.json"))
    for i in range(5):
        limiter.check("1.2.3.4", f"session-{i}", daily_cap=500)

    # 이미 등록된 세션의 후속 발화는 윈도우를 소모하지 않는다.
    limiter.check("1.2.3.4", "session-0", daily_cap=500)
    limiter.check("1.2.3.4", "session-0", daily_cap=500)


def test_window_clears_after_expiry(tmp_path):
    path = tmp_path / "ratelimit.json"
    limiter = ratelimit.RateLimiter(path=str(path))
    for i in range(5):
        limiter.check("1.2.3.4", f"session-{i}", daily_cap=500)

    # 5개의 타임스탬프를 2시간 전으로 되돌려 윈도우 경과를 시뮬레이션한다.
    import json
    from datetime import datetime, timedelta, timezone

    state = json.loads(path.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    state["windows"]["1.2.3.4"] = [old] * 5
    path.write_text(json.dumps(state), encoding="utf-8")

    # 윈도우가 비워졌으니 새 세션은 다시 허용된다.
    limiter.check("1.2.3.4", "session-after-expiry", daily_cap=500)


def test_daily_cap_blocks_new_session(tmp_path):
    limiter = ratelimit.RateLimiter(path=str(tmp_path / "ratelimit.json"))
    with pytest.raises(ratelimit.RateLimitExceeded):
        limiter.check("1.2.3.4", "session-a", daily_cap=0)


# --- main.py 배선: /api/chat 429 응답 ------------------------------------------


def test_api_chat_returns_429_after_six_new_sessions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        main_module, "_rate_limiter", ratelimit.RateLimiter(path=str(tmp_path / "ratelimit.json"))
    )
    monkeypatch.setenv("MODEL", "fake")
    monkeypatch.setenv("KNOWLEDGE_DIR", "knowledge-wiki")
    client = TestClient(main_module.app)

    for i in range(5):
        response = client.post(
            "/api/chat", json={"session_id": f"api-session-{i}", "message": "안녕하세요"}
        )
        assert response.status_code == 200

    response = client.post(
        "/api/chat", json={"session_id": "api-session-6th", "message": "안녕하세요"}
    )
    assert response.status_code == 429
