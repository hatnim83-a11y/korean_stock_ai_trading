# PLAN — 누락 핵심 잡 탐지 + 장중 비정상 재시작 경보 (P0-B)

/ 작업명: vm-resilience-missed-job-alert
/ 작성: 2026-06-05 / 근거: docs/incidents/20260605_vm_freeze_host_fault.md (P0-B)

## 목표
2026-06-05 freeze로 오전 매매 잡 11개가 misfire 폐기됐는데 운영자가 **어떤 잡이 누락됐는지/장중 비정상 재시작인지** 실시간으로 몰랐다. 이를 텔레그램으로 즉시 경보한다. (P0-A off-VM ping은 "VM 이상" 자체를, P0-B는 "잡 누락/재시작" 세부를 커버 — 상호보완)

## 범위 (DB 마이그레이션 없음 — 1차 리뷰 권고)
원래 job_execution_log 테이블 방식은 이벤트루프 동기 write 블로킹 + 마이그레이션 리스크로 기각.
대신 APScheduler **`EVENT_JOB_MISSED`**(인시던트 로그의 "missed by" 경고가 바로 이것) 활용 → DB 불필요.

## 설계 (2차 리뷰 반영 확정)
### B1 — 핵심 잡 누락(misfire) 실시간 경보
- scheduler `add_listener(_on_job_missed, EVENT_JOB_MISSED)` (JOB_RECOVERY_ALERT_ENABLED 시)
- `CANDIDATE_CORE_JOB_IDS` frozenset → setup_schedules 후 **등록된 잡과 교집합**으로 `self.core_job_ids` 확정 (monitoring_start_early 조건부 등록/EARLY_BUY 대응)
- `_on_job_missed(event)`:
  - 🔴 **이벤트루프 안전**: `loop=get_event_loop(); if loop.is_running(): loop.create_task(coro)` (동기잡 추가 시 RuntimeError 방어, 미실행 시 logger.error)
  - **`is_trading_day()` 가드**: MISSED는 잡의 `_skip_on_holiday`보다 먼저 발화 → 휴장일 freeze 오탐 차단
  - core_job_ids 외 잡 무시, `(job_id, scheduled_run_time)` in-memory dedup (set>200 시 clear)
  - logger.error 기록 (전송 실패해도 로그엔 남김)
- main `async def alert_job_missed(job_id, scheduled_time)`: 🔴 **try/except로 예외 소거**(Task exception never retrieved 방지) + `await asyncio.to_thread(notifier.send_message, text)`(동기 httpx 블로킹 회피)

### B2 — 장중 비정상 재시작 경보
- main `_run_startup_recovery_check()`: "시스템 시작 완료"(main.py:284) 직후, `_resume_monitoring_if_needed()`(L287) **앞** 호출
- `is_trading_day() and 09:00<=now_kst().time()<=15:30`이면 "⚠️ 장중 비정상 재시작 (HH:MM) — 09:00 이후 예정 매매 잡이 이 세션 미실행 가능, 포지션·모니터링 점검 요망" 경보 (메시지에 "09:26 이전이면 모니터링 자동 재개 대기" 안내)
- **재시작 루프 스팸 억제**: `/tmp` 파일 30분 cooldown (VM 재부팅 시 파일 소실=첫 재시작 경보 발화, 올바름)
- await asyncio.to_thread(send_message)

### config
- `JOB_RECOVERY_ALERT_ENABLED: bool = True` (순수 관측/경보, 트레이딩 무개입이라 기본 ON)

## 변경 파일
- `config.py` (Field 1개)
- `scheduler.py` (EVENT_JOB_MISSED import + CANDIDATE_CORE_JOB_IDS + core_job_ids 동적확정 + _on_job_missed + on_job_missed_alert 콜백 + 조건부 add_listener)
- `main.py` (alert_job_missed + _run_startup_recovery_check + 콜백 wiring + start()에 호출)
- `tests/test_missed_job_alert.py` (신규)

## 완료 기준
- JOB_RECOVERY_ALERT_ENABLED=False → 리스너 미등록·기존 동작 무변경
- EVENT_JOB_MISSED 시뮬레이션 → core 잡이면 콜백 스케줄/비core 무시/휴장일 무시/dedup 동작
- alert_job_missed 전송 실패해도 예외 전파 없음
- B2: 장중 기동 시 경보 / 장외·휴장일 미발화 / 30분 cooldown
- code-tester 심각 0 / py_compile + 신규·회귀 테스트 통과

## 롤백
- `JOB_RECOVERY_ALERT_ENABLED=false` + restart → 리스너·체크 NO-OP

## 후속(분리)
- B1 전송 실패 시 in-memory 재시도 큐(네트워크 복구 후 재통보) — 이번엔 logger.error만, P0-B-2로 분리
- 다중 동시 누락 3초 window 집약 메시지 (polish)
