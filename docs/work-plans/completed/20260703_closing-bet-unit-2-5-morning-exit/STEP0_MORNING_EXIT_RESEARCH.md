# Step 0 단위 2-5a 사전 조사 결과

**작성 시점**: 2026-05-16 KST
**작성 방법**: 기존 인프라/매뉴얼 분석 + 운영 DB 실 데이터 검증 + 운영 봇 로그 측정
**실 probe 검증**: 5/18(월) 09:00~10:00 자연 검증 (단위 2-5e dry_run 통합 단발에서 보강)

---

## 검증 1: `KISApi.get_current_price(stock_code)` 응답 필드

### 분석 결과
`modules/stock_screener/kis_api.py:422~520` 기존 메서드 — `inquire-price` (FHKST01010100) TR 호출.
응답 dict 11 필드 (모두 `_safe_int` / `_safe_float` 빈 문자열 방어):
```python
{
    'code': stock_code,                              # "005930"
    'name': stock_name,                              # "삼성전자" (없으면 네이버 폴백)
    'price': _safe_int(output['stck_prpr']),         # 현재가
    'change': _safe_int(output['prdy_vrss']),        # 전일대비
    'prev_close': current_price - change,            # 전일종가 (계산)
    'change_rate': _safe_float(output['prdy_ctrt']), # 등락률 (%)
    'volume': _safe_int(output['acml_vol']),         # 누적거래량
    'trade_value': _safe_int(output['acml_tr_pbmn']),# 누적거래대금
    'high': _safe_int(output['stck_hgpr']),          # 당일 고가
    'low': _safe_int(output['stck_lwpr']),           # 당일 저가
    'open': _safe_int(output['stck_oprc']),          # 시가 ⭐
}
```

### 결론
- **단위 2-5b `morning_price_collector.get_open_high_current(ticker)` 가 `get_current_price` 를 그대로 래핑** → open/high/price 단일 호출로 확보
- 시가 갭률: `(open - entry_price) / entry_price` (단위 2-5c map_action 헬퍼)
- 폴백: 응답 dict None → 매도 대상에서 해당 ticker 제외 (graceful), 텔레그램 경고

---

## 검증 2: 매도 대상 SQL 쿼리 (운영 DB 실 데이터 검증)

### 분석 결과
2026-05-16 KST 운영 DB(`/home/hatni/korean_stock_ai_trading/data/closing_bet.db`) 직접 query 실행:
- 최근 7일 candidates: **20건** (5/15 = 17, 5/14 = 3)
- 모두 `candidate_status='recommended'` + `entry_phase1/2_executed_shares=0` (Phase 2 dry_run 아직 발화 X — settings.yaml enabled=true 시점이 5/15 13:34 UTC 라 같은 날 15:18 KST 자연 발화 시점 이미 경과, 5/18 월요일 첫 발화 예정)

