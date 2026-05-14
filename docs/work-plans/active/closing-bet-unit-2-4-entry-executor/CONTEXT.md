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

---

## 작업 중 발견 사항 (2026-05-14 세션 종합)

### 발견 1: TR_ORDER_STATUS 이미 구현됨
- `modules/trading_engine/kis_order_api.py:569` `get_order_status(order_id, order_date)` 이미 존재
- TR_ID `TTTC8001R` (모의 `VTTC8001R`) 사용, URL `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`
- 반환 dict 필드: `order_id` / `order_qty` / `filled_qty`(`tot_ccld_qty`) / `filled_price`(`avg_prvs`) / `status`("체결"/"미체결")
- 부분 체결도 지원 (order_qty ≠ filled_qty 시 "미체결")
- **결론**: fill_checker는 thin async wrapper로 충분, 신규 KIS HTTP 호출 불필요 → 단위 2-4b 시간 단축

### 발견 2: 동시호가 ord_dvsn = "00" 단일 사용
- KIS 공식 매뉴얼은 인증 필요로 직접 fetch 불가 (apiportal.koreainvestment.com)
- WikiDocs/GitHub 샘플: `ord_dvsn` 명시 코드는 "00"(지정가) / "01"(시장가) / "02"(조건부) / "05"(장전 시간외)
- 장후 시간외 / 동시호가 별도 코드 명시 없음
- **KRX 메커니즘 결론**: 정규장 마감 동시호가(15:20~30)는 정규장 시간 안에 포함 → `ord_dvsn="00"` 지정가 주문이 시간대 따라 자동 동시호가 큐 진입
- **5/15 dry_run 최종 검증 필요**: 15:25 시점 모의 주문 1건 발주 시 rt_cd=0 + ODNO 반환 확인
- phase2_enabled=True default 유지, 폴백 시 settings.yaml 토글로 False (코드는 보존)

### 발견 3: KISApi 분봉 메서드 신규 추가 (FHKST03010200)
- 기존 시스템에 분봉 조회 없음 → `get_minute_price(stock_code, time_to, count)` 신규
- URL: `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice`
- 응답 추정 필드: `stck_cntg_hour` / `stck_oprc/hgpr/lwpr/prpr` / `cntg_vol`
- **5/15 dry_run 검증 필요**: 실제 응답 형식이 추정과 다를 시 vwap_collector 보정

### 발견 4: KisOrderbookCollector 클래스명 (Kis 소문자 i)
- 다른 모듈은 `KISOrderApi` (대문자 IS) 인데 이 클래스만 `KisOrderbookCollector`
- 단위 테스트 작성 시 ImportError 발생 → 클래스명 그대로 사용
- 향후 일관성 정리는 별도 단위로 분리 (현 작업 범위 밖)

### 발견 5: phase25 시뮬레이터/walkforward 게이트 PASS
- 5/14 2차 walkforward (recommended 103건, prd_split_realistic 정책):
  - 전체 EV +1.04% / Sharpe +1.57 / WL ∞ → **🟢 4 게이트 모두 PASS**
  - score≥2 (n=66): EV +1.60% / Sharpe +2.08 (표본만 부족)
  - score≥3 (n=24): EV +2.32% / Sharpe +2.78
- 단위 2-4 entry_executor 진입 결정 근거 확보
- 사용자 결정: score≥2 / 포지션 70% / 옵션 C (realistic 게이트 + optimistic 동시 측정) / 폴링 5초

### 발견 6: 5/13 라벨 누락 1건
- 5/13 후보 17건 중 16건만 라벨링 (1건 KIS 500 케이스 추정)
- 단위 2-4 작업 범위 밖, 별도 단발 백필 필요
- 표본 영향 미미 (n=103 중 1건)

### 발견 7: 단위 2-4 플랜 리뷰 P0/P1 12건
- strategy-planner: 미체결 정책 충돌 / Kill Switch 누락 / 폴링 PRD 1초 vs 5초 / fund_guard daily_entries / 1차 체결+2차 미체결 인터페이스 / 옵션 C 3점 비교 코드 / 2-4f 게이트 / position_ratio 확대 기준
- strategy-coder: 체결 확인 누락(fill_checker) / ODNO DB 컬럼 / Step 0 폴백 / async 패턴 / KIS rate limit / dry_run 토글 / mark_entered 옵션 A / 잡 충돌 / 70% 적용 위치 / 호가 단위 정렬 / 인덱스 / 텔레그램 / 테스트 시나리오 / settings 외부화
- **모두 마스터 플랜에 반영 완료** (`/home/hatni/.claude/plans/recursive-questing-zephyr.md`)

