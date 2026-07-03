# CONTEXT: 종가베팅 단위 2-5 morning_exit_manager

## 변경 이유

### 1. 단위 2-4f 활성화 게이트 조건
- 5/14 walkforward 게이트 PASS (n=103, realistic EV +1.04% / Sharpe +1.57 / W-L ∞)
- 단위 2-4 EntryExecutor 인프라 완료 (5/15, 운영 봇 배포, dry_run 활성)
- **활성화 게이트 조건**: 단위 2-5 morning_exit_manager 완료 + 1주 dry_run + 사용자 승인
- 매수만 자동/매도 부재 시 보유 종목 리스크 노출 → 매수와 매도 짝 맞춘 안정성 확보 필수

### 2. PRD 10-1 시가 액션 매트릭스
- settings.yaml `exit:` 섹션 9 키 박제 (gap_up_high_threshold=0.02 / gap_up_high_partial_ratio=0.6 / gap_up_low_threshold=0.005 / flat 범위 ±0.005 / weak_gap_down_threshold=-0.005 / hard_stop_loss=-0.01 / trailing_stop_pct=-0.015)
- 6단계 분류 (emergency_stop / gap_up_high / gap_up_low / flat / weak_gap_down / trailing_stop)
- 시점 분리: 09:00 (emergency_stop만) / 09:30~10:30 (나머지) / 10:30 force_close

### 3. 단위 2-4 EntryExecutor 인터페이스 계약
- 단위 2-4c `_finalize_mark_entered` 옵션 A: phase2 완료 시만 status='entered' + mark_entered 1회 호출
- phase1 only 보유 상태는 `candidate_status='recommended' AND entry_phase1_executed_shares > 0 AND entry_phase2_executed_shares IS NULL`
- 본 단위 2-5는 두 보유 상태 모두 매도 대상으로 식별 필요

## 현재 코드 상태

### 재사용 가능 인프라
- `modules/stock_screener/kis_api.get_current_price(stock_code) -> Optional[dict]` (modules/stock_screener/kis_api.py:422) — open/high/low/price/change_rate 모두 반환
- `modules/trading_engine/kis_order_api.sell_market_order(stock_code, quantity)` (modules/trading_engine/kis_order_api.py:354) — 시장가 매도 (긴급/force_close용)
- `modules/trading_engine/kis_order_api.sell_limit_order(stock_code, quantity, price)` (modules/trading_engine/kis_order_api.py:377) — 지정가 매도 (gap_up_high 분할 매도용)
- `closing_bet_system/storage/candidate_logger.log_exit(candidate_id, exit_price, exit_time, shares, slippage_rate)` (closing_bet_system/storage/candidate_logger.py:300) — cost_engine 비용 분해 + UPDATE
- `closing_bet_system/execution/fill_checker.FillChecker` (단위 2-4b) — 매도 체결 확인 재사용 가능
- `closing_bet_system/execution/price_utils.align_to_tick(price, "sell")` (단위 2-4b) — 매도 호가 단위 정렬

### candidates 테이블 스키마 (단위 2-4c v3 적용 후)
- entry_price (옵션 A 가중평균) / entry_amount / entry_time / exit_price / exit_time
- entry_phase1_order_id / entry_phase1_executed_price / entry_phase1_executed_shares
- entry_phase2_order_id / entry_phase2_executed_price / entry_phase2_executed_shares
- candidate_status: 'recommended' / 'entered' / 'rejected_filter' / 'rejected_manual' / 'exited'
- buy_commission / sell_commission / transaction_tax / estimated_slippage / net_pnl_pct (log_exit이 채움)

### 매도 대상 식별 SQL (단위 2-5b 핵심 쿼리)
```sql
SELECT candidate_id, ticker, name,
       candidate_status,
       entry_price, entry_amount, entry_time,
       entry_phase1_executed_price, entry_phase1_executed_shares,
       entry_phase2_executed_price, entry_phase2_executed_shares
FROM candidates
WHERE trade_date = ?    -- T-1 (어제 진입한 후보)
  AND (
    candidate_status = 'entered'                              -- phase1+phase2 완료 (옵션 A mark_entered)
    OR (
      candidate_status = 'recommended'
      AND COALESCE(entry_phase1_executed_shares, 0) > 0       -- phase1 only 보유
      AND entry_phase2_executed_shares IS NULL                -- phase2 미체결
    )
  )
  AND exit_time IS NULL
```

