"""SQLite 상담 로그를 읽어 내담자 통계 대시보드 JSON으로 변환한다."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data/chatlog.db")


_SLOT_LABELS = {
    "track": "상담 트랙",
    "chief_complaint": "호소 문제",
    "symptom_context": "증상 시기·일상 영향",
    "relationship_context": "관계 대상·기간",
    "crisis_plan_means": "자해 계획·수단",
    "crisis_attempt_history": "과거 시도 이력",
    "addiction_type": "중독 문제 유형",
    "addiction_severity": "중독 안내 긴급도",
    "addiction_referral": "전문기관 연결",
    "coping": "대처 시도",
    "support": "지지체계",
    "expectation": "상담 기대",
}


def _empty_stats(db_path: Path, filters: dict[str, str | None] | None = None) -> dict[str, Any]:
    return {
        "database": str(db_path),
        "exists": db_path.exists(),
        "filters": filters or {},
        "totals": {
            "participants": 0,
            "conversations": 0,
            "turns": 0,
            "user_turns": 0,
            "assistant_turns": 0,
            "summaries": 0,
            "red_flag_sessions": 0,
            "avg_user_turns_per_conversation": 0,
            "notable_sessions": 0,
        },
        "track_counts": [],
        "slot_completion": [],
        "daily_counts": [],
        "recent_sessions": [],
        "individual_flags": [],
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _escape_like(value: str) -> str:
    """LIKE prefix의 와일드카드(%,_)와 이스케이프 문자(\\)를 리터럴로 만든다(F5).

    ESCAPE '\\' 절과 함께 써서 사용자 제어 prefix가 와일드카드로 해석돼 필터를
    우회(예: '%'로 전체 매칭)하는 것을 막는다.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conversation_filter(
    participant_prefix: str | None,
    session_prefix: str | None,
    alias: str = "c",
) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    if participant_prefix:
        clauses.append(f"{alias}.participant_id LIKE ? ESCAPE '\\'")
        params.append(f"{_escape_like(participant_prefix)}%")
    if session_prefix:
        clauses.append(f"{alias}.session_id LIKE ? ESCAPE '\\'")
        params.append(f"{_escape_like(session_prefix)}%")
    return (" WHERE " + " AND ".join(clauses), tuple(params)) if clauses else ("", ())


