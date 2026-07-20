#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
PORT="${PORT:-8934}"
HOST="${HOST:-127.0.0.1}"
PACK="${PACK:-${KNOWLEDGE_DIR:-knowledge}}"
WORK_DIR="$(mktemp -d)"
RESPONSE_FILE="$WORK_DIR/last_response.json"
SERVER_PID=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pack)
      PACK="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --python)
      PYTHON="$2"
      shift 2
      ;;
    *)
      echo "FAIL: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

case "$PACK" in
  /*) PACK_DIR="$PACK" ;;
  *) PACK_DIR="$REPO_ROOT/$PACK" ;;
esac
[ -d "$PACK_DIR" ] || fail "knowledge pack directory not found: $PACK_DIR"
[ -x "$PYTHON" ] || fail "python not executable: $PYTHON"

export MODEL="${MODEL:-fake}"
export KNOWLEDGE_DIR="$PACK_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [ -f "$REPO_ROOT/scripts/validate_knowledge_pack.py" ] && [ -f "$PACK_DIR/_validation_scenario.json" ]; then
  "$PYTHON" "$REPO_ROOT/scripts/validate_knowledge_pack.py" "$PACK_DIR" >/dev/null
fi

cd "$WORK_DIR"
"$PYTHON" -m uvicorn app.main:app --app-dir "$REPO_ROOT" \
  --host "$HOST" --port "$PORT" --log-level warning &
SERVER_PID=$!

ready=0
for _ in $(seq 1 450); do
  if curl -s -o /dev/null "http://$HOST:$PORT/api/config"; then
    ready=1
    break
  fi
  sleep 0.2
done
[ "$ready" -eq 1 ] || fail "server did not start on $HOST:$PORT"

post_chat() {
  curl -s -o "$RESPONSE_FILE" -w '%{http_code}' -X POST "http://$HOST:$PORT/api/chat" \
    -H 'Content-Type: application/json' \
    -d "{\"schema_version\":2,\"metadata\":{\"smoke\":true},\"session_id\":\"$1\",\"message\":\"$2\"}"
}

# 본문 캡처 -- post_chat()이 방금 쓴 $RESPONSE_FILE에서 reply 필드만 뽑는다.
# 기존엔 상태코드만 보고 본문은 버려서(-o /dev/null) 고정 거부가 나와도 200이면
# 통과했다 -- 근거 답변/범위 밖 거부를 판정하려면 본문이 있어야 한다.
reply_text() {
  "$PYTHON" - "$RESPONSE_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f)["reply"])
PY
}

for i in 1 2 3 4 5; do
  status=$(post_chat "smoke-session-$i" "안녕하세요")
  [ "$status" = "200" ] || fail "new session $i expected 200, got $status"
done
status=$(post_chat "smoke-session-6" "안녕하세요")
[ "$status" = "429" ] || fail "6th new session expected 429, got $status"

status=$(post_chat "smoke-session-1" "두 번째 질문")
[ "$status" = "200" ] || fail "second turn expected 200, got $status"
status=$(post_chat "smoke-session-1" "세 번째 질문")
[ "$status" = "200" ] || fail "third turn expected 200, got $status"

TODAY=$(date +%F)
CONV_FILE="$WORK_DIR/data/conversations/$TODAY/smoke-session-1.json"
[ -f "$CONV_FILE" ] || fail "conversation JSON missing: $CONV_FILE"
turn_count=$("$PYTHON" - "$CONV_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as f:
    payload = json.load(f)
turns = payload.get("turns", payload) if isinstance(payload, dict) else payload
print(len(turns))
PY
)
[ "$turn_count" = "6" ] || fail "JSON turns expected 6, got $turn_count"

"$PYTHON" "$REPO_ROOT/scripts/load_to_sqlite.py" --date "$TODAY"

DB_FILE="$WORK_DIR/data/chatlog.db"
[ -f "$DB_FILE" ] || fail "SQLite DB missing: $DB_FILE"
row_count=$("$PYTHON" - "$DB_FILE" <<'PY'
import sqlite3
import sys
conn = sqlite3.connect(sys.argv[1])
try:
    print(conn.execute("SELECT COUNT(*) FROM turns WHERE session_id = 'smoke-session-1'").fetchone()[0])
finally:
    conn.close()
PY
)
[ "$row_count" = "6" ] || fail "SQLite rows expected 6, got $row_count"

# CAP10 -- 근거 답변 1건(인용 포함) + 범위 밖 질문 고정 거부 1건.
# grounded/coaching 팩(_intake_schema.md 없음, app/chat.py is_grounded_mode)에서만
# 의미가 있다 -- 접수 팩(knowledge·knowledge-alt)은 스키마 대화 흐름이라 이
# 게이트 자체가 적용되지 않으므로 조용히 건너뛴다(deploy/README.md, docs/
# customization-guide.md의 기존 `--pack knowledge-alt` 호출을 깨지 않는다).
# 이미 등록된 세션(2, 3)을 재사용한다 -- 새 세션 id를 쓰면 위 5회 신규 세션
# 한도를 넘겨 6번째-429 단언과 충돌한다.
if [ ! -f "$PACK_DIR/_intake_schema.md" ]; then
  NO_GROUNDING_REPLY=$("$PYTHON" -c "from app.chat import _NO_GROUNDING_REPLY as m; print(m)")

  status=$(post_chat "smoke-session-2" "문서 근거와 해석은 어떻게 구분하나요?")
  [ "$status" = "200" ] || fail "grounded question expected 200, got $status"
  reply=$(reply_text)
  [ "$reply" != "$NO_GROUNDING_REPLY" ] || fail "grounded question got the fixed refusal instead of an answer"
  case "$reply" in
    *근거*) : ;;
    *) fail "grounded question reply has no citation ('근거'): ${reply:0:80}" ;;
  esac

  status=$(post_chat "smoke-session-3" "파이썬 데코레이터가 뭔지 설명해줘")
  [ "$status" = "200" ] || fail "out-of-scope question expected 200, got $status"
  reply=$(reply_text)
  [ "$reply" = "$NO_GROUNDING_REPLY" ] || fail "out-of-scope question was not refused: ${reply:0:80}"
fi

echo "OK: local smoke passed pack=$PACK_DIR port=$PORT"