## 핵심 스니펫: 매도 액션 매핑 (단위 2-5c 설계 초안, 사전 리뷰 P0-1 반영)

```python
class ExitAction(Enum):
    EMERGENCY_STOP = "emergency_stop"       # 시가 ≤ -1% 즉시 전량 시장가 (09:01)
    GAP_UP_HIGH = "gap_up_high"             # 시가 ≥ +2% 50% 분할 매도 (시초가 50% + 10:30 50%)
    GAP_UP_LOW = "gap_up_low"               # +0.5%~+2% 09:30 시초가 100% 시장가
    FLAT = "flat"                           # ±0.5% 09:30 시초가 100% 시장가 (시뮬 정합)
    WEAK_GAP_DOWN = "weak_gap_down"         # -1%~-0.5% 09:30 시초가 100% 시장가
    # TRAILING_STOP은 단위 2-5g 별도 분리 (09:30~10:30 폴링 루프 필요)

def map_action(open_price, entry_price, exit_cfg) -> ExitAction:
    gap_rate = (open_price - entry_price) / entry_price
    if gap_rate <= exit_cfg.hard_stop_loss:        # -0.01
        return ExitAction.EMERGENCY_STOP
    if gap_rate >= exit_cfg.gap_up_high_threshold: # +0.02
        return ExitAction.GAP_UP_HIGH
    if gap_rate >= exit_cfg.gap_up_low_threshold:  # +0.005
        return ExitAction.GAP_UP_LOW
    if gap_rate >= exit_cfg.flat_lower:            # -0.005
        return ExitAction.FLAT
    return ExitAction.WEAK_GAP_DOWN                # -0.01 < gap < -0.005
```

**P0-1 시뮬 정합성 박제**: 모든 액션이 시초가 시장가 매도로 통일 → `phase25_simulator.py` `prd_split_realistic` 정책 `open_pct` 가정과 일치. `gap_up_high` 분할만 예외 (시뮬 `prd_split_gapup` morning_high 가정 vs 실 10:30 시장가 → delta 발생 가능, 단위 2-5e 정합성 게이트에서 측정).

## 과거 버그/주의사항 (사전 리뷰 P0/P1 반영)
- **NULL 가드**: `pd.isna()` 사용 (CLAUDE.md)
- **KIS 토큰 공유**: 메인 봇 `_shared_token` 패턴 — 신규 collector 도 기존 KISApi/KISOrderApi 싱글톤 사용
- **KST 타임존**: `from config import now_kst` (서버 UTC)
- **부분 체결 잔량**: phase2 부분 체결 종목 처리 — `total_shares = phase1_executed_shares + COALESCE(phase2_executed_shares, 0)`
- **P0-2 phase1 only `log_exit` LookupError 회피**: `candidate_logger.log_exit()` 는 `entry_price IS NULL` 시 `LookupError` raise. phase1 only 보유는 mark_entered 미호출 → entry_price NULL. **해결**: ExitExecutor 가 매도 발주 전 `mark_entered_phase1_only(candidate_id)` 헬퍼를 호출하여 phase1_executed_price 를 entry_price 로 박제 (옵션 A 권장 — 데이터 정합성 보존). 헬퍼는 `closing_bet_system/storage/candidate_logger.py` 에 신규 추가.
- **P0-3 09:00 race 회피**: 메인 봇 `scheduler.py:163` `monitoring_start_early` + `:218` `midweek_sell_profit` 모두 09:00 cron. 종가베팅 `emergency_stop`은 **09:01** (60초 오프셋) — `EMERGENCY_STOP_SCHEDULE_HOUR=9, _MINUTE=1` 상수 박제. KIS `_shared_token` race 회피.
- **P1-2 idempotency**: 09:01 emergency_stop 발주 직후 `exit_time` 또는 별도 `exit_in_progress` 플래그를 DB 에 즉시 박제 → 09:30 morning_exit이 같은 종목 select 단계에서 제외. 부분 체결 시점에는 `exit_time IS NULL` 유지 (10:30 force_close가 잔량 처리).
- **P1-5 SellLock 결정 박제**: 메인 봇 `modules/trading_engine/sell_lock.py` **재사용** + owner 네임스페이스 분리 (`"closing_bet:emergency_stop"` / `"closing_bet:morning_exit"` / `"closing_bet:force_close"`). 메인 봇 portfolio 종목과 종가베팅 candidates 가 같은 ticker 일 때 ticker 단위 lock 으로 충돌 봉쇄. release 는 메인 봇 15:30 `clear_all()` 이 일괄 처리.
- **P1-4 force_close 미체결 취소 순서**: 10:30 force_close 시 09:30 morning_exit 미체결 잔량이 KIS 주문 큐에 남아있을 수 있음 → 시장가 추가 발주 시 초과 매도 위험. 순서: **취소 발주 → 취소 확인 (fill_checker) → 시장가 재발주**. 단위 2-5a Step 0 에서 `KISOrderApi` 주문 취소 엔드포인트 가능 여부 검증 필수.
- **MarketGuard**: 09:25 메인 봇 매수 전 사용 — 종가베팅 매도는 09:01~10:30이므로 시점 분리, 충돌 없음
- **dry_run 토글 정책 (entry_executor 일관성)**: dry_run=True 시 (a) KIS sell_market/limit_order 건너뜀 (b) `log_exit` 도 건너뜀 (DB 무변경) (c) simulated_exit 로그 dict 생성 + 텔레그램 "[DRY-RUN] would have sold at open=X qty=Y action=Z" 발송. exit_executor 가 직접 텔레그램 발화 X — exit_notifier.send_*_result(result, dry_run=True) 로 위임.

