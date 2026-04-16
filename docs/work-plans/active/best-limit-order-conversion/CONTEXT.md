# CONTEXT: 최유리지정가 전환 배경 및 함정

## 변경 이유

**문제**
- 시장가(`ORD_DVSN=01`) 주문 시 KIS 서버가 **상한가(전일종가 × 1.30) × 수량** 만큼 증거금을 차감
- 슬롯당 이론 상한 대비 실제 투자금이 `1/1.3 = 76.9%`로 축소
- CASH_BUFFER 0.05까지 합치면 실투자 비율 `0.95 / 1.3 = 73.1%`
- 2026-04-16 LG디스플레이 건: 슬롯 상한 1,828,794원 → 실매수 1,240,800원 (67.9%)

**해결**
- 최유리지정가(`ORD_DVSN=03`)는 반대편 1호가 즉시 체결되는 지정가 계열
- 증거금 요구: 주문가 × 수량 × 1.0 (안전 마진 1.02로 설정)
- 실투자 비율 `0.95 / 1.02 = 93.1%` — **종목당 +27.5% 자본 추가 투입 가능**

## 현재 코드 상태 (2026-04-16 조사 기준)

### 주문 유형 상수 (`modules/trading_engine/kis_order_api.py:60-62`)
```python
ORDER_TYPE_MARKET = "01"       # 시장가
ORDER_TYPE_LIMIT = "00"        # 지정가
ORDER_TYPE_CONDITIONAL = "02"  # 조건부지정가
# ORDER_TYPE_BEST_LIMIT = "03" ← 신규 추가 필요
```

### 매수 경로 진입점 (`modules/trading_engine/trading_engine.py:221`)
```python
# ⚠️ 함정: price == 0 조건이 있어 최유리지정가(price=0)도 시장가로 처리됨
if order_type == "market" or price == 0:
    result = self.order_api.buy_market_order(stock_code, quantity)
else:
    result = self.order_api.buy_limit_order(stock_code, quantity, price)
```
→ **`or price == 0` 제거 필요**. 최유리지정가 경로 분기 추가.

### 증거금 사전검증 (`modules/trading_engine/trading_engine.py:201-216`)
- 현재 시장가에만 적용 (`UPPER_LIMIT_RATIO = 1.3`)
- 지정가 경로는 검증 없음 → 최유리지정가용 `BEST_LIMIT_MARGIN_RATIO = 1.02` 추가 후 3갈래 분기

### KIS API 유틸 (재사용 가능)
- `_place_order()` — kis_order_api.py:400-496. ORD_DVSN만 바꿔 호출 가능
- `cancel_order(order_id, stock_code, qty)` — kis_order_api.py:500-561. 부분취소 지원
- `get_order_status(order_id=...)` — kis_order_api.py:565-650. tot_ccld_qty/avg_prvs 제공
- `_rate_limit()` — kis_order_api.py:146. 60ms 간격, 초당 16회 여유

### 기존 재시도 로직 (`modules/trading_engine/trading_engine.py:219-258`)
- `MAX_RETRY = 3`, `RETRY_QTY_REDUCTION = 0.9`
- "주문가능금액 초과" 에러에만 반응, **미체결 관리는 없음**

### 매도 경로 체결가 확인 (`modules/trading_engine/trading_engine.py:426-436`)
```python
# time.sleep(1) + get_order_status(order_id) 패턴
# → 취소 후 확정 조회에 동일 패턴 재사용
```

### Order dict 스키마 (`modules/portfolio_optimizer/optimizer.py:313-320`)
```python
{
    "stock_code", "stock_name", "order_type"("market"|"limit"),
    "quantity", "price", "expected_price",  # expected_price는 스크리닝 시점 가격
    "amount", "stop_loss", "take_profit", "theme", "final_score"
}
```

### DB 스키마 (v12, `data/trading.db`)
- `trades.filled_price`, `trades.slippage`, `trades.remaining_shares` 컬럼 존재
- 현재 매수에서는 활용 X (매도 시만 slippage 기록)
- **마이그레이션 불필요** — 기존 컬럼 그대로 사용

## 핵심 함정 (구현 시 반드시 회피)

### 1. `price == 0` 시장가 분기 (위험 ★★★)
**위치**: `trading_engine.py:221`
**문제**: 최유리지정가도 `ORD_UNPR="0"`이므로 price=0 → 조건문에서 시장가로 라우팅됨
**해결**: `or price == 0` 제거하고 `order_type`만으로 분기

### 2. 부분체결 누적 평균단가 (위험 ★★★)
**문제**: 재시도마다 새 order_id 발급 → 각 order_id별 체결량/체결가 합산 필요
**해결**: 가중평균 계산
```python
total_qty = Σ filled_qty_per_order
total_cost = Σ (filled_qty × avg_prvs)_per_order
weighted_avg = total_cost / total_qty
```
**DB 저장**: `filled_price`에 가중평균 기록, `price`에도 동일값, `slippage = (가중평균 - expected_price) / expected_price × 100`