def _turn_join_filter(
    participant_prefix: str | None,
    session_prefix: str | None,
    role: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    where, params = _conversation_filter(participant_prefix, session_prefix, "c")
    clauses: list[str] = []
    merged = list(params)
    if where:
        clauses.append(where[7:])
    if role:
        clauses.append("t.role=?")
        merged.append(role)
    return (" WHERE " + " AND ".join(clauses), tuple(merged)) if clauses else ("", ())


def _load_summaries(
    conn: sqlite3.Connection,
    participant_prefix: str | None,
    session_prefix: str | None,
) -> list[dict[str, Any]]:
    where, params = _turn_join_filter(participant_prefix, session_prefix, "intake_summary")
    rows = conn.execute(
        f"""
        SELECT t.date, t.session_id, t.text
        FROM turns t
        JOIN conversations c
          ON c.date=t.date AND c.session_id=t.session_id
        {where}
        ORDER BY t.date DESC, t.session_id ASC, t.seq ASC
        """,
        params,
    ).fetchall()
    summaries: list[dict[str, Any]] = []
    for day, session_id, text in rows:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed["date"] = day
            parsed["session_id"] = session_id
            summaries.append(parsed)
    return summaries


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _individual_flag_record(
    day: str,
    session_id: str,
    participant_id: str,
    summary: dict[str, Any],
    user_turns: int,
) -> dict[str, Any] | None:
    """세션별로 사람이 다시 봐야 할 특이 사항을 도출한다.

    진단이 아니라 운영 대시보드용 triage 신호다. 모델 판단을 새로 만들지 않고
    이미 적재된 summary JSON만 사용한다.
    """
    slots = summary.get("slots") if isinstance(summary.get("slots"), dict) else {}
    unfilled = summary.get("unfilled") if isinstance(summary.get("unfilled"), dict) else {}
    red_flags = summary.get("red_flags") if isinstance(summary.get("red_flags"), list) else []
    track = str(summary.get("track") or "미확인")

    flags: list[dict[str, str]] = []
    severity_rank = 0

    def add(severity: str, label: str, detail: str) -> None:
        nonlocal severity_rank
        rank = {"high": 3, "medium": 2, "low": 1}.get(severity, 1)
        severity_rank = max(severity_rank, rank)
        flags.append({"severity": severity, "label": label, "detail": detail})

    if red_flags or track == "위기":
        add("high", "위기 우선 확인", "위기 트랙 또는 red flag 슬롯이 감지됐습니다.")
        if "crisis_plan_means" in unfilled or "crisis_plan_means" not in slots:
            add("high", "현재 계획·수단 미확인", "구체적 계획·수단 여부를 사람 상담자가 다시 확인해야 합니다.")
        if "crisis_attempt_history" in unfilled or "crisis_attempt_history" not in slots:
            add("medium", "과거 시도 이력 미확인", "과거 자해·자살 시도 이력이 비어 있습니다.")

    addiction_handoff = track == "중독"
    referral = str(slots.get("addiction_referral") or "")
    if addiction_handoff:
        addiction_severity = str(slots.get("addiction_severity") or "평가 필요")
        if not referral:
            add("high", "중독 전문기관 연결 미확인", "전문기관 정보 제공 여부를 다시 확인해야 합니다.")
        elif addiction_severity == "응급":
            add("high", "중독 응급 안내", "119 또는 응급실 우선 안내가 기록됐습니다.")
        elif addiction_severity == "고위험":
            add("medium", "중독 전문기관 우선", "중독 전문기관의 빠른 평가가 필요한 경로입니다.")
        else:
            add("low", "중독 전문기관 안내", "일반 상담 대신 중독 전문기관 정보가 제공됐습니다.")

    if track == "미확인":
        add("medium", "트랙 미확인", "정서·관계·중독·위기 중 어느 경로인지 아직 분기되지 않았습니다.")

    if "chief_complaint" in unfilled or "chief_complaint" not in slots:
        add("medium", "호소 문제 미확인", "상담 신청 이유가 요약에 충분히 잡히지 않았습니다.")

    support = str(slots.get("support") or "")
    if not addiction_handoff:
        if "support" in unfilled or not support:
            add("medium", "지지체계 미확인", "도와주는 사람이 있는지 아직 확인되지 않았습니다.")
        elif _has_any(support, ("없", "혼자", "거의 없", "모르", "감당")):
            add("medium", "지지체계 취약", support)

    coping = str(slots.get("coping") or "")
    if not addiction_handoff and coping and _has_any(coping, ("참고", "버티", "한계", "오래가지는")):
        add("low", "대처 전략 취약", coping)

    expectation = str(slots.get("expectation") or "")
    if expectation and _has_any(expectation, ("안전", "넘기는", "위험")):
        add("medium", "안전계획 기대", expectation)

    if user_turns < 3 and not (addiction_handoff and referral):
        add("low", "조기 이탈 가능", f"사용자 입력이 {user_turns}턴뿐입니다.")

    if not flags:
        return None

    severity = {3: "high", 2: "medium", 1: "low"}[severity_rank]
    missing_labels = [_SLOT_LABELS.get(slot_id, slot_id) for slot_id in unfilled.keys()]
    return {
        "date": day,
        "session_id": session_id,
        "participant_id": participant_id,
        "track": track,
        "severity": severity,
        "flags": flags,
        "chief_complaint": slots.get("chief_complaint", ""),
        "addiction_type": slots.get("addiction_type", ""),
        "addiction_severity": slots.get("addiction_severity", ""),
        "addiction_referral": referral,
        "support": support,
        "expectation": expectation,
        "missing": missing_labels,
        "user_turns": user_turns,
    }


def read_stats(
    db_path: str | Path = DEFAULT_DB_PATH,
    participant_prefix: str | None = None,
    session_prefix: str | None = None,
) -> dict[str, Any]:
    db_path = Path(db_path)
    filters = {"participant_prefix": participant_prefix, "session_prefix": session_prefix}
    if not db_path.exists():
        return _empty_stats(db_path, filters)

    conn = sqlite3.connect(db_path)
    try:
        if not all(_table_exists(conn, table) for table in ("participants", "conversations", "turns")):
            return _empty_stats(db_path, filters)

        conv_where, conv_params = _conversation_filter(participant_prefix, session_prefix, "c")
        turn_where, turn_params = _turn_join_filter(participant_prefix, session_prefix)
        user_where, user_params = _turn_join_filter(participant_prefix, session_prefix, "user")
        assistant_where, assistant_params = _turn_join_filter(participant_prefix, session_prefix, "assistant")
        summary_where, summary_params = _turn_join_filter(participant_prefix, session_prefix, "intake_summary")

        participant_total = _count(
            conn,
            f"SELECT COUNT(DISTINCT c.participant_id) FROM conversations c{conv_where}",
            conv_params,
        )
        conversation_total = _count(
            conn,
            f"SELECT COUNT(*) FROM conversations c{conv_where}",
            conv_params,
        )
        turn_total = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM turns t
            JOIN conversations c
              ON c.date=t.date AND c.session_id=t.session_id
            """
            + turn_where,
            turn_params,
        )
        user_turns = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM turns t
            JOIN conversations c
              ON c.date=t.date AND c.session_id=t.session_id
            """
            + user_where,
            user_params,
        )
        assistant_turns = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM turns t
            JOIN conversations c
              ON c.date=t.date AND c.session_id=t.session_id
            """
            + assistant_where,
            assistant_params,
        )
        summary_total = _count(
            conn,
            """
            SELECT COUNT(*)
            FROM turns t
            JOIN conversations c
              ON c.date=t.date AND c.session_id=t.session_id
            """
            + summary_where,
            summary_params,
        )
        avg_user_turns = round(user_turns / conversation_total, 2) if conversation_total else 0

        summaries = _load_summaries(conn, participant_prefix, session_prefix)
        track_counter: Counter[str] = Counter()
        completed_slots: Counter[str] = Counter()
        missing_slots: Counter[str] = Counter()
        red_flag_sessions = 0
        for summary in summaries:
            track_counter[str(summary.get("track") or "미확인")] += 1
            slots = summary.get("slots") if isinstance(summary.get("slots"), dict) else {}
            for slot_id, value in slots.items():
                if value not in (None, "", "미확인"):
                    completed_slots[str(slot_id)] += 1
            unfilled = summary.get("unfilled") if isinstance(summary.get("unfilled"), dict) else {}
            for slot_id in unfilled:
                missing_slots[str(slot_id)] += 1
            red_flags = summary.get("red_flags") if isinstance(summary.get("red_flags"), list) else []
            if red_flags:
                red_flag_sessions += 1

        all_slot_ids = sorted(set(_SLOT_LABELS) | set(completed_slots) | set(missing_slots))
        slot_completion = []
        denominator = summary_total or conversation_total or 1
        for slot_id in all_slot_ids:
            completed = completed_slots[slot_id]
            missing = missing_slots[slot_id]
            slot_completion.append(
                {
                    "slot_id": slot_id,
                    "label": _SLOT_LABELS.get(slot_id, slot_id),
                    "completed": completed,
                    "missing": missing,
                    "rate": round(completed / denominator, 3),
                }
            )
        slot_completion.sort(key=lambda item: (-item["completed"], item["slot_id"]))

        daily_counts = [
            {"date": row[0], "conversations": row[1], "participants": row[2]}
            for row in conn.execute(
                f"""
                SELECT c.date, COUNT(*), COUNT(DISTINCT c.participant_id)
                FROM conversations c
                {conv_where}
                GROUP BY c.date
                ORDER BY c.date DESC
                LIMIT 14
                """,
                conv_params,
            ).fetchall()
        ]

        user_turn_count_by_session = defaultdict(int)
        for day, session_id, count in conn.execute(
            f"""
            SELECT t.date, t.session_id, COUNT(*)
            FROM turns t
            JOIN conversations c
              ON c.date=t.date AND c.session_id=t.session_id
            {user_where}
            GROUP BY t.date, t.session_id
            """,
            user_params,
        ).fetchall():
            user_turn_count_by_session[(day, session_id)] = int(count)

        summary_by_session = {(s["date"], s["session_id"]): s for s in summaries}
        session_rows = conn.execute(
            f"""
            SELECT c.date, c.session_id, c.participant_id
            FROM conversations c
            {conv_where}
            ORDER BY c.date DESC, c.session_id DESC
            """,
            conv_params,
        ).fetchall()

        recent_sessions = []
        individual_flags = []
        for day, session_id, participant_id in session_rows:
            summary = summary_by_session.get((day, session_id), {})
            user_turn_count = user_turn_count_by_session[(day, session_id)]
            recent_sessions.append(
                {
                    "date": day,
                    "session_id": session_id,
                    "participant_id": participant_id,
                    "track": summary.get("track", "미확인"),
                    "red_flags": summary.get("red_flags", []),
                    "user_turns": user_turn_count,
                }
            )
            if summary:
                record = _individual_flag_record(
                    day,
                    session_id,
                    participant_id,
                    summary,
                    user_turn_count,
                )
                if record:
                    individual_flags.append(record)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        individual_flags.sort(
            key=lambda item: (
                severity_order.get(item["severity"], 9),
                item["date"],
                item["session_id"],
            )
        )
        recent_sessions = recent_sessions[:20]

        return {
            "database": str(db_path),
            "exists": True,
            "filters": filters,
            "totals": {
                "participants": participant_total,
                "conversations": conversation_total,
                "turns": turn_total,
                "user_turns": user_turns,
                "assistant_turns": assistant_turns,
                "summaries": summary_total,
                "red_flag_sessions": red_flag_sessions,
                "avg_user_turns_per_conversation": avg_user_turns,
                "notable_sessions": len(individual_flags),
            },
            "track_counts": [
                {"track": track, "count": count} for track, count in track_counter.most_common()
            ],
            "slot_completion": slot_completion,
            "daily_counts": daily_counts,
            "recent_sessions": recent_sessions,
            "individual_flags": individual_flags[:100],
        }
    finally:
        conn.close()
