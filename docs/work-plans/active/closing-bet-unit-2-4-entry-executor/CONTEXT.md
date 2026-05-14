# CONTEXT: 종가베팅 단위 2-4 entry_executor

## 변경 이유

### 1. 100건 게이트 PASS (5/14)
- walkforward 2차 실행: n=103, realistic EV +1.04% / Sharpe +1.57 / W-L ∞
- score≥2 (n=66): EV +1.60% / Sharpe +2.08 — 단위 2-4 진입 결정 근거
- Phase 2 자동매매 진입 시점

### 2. 사용자 결정 (5/14 AskUserQuestion)
- 운영 정책: 옵션 C (realistic 게이트 + optimistic 동시 측정)
- 진입 임계: score≥2
- 포지션: PRD 기본 자금 한도 × `max_position_per_stock(0.20)` × **0.70**
- 폴링 간격: 5초 (Step 0 후 재검토)
- 실전 활성화: paper trade 단발 검증 1~2회 통과 후

### 3. 리뷰 P0/P1 반영
- strategy-planner: 미체결 정책 충돌 / Kill Switch 누락 + 7건 보강
- strategy-coder: 체결 확인 누락 / ODNO DB / 폴백 결정 + 7건 보강

## 현재 코드 상태

### 기존 인프라
- `closing_bet_system/main_orchestrator.py:744-802` register_jobs (4잡: 15:10/15:35/10:00/19:27)
- `closing_bet_system/execution/__init__.py` 빈 디렉토리 (entry_executor placeholder 부재)
- `modules/trading_engine/kis_order_api.py:81` `TR_ORDER_STATUS=TTTC8001R` (단, `_place_order:466~491`은 ODNO만 반환, 체결가/수량 미반환)
- `modules/trading_engine/kis_order_api.py:62` `ORDER_TYPE_LIMIT="00"` / `"01"` / `"02"`
- `closing_bet_system/infra/fund_guard.py:116~197` 8단계 검사 (스윙 중복 / 비중 / 자금 / 동시 / 일일 / 주간 손실)
- `closing_bet_system/storage/candidate_logger.py:269` `mark_entered(candidate_id, entry_price, entry_amount, entry_time)` — 1회 호출 가정
- `modules/market_guard.py:31` `MarketGuard.check()` (메인 봇 09:25 매수 전 사용)

### DB 스키마 (현재)
- candidates: candidate_id, trade_date, ticker, name, candidate_status, rejection_reason, layer1/2/3_score, total_score, entry_price, entry_amount, entry_time, exit_price, exit_time, buy_commission, sell_commission, transaction_tax, estimated_slippage, net_pnl_pct, created_at
- candidate_status: 'recommended' / 'rejected_filter' / 'rejected_manual' / 'entered' / 'exited'

## 핵심 스니펫: PRD 9-1/9-2/9-3 매핑

```python
# PRD 9-1 2분할 진입
phase1_time = "15:18"   # 정규장 마지막 50%
phase2_time = "15:25"   # 동시호가 50%

# PRD 9-2 가격 상한
allowed_price = min(
    vwap_14_50_to_15_18 * 1.005,
    today_high,
    estimated_price * 1.002,  # 15:20 이후만
)

# PRD 9-3 보류/취소
if estimated_price > phase1_avg_price * 1.005:
    skip_phase2()                # 보류
if ask_total / bid_total < 0.8:
    cancel_phase2()              # 취소
```

## 과거 버그/주의사항

- **NULL 가드**: `pd.isna()` 사용 (CLAUDE.md)
- **KIS 토큰 공유**: 메인 봇 `_shared_token` 패턴 — 신규 collector도 기존 KISOrderApi 싱글톤 사용
- **KST 타임존**: `from config import now_kst` (서버 UTC)
- **5/13 KIS 500 사건**: label_provider `_fetch_daily_price_with_retry` 패턴 (3회 재시도) — fill_checker도 동일
- **AsyncIOScheduler**: main_orchestrator async 패턴, KIS 호출은 `await asyncio.to_thread(...)`
- **이중 비용 차감 방지**: cost_engine.compute_pnl이 이미 비용 차감 (백테스트 시뮬레이터 기준)
- **idempotency**: 주문 발주 후 timeout 시 ODNO로 재조회 (5/13 사건 영구 누락 패턴 회피)

## 영향 범위

| 시스템 | 영향 |
|---|---|
| 운영 봇 main.py | **무영향** |
| 메인 봇 DB (data/trading.db) | **무영향** |
| 종가베팅 DB (data/closing_bet.db) | candidates +6 컬럼 + 인덱스 (마이그레이션 v2) |
| systemd 재시작 | 단위 2-4d 통합 후 / 단위 2-4f 활성화 시점 |
| 텔레그램 봇 | 진입 알림 추가 (`CLOSING_BET_TELEGRAM_*`) |

## DB 현황 (2026-05-14 기준)
- candidates 138건 / labels 115건 / EV+ 73건 (63.5%)
- 5/14 후보 21건 (recommended 18, rejected 3) — 5/15 자동 라벨링 예정
- 단위 2-4a probe 시점: 5/14 야간 또는 5/15 장중

## 의존 단위
- **단위 2-5 morning_exit_manager** (단위 2-4 직후 필수)
- **단위 2-4g phase2 동시호가** (Step 0 결과 따라 분리 여부 결정)