### 3. cancel_order 비동기성 (위험 ★★★)
**문제**: 취소 요청 직후에도 100~500ms 지연 동안 추가 체결 가능 → 재조회 없이 재주문하면 초과 매수
**해결**:
```python
cancel_order(...)
time.sleep(0.5)  # 취소 반영 대기 필수
status = get_order_status(order_id)  # 최종 체결량 재확인
actual_filled = status.filled_qty
```

### 4. 이미 전량 체결 후 취소 요청 (위험 ★★)
**문제**: KIS는 "취소가능수량 초과" 에러 반환 — **실패가 아닌 정상 흐름**
**해결**: `rt_cd != 0` + 에러 메시지에 "취소가능수량" 포함 시 성공 처리, 루프 종료

### 5. 체결량 = 0 DB 중복 기록 (위험 ★★)
**문제**: 현재 `_save_trades`의 `filled_price or order_price` 폴백으로 0원 매수 기록 가능
**해결**: `if quantity > 0` 가드 추가, 0건은 DB skip + 텔레그램 경보

### 6. ORD_UNPR 전달값 (위험 ★★)
**문제**: 최유리지정가는 반드시 `"0"`. 다른 값 → 호가단위 불일치 등 거부
**해결**: `buy_best_limit_order()`에서 항상 price=0 강제

### 7. 레이트리밋 + 포트폴리오 모니터링 충돌 (위험 ★)
**문제**: 재시도 루프가 초당 빈번한 API 호출 시 `portfolio_monitor_v2` 30초 주기와 경쟁
**해결**: 폴링 주기 3초 (10초 내 3회) + `_rate_limit` 60ms로 직렬화

### 8. `price == 0` 시장가 조건의 숨은 의존성
**위치**: kis_order_api.py 내부 여러 지점 + trading_engine.py:221
**확인**: `price == 0` 기반 시장가 감지 로직을 모두 `order_type == "market"`로 교체

### 9. asyncio 호환성 (위험 ★)
**현재**: main.py가 `asyncio.to_thread(execute_portfolio)` 사용 → 내부 `time.sleep`는 이벤트 루프 블로킹 X
**주의**: 5종목 × 최악 120초 = 10분 소요 가능. 다음 스케줄 잡(09:30+)과 충돌 없는지 확인

### 10. 호가단위(틱) 자동 처리
**보증**: 최유리지정가는 KIS 서버가 호가 산정 → 호가단위 문제 원천 회피
**확인**: Phase 0 모의투자 테스트에서 검증

## 관련 과거 버그
- (2026-03-03) screening_log 저장 누락 — `INSERT OR IGNORE` 패턴으로 해결. **DB write 로직은 항상 예외 가드 필수**
- (2026-03-02) `datetime.now()` UTC 반환 — 시간 관련 로직은 `now_kst()` 사용 확인
- (2026-04-14) 종목당 예산 상한 동적 계산 — main.py:1247 `max_per_stock = int(total_capital) // MAX_POSITIONS` 유지

## 영향 범위

**직접 영향**
- 09:25 매수 경로 (main.py:1080~1310 `execute_buy_orders()`)
- 매수 주문 처리 (`trading_engine.execute_portfolio()` → `_execute_buy_orders()`)
- 포지션 사이즈 계산 (`calculator.calculate_position_size()`)

**간접 영향**
- DB `trades`, `portfolio` 저장 로직 — 가중평균 반영 필요
- 매수 알림 메시지 (`notifier.send_buy_alert()`) — retry_count/slippage 추가 선택
- 포트폴리오 모니터링 (`portfolio_monitor_v2`) — 체결 중 과도기 shares 표시 검토
- 일일 리포트 — 재시도 통계 섹션 추가 선택

**영향 없음 (확인됨)**
- 매도 경로 (손절/익절/트레일링) — 이번 작업은 매수만
- 포트폴리오 스냅샷 저장 로직
- 테마 선정 / 주간 스크리닝
- 대시보드 표시 로직 (읽기만 — 변경 없음)

## 작업 중 발견 사항 (진행 중 업데이트)

### 2026-04-16 Phase 0 조사 결과

**확인된 사실**
- `ORD_DVSN=03` = 최유리지정가 (반대편 1호가 즉시 체결) — 공식 API 스펙에 존재
- `ORD_DVSN=01` (시장가) 관행: `ORD_UNPR="0"` 전송
- 공식 샘플 코드에서 TR_ID: 실전 `TTTC0012U`(매수) / 모의 `VTTC0012U`. 기존 코드는 `TTTC0802U` 사용 중 (구 버전 TR_ID일 가능성 — 현재 동작 중이므로 유지)

**⚠️ 핵심 불확실성 — 증거금 차감 방식**
- 공식 샘플 주석: "**ORD_UNPR(주문단가)가 없는 주문은 상한가로 주문금액을 선정**하고 이후 체결이되면 체결금액로 정산됩니다."
- 이 서술이 ORD_DVSN=03 + ORD_UNPR="0" 케이스에도 적용되는지 **공식 문서에서 확인되지 않음**
- 공개된 사용자 샘플/블로그/GitHub에서 "03 + 증거금 차감 금액 실측 사례" 찾을 수 없음
- 만약 03 + UNPR=0이 "주문단가 없는 주문" 취급 → **시장가와 동일한 1.3배 증거금 적용** → 계획 무효
- 만약 03 + UNPR=0이 "1호가 자동 산정" 취급 → **1.0배 증거금** → 계획 유효

