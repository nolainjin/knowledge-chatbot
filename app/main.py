"""FastAPI 앱: POST /api/chat + static 파일 서빙."""

import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from app import chat, intake, ratelimit, stats, storage
from app.config import ConfigError, Settings
from app.knowledge import KnowledgeSourceError

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 2000
# F7(MED-web) -- Content-Length 기준 본문 상한. 2000자 검사는 파싱 후라 큰 본문이
# 먼저 파싱된다. 정상 최대 본문(2000자 UTF-8 + 필드)도 64KB에 한참 못 미친다.
MAX_BODY_BYTES = 64 * 1024
STATIC_DIR = "static"

# F8(MED-web) -- 모든 응답에 붙이는 보안 헤더. static은 별도 파일(app.js/stats.js)이라
# 인라인 스크립트/스타일이 없어 CSP default-src 'self'로 충분하다(인라인 없음 확인).
_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; object-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}

app = FastAPI()
_rate_limiter = ratelimit.RateLimiter()


@app.middleware("http")
async def _security_and_body_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            oversized = int(content_length) > MAX_BODY_BYTES
        except ValueError:
            oversized = False
        if oversized:
            response = JSONResponse(
                status_code=413, content={"detail": "요청 본문이 허용 크기를 초과했습니다."}
            )
            response.headers.update(_SECURITY_HEADERS)
            return response
    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    return response


@app.post("/api/chat")
def post_chat(request: Request, payload: dict = Body(...)):
    session_id = payload.get("session_id")
    message = payload.get("message")
    participant_id = payload.get("participant_id")

    # session_id는 파일명이 되므로 API 경계에서 화이트리스트를 강제한다 —
    # 통과 못 하면 400. 아래 storage 층 검증까지 내려가 500으로 새는 걸 막는다.
    if not isinstance(session_id, str) or not storage.valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="session_id 형식이 올바르지 않습니다.")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=400, detail="message는 비어있지 않은 문자열이어야 합니다.")
    if len(message) > MAX_MESSAGE_LEN:
        raise HTTPException(status_code=400, detail=f"message는 {MAX_MESSAGE_LEN}자를 넘을 수 없습니다.")
    if participant_id is not None and (
        not isinstance(participant_id, str) or not storage.valid_participant_id(participant_id)
    ):
        raise HTTPException(status_code=400, detail="participant_id 형식이 올바르지 않습니다.")

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    ip = ratelimit.client_ip(request, settings.trust_proxy_hops)
    try:
        _rate_limiter.check(ip, session_id, daily_cap=settings.daily_request_cap)
    except ratelimit.RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        return chat.handle_message(session_id, message, participant_id=participant_id)
    except KnowledgeSourceError as exc:
        # F9(LOW-web) -- 상세(지식 폴더 경로 포함)는 서버 로그로만, 클라이언트엔 일반 메시지.
        logger.error("KnowledgeSourceError: %s", exc)
        raise HTTPException(
            status_code=500, detail="지식 문서를 불러오지 못했습니다. 서버 설정을 확인하세요."
        ) from exc


def _coaching_ui(knowledge_dir: str) -> dict:
    """코칭 팩의 선택 파일 `_ui.json`(인사말·제목·시작 질문 chips)을 읽는다.

    없으면 빈 dict — 프론트는 기본 문구를 쓴다. 깨진 JSON/딕셔너리 아님은 조용히
    빈 값으로 뭉개지 않고 500으로 세운다(No Silent Fallback) — 팩 제작자가
    파일을 넣었다면 의도가 있는 것이고, 오타를 침묵시키면 시작 질문이 소리 없이
    사라진다.
    """
    path = Path(knowledge_dir) / "_ui.json"
    if not path.is_file():
        return {}
    try:
        ui = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"_ui.json 파싱 오류: {exc.msg}") from exc
    if not isinstance(ui, dict):
        raise HTTPException(status_code=500, detail="_ui.json 은 JSON 객체여야 합니다.")
    return ui


@app.get("/api/config")
def get_config():
    """스키마 프로브 + 스키마/팩 소유 UI 문구. ui가 비면 프론트는 기본 문구를 쓴다."""
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    schema = intake.load_schema(settings.knowledge_dir)
    coaching = chat.is_grounded_mode(settings.knowledge_dir)
    if schema is not None:
        ui = schema.ui
    elif coaching:
        ui = _coaching_ui(settings.knowledge_dir)
    else:
        ui = {}
    return {
        "mode": "coaching" if coaching else "intake",
        "intake_schema": schema is not None,
        "ui": ui,
    }


def _require_stats_token(request: Request) -> None:
    """F5(CRIT-web) -- 통계 대시보드는 관리자 전용이므로 토큰 인증을 강제한다.

    STATS_DASHBOARD_TOKEN 미설정이거나 제공 토큰이 불일치면 401. '토큰 없으면
    전체 반환' 기본은 없다(무인증 노출 차단). 헤더(X-Stats-Token) 또는 브라우저
    편의를 위한 쿼리 파라미터(token) 중 하나로 받는다. 비교는 상수시간.
    """
    expected = os.getenv("STATS_DASHBOARD_TOKEN") or ""
    provided = request.headers.get("x-stats-token") or request.query_params.get("token") or ""
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="통계 대시보드 접근 토큰이 필요합니다.")


@app.get("/api/stats")
def get_stats(
    request: Request,
    participant_prefix: str | None = Query(default=None, max_length=64),
    session_prefix: str | None = Query(default=None, max_length=64),
):
    """SQLite 적재 결과를 내담자 통계 대시보드용 JSON으로 반환한다(관리자 토큰 필요)."""
    _require_stats_token(request)
    return stats.read_stats(
        participant_prefix=participant_prefix,
        session_prefix=session_prefix,
    )


# static/은 Phase 5가 채운다 — 아직 없을 수 있으니 존재할 때만 마운트해서
# 이 phase 시점에도 앱이 정상 부팅되게 한다. html=True로 "/" 요청 시
# static/index.html을 서빙한다.
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
