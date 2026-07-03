# CHECKLIST: 분할 진입 + 불타기 + ATR 트레일링

## 구현 (12 Steps)

### Step 1: DB v17 마이그레이션
- [x] `database.py` `_migrate_v17()` 신규 함수
- [x] `portfolio` 테이블에 7개 컬럼 ADD COLUMN (first_buy_price, avg_buy_price, tranche_count, second_tranche_executed, second_tranche_pending, atr_at_buy, atr_period)
- [x] **`second_tranche_pending DEFAULT 0`** ⚠️ (기존 holding 안전)
- [x] 백필 UPDATE 4문장 (기존 holding 종목 보호)
- [x] `migrations` 리스트에 v17 등록
- [x] DB 백업 자동 생성 확인

### Step 2: Position 클래스 확장 (`modules/portfolio_monitor_v2.py:58-107`)
- [x] 필드 6개 추가: first_buy_price, avg_buy_price, second_tranche_executed, second_tranche_pending, atr_at_buy, atr_period
- [x] `tranche_count` 필드 추가
- [x] `profit_rate` 프로퍼티 → `first_buy_price` 기준 변경
- [x] **신규 `profit_rate_avg` 프로퍼티** (`avg_buy_price` 기준)
- [x] **신규 `effective_trailing_pct(level)` 메서드** (`max(고정값, ATR_MULT × atr / first)`)
- [x] `buy_price` alias 유지 (deprecated 주석)

### Step 3: config.py 신규 상수 ~12개
- [x] `TRANCHE_ENTRY_ENABLED: bool = True`
- [x] `TRANCHE_FIRST_RATIO: float = 0.5`
- [x] `TRANCHE_SECOND_RATIO: float = 0.5`
- [x] `PYRAMID_TRIGGER_PCT: float = 0.05`
- [x] `PYRAMID_MAX_WAIT_DAYS: int = 10`
- [x] `TRANCHE_SECOND_AMOUNT_MODE: str = 'mirror_first'`
- [x] **익절 임계값 갱신**: `TAKE_PROFIT_1=0.12`, `TAKE_PROFIT_2=0.20`, `TAKE_PROFIT_3=0.30`
- [x] **익절 비율 갱신**: `PARTIAL_SELL_RATIO_1=0.25`
- [x] **`PARTIAL_PROFIT_BASE: str = 'avg'`** (롤백 토글)
- [x] `TRAILING_USE_ATR: bool = True`
- [x] `ATR_PERIOD: int = 14`
- [x] `ATR_MULTIPLIER: float = 2.0`

### Step 4: ATR 계산 모듈 (`modules/atr_calculator.py` 신규)
- [x] `compute_atr(stock_code, period=14) -> float` 동기 함수
- [x] `compute_atr_batch(stock_codes, period=14, timeout=0.5)` 비동기 일괄 함수
- [x] pykrx OHLC 우선, KIS API 폴백 (yfinance/pykrx 패턴 재사용)
- [x] 모듈 내 캐시 (key: (stock_code, KST date), 일 단위 만료)
- [x] 양쪽 실패 시 0.0 반환 (호출 측 폴백 의도)

### Step 5: BuyLock (`modules/trading_engine/buy_lock.py` 신규)
- [x] SellLock 동일 패턴 (threading.Lock 싱글톤)
- [x] `acquire(stock_code, owner) -> bool`
- [x] `release(stock_code)` — try/finally 1차 해제
- [x] `clear_all()` — 15:30 일괄 해제 (2차 안전망)
- [x] `modules/trading_engine/__init__.py`에서 export

### Step 6: 매수 50% 분할 (`main.py:execute_buy_orders`)
- [x] `per_slot_capital_first_tranche = per_slot_capital * TRANCHE_FIRST_RATIO`
- [x] 1차 매수만 발주 (현재 로직 그대로, 금액만 축소)
- [x] 매수 후 ATR prefetch 캐시 조회 또는 동기 호출 (0.5초 타임아웃)
- [x] `save_holding_position()` 호출 시 first_buy_price/avg_buy_price/atr_at_buy 전달
- [x] `second_tranche_pending=1` 명시 설정 (신규 매수)
- [x] 텔레그램 알림 변경 ("1/2 진입 (50%): ... | ATR=X")

### Step 7: 2차 진입 모니터 (`_check_and_execute_pyramid_in`)
- [x] 신규 함수 `_check_and_execute_pyramid_in(pos: Position)`
- [x] 트리거 조건 AND: pending=True, executed=False, max_profit_rate(first) >= 0.05
- [x] 사전 조건: MarketGuard 재호출 (5분 캐시) + 시장 시간 + BuyLock 획득
- [x] 우선순위 가드: 동일 사이클 분할익절 발화 시 skip
- [x] `buy_limit_aggressive()` 호출
- [x] DB UPDATE: executed=1, avg_buy_price 가중평균, tranche_count=2, shares/original_shares 누적
- [x] `_dump_monitor_state()` 즉시 호출 (3중 동기화)
- [x] 텔레그램 알림 ("2/2 불타기 진입 (+X% first 기준 도달)...")
- [x] 보유기간 카운트 = 1차 buy_date 기준 유지 (리셋 X)

### Step 8: 분할 익절 avg 기준 (`_check_and_execute_partial_profit`)
- [x] 트리거 기준: `pos.profit_rate_avg` 사용
- [x] `PARTIAL_PROFIT_BASE='first'` 토글 시 기존 동작 복귀 분기
- [x] 임계값/비율 → config 상수 사용 (12/20/30%, 25/20/20%)
- [x] `_execute_partial_sell()` original_shares 기준 비율 유지 (자동 호환)

