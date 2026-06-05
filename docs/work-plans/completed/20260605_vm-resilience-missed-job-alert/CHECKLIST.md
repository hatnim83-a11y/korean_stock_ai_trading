# CHECKLIST — 누락 잡 탐지 + 장중 비정상 재시작 경보 (P0-B)

## 구현
- [x] config.py: `JOB_RECOVERY_ALERT_ENABLED: bool = Field(default=True, ...)`
- [x] scheduler.py: import에 `EVENT_JOB_MISSED` 추가
- [x] scheduler.py: `CANDIDATE_CORE_JOB_IDS` frozenset (모듈 상수)
- [x] scheduler.py __init__: `on_job_missed_alert`, `_missed_dedup`, `core_job_ids` 초기값
- [x] scheduler.py __init__: `if JOB_RECOVERY_ALERT_ENABLED: add_listener(_on_job_missed, EVENT_JOB_MISSED)`
- [x] scheduler.py: `_on_job_missed` — is_trading_day 가드 / core 필터 / dedup / loop.is_running create_task / try-except + logger.error
- [x] scheduler.py setup_schedules 끝: `core_job_ids = CANDIDATE & 등록잡` (동적 교집합)
- [x] main.py: `alert_job_missed` (try/except + asyncio.to_thread)
- [x] main.py: `_run_startup_recovery_check` (토글+is_trading_day+09:00~15:30+/tmp 30분 cooldown)
- [x] main.py: `on_job_missed_alert` wiring + start()에서 resume 앞 호출 + KST import

## 검증
- [x] `py_compile config.py scheduler.py main.py`
- [x] tests/test_missed_job_alert.py 14건 PASS (B1 7 + core동적 1 + alert예외 1 + B2 5)
- [x] 회귀 test_healthcheck_ping(10)+test_early_buy_schedule(3) PASS (EARLY_BUY 분리)
- [x] **code-tester 에이전트 — 심각 0 / 주의 2(경미, 즉시 정리) / 참고 2** → 배포 가능
- [x] 실제 settings 스모크: core_job_ids 10개 동적 확정 + MISSED 리스너 등록 확인
- [x] code-tester 지적 정리: 함수 내 `import os` 중복 제거, 09:00 하드코딩 주석 추가

## 배포
- [x] 코드 main 머지
- [ ] (선택) `sudo systemctl restart trading_system` — 기본 True라 재시작 시 활성. 장외 재시작이라 B2 미발화 정상
- [ ] (관찰) 다음 거래일 정상장엔 무경보(오탐 0) 확인

## 문서 업데이트
- [x] CLAUDE.md: 서비스 운영 규칙에 P0-B 항목 추가
- [x] memory: project_incident_20260605_vm_freeze.md P0-B 완료 반영
- [x] docs/incidents/20260605_vm_freeze_host_fault.md §7 P0-B 체크 + P0-B-2 후속 명시
- [x] active/ → completed/20260605_ 아카이브
