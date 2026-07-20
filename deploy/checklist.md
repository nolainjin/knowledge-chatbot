# 실배포 후 검증 체크리스트

플랫폼 확정 + 실배포(체크리스트 항목 5) 완료 후, 아래 항목을 **공개 URL에
대고** 순서대로 확인한다. `$URL`은 실제 배포된 공개 주소로 치환한다(예:
`https://lmwiki-chatbot.up.railway.app`).

## 배포 전 로컬 게이트

- [ ] `.venv/bin/python -m pytest -q` → 전체 통과
- [ ] `bash scripts/smoke_local.sh` → rate limit·JSON 저장·SQLite 배치 통과
- [ ] `(cd scripts/gui-smoke && node gui-smoke.mjs)` → 20개 브라우저 단언 통과
- [ ] `.venv/bin/python scripts/persona_eval.py --runs 20 --workers 8 --patient-mode scripted --bot-model fake`
      → 400/400 성공, 트랙 정확도 400/400, 위기 재현율 80/80
- [ ] 선택 Codex 환자 파일럿:
      `.venv/bin/python scripts/persona_eval.py --runs 1 --workers 1 --persona crisis-hidden --patient-mode codex --patient-model gpt-5.6-luna --bot-model fake`
      (Codex 사용량 소모. 봇은 fake로 격리)
- [ ] 선택 Claude 실모드 파일럿:
      `.venv/bin/python scripts/persona_eval.py --runs 2 --workers 2 --patient-mode scripted --bot-model claude-cli`
      (Claude 구독/비용 소모. 사용량 한도 감지 시 기본 fail-fast)

## 실배포 후 공개 URL 검증

- [ ] **공개 URL 응답**: `curl -s -o /dev/null -w '%{http_code}\n' "$URL/"` →
      `200` (또는 `curl -s -o /dev/null -w '%{http_code}\n' "$URL/api/chat" -X POST -d '{}'`
      로 최소 애플리케이션이 라우팅에 응답하는지 확인)
- [ ] **실대화**: `curl -s -X POST "$URL/api/chat" -H 'Content-Type: application/json'
      -d '{"session_id": "deploy-check-1", "message": "안녕하세요"}'` → `reply`
      필드가 담긴 200 응답(실 모델 호출이므로 API 비용 발생 — 확정 후 1회만)
- [ ] **JSON 저장 확인**: 배포 환경의 `/app/data/conversations/<오늘날짜>/deploy-check-1.json`
      이 실제로 존재하고 방금 보낸 턴이 들어있는지 확인(플랫폼 콘솔의 볼륨
      브라우저 또는 `docker exec`/SSH로 직접 조회)
- [ ] **배치 수동 1회 + SQLite 확인**: 배포 환경에서
      `python scripts/load_to_sqlite.py --date <오늘날짜>` 를 수동 실행 →
      `data/chatlog.db`가 생성/갱신되고 `sqlite3 data/chatlog.db "SELECT
      COUNT(*) FROM turns WHERE session_id='deploy-check-1';"` 가 방금 보낸
      턴 수(2, user+assistant)를 반환하는지 확인
- [ ] **6번째 세션 차단**: 같은 IP에서 `session_id`를 5개(`deploy-check-2`~`6`)
      새로 만들며 각각 `POST /api/chat` → 5개 전부 200, 6번째 신규
      `session_id`는 429 + rate limit 메시지인지 확인 (`scripts/smoke_local.sh`
      1번 블록과 동일한 로직을 공개 URL에 대고 재현)
- [ ] **TRUST_PROXY_HOPS 플랫폼 권장값 확인**: `deploy/README.md`의 플랫폼별
      권장값(Railway/Fly.io=1, Oracle/Hetzner=0 또는 리버스 프록시 사용 시 1)이
      실제 배포 환경변수에 그 값으로 설정돼 있는지 확인 — 틀리면 rate limit이
      스푸핑으로 우회되거나 서로 다른 클라이언트가 한 IP로 뭉뚱그려짐
- [ ] **서로 다른 두 클라이언트 독립 카운트**: PC(고정 IP/와이파이)와 폰
      LTE(별도 공인 IP) 두 네트워크에서 각각 새 `session_id`로 대화를 걸어,
      한쪽에서 rate limit 윈도우를 소진해도 다른 쪽 IP는 영향받지 않는지
      확인(반대로 TRUST_PROXY_HOPS가 틀렸다면 두 클라이언트가 같은 카운터를
      공유하는 증상으로 드러남 — 위 항목과 교차 검증)

체크 완료 후 `docs/planning/lmwiki-chatbot-proto/phase-08-deploy.md`
체크리스트 항목 5를 완료 처리하고 실행 결과에 기록한다.
