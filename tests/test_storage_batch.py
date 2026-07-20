"""JSON 저장 → SQLite 적재 → 조회 왕복 + 멱등성 테스트."""

import json
import sqlite3
from datetime import date, timedelta

import pytest

from app import storage
from scripts import load_to_sqlite


def test_append_turn_rejects_path_traversal(tmp_path):
    conv_dir = tmp_path / "conversations"
    for bad in ("../evil", "a/b", "a\\b", "", "x" * 129):
        with pytest.raises(ValueError):
            storage.append_turn(bad, "user", "공격", base_dir=conv_dir)
    assert not (tmp_path / "evil.json").exists()


def test_append_turn_rejects_bad_participant_id(tmp_path):
    conv_dir = tmp_path / "conversations"
    with pytest.raises(ValueError):
        storage.append_turn("session-a", "user", "공격", participant_id="../person", base_dir=conv_dir)


def test_append_turn_writes_json(tmp_path):
    conv_dir = tmp_path / "conversations"
    storage.append_turn(
        "session-a", "user", "안녕하세요", participant_id="person-001", base_dir=conv_dir
    )
    storage.append_turn(
        "session-a", "assistant", "반갑습니다", participant_id="person-001", base_dir=conv_dir
    )

    day_file = conv_dir / date.today().isoformat() / "session-a.json"
    payload = json.loads(day_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["session_id"] == "session-a"
    assert payload["participant_id"] == "person-001"
    assert payload["turns"] == [
        {"seq": 0, "role": "user", "text": "안녕하세요"},
        {"seq": 1, "role": "assistant", "text": "반갑습니다"},
    ]


def test_load_to_sqlite_round_trip(tmp_path):
    conv_dir = tmp_path / "conversations"
    db_path = tmp_path / "chatlog.db"
    today = date.today()

    storage.append_turn("session-b", "user", "질문", participant_id="person-b", base_dir=conv_dir)
    storage.append_turn("session-b", "assistant", "답변", participant_id="person-b", base_dir=conv_dir)

    loaded = load_to_sqlite.load_day(today, conversations_dir=conv_dir, db_path=db_path)
    assert loaded == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT participants.participant_id, conversations.session_id, turns.role, turns.text
        FROM turns
        JOIN conversations
          ON conversations.date = turns.date
         AND conversations.session_id = turns.session_id
        JOIN participants
          ON participants.participant_id = conversations.participant_id
        WHERE turns.date = ? AND turns.session_id = ?
        ORDER BY turns.seq
        """,
        (today.isoformat(), "session-b"),
    ).fetchall()
    conn.close()
    assert rows == [
        ("person-b", "session-b", "user", "질문"),
        ("person-b", "session-b", "assistant", "답변"),
    ]


def test_load_to_sqlite_is_idempotent(tmp_path):
    conv_dir = tmp_path / "conversations"
    db_path = tmp_path / "chatlog.db"
    today = date.today()

    storage.append_turn("session-c", "user", "중복 확인", base_dir=conv_dir)

    load_to_sqlite.load_day(today, conversations_dir=conv_dir, db_path=db_path)
    load_to_sqlite.load_day(today, conversations_dir=conv_dir, db_path=db_path)  # 재실행

    conn = sqlite3.connect(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE date = ? AND session_id = ?",
        (today.isoformat(), "session-c"),
    ).fetchone()[0]
    conn.close()
    assert total == 1


def test_load_to_sqlite_keeps_same_session_id_on_different_dates(tmp_path):
    conv_dir = tmp_path / "conversations"
    db_path = tmp_path / "chatlog.db"
    first_day = date(2026, 7, 12)
    second_day = first_day + timedelta(days=1)

    for day, text in ((first_day, "첫날 질문"), (second_day, "다음날 질문")):
        day_dir = conv_dir / day.isoformat()
        day_dir.mkdir(parents=True)
        (day_dir / "same-session.json").write_text(
            json.dumps([{"seq": 0, "role": "user", "text": text}], ensure_ascii=False),
            encoding="utf-8",
        )

    assert load_to_sqlite.load_day(first_day, conversations_dir=conv_dir, db_path=db_path) == 1
    assert load_to_sqlite.load_day(second_day, conversations_dir=conv_dir, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT date, session_id, seq, text FROM turns ORDER BY date, seq"
    ).fetchall()
    conn.close()

    assert rows == [
        ("2026-07-12", "same-session", 0, "첫날 질문"),
        ("2026-07-13", "same-session", 0, "다음날 질문"),
    ]


def test_load_to_sqlite_links_multiple_sessions_to_one_participant(tmp_path):
    conv_dir = tmp_path / "conversations"
    db_path = tmp_path / "chatlog.db"
    target_day = date(2026, 7, 13)
    day_dir = conv_dir / target_day.isoformat()
    day_dir.mkdir(parents=True)

    for session_id, text in (("session-1", "첫 상담"), ("session-2", "재방문")):
        (day_dir / f"{session_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "session_id": session_id,
                    "participant_id": "person-shared",
                    "turns": [{"seq": 0, "role": "user", "text": text}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    assert load_to_sqlite.load_day(target_day, conversations_dir=conv_dir, db_path=db_path) == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT participant_id, session_id FROM conversations ORDER BY session_id"
    ).fetchall()
    participant_count = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    conn.close()

    assert participant_count == 1
    assert rows == [("person-shared", "session-1"), ("person-shared", "session-2")]


def test_load_to_sqlite_migrates_legacy_session_id_primary_key(tmp_path):
    db_path = tmp_path / "chatlog.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE conversations (
            session_id TEXT PRIMARY KEY,
            date TEXT NOT NULL
        );
        CREATE TABLE turns (
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (session_id, seq)
        );
        INSERT INTO conversations (session_id, date) VALUES ('legacy-session', '2026-07-11');
        INSERT INTO turns (session_id, seq, role, text)
        VALUES ('legacy-session', 0, 'user', '레거시 질문');
        """
    )
    conn.commit()
    conn.close()

    conv_dir = tmp_path / "conversations"
    day_dir = conv_dir / "2026-07-12"
    day_dir.mkdir(parents=True)
    (day_dir / "new-session.json").write_text(
        json.dumps([{"seq": 0, "role": "assistant", "text": "신규 답변"}], ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_to_sqlite.load_day(date(2026, 7, 12), conversations_dir=conv_dir, db_path=db_path) == 1

    conn = sqlite3.connect(db_path)
    turn_columns = [row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()]
    conversation_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
    ]
    participant_rows = conn.execute("SELECT participant_id FROM participants ORDER BY participant_id").fetchall()
    rows = conn.execute(
        "SELECT date, session_id, seq, role, text FROM turns ORDER BY date, session_id"
    ).fetchall()
    conn.close()

    assert "date" in turn_columns
    assert "participant_id" in conversation_columns
    assert participant_rows == [("legacy-session",), ("new-session",)]
    assert rows == [
        ("2026-07-11", "legacy-session", 0, "user", "레거시 질문"),
        ("2026-07-12", "new-session", 0, "assistant", "신규 답변"),
    ]


def test_load_to_sqlite_missing_day_returns_zero(tmp_path):
    loaded = load_to_sqlite.load_day(
        date(2000, 1, 1),
        conversations_dir=tmp_path / "conversations",
        db_path=tmp_path / "chatlog.db",
    )
    assert loaded == 0
