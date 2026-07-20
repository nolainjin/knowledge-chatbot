# 배포 가이드

플랫폼 확정 전 공통 준비물(Dockerfile, `.dockerignore`)은 이미 리포 루트에
있다. 이 문서는 후보 4개 플랫폼(D01, `docs/planning/lmwiki-chatbot-proto/decisions.md`
참고 — **아직 미확정**)의 배포 절차를 각각 정리한다. 소스는
`docs/planning/lmwiki-chatbot-proto/research.md`(2026-07-11 공식 문서 조사).

## 공통 사항

### 환경변수

`.env.example` 기준. 실제 값은 플랫폼의 env/secret 설정 화면에 입력한다 —
`.env` 파일을 이미지에 굽거나 리포에 커밋하지 않는다(`.dockerignore`,
`.gitignore` 둘 다 `.env` 제외).

| 변수 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 필수 | Claude API 키 |
| `KNOWLEDGE_DIR` | 선택 (기본 `knowledge`) | 지식 디렉토리 — 이미지에 `knowledge/`가 빌드타임에 포함됨 |
| `MODEL` | 선택 (기본 `claude-haiku-4-5`) | `fake`로 두면 오프라인 스텁 응답(API 미호출) |
| `TRUST_PROXY_HOPS` | 선택 (기본 `0`) | 플랫폼별 권장값은 아래 표 참고 |
| `DAILY_REQUEST_CAP` | 선택 (기본 `500`) | 전역 일일 요청 상한 |
| `STATS_DASHBOARD_TOKEN` | 관리자 대시보드 쓸 때 필수 | `/api/stats` 접근 토큰. 미설정이면 401(무인증 덤프 차단) |

### 영속 볼륨

`data/` 디렉토리(컨테이너 경로 `/app/data`) 하나에 `ratelimit.json`,
`conversations/YYYY-MM-DD/*.json`, `chatlog.db`가 전부 들어간다. 이 경로가
재시작 후에도 남아있지 않으면 rate limit·대화 이력·배치 적재 결과가 전부
날아간다 — **반드시 영속 볼륨/디스크를 `/app/data`에 붙인다.**

### 배치(일 1회 SQLite 적재) 실행

이미지에 별도 ENTRYPOINT가 없으므로 같은 이미지의 CMD를 오버라이드해서
배치를 돌린다. 웹 서비스와 배치가 컨테이너 내부에서 같은 `/app/data`를
봐야 하므로, 배치도 웹 서비스와 **동일한 볼륨/디스크**를 마운트해서 실행한다.

```bash
docker run --rm -v <host_or_platform_data>:/app/data <image> \
  python scripts/load_to_sqlite.py            # 기본: 어제 날짜
docker run --rm -v <host_or_platform_data>:/app/data <image> \
  python scripts/load_to_sqlite.py --date 2026-07-11   # 특정 날짜 재실행(멱등)
```

### TRUST_PROXY_HOPS

`app/ratelimit.py`의 `client_ip()`는 `TRUST_PROXY_HOPS=0`이면 소켓 원격
주소를 그대로 쓰고(X-Forwarded-For 완전 무시), `>=1`이면 그 hop 수만큼
XFF 오른쪽에서 신뢰 가능한 IP를 뽑는다. 값이 실제 프록시 hop 수보다 작으면
클라이언트가 IP를 스푸핑해 rate limit을 우회할 수 있고, 값이 크면 모든
요청이 같은 IP로 뭉뚱그려져 rate limit이 과도하게 걸린다 — **플랫폼의 엣지
프록시 구조와 정확히 맞춰야 한다.** `TRUST_PROXY_HOPS=0`인데 XFF 헤더가
관측되면(= 프록시 뒤인데 0으로 둔 footgun) `app/ratelimit.py`가 서버 로그에
1회 경고를 남긴다 — 배포 직후 로그에서 이 경고를 확인해 홉수 오설정을 잡는다.

## 보안 하드닝 (Phase 7 후속)

배포 전/후 확인 항목. 코드에 이미 반영된 방어층과 운영자가 챙길 설정을 정리한다.

### 통계 대시보드 인증 (`STATS_DASHBOARD_TOKEN`)

- `/api/stats`와 `static/stats.html`은 **관리자 전용**이다. `STATS_DASHBOARD_TOKEN`을
  랜덤 문자열로 설정하지 않으면 `/api/stats`는 전부 401을 반환한다(무인증 전체
  통계 덤프를 원천 차단).
- 브라우저 대시보드는 `stats.html?token=<토큰>`으로 연다. `stats.js`가 이 값을
  `X-Stats-Token` 헤더로 전달한다(쿼리 파라미터 `token`도 허용하지만, 헤더 방식이
  접근 로그·리퍼러에 토큰이 남지 않아 권장). 토큰 비교는 상수시간(`hmac.compare_digest`).

