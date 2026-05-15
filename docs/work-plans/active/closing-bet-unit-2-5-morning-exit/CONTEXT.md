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