## 영향 범위

| 시스템 | 영향 |
|---|---|
| 운영 봇 main.py | **무영향** (별도 모듈, 별도 잡) |
| 메인 봇 DB (data/trading.db) | **무영향** |
| 종가베팅 DB (data/closing_bet.db) | candidates 컬럼 추가 가능 (v4, 설계 시 결정) |
| systemd 재시작 | 단위 2-5d 통합 후 (잡 3건 추가) |
| 텔레그램 봇 | 매도 알림 추가 (`CLOSING_BET_TELEGRAM_*`) |

## 의존 단위
- **단위 2-4 EntryExecutor** (완료, 인터페이스 계약 보존 필수)
- **단위 2-4f 활성화** 시 단위 2-5와 묶어서 단발 활성화 권장 (매수+매도 짝 맞춤)

## 다음 세션 진입 가이드
- **PLAN.md / CONTEXT.md / CHECKLIST.md 읽기**
- **strategy-planner + strategy-coder 병렬 사전 리뷰** (P0/P1 발견 후 PLAN 갱신)
- **승인 후 코딩 시작** (CLAUDE.md feedback_plan_review_process)
- 컨텍스트 크기 주의 — 단위 2-5는 entry_executor와 비슷한 규모(~500줄 + 30~40건 테스트)

---

## 본 세션 작업 요약 (2026-05-16 KST)

### 발견 사항
1. **사전 리뷰 P0 3건**:
   - **P0-1** (planner 단독): 시뮬레이터(`phase25_simulator.py` 3구간 `prd_split_gapup/flat/gapdown`) vs 실 매도 6단계 매핑 불일치 → walkforward EV +1.04% (n=103) 실전 정합성 위험. 해결: 5단계로 통일(trailing_stop은 단위 2-5g 분리) + 모든 액션 시초가 시장가 매도로 통일 → 시뮬 `open_pct` 가정과 정합 + 단위 2-5e 정합성 게이트 (delta ≤ 0.1%) 추가
   - **P0-2** (둘 다): `candidate_logger.log_exit()` 가 `entry_price IS NULL` 시 `LookupError` raise. 단위 2-4c 옵션 A 인터페이스 계약상 phase1 only 보유는 `candidate_status='recommended' + entry_price=NULL` → 매도 발주 직전 `mark_entered_phase1_only(candidate_id)` 헬퍼 신규 호출 패턴 박제
   - **P0-3** (coder 단독): 09:00 메인 봇 잡 race — `scheduler.py:163` `monitoring_start_early` + `:218` `midweek_sell_profit` 모두 09:00 cron → KIS `_shared_token` 경합 위험. 해결: emergency_stop 09:00 → **09:01** 오프셋 박제
