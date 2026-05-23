# CONTEXT — 자본 배분 동적 분리

## 변경 이유
종가베팅 5/25 실발주 활성화와 함께 자본 분리 명시화. 현재 비대칭(스윙 무제한, 종가베팅 10%만) → 동적 분리.

## 현재 코드 상태

### 스윙 자본 배분 (main.py:1340)
```python
max_per_stock = int(total_capital) // settings.MAX_POSITIONS  # 총자본 ÷ 5 = 20%/종목
per_slot_capital = min(available_cash // available_slots, max_per_stock)
```
- `total_capital`: KIS `get_balance()['total_value']`
- `MAX_POSITIONS=5`, 스윙 한도 강제 없음
- TRANCHE 활성 시 1차에 50%만 사용 (`TRANCHE_FIRST_RATIO=0.5`)

### 종가베팅 fund_guard (fund_guard.py:147-148)
```python
capital_limit = int(total_value * cfg.capital_ratio)  # 10% 고정
per_stock_limit = int(capital_limit * cfg.max_position_per_stock)  # × 0.25
```

### 종가베팅 entry_executor (entry_executor.py:573-584)
```python
base = total_value * capital_ratio * max_position_per_stock
# × position_ratio(0.7) × phase1_ratio(0.5) × market_guard_mult
```
- fund_guard와 entry_executor가 **두 경로로 한도 계산** (S-2 위험)

### portfolio 테이블 스키마 (data/trading.db)
- `quantity`, `buy_price` 컬럼 있음
- `current_price` 컬럼 **없음** (S-3 위험) → cost_basis 모드가 안전

### position_state 테이블 (data/trading.db, v8+)
- 트레일링 상태 박제 + 현재가 (없을 수도 있음)
- evaluation 모드 시 JOIN 필요

## 핵심 함수 재사용

| 함수 | 파일 | 용도 |
|---|---|---|
| `get_portfolio()` | `database.py:1076` | 스윙 보유 dict |
| `get_swing_holding_codes()` | `closing_bet_system/infra/swing_db_reader.py:66` | 스윙 ticker set (참고 패턴) |
| `get_total_account_value()` | `closing_bet_system/infra/kis_client.py:76` | KIS 총자산 |
| `allow_order()` | `closing_bet_system/infra/fund_guard.py:116` | 자금 한도 게이트 |

## 알고리즘

### 동적 capital_limit 계산
```
base_pool = total × 0.10
IF absorb_swing_idle=False OR (external_risk_active AND disable_absorb_on_crisis):
    RETURN base_pool
swing_pool = total × 0.9
swing_used = get_swing_used_value(source=cost_basis|evaluation)
swing_idle = max(0, swing_pool - swing_used)
cap_amount = total × 0.5
RETURN min(base_pool + swing_idle, cap_amount)
```

### per_stock_limit (4종목 등분)
```
per_stock_limit = capital_limit // cfg.max_concurrent_positions   # 하드코딩 4 X
```

### 진입 사이즈 (entry_executor)
```
amount = per_stock_limit × position_ratio(0.7) × phase1_ratio(0.5) × market_guard_mult
```

## 시나리오 예시 (총자본 10,000,000원)

| 시나리오 | 스윙 사용 | swing_idle | 종가베팅 풀 | 1종목 |
|---|---|---|---|---|
| 스윙 0건 | 0 | 9,000,000 | min(1,000,000 + 9,000,000, 5,000,000) = **5,000,000** | 1,250,000 |
| 스윙 50% | 4,500,000 | 4,500,000 | min(1,000,000 + 4,500,000, 5,000,000) = **5,000,000** | 1,250,000 |
| 스윙 89% | 8,000,000 | 1,000,000 | min(1,000,000 + 1,000,000, 5,000,000) = **2,000,000** | 500,000 |
| 스윙 100%+ (수익) | 10,000,000 | 0 | min(1,000,000 + 0, 5,000,000) = **1,000,000** | 250,000 |
| CRISIS 흡수 차단 | - | - | 1,000,000 (base만) | 250,000 |

## 위험 / 주의

### 두 경로 한도 계산 (S-2)
- 현재 fund_guard.allow_order vs entry_executor._compute_order_amount 독립 계산
- **해결**: FundGuard.compute_capital_limit() SoT 신설, entry_executor 호출만

### portfolio.current_price 없음 (S-3)
- swing_used_source='cost_basis' 기본: `SUM(quantity × buy_price)`
- 'evaluation' 모드: position_state JOIN + COALESCE 폴백

### swing_used > swing_pool (DC-8)
- 수익 누적 시 평가액이 풀 초과 가능
- `swing_idle = max(0, ...)` 방어

### settings 키 누락 (DC-9)
- `from_settings()` `.get(key, cls.default)` 폴백 적용

### CRISIS 흡수 역설 (planner 심각 2)
- KOSPI -2% 시 스윙 미사용 → 종가베팅이 cap=50% 흡수 = 위험 증폭
- 해결: `external_risk_active=True` 시 absorb 비활성 (base만)

### per_stock_limit 하드코딩 (S-1)
- `capital_limit // 4` → `capital_limit // cfg.max_concurrent_positions`

## 단위 테스트 매트릭스 (12건)

| ID | 시나리오 | 기대 결과 |
|---|---|---|
| DC-1 | 스윙 0% 사용 | cap=50% 도달 |
| DC-2 | 스윙 100%+ 사용 | base 10%만 |
| DC-3 | 스윙 50% 사용 | 10% + 40% = 50% (cap 미적용) |
| DC-4 | absorb_swing_idle=false | 기존 10% 고정 |
| DC-5 | 후보 5건 → top 4 진입 | 상위 4건만 |
| DC-5-B | 후보 2건 → LIMIT 4 가드 | 2건 반환 |
| DC-6 | 빈 portfolio | swing_used=0 |
| DC-6-B | 스윙 DB 파일 없음 | fund_guard 폴백 swing_pool |
| DC-7 | SoT 동기화 | fund_guard == entry_executor |
| DC-8 | swing_used > swing_pool | swing_idle=0 |
| DC-9 | settings 키 누락 | 기본값 사용 |
| DC-10 | external_risk_active=True | absorb 비활성 (base만) |

## 영향 범위
- 종가베팅 fund_guard 한도 동적 (10%→최대 50%)
- 스윙 한도 명시화 (100%→90%)
- 4종목 강제 진입 (현재 dry-run 1~2종목 → 다양성 확보)
- T+1 매도 환원: 로그만, 로직 변경 없음

## 5/24 작업 시점 표

| 시각(KST) | 작업 |
|---|---|
| 12:00 | Phase 0 ✅ + 3문서 작성 |
| 13:00 | Phase A 설정 + 헬퍼 |
| 14:00 | Phase B SoT |
| 15:00 | Phase C entry_executor |
| 16:00 | Phase D 스윙 + exit |
| 17:00 | Phase E 단위 테스트 |
| 18:00 | Phase F code-tester + 회귀 |
| 19:00 | Phase G commit + push |
| 22:30 | systemctl restart |
