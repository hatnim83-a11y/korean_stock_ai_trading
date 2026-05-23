# CONTEXT: 분할 진입 + 불타기 + ATR 트레일링

## 변경 이유 (Why Now)

### 데이터 근거 (focus_stop_loss_20260520.md + W21 weekly)
- W21 매도 4건 전부 손실 (승률 0%, -28.93%p)
- 평시 손절 10건 분석 (2026-04-09 ~ 05-20):
  - 80%(8건)가 -7% 이상 갭다운 (Day0/Day1 즉시 손절)
  - D+5 평균 변화율 -3.99% → 손절 후 추가 하락 일반적
  - max_profit ≥ +5% 도달 = 2건(20%) — 수익 종목 발굴 빈도 낮음
- 5/19 화요일 테마 재선정 직후 반도체 카테고리(HPSP + 이오테크닉스) 동시 갭다운
- MDD -3.07% → -7.62%로 확대

### 사용자 가설 검증 (Sequential Thinking 8단계)
| 가설 | 데이터 입증도 | 본 설계의 달성 가능성 |
|------|--------------|--------------------|
| ① 수익 끌기 | 부분 (20% 빈도 표본) | 익절 임계 +12/+20/+30% (avg) + ATR 트레일링으로 부분 달성 |
| ② 리스크 축소 | 명확 (수학적) | 1차 50% → 손실 절대액 -50% 명확 |

### 두 리뷰 에이전트 강력 권고: 단계적 도입 권고했으나 사용자 일괄 도입 결정
→ 리뷰 보완사항(Top 5 위험 + 누락 5건)을 모두 반영하여 위험 완화

---

## 현재 코드 상태 (Phase 1 Explore 결과)

### 매수 흐름 (현재)
- **`main.py:execute_buy_orders()`** (line 1097, 1337-1345):
  ```python
  per_slot_capital = min(available_cash // available_slots, TOTAL_CAPITAL // MAX_POSITIONS)
  # 종목당 단일 매수 (100%)
  ```
- **`modules/trading_engine/__init__.py` → `buy_limit_aggressive()`** (line 469-657):
  - 매도 1호가 지정가 + 폴링 + 재시도 + 평균가 계산
  - `_compute_weighted_avg_price()` 재사용 가능
- **DB 저장**: `database.py:save_holding_position()` (line 1088):
  - `portfolio.buy_price`, `original_shares` 등 저장
  - 동일 stock_code holding/pending → status='replaced' 처리

### 분할 익절 (현재)
- **`modules/portfolio_monitor_v2.py:_check_and_execute_partial_profit()`** (line 1076-1135):
  - 트리거: `pos.profit_rate >= TAKE_PROFIT_X` (buy_price 기준)
  - 임계: `+10%/+15%/+20%` (config.TAKE_PROFIT_1/2/3)
  - 비율: `30%/20%/20%` (config.PARTIAL_SELL_RATIO_1/2/3)
- **`_execute_partial_sell()`** (line 1137):
  - `sell_shares = int(pos.shares * partial_sell_ratio_X)` — `pos.shares`(원본) 기준
  - 잔여 0주 → `_close_position_in_db` + `remove_position()` (2026-05-13 fix)

### 트레일링 (현재)
- **`_update_trailing_stop()`** (line 1225-1308):
  - L1(+8%/-5%): `profit_rate >= 0.08` → `trailing_active=True`, `stop_loss_price=buy_price`
  - L2(+15%/-3%): `profit_rate >= 0.15`
  - L3(+25%/-2%): `profit_rate >= 0.25`
  - `trailing_stop = highest_price × (1 - TRAIL_LEVEL_X_PCT)`
- **BE 손절**: `max_profit_rate >= +5%` → `stop_loss_price = buy_price × 0.99`

