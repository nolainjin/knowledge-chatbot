"""F6 (HIGH-web): ratelimit.json + chat._sessions 무한 증식 방지.

- ratelimit: 하루 지난 known_sessions / 만료된 window IP를 prune.
- chat._sessions: 상한을 두고 LRU로 evict.
"""

import json
from datetime import datetime, timedelta, timezone

from app import chat, ratelimit


# --- ratelimit prune -------------------------------------------------------------


def test_stale_known_sessions_and_windows_are_pruned(tmp_path):
    path = tmp_path / "ratelimit.json"
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    fresh = (now - timedelta(minutes=5)).isoformat()
    path.write_text(
        json.dumps(
            {
                "windows": {"9.9.9.9": [old, old], "1.1.1.1": [fresh]},
                "known_sessions": {"stale-sess": old, "recent-sess": fresh},
                "daily": {"date": now.date().isoformat(), "count": 3},
            }
        ),
        encoding="utf-8",
    )
    limiter = ratelimit.RateLimiter(path=str(path))

    # 새 세션 등록이 prune을 돌린다.
    limiter.check("2.2.2.2", "brand-new", daily_cap=500)

    state = json.loads(path.read_text(encoding="utf-8"))
    assert "stale-sess" not in state["known_sessions"]
    assert "recent-sess" in state["known_sessions"]
    assert "9.9.9.9" not in state["windows"]  # 전부 만료 → IP 키 제거
    assert "1.1.1.1" in state["windows"]


def test_prune_preserves_rate_limit_behavior(tmp_path):
    """prune 후에도 6번째 새 세션은 여전히 차단된다(멱등·기존 동작 보존)."""
    limiter = ratelimit.RateLimiter(path=str(tmp_path / "ratelimit.json"))
    for i in range(5):
        limiter.check("5.5.5.5", f"s-{i}", daily_cap=500)
    import pytest

    with pytest.raises(ratelimit.RateLimitExceeded):
        limiter.check("5.5.5.5", "s-6th", daily_cap=500)


# --- chat._sessions LRU 상한 -----------------------------------------------------


def test_sessions_dict_is_capped_and_evicts_oldest(monkeypatch):
    monkeypatch.setattr(chat, "MAX_SESSIONS", 2)
    chat._sessions.clear()

    chat._get_session("a")
    chat._get_session("b")
    chat._get_session("c")  # a를 밀어내야 한다

    assert len(chat._sessions) == 2
    assert "a" not in chat._sessions
    assert "b" in chat._sessions and "c" in chat._sessions


def test_sessions_dict_lru_refreshes_on_access(monkeypatch):
    monkeypatch.setattr(chat, "MAX_SESSIONS", 2)
    chat._sessions.clear()

    chat._get_session("a")
    chat._get_session("b")
    chat._get_session("a")  # a를 최근으로 갱신
    chat._get_session("c")  # 이제 b가 가장 오래됨 → evict

    assert "a" in chat._sessions
    assert "b" not in chat._sessions
    assert "c" in chat._sessions
