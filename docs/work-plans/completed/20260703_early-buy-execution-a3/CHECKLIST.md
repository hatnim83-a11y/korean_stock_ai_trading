# CHECKLIST: 아침 매수 09:25 → 09:05 조기화 (A3안)

> 진행 시 [x] 체크. 5개 단위(B→D→A→E→C) + 테스트 + 배포 + 문서.
> 모든 변경은 `EARLY_BUY_ENABLED=False` 기본값이라 legacy 100% 보존.

## 사전 준비

- [x] PLAN/CONTEXT/CHECKLIST 3문서 저장
- [x] worktree 격리 (`.claude/worktrees/early-buy-09-05`)
- [ ] 본가동 시각 결정 (D+2 월요일 권장)

## 구현 — 단위 B: config.py (5개 Field + 5개 property)

- [ ] `config.py:686~745` (Morning Filter 섹션 끝)에 5개 Field 추가:
  - [ ] `EARLY_BUY_ENABLED: bool = Field(default=False, ...)`
  - [ ] `EARLY_BUY_OBSERVATION_MINUTES: int = Field(default=0, ...)`
  - [ ] `EARLY_BUY_DISABLE_VOLUME_FILTER: bool = Field(default=True, ...)`
  - [ ] `EARLY_BUY_DISABLE_STRENGTH_FILTER: bool = Field(default=False, ...)`
  - [ ] `EARLY_BUY_MARKET_GUARD_DELAY_MINUTES: int = Field(default=15, ...)`
- [ ] 5개 `@computed_field` 추가 (시간 문자열):
  - [ ] `buy_time_str` → "09:05" / "09:25"
  - [ ] `screening_time_str` → "09:02" / "09:05"
  - [ ] `monitoring_time_str` → "09:06" / "09:26"
  - [ ] `hold_period_time_str` → "09:02" / "09:15"
  - [ ] `midweek_loss_time_str` → "09:01" / "09:10"
- [ ] 검증: `python -c "from config import settings; print(settings.EARLY_BUY_ENABLED, settings.buy_time_str)"`

## 구현 — 단위 D: 메시지/로그 변수화 (24+3 위치)

- [ ] `main.py` 사용자 메시지 14곳 치환 (settings.buy_time_str 등):
  - [ ] 727, 830, 859, 898, 918, 987, 992, 1066, 1082, 1109, 1193, 1195, 2149, 2152, 2163, 2413, 2419
- [ ] `main.py` 로그 메시지 8곳 치환:
  - [ ] 455, 666, 863, 961, 1005, 1168, 2141, 2144, 2235
- [ ] `scheduler.py` 잡 이름 3곳 (`name="..."`):
  - [ ] 145, 175, 238
- [ ] `modules/reporter/telegram_notifier.py:835~836` `/pause` 응답 치환
- [ ] 검증: `grep -rn "09:25\|09:05" main.py scheduler.py modules/` 결과 0건 (또는 합리적 잔재만)

## 구현 — 단위 A: scheduler.py (7개 add_job + _print_schedules)

- [ ] `setup_jobs()` 내부에 5개 변수 추가:
  ```python
  EARLY = settings.EARLY_BUY_ENABLED
  screening_minute = 2 if EARLY else 5
  buy_minute = 5 if EARLY else 25
  monitoring_minute = 6 if EARLY else 26
  hold_period_minute = 2 if EARLY else 15
  midweek_loss_minute = 1 if EARLY else 10
  ```
- [ ] 7개 `add_job`의 `CronTrigger(minute=...)` 치환
- [ ] `_print_schedules()`에 `EARLY_BUY_ENABLED={settings.EARLY_BUY_ENABLED}` 출력
- [ ] 검증: `EARLY_BUY_ENABLED=false python -c "..."` → legacy 시간
- [ ] 검증: `EARLY_BUY_ENABLED=true python -c "..."` → early 시간 (09:00/01/02/02/05/06)

## 구현 — 단위 E: asyncio.Event + Market Guard (CRITICAL)

- [ ] `main.py.__init__`: `self._screening_done = asyncio.Event()` + `self._screening_done.set()` (초기 set)
- [ ] `main.py.run_stock_screening`:
  - [ ] 진입 시 `self._screening_done.clear()`
  - [ ] try/finally의 finally에 `self._screening_done.set()`
- [ ] `main.py.execute_buy_orders` Phase 0 진입 시 (1180 부근):
  - [ ] `await asyncio.wait_for(self._screening_done.wait(), timeout=180)` 추가
  - [ ] timeout 발생 시 logger.error + 텔레그램 alert
- [ ] Market Guard 지연 (1129~1142):
  - [ ] `delay_min = settings.EARLY_BUY_MARKET_GUARD_DELAY_MINUTES if settings.EARLY_BUY_ENABLED else settings.MARKET_GUARD_DELAY_MINUTES`
- [ ] 검증: Event wait/set 단위 테스트

## 구현 — 단위 C: observer 미생성 + timeout + filter 토글

