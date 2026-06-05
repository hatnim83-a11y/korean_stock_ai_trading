# CHECKLIST — Healthcheck Ping

## 구현
- [x] config.py: `HEALTHCHECK_ENABLED`(False) / `HEALTHCHECK_PING_URL`("") / `HEALTHCHECK_PING_INTERVAL_MIN`(5) / `HEALTHCHECK_PING_TIMEOUT_SEC`(5) Field 추가
- [x] modules/system_guard/__init__.py 생성
- [x] modules/system_guard/healthcheck.py: `async def send_healthcheck_ping(url, timeout=5)->bool` (httpx.AsyncClient, 모든 예외 삼킴, raise 금지)
- [x] scheduler.py: `from apscheduler.triggers.interval import IntervalTrigger`
- [x] scheduler.py __init__: `self.on_healthcheck_ping: Optional[Callable] = None`
- [x] scheduler.py: `_run_healthcheck_ping()` async, **@_skip_on_holiday 미적용**, 예외 격리(에러알림 호출 안 함)
- [x] scheduler.py: `_setup_healthcheck_job()` 헬퍼(분리) + setup_schedules에서 호출, ENABLED+URL.strip() 검증, 공백 URL warning+skip
- [x] main.py: `_setup_scheduler_callbacks()`에 on_healthcheck_ping wiring + 상단 import
- [x] main.py: `async def run_healthcheck_ping()` (await send_healthcheck_ping, 실패 시 warning만)
- [x] docs/runbooks/gcp_process_down_alert.md 가이드 작성

## 검증
- [x] `py_compile` 통과 (config/scheduler/main/healthcheck/test)
- [x] import 무오류 (system_guard + config 로드, HEALTHCHECK_ENABLED=False 확인)
- [x] tests/test_healthcheck_ping.py 10건 PASS (성공/실패/타임아웃 raise없음, ENABLED=False/URL공백 미등록, ENABLED+URL 등록, 데코레이터 부재, 콜백 호출/예외격리)
- [x] 기존 test_early_buy_schedule.py 3건 회귀 PASS
- [x] **code-tester 에이전트 검증 — 심각 0 / 주의 0 / 참고 4(경미)** → 배포 가능
- [x] 실제 settings 전체 setup_schedules 스모크: 기본 28잡(healthcheck 미등록)/ENABLED=True 시 등록(interval 5분)

## 배포
- [ ] (사용자) healthchecks.io 등 외부 서비스에서 ping URL 발급 + grace 설정(period=interval, grace≤15min)
- [ ] (사용자) .env에 `HEALTHCHECK_ENABLED=true` / `HEALTHCHECK_PING_URL=https://hc-ping.com/...` 추가
- [ ] (사용자) `sudo systemctl restart trading_system` → 로그 "🩺 Off-VM 헬스체크 ping 등록" 확인 + 외부 대시보드 ping 수신
- [ ] (사용자) 의도적 stop으로 무신호 → 외부 알림 수신 확인(운영 검증)
- [x] 코드 main 머지

## 문서 업데이트
- [x] CLAUDE.md: 서비스 운영 규칙에 Off-VM 헬스체크 항목 추가
- [x] memory: project_incident_20260605_vm_freeze.md에 P0-A 구현 완료 반영
- [x] docs/incidents/20260605_vm_freeze_host_fault.md §7 액션아이템 P0-A 체크 + P0-B 분리 명시
- [x] active/ → completed/20260605_ 아카이브