### Position 클래스 (현재)
**`modules/portfolio_monitor_v2.py:58-107`**:
```python
@dataclass
class Position:
    stock_code: str
    stock_name: str
    shares: int                  # 원본 수량
    remaining_shares: int        # 분할 익절 후 남은 수량
    buy_price: float             # 매수가 (단일값)
    stop_loss_price: float
    current_price: float = 0
    highest_price: float = 0
    trailing_stop: Optional[float] = None
    theme: str = ""
    buy_date: datetime = now_kst()
    price_confirmed: bool = False
    partial_1_executed: bool = False
    partial_2_executed: bool = False
    partial_3_executed: bool = False
    trailing_active: bool = False
    trailing_level: int = 0
    max_profit_rate: float = 0.0
```

### DB 스키마 (현재 v16)
**`portfolio` 테이블 핵심 컬럼**:
- 기본: `id, date, stock_code, stock_name, status, theme, weight`
- 매수: `buy_price, buy_date, shares, original_shares, current_price`
- 손익: `profit_rate, profit_amount, stop_loss, take_profit`
- 분할익절: `partial_1/2/3_executed, remaining_shares`
- 트레일링: `trailing_active, trailing_level, trailing_stop, highest_price, max_profit_rate`
- v15 (2026-05-08): `buy_message TEXT`
- v16 (2026-05-11): 외국인/기관 수급 신호 7개 컬럼

**마이그레이션 패턴** (`database.py:150-211`):
- `schema_version` 테이블로 idempotent 보장
- WAL/SHM 포함 자동 백업
- `_has_column()` 헬퍼로 ALTER TABLE 중복 방지

### 동시성 잠금 (현재)
- **SellLock** (`modules/trading_engine/sell_lock.py`): threading.Lock 싱글톤, `acquire/release/clear_all`
- 매도 시 acquire → 15:30 `monitoring_stop()`에서 `clear_all()` 일괄 해제
- **매수 측 BuyLock 없음** ← 본 작업에서 신규 추가

### monitor_state.json 동기화 (3중)
- 메모리(Position) ↔ DB(`position_state` 테이블) ↔ JSON(`data/monitor_state.json`)
- 30초 주기 + `stop_monitoring()` 직전 1회 강제 dump
- 잔재 회귀 차단: `highest_price > buy_price × 1.02` sanity (2026-05-13 도입)
- `holding` 화이트리스트 + `MAX_SELL_FAILURES=3` (2026-05-18 도입)

---

## 과거 버그 / 회귀 패턴 (반드시 회피)

### 2026-05-12 한화오션 사건 (Phase 1 fix)
- closed 종목의 monitor_state.json 잔재 → BE 손절가 즉시 활성화 회귀
- **대응**: 매도 시 메모리/DB/JSON 3중 동기화 (`remove_position()` 내부)
- **본 작업 영향**: avg_buy_price 누락 시 동일 catastrophic bug 가능 → `avg <= 0` 폴백 명시

### 2026-05-18 기아 사건 (Phase 2 fix)
- monitor_state.json 키 잔재 + 매도 실패 무한 반복 → 텔레그램 도배
- **대응**: holding 화이트리스트 + `MAX_SELL_FAILURES=3` 강제 제거
- **본 작업 영향**: 2차 매수 실패 시 카운터 필요한가? → 추후 검토, 현재는 BuyLock try/finally + 15:30 clear_all 이중 보장으로 충분 판단

### sanity 임계 한계
- 현재 `× 1.02` 임계는 단일 진입 가정
- 2차 진입 후 `highest > first × 1.02`는 정상 데이터 → 잔재로 오인 위험
- **본 작업 대응**: `tranche_count==2` 분기, `max(first×1.02, avg×1.02)`

---

## 영향 범위 (호출처 매트릭스)

### `pos.buy_price` 직접 참조 11개 호출처 (목적별 분기)

