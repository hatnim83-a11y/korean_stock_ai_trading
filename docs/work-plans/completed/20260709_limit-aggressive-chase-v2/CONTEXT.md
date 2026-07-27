# CONTEXT — limit_aggressive 추격 B안

## 현재 코드 상태
- `modules/trading_engine/trading_engine.py:423` `_compute_aggressive_limit_price(stock_code, fallback_price)`
  - `raw_price = _add_ticks(ask1, ticks)` (ask1 + N틱)
  - `cap_pct = LIMIT_AGGRESSIVE_MAX_CHASE_PCT(0.005)`
  - `cap_price = _floor_to_tick(fallback_price*(1+cap_pct), tick)`
  - `if price > cap_price: price = cap_price; capped=True`  ← **문제 지점**
  - `self._last_aggressive_quote` dict 세팅 후 `(price, source)` 반환
- `modules/trading_engine/trading_engine.py:533` `_place_aggressive_limit_with_retry`
  - `bid_price, src = self._compute_aggressive_limit_price(...)` (581)
  - `if bid_price <= 0: last_error_msg="가격 산출 실패..."; break` (592)
  - 결과 dict: quote_samples[-5:], inferred_reason, limit_price_min/max, message (727~750)
- 헬퍼: `_krx_tick_size`(43) / `_floor_to_tick`(60) / `_add_ticks`(66)
- config: `config.py:257` `LIMIT_AGGRESSIVE_MAX_CHASE_PCT=0.005`

## 핵심 스니펫 (교체 대상)
```python
cap_pct = float(getattr(settings, "LIMIT_AGGRESSIVE_MAX_CHASE_PCT", 0.0) or 0.0)
capped = False
price = raw_price
if fallback_price > 0 and cap_pct > 0:
    tick = _krx_tick_size(raw_price)
    cap_price = _floor_to_tick(int(fallback_price * (1 + cap_pct)), tick)
    if cap_price > 0 and price > cap_price:
        price = cap_price
        capped = True
```

## B안 산식 (fallback_price = expected/기준가)
- `gap_pct = (ask1 - fallback_price) / fallback_price` — 과열 판정용
- `base_cap_price = floor_to_tick(fallback_price * (1 + 0.005))`
- `cap_gap_pct = (ask1 - base_cap_price) / base_cap_price` — 동적 추격 발동 판정용
- `dyn_cap_price = floor_to_tick(base_cap_price * (1 + 0.012))`
  - 중요: dynamic cap은 fallback이 아니라 **base cap 기준 확장**이다. 오늘 KT처럼 기존 주문가(base cap)가 54,500원인데 ask1이 55,100원인 경우를 살리기 위함.
- 판정 순서:
  1. `gap_pct >= 0.018` → overheat: `price=0`, source `:overheat`
  2. `raw_price <= base_cap_price` → 정상 (기존)
  3. `raw_price > base_cap_price` & `cap_gap_pct <= 0.012` → dynamic
     - `price = min(raw_price, dyn_cap_price)` (초과 시 dyn_capped)
  4. `raw_price > base_cap_price` & `cap_gap_pct > 0.012` → base cap 고정 (:capped, 기존)

## 오늘 사례 수치 검증
- KT 재현: fallback 54,250 → base_cap 54,500 / ask1 55,100 → cap_gap 1.10%(≤1.2%) → dynamic, dyn_cap 55,100 → 주문가 55,100 (> base cap ✓)
- KT ask1 55,000 → 주문가 55,100(dyn_capped)로 기존 54,500 반복보다 개선
- SKT 재현: fallback 86,200 → base_cap 86,600 / ask1 87,000 → cap_gap 0.46%(≤1.2%) → dynamic, 주문가 87,200 (> base cap ✓)
- 과열: expected 100,000 / ask1 102,000 → gap=2.0%≥1.8% → overheat, 주문 미발주

## 과거 관련 버그/주의
- source 라벨은 diagnostics/로그에 노출됨 → 기존 ":capped" 케이스 유지 필요
- dyn_cap_pct=0 이면 dynamic 분기 skip → base cap 동작(롤백 안전)
- overheat 시 retry 루프는 order 미발주로 fast-break → retry_count 0

## 영향 범위
- 매수 실행 경로 한정. 매도/모니터/종가베팅 무관.
- config 신규 3키는 기존 dirty(config.py Claude bridge, 1072행대)와 물리적으로 분리(257행대).