### 요청 본문 크기 / 보안 헤더 (코드 반영, 무설정)

- 미들웨어가 `Content-Length` > 64KB 요청을 파싱 전에 413으로 거부한다.
- 모든 응답에 `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy: default-src 'self'; object-src 'none'; frame-ancestors 'none'`,
  `Referrer-Policy: no-referrer`를 붙인다. static 자산은 별도 `.js`/`.css` 파일이라
  인라인 스크립트/스타일이 없어 CSP `default-src 'self'`로 충분하다. **static에
  인라인 스크립트를 추가하면 CSP가 깨지므로** 새 자산도 별도 파일로 둔다.

### 지식 팩 provenance (RAG 신뢰 경계)

- 지식 문서 본문은 시스템 채널(프롬프트)에 주입된다. 따라서 **신뢰된 큐레이션
  팩만** `KNOWLEDGE_DIR`로 연결한다. 커뮤니티·PR로 들어온 팩을 검토 없이 붙이면
  문서 안에 심어진 지시가 인젝션 벡터가 된다 — 팩 수용은 별도 신뢰 경계(리뷰·
  서명 등)를 두고, 코드의 결정론 방어층(`app/safety.py`, 인용 대조)에만 의존하지
  않는다.

### 배포 전 의존성 감사

- 공개/호스팅 전 `pip-audit -r requirements.txt`로 알려진 취약점을 점검한다
  (이 작업 세션 샌드박스에서는 네트워크 제약으로 미실행 — 배포 파이프라인에서 실행).

## Railway (Hobby, $5/월 고정)

- 근거: `https://docs.railway.com/pricing/plans` (2026-07-11 확인) — 영속
  Volume(~$0.15/GB·월) + 서비스 내장 Cron Schedule + spin-down 없음.
- 배포: 리포 루트의 `Dockerfile`을 그대로 인식(Railway가 자동 빌드). 서비스
  생성 시 "Deploy from Dockerfile" 선택.
- 볼륨: 서비스 설정 > Volumes에서 마운트 경로를 `/app/data`로 지정해 attach.
- 배치: 서비스 설정 > Cron Schedule에 새 크론 잡 등록. 커맨드는
  `python scripts/load_to_sqlite.py`, 스케줄은 `0 3 * * *`(매일 03:00) 권장.
  같은 서비스/같은 볼륨을 참조하므로 별도 `docker run` 불필요.
- env: Variables 탭에 위 공통 환경변수 입력.
- **TRUST_PROXY_HOPS=1** — Railway 엣지 프록시가 XFF에 1홉을 추가한다.
- 참고: "월 $1 크레딧 Free 플랜" 존재 여부는 문서 간 상충(research.md
  Remaining Uncertainty) — 가입 화면에서 실제 청구 방식 재확인.

## Fly.io (월 $5~10)

- 근거: `https://fly.io/docs/about/pricing/` (2026-07-11 확인) — 2024-10
  이후 신규 무료 티어 없음(트라이얼만). Volumes $0.15/GB·월 + 머신 초 단위
  과금. 소형 상시 구성 월 $5~10.
- 배포: `fly launch` (Dockerfile 자동 인식) 또는 `fly deploy`.
- 볼륨: `fly volumes create data --size 1` 로 생성 후 `fly.toml`의
  `[mounts]`에 `source = "data"`, `destination = "/app/data"` 지정.
- 배치: Fly.io는 서비스 내장 크론이 없다. 둘 중 하나를 쓴다.
  - **스케줄 머신**: `fly machine run <image> --schedule daily
    --command "python scripts/load_to_sqlite.py"` (같은 앱의 볼륨을 재마운트)
  - **외부 크론 트리거**: 외부 스케줄러(예: GitHub Actions cron, cron-job.org)가
    HTTP로 볼 수 있는 별도 관리 엔드포인트를 앱에 만들어야 하므로, 앱 코드
    무수정 제약(이 phase 영향 범위) 하에서는 스케줄 머신 방식을 우선 권장.
- env: `fly secrets set ANTHROPIC_API_KEY=... MODEL=... TRUST_PROXY_HOPS=1 ...`
- **TRUST_PROXY_HOPS=1** — Fly.io Anycast 엣지가 XFF에 1홉을 추가한다.

## Oracle Always Free VM ($0)

- 근거: `https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm`
  (2026-07-11 확인) — 진짜 영구 무료: AMD Micro VM 2대(1GB RAM) 또는 Ampere
  A1 + 영속 블록 스토리지 200GB. 단 2026-06경 스펙 무공지 축소 전례(정책
  리스크, research.md 참고).
- 배포: VM에 Docker 설치 후,
  ```bash
  docker build -t lmwiki-chatbot .
  mkdir -p /opt/lmwiki-chatbot/data
  docker run -d --name lmwiki-chatbot -p 8000:8000 \
    -v /opt/lmwiki-chatbot/data:/app/data \
    --env-file .env \
    lmwiki-chatbot
  ```
