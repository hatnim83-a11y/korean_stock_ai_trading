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

---

## 작업 중 발견 사항 (2026-05-23 ~ 2026-05-28 세션)

### 2026-05-23 (토) — v17 전면 활성화 + 종가베팅 충돌 hotfix
**활성화 절차 완료**:
- 워크트리 commit `6be6193` → main 머지 `21e6561` → push 완료
- `.env`에 `TRANCHE_ENTRY_ENABLED=true / DRY_RUN_PYRAMID=false` 2줄 추가 → 전면 활성화
- 봇 + 대시보드 restart 완료, DB v17 마이그레이션 자동 실행 + 백필 정확 (셀트리온/심텍 `pending=0`)
- bot-health-checker **GO 판정** (심각/주의 0건)

**종가베팅 충돌 hotfix (commit `3325f07`)**:
- 사용자 지적: 종가베팅 `closing_bet_pool_cap=0.5` + `absorb_swing_idle=true` 활성 → v17 2차 진입과 자본 충돌 가능
- fund_guard 정밀 분석: 종가베팅 한도 = `min(10% + swing_idle, 50%)`
- 충돌 시나리오: v17 2차가 swing_pool 한도 미적용이라 종가베팅 풀(50%) 잠식 → phase2 fund_guard 차단 또는 KIS 가용현금 부족
- **가드 A** (시간 차단, line 1394~): 15:15 이후 v17 2차 진입 차단 (종가베팅 phase1=15:18 직전 안전)
- **가드 B'** (자본 한도, line 1462~): `swing_pool = total × SWING_CAPITAL_RATIO(0.9)` / `swing_used = Σ(first × shares)` (fund_guard cost_basis 식) → target_amount 축소
- 신규 테스트 2건 추가 (swing_pool_calculation + time_guard) → 15/15 + 회귀 10/10 PASS

### 2026-05-27 (수) — Critical 버그 발견 + 1차 hotfix
**5/27 삼현(437730) 케이스 실증**:
- 09:05 매수 9주 @61,500원 → DB에 `second_tranche_pending=1, atr_at_buy=4992.86` 정확 INSERT
- 09:26 모니터 재시작 → `load_positions_from_db` → `add_position()` 호출
- **버그**: `add_position()` 시그니처가 v17 필드 7개 안 받음 → Position default 값(`pending=False, atr=0.0`)
- `_check_and_execute_pyramid_in()` 1단계 가드 `if not pos.second_tranche_pending: return False` → 즉시 차단
- 10:24:25 max_profit=+8.46% 도달 → 2차 진입 0건 → L1 트레일링 매도 (+3.90%)
- **영향**: 사용자 의도 ②(리스크 축소, 1차 50% 진입)는 정상 작동, ①(수익 끌기)만 무력화. 손실 없음
- 5/25~5/27 모든 신규 매수에서 동일 버그 (v17 2차 진입 100% 미발화 + ATR 트레일링 효과 0)

**1차 hotfix (commit `082f9e3`)**:
- `add_position()` 시그니처에 v17 필드 7개 추가 (Optional/default 안전)
- `load_positions_from_db()`에서 `item.get()` 안전 폴백으로 v17 컬럼 전달
- 신규 테스트 2건 (`add_position_v17_fields_restored` + `add_position_v17_defaults_safe`) → 17/17 PASS

### 2026-05-28 (목) — code-tester 검증 + 옵션 A 추가 hotfix
**code-tester Partial 판정** — 핵심 fix 완료, 주의 1건 잠재 위험:
- 주의 1번 (핵심): `position_state` 테이블 DDL에 v17 컬럼 없음 → `_restore_trailing_state` DB source 경로에서 `s.get("second_tranche_pending", False)` 항상 False 반환 → add_position 복원값 덮어쓰기 위험
- 주의 2번: `_close_position_in_db` profit_amount 계산이 first 기준 → avg 기준 대비 ~5만원 bias (통계 정확성, 거래 안전 무관)
- 참고 4건: 테스트 커버리지 + 코드 가독성

**옵션 A 즉시 적용 (commit `2d95090`)**:
- `_restore_trailing_state` v17 복원 블록(line 632~660) 키 존재 체크 패턴으로 변경
- `"키 in s and s.get(키) is not None"` → position_state v17 키 부재 시 덮어쓰기 skip
- JSON 폴백 경로는 v17 키 dump됨 → 정상 덮어쓰기
- 신규 테스트 2건 (`db_source_v17_key_missing` + `json_v17_key_present`) → **19/19 PASS** + 회귀 10/10 PASS

