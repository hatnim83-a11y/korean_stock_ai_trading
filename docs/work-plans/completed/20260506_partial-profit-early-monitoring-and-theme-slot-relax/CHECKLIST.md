# CHECKLIST — 09:00 조기 모니터링 + 테마 슬롯 조건부 상향

## 구현

### Phase 1 — 인프라
- [x] `config.py` 신규 상수 3개 추가
  - `PARTIAL_PROFIT_EARLY_MONITORING_ENABLED: bool = True`
  - `MAX_STOCKS_PER_THEME_RELAXED: int = 3`
  - `THEME_SLOT_RELAXATION_ENABLED: bool = True`
- [x] `modules/trading_engine/sell_lock.py` 신규 작성
  - 클래스 `SellLockRegistry` + 모듈 싱글톤 `sell_lock`
  - 메서드: `acquire(stock_code, owner) -> bool`, `release(stock_code)`, `is_locked(stock_code) -> bool`, `clear_all()`, `snapshot() -> dict`
  - `threading.Lock` + `from config import now_kst`
- [x] `tests/test_sell_lock.py` 신규 작성 (6/6 통과)

### Phase 2 — 변경 1 (모니터 측)
- [~] ~~`portfolio_monitor_v2.py:start_monitoring()` idempotent 수정~~ **설계 변경 (옵션 D-1)**: KIS WebSocket이 활성 세션에 동적 SUBSCRIBE 메시지 전송 메커니즘 부재. 09:26 잡을 stop+start 재시작 패턴으로 변경 (main.py에 `restart_monitoring()` 신규, Phase 4에서 처리). start_monitoring 자체는 미수정.
- [x] `_check_and_execute_partial_profit()` 진입부 sell_lock acquire 가드 + 실패 skip (트리거 충족 시만 acquire)
- [x] `_execute_stop_loss()` 진입부 동일 가드
- [x] `_execute_trailing_stop()` 진입부 동일 가드

### Phase 3 — 변경 1 (매도 잡 측)
- [x] `main.py:run_hold_period_sells()` sell_targets 확정 직후 acquire 루프 (실패 종목 제외)
- [x] `main.py:_execute_midweek_profit_sells()` 동일 패턴
- [x] `main.py:_execute_midweek_loss_sells()` 동일 패턴
- [x] `main.py` import에 `from modules.trading_engine.sell_lock import sell_lock` 추가

### Phase 4 — 변경 1 (스케줄러)
- [x] `scheduler.py` 09:00 `monitoring_start_early` 잡 신규 등록 (플래그 분기)
- [x] `scheduler.py` 09:26 잡 유지 (이름 변경: '모니터링 재시작', 동일 콜백)
- [x] `main.py:start_monitoring()` 재호출 가능하도록 수정 (`_running` 체크 → stop+restart 패턴, 옵션 D-1)
- [x] `main.py:stop_monitoring()` 끝에서 `sell_lock.clear_all()` 호출 (15:30 일괄 해제)
- [x] `main.py` 콜백 등록부 변경 없음 확인 (`on_monitoring_start = self.start_monitoring` 동일 함수 두 잡 공유)

### Phase 5 — 변경 2 (테마 슬롯 알고리즘)
- [x] `apply_diversity_filter()` 순수 함수 신설 — `modules/trading_engine/diversity_filter.py`
- [x] 카운터 복사본 사용 (in-place 부작용 회피, test_initial_counts_not_mutated 검증)
- [x] 1차/2차 패스 알고리즘 구현
- [x] `tests/test_diversity_filter.py` 신규 (10/10 통과: A~I + initial_counts 불변)

### Phase 6 — 변경 2 (호출부)
- [x] `main.py:execute_buy_orders()` 진입부 `_theme_relaxation_applied`/`_theme_relaxation_count` 초기화 추가
- [x] `main.py:1213-1242` Phase 5.5 영역을 헬퍼 호출로 대체 (분산 제외 로그도 정리)
- [x] `main.py:_send_buy_summary` 에 `_theme_relaxation_applied=True` 시 헤더 표시 추가

## 검증
- [x] `python -m py_compile config.py main.py scheduler.py modules/trading_engine/{sell_lock,portfolio_monitor_v2,diversity_filter}.py` 통과
- [x] `python tests/test_sell_lock.py` 6/6 PASS
- [x] `python tests/test_diversity_filter.py` 10/10 PASS
- [x] code-tester 에이전트 실행 → 심각 0건 / 주의 2건 (1건 즉시 패치, 1건 follow-up) / 종합 판정 "배포 가능"
- [ ] dry-run: 사용자가 결정 (선택적 — 단위 테스트로 핵심 로직 검증 완료)
- [N/A] WebSocket dynamic subscribe — KIS WebSocket이 동적 추가 미지원 확인됨, 옵션 D-1(stop+restart) 채택

## 배포
- [x] 기존 프로세스 확인 (PID 2732694 → 3247862)
- [x] `sudo systemctl restart trading_system` (KST 12:22, 장 중 재시작, 사용자 승인)
- [x] 재시작 직후 로그 확인 — 정상 가동 확인:
  - `조기 모니터링 시작 (09:00): cron[hour='9', minute='0']` 신규 잡 등록 ✓
  - `모니터링 재시작 (09:26, 신규 매수분 포함)` 잡 이름 적용 ✓
  - `🔄 장 중 재시작 감지 — 모니터링 자동 재개 (2종목 보유 중)` ✓
  - `📊 모니터링 중: 2종목` 정상 가동 ✓
- [ ] **다음 거래일 (2026-05-07 목) 09:00~09:35 로그 관찰** (사후 검증)
  - `[09:00] 조기 모니터링 시작` 라인
  - `[09:26] 모니터링 재시작 (신규 매수분 포함)` 라인
  - midweek/hold_period 매도와 모니터 race 0건
  - `[SellLock] ... skip` 메시지 발생 빈도 (정상 차단 케이스)
  - `테마 슬롯 한도 상향 적용` 메시지 발생 빈도

## 문서 업데이트
- [x] `docs/improvements/change_log.md` 2026-05-06 행 추가 (변경 1+2 통합 1줄)
- [x] `CLAUDE.md` "매도 동시성 잠금 (SellLock — 2026-05-06 도입)" 섹션 추가
- [x] `memory/MEMORY.md` 갱신
  - Memory Files 인덱스 1줄 추가 (project_partial_profit_early_monitoring.md)
  - Strategy 섹션 정정: "테마 3개×종목 3개" → "테마 슬롯 한도 2 (조건부 3까지 상향, 2026-05-06)"
  - 모니터링 스케줄 1줄 추가 (09:00/09:26/15:30)
- [x] 신규 메모 파일 작성: `memory/project_partial_profit_early_monitoring.md` (변경 1+2+3 종합)
- [x] 작업 디렉토리 `active/` → `completed/20260506_partial-profit-early-monitoring-and-theme-slot-relax/` 이동 완료
