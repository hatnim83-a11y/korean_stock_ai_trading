# PLAN — Off-VM Dead-man's-switch (Healthcheck Ping)

/ 작업명: vm-resilience-healthcheck-ping
/ 작성: 2026-06-05 / 근거: docs/incidents/20260605_vm_freeze_host_fault.md (P0-A)

## 목표
2026-06-05 GCP 호스트 장애 때 봇 네트워크가 끊겨 **운영자가 어떤 알림도 못 받은** 공백을 메운다.
봇이 외부 헬스체크 서비스(healthchecks.io 스타일)에 주기적으로 ping을 보내고, **봇/VM이 죽으면 외부(off-VM)에서 무신호를 감지해 이메일/푸시로 경보**한다. 텔레그램이 죽어도 알림이 가는 게 핵심.

## 배경
- incident: VM 77분 freeze + 네트워크 단절. 봇은 살아있어도 텔레그램 송신 불가 → 운영자 무감지.
- dead-man's-switch는 "신호가 오는 것"이 아니라 "신호가 끊기는 것"을 외부에서 감지하므로 이 실패모드를 정확히 커버.

## 범위 (이 단위 = Feature A만)
- B(누락 잡 탐지 + 장중 비정상 재시작 경보)는 **다음 단위**로 분리 (DB 마이그레이션·이벤트루프 오프로딩 별도 검증 필요 — 리뷰 권고).
- GCP Ops Agent 프로세스 감지는 **가이드 문서로만** 제공(코드 무변경, 콘솔 설정).

## 설계 (리뷰 반영 확정)
리뷰(strategy-planner + code-tester)에서 나온 심각 이슈 3건을 모두 설계에 반영:
1. **휴장일에도 ping** — `_run_healthcheck_ping`에 `@_skip_on_holiday` **금지** (24시간 ping이어야 dead-man's-switch 성립)
2. **이벤트루프 비블로킹** — ping은 `httpx.AsyncClient`(async)로. scheduler `_run_*`가 `async def`라 동기 httpx.post는 루프 블로킹
3. **트레이딩 무간섭** — ping 실패는 예외 삼키고 `logger.warning`만(텔레그램 스팸 금지, raise 금지)

추가 반영:
- 기본 **비활성(opt-in)**: `HEALTHCHECK_ENABLED=False`
- URL 공백인데 ENABLED=True면 잡 미등록 + warning (httpx.InvalidURL silent fail 방지)
- timeout 하드코딩 금지 → config `HEALTHCHECK_PING_TIMEOUT_SEC`

## 구현 단계
1. **config.py**: 4개 Field 추가 (ENABLED/URL/INTERVAL_MIN/TIMEOUT_SEC), `class Config:`(L946) 직전 삽입
2. **modules/system_guard/** 신규 패키지: `__init__.py` + `healthcheck.py`
   - `async def send_healthcheck_ping(url, timeout=5) -> bool` — httpx.AsyncClient GET, 모든 예외 삼킴, bool 반환, **절대 raise 안 함**
3. **scheduler.py**:
   - import `IntervalTrigger`
   - `__init__`에 `self.on_healthcheck_ping: Optional[Callable] = None`
   - `_run_healthcheck_ping()` 메서드 (**데코레이터 없음**, async, 콜백 호출, 예외 격리)
   - `setup_schedules()` 끝(closing_bet 등록 직전)에서 ENABLED+URL 검증 후 `IntervalTrigger(minutes=...)` 잡 등록(id='healthcheck_ping')
4. **main.py**:
   - `_setup_scheduler_callbacks()`에 `self.scheduler.on_healthcheck_ping = self.run_healthcheck_ping`
   - `async def run_healthcheck_ping()` 구현 (send_healthcheck_ping await, 실패 시 warning만)
5. **docs/runbooks/gcp_process_down_alert.md**: GCP Ops Agent 프로세스 감지 설정 가이드(코드 무변경)
6. **.env**: 사용자가 직접 키 추가(코드 미변경, CHECKLIST에 키/예시 명시)

## 변경 파일 목록
- `config.py` (Field 4개)
- `modules/system_guard/__init__.py` (신규)
- `modules/system_guard/healthcheck.py` (신규)
- `scheduler.py` (import + 콜백선언 + _run_healthcheck_ping + setup 등록)
- `main.py` (콜백 wiring + run_healthcheck_ping)
- `docs/runbooks/gcp_process_down_alert.md` (신규 가이드)
- `tests/test_healthcheck_ping.py` (신규 테스트)

## 완료 기준
- `HEALTHCHECK_ENABLED=False`(기본)에서 잡 미등록·기존 동작 무변경 (py_compile + import OK)
- ENABLED=True+URL 설정 시 IntervalTrigger 잡 등록, ping이 async로 동작(루프 블로킹 없음)
- ping 실패해도 예외 전파 없음(트레이딩 무영향)
- 휴장일/장외에도 ping 발신(데코레이터 미적용 검증)
- code-tester 심각 이슈 0
- healthchecks.io(또는 동등) 측에서 무신호 시 외부 알림 수신 확인(운영 검증 — 사용자 URL 발급 후)

## 롤백
- `HEALTHCHECK_ENABLED=False` + systemctl restart → 잡 등록 분기 자체 skip, 완전 NO-OP
- 코드 롤백 시 신규 모듈/Field는 다른 코드가 참조하지 않으므로 독립 제거 가능
