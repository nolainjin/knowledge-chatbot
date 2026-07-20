"""F5 (CRIT-web): /api/stats 무인증 노출 차단 + LIKE 와일드카드 이스케이프.

- STATS_DASHBOARD_TOKEN 미설정/불일치면 401(상수시간 비교). 토큰 없을 때
  '전체 반환' 기본을 없앤다.
- LIKE prefix의 %/_ 를 ESCAPE로 이스케이프해 와일드카드 주입을 막는다.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app import stats
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


# --- 토큰 인증 -------------------------------------------------------------------


def test_stats_without_token_env_is_unauthorized(monkeypatch):
    monkeypatch.delenv("STATS_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(stats, "read_stats", lambda **_k: {"totals": {}})
    response = client.get("/api/stats")
    assert response.status_code == 401


def test_stats_with_wrong_token_is_unauthorized(monkeypatch):
    monkeypatch.setenv("STATS_DASHBOARD_TOKEN", "s3cret")
    monkeypatch.setattr(stats, "read_stats", lambda **_k: {"totals": {}})
    response = client.get("/api/stats", headers={"X-Stats-Token": "wrong"})
    assert response.status_code == 401


def test_stats_with_correct_token_is_ok(monkeypatch):
    monkeypatch.setenv("STATS_DASHBOARD_TOKEN", "s3cret")
    monkeypatch.setattr(
        stats, "read_stats", lambda **_k: {"totals": {"participants": 1}, "track_counts": []}
    )
    response = client.get("/api/stats", headers={"X-Stats-Token": "s3cret"})
    assert response.status_code == 200
    assert response.json()["totals"]["participants"] == 1


def test_stats_token_accepts_query_param_for_browser(monkeypatch):
    monkeypatch.setenv("STATS_DASHBOARD_TOKEN", "s3cret")
    monkeypatch.setattr(stats, "read_stats", lambda **_k: {"totals": {"participants": 2}})
    response = client.get("/api/stats", params={"token": "s3cret"})
    assert response.status_code == 200


# --- LIKE 와일드카드 이스케이프 -------------------------------------------------


def _make_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE participants (participant_id TEXT PRIMARY KEY);
        CREATE TABLE conversations (
            date TEXT NOT NULL, session_id TEXT NOT NULL, participant_id TEXT NOT NULL,
            PRIMARY KEY (date, session_id)
        );
        CREATE TABLE turns (
            date TEXT NOT NULL, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY (date, session_id, seq)
        );
        """
    )
    conn.execute("INSERT INTO participants VALUES ('demo-person-001')")
    conn.execute("INSERT INTO conversations VALUES ('2026-07-14','s1','demo-person-001')")
    conn.commit()
    conn.close()


def test_like_wildcard_prefix_does_not_match_everything(tmp_path):
    db_path = tmp_path / "chatlog.db"
    _make_db(db_path)

    # '%'는 와일드카드가 아니라 리터럴로 취급돼야 한다 → 아무것도 매칭 안 됨.
    result = stats.read_stats(db_path, participant_prefix="%")
    assert result["totals"]["participants"] == 0

    # 정상 prefix는 그대로 매칭.
    ok = stats.read_stats(db_path, participant_prefix="demo-person-")
    assert ok["totals"]["participants"] == 1
