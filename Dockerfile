# lmwiki-chatbot 배포 이미지.
# python:3.12-slim — 로컬 개발은 3.14지만 이미지는 안정판 slim으로 고정한다.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY static/ static/
COPY scripts/ scripts/
COPY knowledge/ knowledge/

# data/ 는 영속 볼륨 마운트 지점이다.
# app/ratelimit.py(DATA_PATH="data/ratelimit.json"), app/storage.py
# (DEFAULT_CONVERSATIONS_DIR="data/conversations"), scripts/load_to_sqlite.py
# (DEFAULT_DB_PATH="data/chatlog.db")가 전부 cwd 기준 상대경로를 쓰므로,
# 컨테이너 실행 시 cwd가 항상 WORKDIR(/app)이어야 한다. 배포 플랫폼에서
# volume/disk를 /app/data 에 붙인다.
VOLUME ["/app/data"]

EXPOSE 8000

# uvicorn 워커 1 고정 — 반드시 유지한다.
# 1) RateLimiter(app/ratelimit.py)가 단일 프로세스 threading.Lock + 로컬 파일
#    원자적 쓰기를 전제로 함 (수평 확장 시 Redis 등 외부 스토어로 교체 필요).
# 2) 대화 세션 상태(app/chat.py `_sessions`)도 프로세스 메모리 dict라 워커가
#    여러 개면 세션별 10턴 캡이 워커마다 따로 논다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# 배치 실행 진입점: 이 이미지는 ENTRYPOINT를 고정하지 않으므로 CMD를
# 오버라이드해서 같은 이미지로 일배치를 돌린다 (별도 이미지 불필요).
#   docker run --rm -v <host_data>:/app/data <image> \
#     python scripts/load_to_sqlite.py [--date YYYY-MM-DD]
# 크론/스케줄 등록 방법은 deploy/README.md 참고.
