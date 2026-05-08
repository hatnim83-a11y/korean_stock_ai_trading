# CONTEXT: 매도 슬리피지 측정 시스템

## 변경 이유
- 2026-05-01 monthly 분석에서 매도 55건 중 52건(95%) `slippage` NULL 식별
- 측정된 3건도 0.0 (시장가 매도 → `order_price=0`이 분모)
- 공격적 지정가 매수 효과 평가의 매도 측 카운터파트 미확보 → Phase 5 실전 관찰의 누락 지표

## 영향 범위
- **DB 변경 없음** (slippage 컬럼은 v9에서 이미 존재, 단순 쓰기만 추가)
- **읽기 측 영향 없음** (대시보드/리포트는 slippage NULL을 이미 처리하고 있을 가능성 높음)
- **신규 API 호출**: 매도 1건당 `inquire_asking_price()` 1회 (일 평균 매도 < 5건)

## 현재 코드 상태

### 매도 5경로 매핑

**경로 1: 손절 (stop_loss)**
- 트리거: `portfolio_monitor_v2.py:896` `_execute_stop_loss(pos)`
- 주문 실행: `trading_engine.execute_stop_loss(position, current_price)` → `sell_market_order` (line 730)
- DB 기록: `_close_position_in_db(pos, reason, sell_price)` (line 916) → `db.save_trade({..., "buy_price", "filled_price", "remaining_shares": 0, ...})` (slippage 미포함)

**경로 2: 분할 익절 (take_profit / partial)**
- 트리거: `portfolio_monitor_v2.py:975` `_execute_partial_sell(pos, sell_shares, stage, profit_rate)`
- 주문 실행: `trading_engine.execute_take_profit(position, current_price)` → `sell_market_order` (line 781)
- DB 기록 분기:
  - 전량 매도 시: `_close_position_in_db(pos, reason, actual_sell_price, sell_shares)` (line 1029)
  - 부분 매도 시: `_save_partial_sell_to_db(pos, sell_shares, stage, actual_sell_price)` (line 1032) → 직접 db.save_trade

**경로 3: 보유기간 매도 (max_hold)**
- 트리거: `main.py:2027` `run_hold_period_sells()` (09:15 스케줄)
- 주문 실행: `trading_engine.execute_sell_orders(orders, save_to_db=False)` (line 2131)
- DB 기록: main.py에서 직접 `db.save_trade({..., "remaining_shares": 0})` (line 2157) → trade_id 캡처 → `_save_trade_review_for_main_sell` (line 2172)

**경로 4: midweek 수익 매도**
- 트리거: `main.py:2221` `_execute_midweek_profit_sells()` (09:00 스케줄)
- 주문 실행: `execute_sell_orders(save_to_db=False)` (line 2262)
- DB 기록: main.py 직접 `db.save_trade` (line 2285) → review (line 2303)

**경로 5: midweek 손실 매도**
- 트리거: `main.py:2334` `_execute_midweek_loss_sells()` (09:10 스케줄)
- 주문 실행: `execute_sell_orders(save_to_db=False)` (line 2374)
- DB 기록: main.py 직접 `db.save_trade` (line 2396) → review (line 2414)

### 핵심 코드 스니펫

**`trading_engine.py:879-886` 슬리피지 식 (매수만 정상, 매도는 분모 빔)**
```python
slippage = None
if is_sell and filled_price and order_price:
    slippage = round((filled_price - order_price) / order_price * 100, 4)
elif not is_sell and filled_price and expected_price:
    slippage = round((filled_price - expected_price) / expected_price * 100, 4)
```
- 매도는 `order_price`(=주문가)가 시장가일 땐 0이라 항상 None 또는 0 반환

**`trading_engine.py:684` 매도 진입 (시장가)**
```python
result = self.order_api.sell_market_order(stock_code, quantity)
```
- 이 직전에 `inquire_asking_price(stock_code)` 호출 추가 필요
- result에 `reference_price` + `slippage` 채워야 함

**`trading_engine.py:755, 806` 손절/익절 매도 진입**
```python
result = self.order_api.sell_market_order(stock_code, quantity)  # line 755 (stop_loss)
# ... 1초 대기 후 체결가 조회 ...
result = self.order_api.sell_market_order(stock_code, quantity)  # line 806 (take_profit)
```
- 동일 패턴 — `sell_market_order` 직전 호가 캡처

**`portfolio_monitor_v2.py:736-749` `_close_position_in_db` save_trade 호출**
```python
trade_id = db.save_trade({
    "stock_code": pos.stock_code,
    ...
    "buy_price": pos.buy_price,
    "filled_price": sell_price,
    "remaining_shares": 0,
})
```
- slippage 필드 누락 → 함수 시그니처에 `slippage` 추가 + dict에 포함

**`portfolio_monitor_v2.py:803-816` `_save_partial_sell_to_db` 동일 구조**

**`main.py:2157` 보유기간 매도 save_trade**
```python
trade_id = self.db.save_trade({
    "stock_code": code,
    "stock_name": ...,
    "action": "sell",
    "shares": qty,
    "price": filled_price,
    ...
    "buy_price": buy_price,
    "filled_price": filled_price,
    "remaining_shares": 0,
})
```
- midweek 2경로도 거의 동일 → `order.get("slippage")` 추출 후 dict에 포함

### 이미 존재하는 자원
- **`kis_order_api.inquire_asking_price(stock_code)`** (line 819, 2026-04-16 도입) → `bid1`, `current_price` 등 반환
- **`MockOrderApi.inquire_asking_price`** (line 1123) → 시뮬레이터 존재
- **DB `trades.slippage` 컬럼** (v9, REAL nullable)
- **`db.save_trade()` 시그니처** (database.py:1058) → `trade.get('slippage')` 이미 인자 받음 (database.py:1091) — DB 측 변경 불필요

## 과거 버그/주의사항
- **`save_trade()`의 `Optional[int]` 반환**: trade_id를 받아 trade_review FK로 연결해야 함. main.py 3경로에 이미 적용됨 (2026-05-01 fix).
- **호가 조회 실패 시 폴백**: `inquire_asking_price`가 timeout/error 시 success=False 반환 → 분기 처리 필수
- **부분 체결 시 `filled_price`**: 시장가는 보통 즉시 전량 체결이지만, 만에 하나 부분 체결 시 trading_engine의 `_wait_for_fills`가 처리 — slippage는 가중평균 체결가 기준
- **Mock vs Real 모드**: `use_mock_api=True`일 때 `inquire_asking_price`가 mock 가격 반환하는지 확인 필요

## 검증 시 주의
- `trading_engine._wait_for_fills`(매도용)가 result에 filled_price를 채우는 로직 있음 → reference_price는 그 전에 캡처해야 함 (sell_market_order 호출 직전)
- `execute_sell_orders`는 orders 루프 내에서 매 종목마다 reference price 재캡처 필요 (호가는 종목별로 다름)
- 이상치 임계 |slippage| > 2%: 시장가 매도라도 호가가 안정적이면 -0.5% 내외가 정상. 2% 초과는 갭다운/유동성 부족 의심

## 호환성
- `_close_position_in_db` / `_save_partial_sell_to_db` 신규 인자 `slippage=None` default → 기존 호출처 안 건드려도 기존 동작 유지
- main.py의 db.save_trade dict에 slippage 키 추가만 하면 됨
- 5경로 중 어느 하나라도 누락 시에도 NULL 저장 (이전과 동일 동작)