2. **사전 리뷰 P1 9건**: GAPUP 임계값 / idempotency / trailing 모니터링 / force_close 취소 순서 / sell_lock 결정 / dry_run 정책 / 파일 크기 / 잡별 misfire / align_to_tick 기검증 — 모두 PLAN/CONTEXT/CHECKLIST 박제
3. **단위 2-5a 6 검증 PASS**:
   - `KISApi.get_current_price` 응답 open/high/price 11필드 (`_safe_int` 적용)
   - 매도 대상 SQL 운영 DB 직접 query 검증 (5/15 trade_date 0건, 기대값 일치)
   - `KISOrderApi.sell_market_order` 응답 entry_executor 동일 패턴
   - 부분 체결 잔량 = `COALESCE(phase1, 0) + COALESCE(phase2, 0)`
   - **`KISOrderApi.cancel_order(order_id, stock_code, quantity)` 기존 구현 발견** (TR_CANCEL_REAL=TTTC0803U) → P1-4 force_close 재사용 가능
   - 09:00 메인 봇 잡 monitoring_start_early **1초 미만 비동기 위임** → 09:01 60초 여유 확정
4. **code-tester stream idle timeout 대체**: 단위 2-5c 검증 시 stream timeout → 직접 6항목 검증으로 통과 (py_compile 0 / datetime.now 잔존 0 / async to_thread 전체 / sell_market_order 단일 / dry_run 분기 위치 / P1-4 cancel_order 보강)

### 수정/작성 파일 (단위 2-5a~e)
**신규 9개**:
- `closing_bet_system/collectors/morning_price_collector.py` (MorningPriceCollector + MorningPriceSnapshot frozen)
- `closing_bet_system/execution/exit_target_query.py` (select_exit_targets + ExitTarget frozen + is_phase1_only/total_shares property)
- `closing_bet_system/execution/exit_executor.py` (~620줄 — ExitExecutor + ExitExecutorSettings + ExitAction Enum 5단계 + ExitResult + CandidateExit + map_action + 3 public 메서드)
- `closing_bet_system/notification/exit_notifier.py` (3종 알림 + dry_run "[DRY-RUN]" prefix)
- `scripts/test_morning_exit_unit_2_5b.py` (12건 PASS)
- `scripts/test_exit_executor.py` (EX-1~30 + EX-18b = 31건 PASS)
- `docs/work-plans/active/closing-bet-unit-2-5-morning-exit/STEP0_MORNING_EXIT_RESEARCH.md`
- `docs/work-plans/active/closing-bet-unit-2-5-morning-exit/UNIT_2_5e_PARITY_REPORT.md`
- `docs/improvements/change_log.md` (단위 2-5 종합 1줄 추가)

**수정 5개**:
- `closing_bet_system/storage/candidate_logger.py` (`mark_entered_phase1_only` 헬퍼 신규, P0-2)
- `closing_bet_system/main_orchestrator.py` (상수 6건 + exit_executor lazy property + 3개 async 메서드 + register_jobs 잡 3건 추가, 잡 로그 "5건→8건")
- `closing_bet_system/config/settings.yaml` (morning_exit:* 섹션 7키 신규 + schedule.emergency_stop_start "09:00"→"09:01" + morning_exit.enabled=true/dry_run=true 부분 활성화)
- `scripts/test_closing_bet_orchestrator.py` (잡 8건 + 신규 3개 잡 트리거 + 상수 16건 검증 보강)
- `docs/work-plans/active/closing-bet-unit-2-5-morning-exit/PLAN.md / CHECKLIST.md` (P0/P1 반영)

