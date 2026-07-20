"""전일자(또는 --date) JSON 대화 로그를 SQLite(data/chatlog.db)에 적재한다.

날짜+세션+턴순번(PK)로 UPSERT하므로 같은 날짜를 여러 번 돌려도 중복되지 않고,
같은 session_id가 날짜를 넘겨 재사용돼도 서로 덮어쓰지 않는다(멱등).
표준 sqlite3 모듈만 쓴다 — 서버 DB는 두지 않는다.

크론 등록 예시 (실제 등록은 배포 phase에서):
    0 3 * * * cd /path/to/repo && .venv/bin/python scripts/load_to_sqlite.py
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from app import storage

DEFAULT_CONVERSATIONS_DIR = Path("data/conversations")
DEFAULT_DB_PATH = Path("data/chatlog.db")

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS conversations (
    date TEXT NOT NULL,
    session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    PRIMARY KEY (date, session_id),
    FOREIGN KEY (participant_id)
        REFERENCES participants(participant_id)
        ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS turns (
    date TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (date, session_id, seq),
    FOREIGN KEY (date, session_id)
        REFERENCES conversations(date, session_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_turns_date_session_seq
    ON turns(date, session_id, seq);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """현재 스키마를 보장하고, 초기 session_id-only/participant-less 스키마를 보존 마이그레이션한다."""
    turn_columns = _table_columns(conn, "turns")
    conversation_columns = _table_columns(conn, "conversations")
    if turn_columns and "date" not in turn_columns:
        conn.executescript(
            """
            ALTER TABLE conversations RENAME TO conversations_legacy;
            ALTER TABLE turns RENAME TO turns_legacy;
            """
        )
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT OR IGNORE INTO participants (participant_id)
            SELECT session_id
            FROM conversations_legacy
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (date, session_id, participant_id)
            SELECT date, session_id, session_id
            FROM conversations_legacy
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO turns (date, session_id, seq, role, text)
            SELECT conversations_legacy.date, turns_legacy.session_id,
                   turns_legacy.seq, turns_legacy.role, turns_legacy.text
            FROM turns_legacy
            JOIN conversations_legacy
              ON conversations_legacy.session_id = turns_legacy.session_id
            """
        )
        conn.executescript(
            """
            DROP TABLE turns_legacy;
            DROP TABLE conversations_legacy;
            """
        )
        return

    if conversation_columns and "participant_id" not in conversation_columns:
        conn.executescript(
            """
            ALTER TABLE conversations RENAME TO conversations_legacy;
            ALTER TABLE turns RENAME TO turns_legacy;
            """
        )
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT OR IGNORE INTO participants (participant_id)
            SELECT session_id
            FROM conversations_legacy
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (date, session_id, participant_id)
            SELECT date, session_id, session_id
            FROM conversations_legacy
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO turns (date, session_id, seq, role, text)
            SELECT date, session_id, seq, role, text
            FROM turns_legacy
            """
        )
        conn.executescript(
            """
            DROP TABLE turns_legacy;
            DROP TABLE conversations_legacy;
            """
        )
        return

    conn.executescript(_SCHEMA)


def _read_conversation_file(json_file: Path) -> tuple[str, str, list[dict]]:
    raw = json.loads(json_file.read_text(encoding="utf-8"))
    payload = storage.normalize_conversation_payload(raw, json_file.stem)
    return payload["session_id"], payload["participant_id"], payload["turns"]


def load_day(
    day: date,
    conversations_dir: Path = DEFAULT_CONVERSATIONS_DIR,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """day 디렉토리의 세션 JSON을 전부 SQLite에 UPSERT한다. 적재한 턴 수를 반환한다."""
    day_dir = Path(conversations_dir) / day.isoformat()
    if not day_dir.is_dir():
        return 0

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema(conn)
        loaded = 0
        for json_file in sorted(day_dir.glob("*.json")):
            session_id, participant_id, turns = _read_conversation_file(json_file)
            conn.execute(
                "INSERT OR IGNORE INTO participants (participant_id) VALUES (?)",
                (participant_id,),
            )
            conn.execute(
                "INSERT INTO conversations (date, session_id, participant_id) VALUES (?, ?, ?) "
                "ON CONFLICT(date, session_id) DO UPDATE SET participant_id=excluded.participant_id",
                (day.isoformat(), session_id, participant_id),
            )
            for turn in turns:
                conn.execute(
                    "INSERT INTO turns (date, session_id, seq, role, text) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(date, session_id, seq) DO UPDATE SET "
                    "role=excluded.role, text=excluded.text",
                    (day.isoformat(), session_id, turn["seq"], turn["role"], turn["text"]),
                )
                loaded += 1
        conn.commit()
        return loaded
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="적재할 날짜 YYYY-MM-DD (기본값: 어제)")
    args = parser.parse_args(argv)

    target_day = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    loaded = load_day(target_day)
    print(f"{target_day.isoformat()}: {loaded}턴 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
