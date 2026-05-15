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

## 핵심 스니펫: 매도 액션 매핑 (단위 2-5c 설계 초안)

```python
class ExitAction(Enum):
    EMERGENCY_STOP = "emergency_stop"       # 시가 ≤ -1% 즉시 전량 시장가
    GAP_UP_HIGH_PARTIAL = "gap_up_high"     # 시가 ≥ +2% 50~70% 분할
    GAP_UP_LOW_TRAILING = "gap_up_low"      # +0.5%~+2% trailing
    FLAT_TIME_EXIT = "flat"                 # ±0.5% 시간 매도
    WEAK_GAP_DOWN_EXIT = "weak_gap_down"    # -1%~-0.5% 09:30 시장가
    TRAILING_STOP = "trailing_stop"         # -1.5% 도달

def map_action(open_price, entry_price, exit_cfg) -> ExitAction:
    gap_rate = (open_price - entry_price) / entry_price
    if gap_rate <= exit_cfg.hard_stop_loss:        # -0.01
        return ExitAction.EMERGENCY_STOP
    if gap_rate >= exit_cfg.gap_up_high_threshold: # +0.02
        return ExitAction.GAP_UP_HIGH_PARTIAL
    if gap_rate >= exit_cfg.gap_up_low_threshold:  # +0.005
        return ExitAction.GAP_UP_LOW_TRAILING
    if gap_rate >= exit_cfg.flat_lower:            # -0.005
        return ExitAction.FLAT_TIME_EXIT
    return ExitAction.WEAK_GAP_DOWN_EXIT           # -0.01 ~ -0.005
```

## 과거 버그/주의사항
- **NULL 가드**: `pd.isna()` 사용 (CLAUDE.md)
- **KIS 토큰 공유**: 메인 봇 `_shared_token` 패턴 — 신규 collector 도 기존 KISApi/KISOrderApi 싱글톤 사용
- **KST 타임존**: `from config import now_kst` (서버 UTC)
- **부분 체결 잔량**: phase2 부분 체결 종목 처리 — `total_shares = phase1_shares + phase2_executed_shares`
- **idempotency**: 09:00 emergency_stop 발주 후 09:30 morning_exit이 같은 종목 재발주 방지 (exit_time IS NULL 가드 + DB transaction)
- **SellLock**: 메인 봇의 `modules/trading_engine/sell_lock.py` 패턴 참고 — 단위 2-5는 별도 모듈이므로 자체 race 봉쇄 필요 (또는 SellLock 재사용 검토)
- **MarketGuard**: 09:25 메인 봇 매수 전 사용 — 종가베팅 매도는 09:00~10:30이므로 시점 분리, 충돌 없음

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