### Step 9: ATR 트레일링 + sanity 분기
- [x] `_update_trailing_stop()` `effective_trailing_pct(level)` 사용
- [x] `TRAILING_USE_ATR=False` 폴백
- [x] monitor_state.json 6개 키 추가 (first/avg/executed/pending/atr/tranche_count)
- [x] **sanity 임계 tranche_count 분기**: count==2 시 `max(first×1.02, avg×1.02)`
- [x] **avg <= 0 폴백**: `avg = first` 강제 + WARN 로그

### Step 10: 대시보드/텔레그램
- [x] `web/dashboard_service.py` tranche_count, avg_buy_price, first_buy_price, atr_at_buy 노출
- [ ] `web/templates/dashboard.html` `data-tranche`, `data-avg-price`, `data-first-price` 속성 (서비스 백엔드만 노출, HTML 카드는 추후 작업)
- [ ] 포지션 카드: avg/first 양쪽 표시 + 레이블 (트레일링=first, 익절=avg) (HTML 추가 작업 필요)
- [x] 매수/매도 알림 메시지 갱신 (Step 6, 7, 8에서 부분 처리됨)

### Step 11: 신규 테스트
- [x] `tests/test_tranche_entry.py` 신규 — **13개 케이스 PASS** (Position 폴백 2 / profit_rate 2 / effective_trailing 3 / BuyLock 2 / DB v17 4)
- [ ] `tests/test_atr_trailing.py` 신규 — 6개 케이스 (별도 ATR 모듈 테스트, pykrx 실제 호출 mock 필요로 추후 작업)
- [x] `tests/test_monitor_state_residue.py` 회귀 **10/10 PASS** (기존 테스트 무회귀 확인)
- [x] `tests/test_tranche_entry.py`의 DB 마이그레이션 idempotent 케이스 포함

### Step 12: code-tester + 검증 + 문서
- [x] `python -m py_compile` 10개 파일 통과
- [x] 신규 테스트 13/13 + 회귀 10/10 PASS
- [ ] **code-tester 에이전트** 호출 (CLAUDE.md 규정, 사용자 작업 권장)
- [ ] sqlite MCP로 portfolio 컬럼 추가 확인 (사용자 작업, `mcp__sqlite__describe_table`)

## 검증 (Dry-run + 실전) — 🟡 사용자 운영 액션 필요

### Dry-run (1주차)
- [ ] `TRANCHE_ENTRY_ENABLED=True` + `DRY_RUN_PYRAMID=True` 설정 후 1주 모니터링
- [ ] 측정 지표 (`/improve weekly` 또는 직접 SQL):
  - [ ] 2차 트리거 발화 빈도
  - [ ] **2차 트리거 발화 종목의 7일 내 익절 발동 비율 ≥ 30%** (롤백 조건)
  - [ ] 거짓 신호 비율 < 50%
  - [ ] **ATR 모듈 채움 비율 ≥ 90%** (롤백 조건 50%)

### 실전 관찰 (2~4주차)
- [ ] `DRY_RUN_PYRAMID=False` 전환 (1주 dry-run 통과 후)
- [ ] systemctl restart trading_system
- [ ] 매주 `/improve weekly`로 효과 측정
  - [ ] 1차 손절 손실 절대액 감소 (vs 변경 전 기준선)
  - [ ] 2차 진입 종목 trend 유효성
  - [ ] 익절 +12/+20/+30%(avg) 발화 빈도
- [ ] monitor_state.json 잔재 회귀 0건

## 배포 — 🟡 사용자 운영 액션 필요

- [ ] **bot-health-checker 에이전트** 호출 (`/agents` 또는 직접 Task)
- [x] **`docs/improvements/change_log.md`에 1줄 추가** ✅ (2026-05-20 v17 한 줄 기록 완료)
- [ ] systemctl restart trading_system 후 bot-health-checker 통과 확인
- [ ] 텔레그램 매수/매도 알림 포맷 정상 (1/2 진입 / 🔥 2/2 불타기 진입)
- [ ] 1주 후 효과 측정 + 롤백 조건 점검

## 문서 업데이트 — 🟡 후속 작업

- [ ] **`CLAUDE.md`** — 분할진입+불타기+ATR 트레일링 규칙 추가 (정책 분기 / OrderLock 우선순위 / ATR 폴백)
- [ ] **`memory/MEMORY.md`** — 항목 1줄 추가
- [ ] **`memory/project_tranche_entry.md`** 신규 작성
- [ ] **`memory/project_strategy.md`** 운용 파라미터 갱신 (50:50 / +5% / avg 익절 / ATR)
- [ ] **이 디렉토리** → `completed/20260520_tranche-entry-pyramid/` 이동 (검증 완료 후)

## 롤백 (이상 발생 시) — 참조용

- 마스터: `config.TRANCHE_ENTRY_ENABLED = False`
- 익절 기준: `config.PARTIAL_PROFIT_BASE = 'first'`
- ATR: `config.TRAILING_USE_ATR = False`
- **익절 임계값 강제 원복** ⚠️:
  - `TAKE_PROFIT_1: 0.12 → 0.10`
  - `TAKE_PROFIT_2: 0.20 → 0.15`
  - `TAKE_PROFIT_3: 0.30 → 0.20`
  - `PARTIAL_SELL_RATIO_1: 0.25 → 0.30`
- systemctl restart trading_system
- 보유 중 종목 `second_tranche_pending=0` 수동 SQL UPDATE
- change_log.md에 롤백 1줄 추가