| 호출처 | 파일:라인 | 목적 | 변경 |
|--------|----------|------|------|
| `total_cost` 계산 | portfolio_monitor_v2.py:840 | 총 투입자본 | → `avg_buy_price` |
| `actual_profit_rate` | portfolio_monitor_v2.py:861/873/887 | 손익률 보고 | → `avg_buy_price` |
| `profit_rate` 보고 | portfolio_monitor_v2.py:934/946/969 | 손익률 보고 | → `avg_buy_price` |
| 수익률 표시 | web/dashboard_service.py:478/517 | 손익률 보고 | → `avg_buy_price` |
| 텔레그램 매도 알림 | main.py:1662/1684/1712/1761/1777 | 손익률 보고 | → `avg_buy_price` |
| trade_review 저장 | database.py:save_trade_review | 재정산용 | → `avg_buy_price` |
| `_check_stop_loss` | portfolio_monitor_v2.py:989 | 리스크 트리거 | → `first_buy_price` |
| `_check_be_stop` | portfolio_monitor_v2.py 내부 | 리스크 트리거 | → `first_buy_price` |
| `_update_trailing_stop` | portfolio_monitor_v2.py:1225 | 리스크 트리거 | → `first_buy_price` |
| `_check_and_execute_pyramid_in` (신규) | portfolio_monitor_v2.py | 2차 트리거 | → `first_buy_price` |
| `_check_and_execute_partial_profit` | portfolio_monitor_v2.py:1076 | 익절 트리거 | → `avg_buy_price` |
| `run_hold_period_sells` 로그 | main.py:2186 | 손익률 보고 | → `avg_buy_price` |

**구현 컨벤션**: `pos.buy_price` 직접 참조는 deprecated. 모든 호출은 `pos.first_buy_price` 또는 `pos.avg_buy_price` 명시 사용.

### 추가 영향 파일
- `scripts/backtest_full_system.py:82-340` — 백테스트 Position 동기화 (효과 측정 정합성)
- `modules/trading_engine/portfolio_monitor.py` (v1 legacy) — v17 컬럼 mismatch 검토
- `scheduler.py` — `prefetch_atr_for_candidates` 09:20 잡 등록
- `modules/market_guard.py` — 5분 캐시 결과 활용 (2차 진입 재호출용)

---

## 핵심 스니펫 참조

### 마이그레이션 패턴 (database.py:150-211)
```python
def _migrate(self) -> None:
    migrations = [
        (1, "...", self._migrate_v1),
        ...
        (16, "외국인/기관 수급 신호 도입", self._migrate_v16),
        (17, "분할 진입 + 불타기 + ATR 트레일링", self._migrate_v17),  # 신규
    ]
```

### 가중평균 계산 (재사용)
```python
def _compute_weighted_avg_price(fills: list[tuple[int, float]]) -> float:
    total_qty = sum(q for q, _ in fills)
    return sum(q*p for q,p in fills) / total_qty if total_qty > 0 else 0.0
```

### SellLock 패턴 (modules/trading_engine/sell_lock.py)
```python
class SellLock:
    _lock = threading.Lock()
    _holdings: dict[str, str] = {}
    
    def acquire(self, code: str, owner: str) -> bool:
        with self._lock:
            if code in self._holdings:
                return False
            self._holdings[code] = owner
            return True
```

---

## 외부 의존성 / 도입 위험

- **pykrx 의존성**: 이미 봇 다른 모듈에서 사용 중 (post_trade_analyzer/price_tracker.py 폴백 패턴 확인)
- **KIS API 토큰 한도**: 1분 1회 발급 제한, ATR 폴백 호출 시 토큰 공유 → prefetch 09:20 + 0.5초 타임아웃 + 캐시
- **MarketGuard 5분 캐시**: 신규 캐시 도입, 모니터 루프 부담 최소화

---

## 다음 액션 (Phase 2 후속 검토)

- 4주 실전 데이터 누적 후 `/improve` 호출 → 효과 측정
- 측정 지표: 1차 손절 손실 절대액 감소, 2차 진입 종목 trend 유효성, 익절 임계 발화 빈도, ATR 효과
- Phase 2 항목: 3분할, 추세 확정 조건, ATR 일일 갱신, 백테스트 매핑