---

## 수정/작성 파일 (워크트리 commit f1d1e18)

### 신규 (7개)
- `closing_bet_system/execution/price_utils.py` — KRX 호가 단위 정렬
- `closing_bet_system/execution/fill_checker.py` — KIS get_order_status async wrapper + 3회 재시도
- `closing_bet_system/collectors/vwap_collector.py` — 14:50~15:18 VWAP
- `closing_bet_system/collectors/estimated_price_collector.py` — PRD 9-3 예상체결가/호가 잔량
- `scripts/test_price_utils.py` / `test_fill_checker.py` / `test_vwap_collector.py` / `test_estimated_price_collector.py` / `test_kis_orderbook_polling.py`

### 수정 (2개)
- `closing_bet_system/collectors/kis_orderbook_collector.py` — `poll_asking_price` async generator 추가
- `modules/stock_screener/kis_api.py` — `get_minute_price` (FHKST03010200) 신규 (메인 봇 무영향)

### 작업 폴더 (3+1 문서)
- `docs/work-plans/active/closing-bet-unit-2-4-entry-executor/PLAN.md` / `CONTEXT.md` / `CHECKLIST.md` / `STEP0_KIS_RESEARCH.md`

---

## 단위 테스트 결과 (29건 PASS, 누적 89건)

```
TICK 6/6 PASS (price_utils — 7구간 + floor/ceil + 경계 + 0/음수)
FILL 8/8 PASS (fill_checker — 전량/부분/미체결 + 500 재시도 + backoff + empty)
VWAP 6/6 PASS (vwap_collector — 정상 + 시간대 + 빈/0거래량 + 가드 + 예외)
EP   5/5 PASS (estimated_price — 정상 + 15:20 가드 + 빈응답 + 예외 + 빈필드)
POLL 4/4 PASS (orderbook polling — max_iterations + interval + 즉시 yield + cancel)
```

기존 회귀 60건 (phase25 simulator 26 + walkforward 13 + simulator_prd 14 + walkforward_prd 7) 모두 영향 없음.

---

## 다음 세션 단위 2-4c 진입 가이드

### 시작 시 첫 작업
1. **워크트리 재진입**: 워크트리 보존됨 (`worktree-closing-bet-unit-2-4-entry-executor` 브랜치)
2. **이 CONTEXT.md + STEP0_KIS_RESEARCH.md 읽기**
3. **마스터 플랜 단위 2-4c 섹션 참조**: `/home/hatni/.claude/plans/recursive-questing-zephyr.md`

### 단위 2-4c 작업 순서 (예상)
1. **DB v2 마이그레이션** (`closing_bet_system/storage/db.py`) +6 컬럼 + 인덱스 (idempotent)
2. **EntryExecutorSettings + Phase1Result/Phase2Result dataclass**
3. **`closing_bet_system/execution/entry_executor.py`** EntryExecutor 클래스 (~700줄)
   - `execute_phase1()` Phase 0/0.5/1~4 흐름
   - `execute_phase2()` Phase 0/0.5/1~4 흐름
   - `_compute_order_amount()` 70% 헬퍼
   - dry_run 토글 분기
4. **`closing_bet_system/notification/entry_notifier.py`** 텔레그램 알림 포맷
5. **단위 테스트 EE-1~30** 30건 (CHECKLIST 시나리오 참조)

### 주의 사항
- **mark_entered 옵션 A**: phase2 완료 시점에 1회 호출 (가중 평균). phase1만 완료된 50% 보유 상태는 candidate_status 'recommended' 유지 + entry_phase1_executed_shares > 0 으로 식별
- **MarketGuard Phase 0.5 통합**: CRISIS → 전체 스킵, CAUTION → ratio × 0.5
- **fallback_to_next_candidate=True** default (PRD 9-2 정합)
- **dry_run 토글**: KIS 호출 직전 분기, subclass 패턴 X
- **호가 단위 정렬**: 가격 상한 계산 후 align_to_tick(price, "buy") 의무화
- **모든 KIS 호출 async**: `await asyncio.to_thread(kis_api.x, ...)` 패턴 (kis_orderbook_collector 참조)

### 컨텍스트 크기
- 이번 세션 컨텍스트 매우 큼 (walkforward + plan 리뷰 + 단위 2-4a/b)
- 다음 세션은 **새 대화 시작 권장** — `/resume` 명령 또는 직접 이 CONTEXT.md 읽기로 시작