### 단위 2-5 commit 이력 (5/16 본 세션)
| commit | 단위 | 내용 |
|---|---|---|
| `49537cd` | 사전 리뷰 | P0 3건 + P1 9건 PLAN/CONTEXT/CHECKLIST 반영 |
| `ea8d3e7` | 2-5a/b | Step 0 + collectors + mark_entered_phase1_only |
| `f6b957c` | 2-5c | ExitExecutor 620줄 + ExitNotifier + 31건 PASS |
| `4bbcd8b` | 2-5d | APScheduler 통합 + settings.yaml |
| `1f8f55a` | 2-5e | 정합성 분석 + change_log |
| `2e5fcc2` | 2-5e+ | morning_exit dry_run 부분 활성 토글 |
| `e20f2bc` | main 머지 | 5 commits 통합 main 머지 + push (`bf0caa2→e20f2bc`) |

### 배포 상태 (2026-05-16 KST 10:34)
- 워크트리 브랜치: `worktree-closing-bet-unit-2-4-entry-executor` @ `2e5fcc2` (clean)
- main: `e20f2bc` (origin 동기화)
- 운영 봇: **PID 294777** (2026-05-16 01:33:16 UTC restart)
- 종가베팅 잡 **8건 등록**: pipeline/summary/label/flow_reliability/entry_pipeline + **emergency_stop 09:01 / morning_exit 09:30 / morning_force_close 10:30**
- settings.yaml: entry_executor.enabled=true/dry_run=true (단위 2-4) + morning_exit.enabled=true/dry_run=true (단위 2-5) — **자동매매 위험 0** (KIS 미발주 + log_exit 미호출 + DB 무변경)

### 누적 회귀 199건 PASS (14개 슈트)
phase25 60 + 2-4b 29 + 2-4c 31 + orchestrator 16 + candidate_logger 20 + 2-5b 12 + 2-5c 31

### 다음 세션에서 주의할 점 (5/19~5/22 모니터링 + 단위 2-5f 활성화)
1. **5/18(월) 15:18 KST 매수 dry_run 첫 발화 자연 검증**
   - 텔레그램 phase1/phase2/pipeline_summary 알림 도착 확인
   - DB candidates 박제 (ODNO 없이 status='recommended' 유지, entry_phase1_executed_shares = NULL — dry_run이라)
   - 잡 발화 시점에 KIS 호출 미발주 확인 (journalctl: `[entry_executor] DRY_RUN phase1: ...`)
2. **5/19(화) 09:01/09:30/10:30 매도 dry_run 첫 발화 자연 검증**
   - dry_run에서는 entry_phase1_executed_shares = NULL이라 매도 대상 select 0건 가능 — 정상 동작
   - 만약 자동매매 실 활성화 후라면 매도 대상 select N건 + 4단계 매트릭스 분포 알림
3. **5/22(금) 17:30+ 별도 세션 — 단위 2-5f 활성화 결정**:
   - 1주 dry_run 데이터 누적 검증 (5/18~5/22)
   - `phase25_walkforward.py --start 2026-05-04 --end 2026-05-22` 재실행
   - UNIT_2_5e_PARITY_REPORT 정합성 게이트 재측정 (실 매도 추정 EV +0.55~0.75%)
   - 사용자 명시 승인 → entry_executor.dry_run=false + morning_exit.dry_run=false **묶어서** 활성화 (매수+매도 짝 맞춤)
4. **bot-health-checker 5/19 발화 후 자동 점검 권장**
5. **단위 2-5g (선택)**: trailing_stop 모니터링 루프 — 09:30~10:30 폴링 (별도 비동기 잡 또는 2분 cron). 단위 2-5f 활성화 후 후속.
6. **단위 2-7d (선택)**: 시뮬레이터 5단계 정합성 모델 도입 (GAP_UP_LOW 100% 시초가 분리 + GAP_UP_HIGH force_close 정밀 모델). UNIT_2_5e_PARITY_REPORT delta +0.3~0.5%p 해소.

### 컨텍스트 크기
- 이번 세션 컨텍스트 매우 큼 (단위 2-4 옵션 1 배포 + 옵션 B 부분 활성화 + 단위 2-5 PLAN + 사전 리뷰 + 2-5a~e 완료 + main 머지 + restart)
- **다음 세션은 새 대화 시작 강력 권장** — `/resume` 명령으로 PLAN.md + CONTEXT.md + CHECKLIST.md 자동 로드
- 단위 2-5f 활성화 별도 세션은 5/22(금) 17:30+ 예정