### 핵심 판단 근거
- 사용자가 일괄 도입 + 즉시 활성화 결정 → 권장 dry-run 1주 skip
- 사용자가 종가베팅 50% cap 정책을 정확히 지적 → 가드 A+B' 동시 적용 결정
- 5/27 critical 버그는 사용자 지적("불타기 발동 안 됨")으로 발견 → 데이터 분석으로 정확한 원인 파악
- 옵션 A는 임시방편이지만 5/28 안전 보장 → DB v18 마이그레이션은 후속 작업으로 미룸

---

## 🔵 다음 세션 후속 작업 (우선순위 순)

### 1순위: 5/28 첫 매수 발화 검증 (필수, 자동 발화)
**시점**: 2026-05-28 (목) 또는 다음 영업일 09:25 매수 발화 후

**검증 항목**:
- 텔레그램 알림 `🟢 매수 완료 — 1/2 진입 (50%) | ATR=X` 표시 확인
- DB `portfolio` 신규 row의 `second_tranche_pending=1`, `atr_at_buy>0` 확인
- 모니터 로그 `포지션 추가: ... (tranche=1, pending=True, atr=XXXX.XX)` 확인
- 보유 중 +5% 도달 시 `🔥 2/2 불타기 진입` 알림 발생 여부

**확인 명령어**:
```bash
sudo journalctl -u trading_system --since "2026-05-28 09:00" --no-pager | grep -E "포지션 추가|pyramid|불타기"
sqlite3 -header /home/hatni/korean_stock_ai_trading/data/trading.db \
  "SELECT stock_code, stock_name, buy_date, first_buy_price, avg_buy_price, tranche_count, second_tranche_pending, atr_at_buy FROM portfolio WHERE buy_date >= '2026-05-28' ORDER BY id DESC LIMIT 5"
```

**실패 시**: 1차 매수 직후 → 09:26 재시작 → 다시 같은 버그 발생 가능. 즉시 추가 조사. monitor add_position 호출 시점에 v17 필드 어디서 누락되는지 추적.

---

### 2순위: DB v18 마이그레이션 — 옵션 A 임시방편 → 완전 fix (Medium 우선순위)
**배경**: code-tester 주의 1번의 근본 해결 (옵션 A는 임시 덮어쓰기 방지만)

**구현 단계**:
1. `database.py:_migrate_v18()` 신규 — `position_state` 테이블에 v17 컬럼 3개 ADD COLUMN
   - `second_tranche_pending BOOLEAN DEFAULT 0`
   - `atr_at_buy REAL DEFAULT 0`
   - `tranche_count INTEGER DEFAULT 1`
2. `database.py:upsert_position_state()` — 3개 키 INSERT/UPDATE에 추가
3. `portfolio_monitor_v2.py:_dump_monitor_state()` — DB upsert dict에 v17 키 전달
4. `portfolio_monitor_v2.py:_restore_trailing_state()` — DB source 경로 옵션 A 패턴 유지 OR 단순화 (DB에 키 보장되므로 키 존재 체크 불필요)
5. 백필 SQL: 기존 position_state 행에 portfolio 테이블에서 v17 값 복사
6. 신규 테스트: DB v18 마이그레이션 idempotent + position_state v17 round-trip 검증

**예상 소요**: 30분~1시간

**rollback 위험**: 낮음 (옵션 A 잠재 위험 제거 + 양방향 동기화 완전)

---

### 3순위: profit_amount avg 기준 fix (code-tester 주의 2번)
**위치**: `modules/trading_engine/portfolio_monitor_v2.py:_close_position_in_db()` (line 988-989, 1061)

**현재 코드**:
```python
actual_profit_rate = (sell_price - pos.buy_price) / pos.buy_price * 100
actual_profit_amount = (sell_price - pos.buy_price) * shares
```

**변경**:
```python
# v17 정책 분기: 손익률 보고 = avg_buy_price 기준
ref_price = pos.avg_buy_price if pos.avg_buy_price > 0 else pos.buy_price
actual_profit_rate = (sell_price - ref_price) / ref_price * 100
actual_profit_amount = (sell_price - ref_price) * shares
```

**영향**: 2차 진입 완료 종목 매도 시 trade_reviews/daily_snapshots 손익 통계 ~5만원 단위 정확성 향상. 거래 안전 무관.

**검증**: 1주 실전 데이터 후 권장 (운영 영향 측정)

---

### 4순위: 보유 종목 ATR 백필 (참고급, 기존 holding 한정)
**배경**: v17 활성화 이전 매수된 보유 종목은 `atr_at_buy=NULL/0.0` → 트레일링 ATR 효과 0 (고정값 폴백)

**대응**:
- 5/25 머지 이전 holding (셀트리온/심텍)은 5/25 이미 매도됨 → 영향 없음
- 단 향후 v18 마이그레이션 시 portfolio에 atr_at_buy NULL인 holding 종목이 있으면 일괄 백필 SQL 실행 권장
- 또는 09:20 prefetch 잡 도입 시 보유 종목까지 일괄 계산

