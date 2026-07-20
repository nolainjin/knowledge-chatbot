"""IP별 rate limit — 신규 대화 세션 1시간당 5회 + 전역 일일 요청 상한.

기존 세션의 후속 발화는 카운트하지 않는다(10턴 캡이 이미 세션 내부 상한을
담당). client_ip()는 TRUST_PROXY_HOPS env로 X-Forwarded-For 신뢰 범위를
결정해 스푸핑을 막는다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from fastapi import Request

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=1)
MAX_SESSIONS_PER_WINDOW = 5
# known_sessions는 같은 세션의 후속 발화를 재카운트하지 않으려는 캐시일 뿐이라,
# 하루 지난 항목은 버려도 rate limit 정확성에 영향이 없다(F6 무한 증식 방지).
KNOWN_SESSION_TTL = timedelta(hours=24)
DATA_PATH = "data/ratelimit.json"

RATE_LIMIT_MESSAGE = "이 IP에서 1시간 동안 새 대화 세션을 5회 넘게 시작했습니다. 잠시 후 다시 시도해 주세요."
DAILY_CAP_MESSAGE = "오늘의 서비스 요청 상한에 도달했습니다. 내일 다시 이용해 주세요."

_xff_warned = False  # 프로세스당 1회만 경고 로그를 남기기 위한 플래그


class RateLimitExceeded(Exception):
    """rate limit 초과 시 raise — main.py에서 429로 변환한다."""


def client_ip(request: Request, trust_proxy_hops: int) -> str:
    """TRUST_PROXY_HOPS만큼 X-Forwarded-For 오른쪽에서 신뢰 가능한 주소를 뽑는다.

    hops=0이면 소켓 원격 주소를 그대로 쓰고 XFF는 완전히 무시한다(스푸핑 방지).
    hops>=1이면 오른쪽에서 hops번째 값만 신뢰한다 — 그 왼쪽은 클라이언트가
    자유롭게 조작 가능하므로 신뢰하지 않는다.
    """
    global _xff_warned
    remote = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for")

    if trust_proxy_hops <= 0:
        if xff and not _xff_warned:
            logger.warning(
                "TRUST_PROXY_HOPS=0인데 X-Forwarded-For 헤더가 관측됐습니다. "
                "프록시 뒤에 있다면 TRUST_PROXY_HOPS를 설정하세요."
            )
            _xff_warned = True
        return remote

    if not xff:
        return remote

    hops = [h.strip() for h in xff.split(",") if h.strip()]
    if len(hops) < trust_proxy_hops:
        return remote
    return hops[-trust_proxy_hops]


class RateLimiter:
    """IP별 신규 세션 슬라이딩 윈도우 + 전역 일일 캡. 상태는 JSON 파일에 영속.

    # ponytail: 단일 uvicorn 워커 전제(threading.Lock + 로컬 파일 원자적 쓰기).
    # 수평 확장(멀티 워커/멀티 인스턴스) 시 Redis 등 외부 스토어로 교체.
    """

    def __init__(self, path: str = DATA_PATH, max_per_window: int = MAX_SESSIONS_PER_WINDOW):
        self._path = path
        self._max_per_window = max_per_window
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return {"windows": {}, "known_sessions": {}, "daily": {"date": "", "count": 0}}
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, state: dict) -> None:
        dirname = os.path.dirname(self._path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{self._path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp_path, self._path)  # 원자적 교체

    @staticmethod
    def _prune(state: dict, now: datetime) -> None:
        """만료된 window IP와 하루 지난 known_sessions를 걷어낸다(F6).

        멱등하며 rate limit 판정에 영향이 없다 — window는 어차피 WINDOW로
        필터되고, known_sessions는 재카운트 방지 캐시일 뿐이다.
        """
        windows = state.get("windows", {})
        for ip in list(windows):
            fresh = [ts for ts in windows[ip] if now - datetime.fromisoformat(ts) < WINDOW]
            if fresh:
                windows[ip] = fresh
            else:
                del windows[ip]

        known = state.get("known_sessions", {})
        for session_id in list(known):
            try:
                seen = datetime.fromisoformat(known[session_id])
            except (ValueError, TypeError):
                del known[session_id]
                continue
            if now - seen >= KNOWN_SESSION_TTL:
                del known[session_id]

    def check(self, ip: str, session_id: str, daily_cap: int = 500) -> None:
        """새 세션이면 카운트하고 초과 시 RateLimitExceeded를 raise한다.

        이미 알고 있는 session_id(같은 세션의 후속 발화)는 그대로 통과시킨다.
        """
        with self._lock:
            state = self._load()
            known = state.setdefault("known_sessions", {})
            if session_id in known:
                return

            now = datetime.now(timezone.utc)
            self._prune(state, now)  # 새 세션 등록 경로에서만 prune(항상 save로 이어짐)
            today = now.date().isoformat()

            windows = state.setdefault("windows", {})
            timestamps = [
                ts for ts in windows.get(ip, []) if now - datetime.fromisoformat(ts) < WINDOW
            ]
            windows[ip] = timestamps
            if len(timestamps) >= self._max_per_window:
                self._save(state)
                raise RateLimitExceeded(RATE_LIMIT_MESSAGE)

            daily = state.setdefault("daily", {"date": today, "count": 0})
            if daily.get("date") != today:
                daily["date"] = today
                daily["count"] = 0
            if daily["count"] >= daily_cap:
                self._save(state)
                raise RateLimitExceeded(DAILY_CAP_MESSAGE)

            timestamps.append(now.isoformat())
            daily["count"] += 1
            known[session_id] = now.isoformat()
            self._save(state)