### 검증 필요 (진행 전 반드시)

**옵션 A (권장): 모의투자 1주 테스트**
- 모의투자 계정으로 `ORD_DVSN=03`, `ORD_UNPR="0"`, 1주 매수
- `get_orderable_cash()` 주문 전/후 비교로 증거금 차감량 확인
- 차감 금액이 1호가 기준이면 계획 진행, 상한가 기준이면 Plan B/C 검토

**옵션 B (차선): 실전 1주 소액 테스트**
- 모의투자가 03 지원 안 할 가능성 대비
- 저가 종목(현재가 5천원 미만) 1주로 실전 테스트
- 실제 증거금 차감량 + 체결가 동시 확인

**옵션 C: Plan B/C 백업안 설계**
- **Plan B**: `ORD_DVSN=03` + `ORD_UNPR=현재가` 또는 `현재가×1.02` 전달 → 지정가지만 "최유리" 특성으로 즉시 체결 기대
- **Plan C**: 일반 지정가(`ORD_DVSN=00`) + 공격적 가격(현재가 ×1.01~1.02) → 확실한 1.0배 증거금 + 즉시 체결 가능성 높음
- 두 안 모두 Phase 0 실측 결과에 따라 선택

### 결론
**Phase 0 Gate**: 증거금 차감 방식을 실측으로 확인하기 전까지 Phase 1+ 착수 보류. 계획의 전제(1.3x → 1.0x)가 깨지면 전체 설계 재검토 필요.

### 2026-04-16 11:25 실측 결과

`scripts/verify_best_limit_margin.py` 실행 결과 (삼성전자 217,500원, 주문가능현금 4,549,192원):

| ORD_DVSN | UNPR | 최대매수수량 | 증거금 비율 |
|---|---|---|---|
| 01 (시장가) | 0 | 16주 | **×1.30** |
| 03 (최유리지정가) | 0 | 16주 | **×1.30** ❌ |
| 03 (최유리지정가) | 217500 | 16주 | **×1.30** ❌ |
| 00 (일반 지정가) | 217500 | 20주 | **×1.04** ✅ |

**결론**: ORD_DVSN=03은 UNPR과 무관하게 시장가와 동일한 1.3배 증거금 적용. **원 계획(Plan A) 및 Plan B 모두 무효**. **Plan C(일반 지정가 00 + 공격적 가격)만이 유효**.

### Plan C 전환 — 신규 설계 방향

**핵심 변경**
- `ORDER_TYPE_DEFAULT = "limit_aggressive"` (이전: `"best_limit"`)
- ORD_DVSN="00" 일반 지정가 + 매도 1호가 또는 현재가 +1틱을 ORD_UNPR에 명시
- `buy_best_limit_order()` 대신 **기존 `buy_limit_order()` 재사용** (KIS API 메서드 추가 불필요)

**매수 가격 결정 로직 (신규)**
1. 호가창 조회(`inquire-asking-price`) → 매도 1호가(ask1) 취득
2. 매도 1호가에 지정가 주문 → 즉시 체결 가능성 ≈ 최유리지정가 수준
3. 호가 단위(틱) 자동 준수 — KIS 서버가 매도 1호가를 반환하므로 별도 조정 불필요
4. 호가 조회 실패 시 폴백: `현재가 × 1.005`를 호가 단위에 맞게 내림/올림

**재시도 루프 (기존 설계 재사용)**
- 10초 타임아웃 → 취소 → 새 호가 조회 → 재주문
- 가격이 움직이면 자동으로 새 매도 1호가 반영 (최유리지정가 특성을 지정가로 에뮬레이션)

**리스크 재평가**
- 체결률: 최유리지정가와 동등하거나 약간 낮음 (매도 1호가가 튀면 미체결 가능)
- 슬리피지: 동등 (어차피 매도 1호가로 체결)
- 증거금: **1.04배 확정** → 슬롯당 실투자금 ≈ 91% 달성 (기존 73%→91%, +25%)

**파일 수정 변경점 (기존 플랜 대비)**
- ❌ 제거: `ORDER_TYPE_BEST_LIMIT="03"` 상수, `buy_best_limit_order()` / `sell_best_limit_order()` 메서드, MockOrderApi의 best_limit 시뮬레이터
- ✅ 추가: `inquire_asking_price(stock_code)` KIS API 래퍼 (호가창 조회), `_compute_aggressive_limit_price(stock_code)` 유틸
- ✅ 변경: `_place_best_limit_with_retry()` → `_place_aggressive_limit_with_retry()` (이름 + 내부 로직 일부)
- ✅ 변경: `ORDER_TYPE_DEFAULT` 값 `"limit_aggressive"`, config 상수명도 `LIMIT_AGGRESSIVE_*`로 리네임
- ✅ 유지: 재시도 루프 골격, 부분체결 가중평균, cancel+sleep+재조회, 증거금 3갈래 검증(limit×1.04)