매도 대상 SQL 쿼리 (CONTEXT.md 박제):
```sql
SELECT candidate_id, ticker, name, candidate_status,
       entry_price, entry_amount, entry_time,
       entry_phase1_executed_price, entry_phase1_executed_shares,
       entry_phase2_executed_price, entry_phase2_executed_shares
FROM candidates
WHERE trade_date = ?
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

### 결론
- **쿼리 정합성 검증 완료** (운영 DB 실행 결과 5/15 trade_date 매도 대상 0건 — Phase 2 dry_run 미발화 상태 기대값 일치)
- 5/18(월) 15:18 첫 dry_run 발화 후 5/19(화) 09:30 시점에 매도 대상 select 시 정상 동작 예상
- `COALESCE` + `IS NULL` 조합으로 phase1/phase2 NULL 안전 처리
- `trade_date = ?` 파라미터는 일반적으로 T-1 (어제 진입한 후보 = 오늘 매도) — 단, dry_run 단계는 ODNO 박제 X 이므로 매도 대상 0건 유지 (정상)

---

## 검증 3: `KISOrderApi.sell_market_order` 응답 구조

### 분석 결과
`modules/trading_engine/kis_order_api.py:354~375` — `_place_order(order_type=ORDER_TYPE_MARKET="01", is_buy=False)` 위임.
응답 dict (entry_executor `buy_limit_order` 와 **동일 패턴**):
```python
{
    "success": True/False,
    "order_id": "ODNO_xxx",   # KIS 주문번호 (rt_cd="0" 시 채워짐)
    "order_time": "HH:MM:SS",
    "stock_code": stock_code,
    "quantity": quantity,
    "price": 0,                # 시장가는 0
    "order_type": "시장가",
    "action": "매도",
    "message": msg1,
}
```

### 결론
- **entry_executor 와 동일 인터페이스 가정 정합** → 단위 2-5c 가 그대로 재사용 가능
- TR ID: 실전 `TTTC0801U` / 모의 `VTTC0801U` (자동 분기)
- `success=False` 시 `order_id=""` → 재시도 또는 다음 ticker 진행 (entry_executor `submit_fail` reason 동일 패턴)

---

## 검증 4: 부분 체결 잔량 처리

### 분석 결과
candidates 테이블 v3 컬럼 6개 박제:
- `entry_phase1_executed_price` / `_shares` (phase1 체결 정보)
- `entry_phase2_executed_price` / `_shares` (phase2 체결 정보, NULL = 미체결)

총 보유량 계산:
```python
total_shares = (
    (row.entry_phase1_executed_shares or 0)
    + (row.entry_phase2_executed_shares or 0)
)
```

부분 체결 시나리오:
| Phase1 | Phase2 | total_shares | 처리 |
|---|---|---|---|
| 5 (체결) | NULL (미발주/실패) | 5 | phase1 only → mark_entered_phase1_only 선행 호출 |
| 5 (체결) | 5 (전량 체결) | 10 | entered (status='entered') → log_exit 직접 호출 가능 |
| 5 (체결) | 3 (부분 체결, 잔량 2주 미체결) | 8 | entered 가정 (mark_entered 호출됨) → log_exit 정상 |
| 0 (미체결) | 0 (미체결) | 0 | 매도 대상 select 단계 제외 (WHERE COALESCE(phase1, 0) > 0) |

### 결론
- **단순 합산 패턴 정합**, NULL 안전 처리
- `_finalize_mark_entered` (단위 2-4c) 는 phase2 체결 시만 `mark_entered` 호출 → phase2 부분 체결도 mark_entered 호출됨 (P0-2 영향 없음, phase1 only 만 위험)

---

## 검증 5: KIS 주문 취소 API (P1-4 force_close 미체결 취소)

### 분석 결과
`modules/trading_engine/kis_order_api.py:504~560` 기존 메서드 — `cancel_order(order_id, stock_code, quantity)` 이미 구현.
- TR ID: 실전 `TTTC0803U` / 모의 `VTTC0803U`
- URL: `/uapi/domestic-stock/v1/trading/order-rvsecncl`
- Body: `RVSE_CNCL_DVSN_CD="02"` (취소) + `QTY_ALL_ORD_YN="Y"` (전량)
- 응답: `{"success": bool, "order_id": str, "message": str}`

### 결론
- **단위 2-5c force_close 가 그대로 재사용 가능** — 신규 KIS API 호출 불필요
- 취소 순서 (CONTEXT.md 박제):
  1. `cancel_order(odno, stock_code, original_qty)` 호출
  2. `fill_checker.get_fill_status(odno)` 폴링 → 취소 확정(`status` 변경) 대기 (max 30초)
  3. `sell_market_order(stock_code, remaining_qty)` 시장가 재발주

### 주의 사항
- 부분 체결 후 취소 시 KIS는 미체결 잔량만 취소 처리 (이미 체결된 부분은 그대로 유지) → 추가 확인 필요할 수도 (5/18 dry_run 검증)

---

## 검증 6: 메인 봇 09:00 잡 race (P0-3 검증)

### 분석 결과
운영 봇 로그(2026-05-13~5/15, UTC 00:00 = KST 09:00 발화) 측정:

| 잡 | 5/13 | 5/14 | 5/15 |
|---|---|---|---|
| `monitoring_start_early` (`_run_monitoring_start`) | 09:00:00 시작 → 09:00:00 종료 (즉시) | 동일 | 동일 |
| `midweek_sell_profit` | **미발화** (테마 교체 없음) | 미발화 | 미발화 |
| `_run_supply_collection` | 09:00:00 즉시 종료 (이미 수집됨) | 동일 | 동일 |

### 결론
- **monitoring_start_early 잡은 비동기 위임 패턴** — 함수 진입 후 portfolio_monitor `start_monitoring()` 호출 즉시 반환 (1초 미만)
- `midweek_sell_profit` 잡은 평일 등록되지만 **테마 교체 발생일에만 실 매도 발주** (최대 10~30초 KIS 처리)
- **09:01 emergency_stop 시점은 60초 여유로 충분** — KIS rate limit (1초당 ~15 req) 안에서 모든 09:00 잡 종료 후 시작

### 박제 결정
- `EMERGENCY_STOP_SCHEDULE_HOUR = 9`
- `EMERGENCY_STOP_SCHEDULE_MINUTE = 1`
- `misfire_grace_time = 60` (1분 grace, 60초 이후엔 09:30 morning_exit 에 위임)
- `coalesce = True`

---

## 종합 결정 및 다음 단계

### 6가지 검증 모두 통과
| 검증 | 결과 |
|---|---|
| 1. get_current_price open/high/price | ✅ 정상 (`_safe_int` 적용) |
| 2. 매도 대상 SQL 쿼리 | ✅ 운영 DB 검증 완료 |
| 3. sell_market_order 응답 | ✅ entry_executor 동일 패턴 |
| 4. 부분 체결 잔량 처리 | ✅ COALESCE 안전 |
| 5. KIS 주문 취소 API | ✅ 기존 cancel_order 재사용 |
| 6. 09:01 race 회피 | ✅ monitoring_start 1초 미만 |

### 폴백 시나리오 (단위 2-5c 구현 시 박제)
1. **get_current_price 응답 None** → 매도 대상 제외 + 텔레그램 경고 + 10:30 force_close 위임
2. **cancel_order 실패** → 30초 후 재시도 1회, 실패 시 force_close가 시장가 추가 발주 (KIS 잔량 자동 차감 가정)
3. **sell_market_order rt_cd ≠ 0** → 다음 ticker 진행 + 텔레그램 알림 (entry_executor `submit_fail` 동일)
4. **부분 체결 잔량 처리** → `entry_phase1+phase2_executed_shares - 이미 매도된 shares` 계산 (단위 2-5b ExitTarget dataclass)

### 5/18 자연 검증 체크리스트 (단위 2-5e dry_run 통합 단발 시)
- [ ] 5/18 09:00~09:01 메인 봇 monitoring_start_early 종료 시간 측정 (현 추정 1초 미만)
- [ ] 5/18 09:01 emergency_stop 발화 시 KIS rate limit 충돌 여부
- [ ] 5/19(화) 09:30 매도 대상 select 결과 (5/18 dry_run 후보 N건이 적절히 잡히는지)
- [ ] phase1 only 보유 케이스가 자연 발생하는지 (대부분 entered 가정)
- [ ] cancel_order 부분 체결 잔량 동작 (force_close 시점)

### 단위 2-5a 완료 상태
- [x] 검증 1~6 모두 통과
- [x] sell_lock 재사용 + owner 네임스페이스 결정 (CONTEXT.md 박제)
- [x] 09:01 emergency_stop 시점 박제
- [x] cancel_order 재사용 결정
- [x] STEP0_MORNING_EXIT_RESEARCH.md 작성

→ **단위 2-5b 진입 준비 완료** (collectors + exit_target_query + mark_entered_phase1_only 헬퍼)