- [ ] `main.py:1000` (observer 생성 조건):
  ```python
  if (
      settings.ENABLE_MORNING_FILTER
      and not settings.EARLY_BUY_ENABLED
      and self.today_candidates
  ):
  ```
- [ ] `main.py:1185` (Phase 0 observer timeout):
  ```python
  timeout_min = (
      settings.EARLY_BUY_OBSERVATION_MINUTES
      if settings.EARLY_BUY_ENABLED
      else settings.MORNING_OBSERVATION_MINUTES
  )
  ```
- [ ] `modules/morning_filter/morning_screener.py:84~120` `__init__`:
  - [ ] `enable_volume_filter: bool = None` (기본 None)
  - [ ] `enable_strength_filter: bool = None` (기본 None)
  - [ ] None 분기 → settings 토글로 결정
- [ ] 검증: `EARLY_BUY_ENABLED=true` → `screener.volume_filter is None` (OFF) + `screener.strength_filter is not None` (보존)

## 검증 — Phase 1: 단위 테스트

- [ ] `tests/test_early_buy_schedule.py` 작성
  - [ ] EARLY=True → 7개 잡 시간 09:00/01/02/02/05/06 assert
  - [ ] EARLY=False → legacy 09:00/05/10/15/25/26 assert
- [ ] `tests/test_early_buy_event_sync.py` 작성
  - [ ] `_screening_done.wait()` clear 상태에서 대기, set 후 진행
  - [ ] 180초 timeout 발생 시 alert
- [ ] `tests/test_morning_screener_early.py` 작성
  - [ ] EARLY=True → `volume_filter is None` / `strength_filter is not None`
  - [ ] EARLY=False → 둘 다 not None
- [ ] `pytest tests/test_early_buy_*.py -v` 전부 PASS

## 검증 — Phase 2: Dry-run (TEST_MODE)

- [ ] `TEST_MODE=true EARLY_BUY_ENABLED=true python -c "..."` 단발 실행
- [ ] 로그 확인:
  - [ ] "💰 빈 슬롯 매수 실행 (09:05)" 출력
  - [ ] `_observer_task is None` (Phase 0 즉시 통과)
  - [ ] Step 3 (거래량) 로그 부재 — "거래량 필터: OFF"
  - [ ] Step 4 (체결강도) 작동
  - [ ] Step 5 "관찰 데이터 없음 - 스킵"
  - [ ] 실제 KIS 주문 미발사

## 검증 — code-tester 에이전트

- [ ] code-tester 호출하여 5개 파일 변경 사항 검증
- [ ] 심각 0 / 주의 ≤2 통과
- [ ] 발견 이슈 즉시 수정

## 배포

- [ ] py_compile 통과 (`python -m py_compile config.py scheduler.py main.py modules/morning_filter/morning_screener.py modules/reporter/telegram_notifier.py`)
- [ ] 단위 B/D/A/E/C 5개 commit 분리 (default=False 유지)
- [ ] worktree merge (main으로) 또는 PR
- [ ] D+1: 기존 프로세스 확인 + systemctl restart (`sudo systemctl restart trading_system`)
- [ ] D+1: `journalctl -u trading_system -n 50 | grep "EARLY_BUY"` 로 False 확인 (legacy)
- [ ] D+2 (월요일): `.env`에 `EARLY_BUY_ENABLED=true` 추가 + `sudo systemctl restart trading_system`
- [ ] D+2: 09:00~09:06 전체 잡 발화 실시간 모니터링

## 검증 — Phase 3+: 1주 모니터링 지표

- [ ] **슬리피지** (매수 체결가 vs 09:00 시초가): 매수 3건 이상 누적 + 평균 추적
  - 롤백 트리거: 평균 > +1.5%
- [ ] **매수 후 당일 종가 승률**: 당일 종가 ≥ 매수가 비율
  - 롤백 트리거: 3건 이상 누적 + < 40%
- [ ] **Screening Event timeout**: `grep "Screening timeout" logs/`
  - 롤백 트리거: 1회 발생 즉시 alert
- [ ] **모닝 필터 차단율**: Step 1/2/4/5 통과율
- [ ] **SellLock 매수 skip**: `grep "[SellLock]" logs/`
  - 롤백 트리거: > 3건/주

## 롤백

### 즉시 (지표 미달 시)
- [ ] `.env`에 `EARLY_BUY_ENABLED=false` 설정
- [ ] `sudo systemctl restart trading_system`
- [ ] `journalctl -u trading_system -n 50 | grep "EARLY_BUY"` 로 False 확인

### 완전 (불필요 — 모든 변경 토글 분기)
- 부분 롤백 필요 시 git revert 단위별 가능

## 문서 업데이트 (CLAUDE.md 규칙)

- [ ] `docs/improvements/change_log.md`에 1줄 추가 (before/after 추적)
- [ ] `memory/MEMORY.md` 또는 `memory/project_*.md`에 EARLY_BUY 운영 가이드 추가
- [ ] `CLAUDE.md` (프로젝트)에 새 토글/스케줄 명시
- [ ] D+8 회고 결과 → `docs/work-plans/completed/YYYYMMDD_early-buy-execution-a3/` 아카이브