---

### 5순위: 09:20 ATR prefetch 잡 (Phase 2, 권장)
**배경**: 현재 매수 시점에 동기 ATR 계산 (0.5초 타임아웃). 09:25 매수 직전 09:20에 prefetch하면 캐시 hit + 동기 호출 부담 0

**구현 단계**:
1. `scheduler.py`에 `prefetch_atr_for_candidates` 09:20 잡 등록
2. 후보 종목 (오늘의 AI 분석 결과 또는 보유 종목) 5~10개에 `compute_atr_batch` 비동기 일괄 호출
3. 캐시 채움 → 09:25 매수 시점에 캐시 hit으로 즉시 박제

**예상 소요**: 1~2시간

---

### 6순위: ATR 일일 재계산/갱신 (Phase 2, Low 우선순위)
**배경**: 현재 매수 시점 박제만 → 보유 중 변동성 변화 미반영

**대응 옵션**:
- A: 매일 16:10 헬스체크 잡에서 보유 종목 ATR 갱신
- B: 5거래일 마다 갱신
- 우선순위: Low — 박제 정책으로 안정 운영하고 4주 데이터 누적 후 효과 측정 결정

---

### 7순위: 4주 실전 데이터 누적 후 효과 측정 (필수 검증, 자동 발화)
**시점**: 2026-06 중순

**측정 지표**:
- 1차 손절 손실 절대액 감소 (vs v17 이전 기준선)
- 2차 진입 종목 trend 유효성 (2차 발화 종목의 7일 내 익절 발동 비율 ≥ 30%)
- 익절 +12/+20/+30%(avg) 발화 빈도 vs 기존 +10/+15/+20%
- ATR 트레일링 효과 (atr_pct > 고정값 종목의 매도 시점 정확성)

**도구**: `/improve weekly` 또는 `/improve monthly` 호출

---

### 8순위 (선택): active → completed 아카이브 (1주 검증 통과 후)
- `docs/work-plans/active/tranche-entry-pyramid/` → `docs/work-plans/completed/20260528_tranche-entry-pyramid/` 이동
- 또는 1~2주 추가 모니터링 후

---

## ⚠️ 다음 세션 시작 시 주의사항

1. **봇 가동 상태 먼저 확인** — PID `pgrep -f "main.py --real"` + `systemctl is-active trading_system`
2. **DB 컬럼 확인** — `PRAGMA table_info(portfolio)` 또는 `PRAGMA table_info(position_state)` (v18 마이그레이션 여부 체크)
3. **`.env` 토글 상태 확인** — `grep -E "TRANCHE_ENTRY_ENABLED|DRY_RUN_PYRAMID|SWING_CAPITAL_RATIO" .env`
4. **최근 매수/매도 확인** — `trade_reviews` + `portfolio` 조회로 5/28 이후 신규 매수 종목 추적
5. **5/27 삼현 케이스 재발 여부** — 신규 매수 시점에 `tranche_count=1, pending=1, atr>0` 확인 필수
6. **컨텍스트 크기 주의** — 이번 세션 매우 길어서 새 대화 시작 권장
   - `/resume` 명령으로 본 CONTEXT.md + PLAN.md + CHECKLIST.md 로드하여 이어가기

---

## 📦 본 세션 최종 commits (origin/main 동기화)

```
ca0747c Merge branch 'worktree-tranche-entry-pyramid' (5/28 옵션 A)
2d95090 fix: v17 hotfix 옵션 A — _restore_trailing_state DB source 덮어쓰기 방지
78d2ea0 Merge branch 'worktree-tranche-entry-pyramid' (5/27 critical hotfix)
082f9e3 fix: v17 critical hotfix — add_position v17 필드 누락 복구
78f31a4 docs: 2026-05 분석 제안서 3건 추가
761d1af docs(improvements): 2026-05 분석 제안서 3건 추가
e930fec Merge branch 'worktree-tranche-entry-pyramid' (5/23 종가베팅 hotfix)
3325f07 fix: v17 hotfix — 종가베팅 충돌 가드 B' + A 추가
6be6193 feat: v17 분할 진입(50:50) + 불타기(+5%) + 익절 avg + ATR 트레일링
```

**테스트 상태**: 19/19 PASS + 회귀 monitor_state_residue 10/10 PASS
**봇 상태**: active (PID 376395, 2026-05-28 01:21:21 restart)
**대시보드**: active (PID 2673738)
**다음 발화 대기**: 다음 영업일 09:25 매수 (v17 + 2 hotfix + 옵션 A 적용 상태)