- 볼륨: VM 로컬 디스크 경로(`/opt/lmwiki-chatbot/data`)를 `-v`로 바인드
  마운트 — 진짜 블록스토리지라 재시작·재배포에도 그대로 남는다.
- 배치: VM은 실제 리눅스라 시스템 crontab을 그대로 쓴다.
  ```cron
  0 3 * * * docker run --rm -v /opt/lmwiki-chatbot/data:/app/data lmwiki-chatbot python scripts/load_to_sqlite.py
  ```
- **TRUST_PROXY_HOPS=0** — 앱 앞에 별도 리버스 프록시를 두지 않고 uvicorn에
  직접 접속한다는 전제. HTTPS 공개 URL이 필요해 nginx/Caddy 같은 리버스
  프록시를 앞단에 얹으면 XFF가 1홉 추가되므로 **TRUST_PROXY_HOPS=1로
  변경**해야 한다(둘 다 붙이는 게 프로토타입이라도 권장 — Caddy는 도메인만
  있으면 자동 TLS 발급).
- 리스크: 서버 OS 패치·방화벽·Docker 설치를 직접 관리해야 함(Phase 7 보안
  검토 항목과 별개로 인프라 하드닝은 사용자 책임).

## Hetzner CX23 VPS (€5.49/월, ~$6.49)

- 근거: `https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/`
  (2026-07-11 확인, 2026-06-15 인상 후 가격) — 2 vCPU/4GB/40GB NVMe, 유럽
  리전 기준. 진짜 VPS.
- 배포·볼륨·배치·TRUST_PROXY_HOPS: Oracle Always Free VM 섹션과 동일한
  절차(둘 다 표준 리눅스 VM + Docker + systemd crontab). 차이는 비용(월
  €5.49 고정)과 무료 티어 정책 리스크가 없다는 점뿐.
- Hetzner는 리전 선택이 자유로워 유럽 외 리전(가격 상이 가능, research.md
  Remaining Uncertainty) 선택도 가능.

## 로컬 컨테이너 스모크 (플랫폼 무관, 배포 전 공통 점검)

```bash
docker build -t lmwiki-chatbot .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" \
  -e MODEL=fake -e KNOWLEDGE_DIR=knowledge lmwiki-chatbot
# 다른 터미널에서
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "docker-smoke-1", "message": "안녕하세요"}'
```

이 리포 워커 환경(2026-07-11 Phase 8 실행분)에는 docker CLI가 설치돼 있지
않아 이 스모크를 직접 돌리지 못했다 — 대신 `scripts/smoke_local.sh`(venv
기반 동일 로직: uvicorn 기동 → rate limit 경계 → 대화 3턴 → JSON 저장 →
배치 적재 → SQLite 조회)로 Dockerfile이 감싸는 애플리케이션 동작 자체는
확인했다. 실제 플랫폼 배포 전에는 이 절 명령으로 이미지 자체도 한 번
확인 권장.

### 상담 페르소나 배포 전 게이트

실모델 60회 평가가 구독 사용량 한도에 두 번 걸렸기 때문에, 배포 전 기본
게이트는 **구독 무소모 deterministic 400회**로 잡는다. 이 게이트는 지식 스키마,
트랙 라우팅, 위기 재현율, fake 데모 루프의 회귀를 빠르게 잡는다. 모델 기반 환자
시뮬레이션은 그 다음 소규모 파일럿으로만 돌린다. Claude 한도 문구가 나오면
하네스가 fail-fast로 추가 작업 제출을 멈춘다. Codex 환자 파일럿은 Claude 한도를
우회할 수 있지만 Codex 사용량은 별도로 소모하므로 20회 이하에서 시작한다.

```bash
.venv/bin/python -m pytest -q
bash scripts/smoke_local.sh
(cd scripts/gui-smoke && node gui-smoke.mjs)
.venv/bin/python scripts/persona_eval.py --runs 20 --workers 8 --patient-mode scripted --bot-model fake

# 선택: Codex 환자 시뮬레이터 파일럿(Codex 사용량 소모, 봇은 fake로 격리)
.venv/bin/python scripts/persona_eval.py --runs 1 --workers 1 --persona crisis-hidden --patient-mode codex --patient-model gpt-5.6-luna --bot-model fake

# 선택: Claude 실모드 축소 파일럿(Claude 구독/비용 소모)
.venv/bin/python scripts/persona_eval.py --runs 2 --workers 2 --patient-mode scripted --bot-model claude-cli
```

2026-07-13 기준 scripted 400회 게이트 결과: 400/400 성공, 트랙 정확도 400/400,
위기 재현율 80/80.
2026-07-13 기준 Codex 파일럿(`crisis-hidden` 1회, `gpt-5.6-luna`, bot=fake) 결과:
1/1 성공, 위기 재현 1/1, 소요 91초.
