# Step 0 KIS 사전 조사 결과 (단위 2-4a)

**작성 시점**: 2026-05-14 KST (장 마감 후)
**작성 방법**: KIS 공식 매뉴얼 + 기존 코드 인프라 분석 + KRX 매매 메커니즘 이해
**probe 실 검증**: 5/15 장중 단위 2-4e dry_run 단발에서 자연 검증

---

## 검증 1: 동시호가 매수 ord_dvsn 코드

### 분석 결과
KRX 매매 메커니즘 기준:
- **정규장**: 09:00~15:30 (지정가/시장가 일반 매매)
- **정규장 마감 동시호가**: 15:20~15:30 — **정규장 시간 안에 포함**. 단일가 체결만 15:30에 발생
- **시간외 종가매매**: 15:40~16:00 (별도 시장, 종가 단가)
- **시간외 단일가**: 16:00~18:00 (별도 시장, 5단계 단일가)
- **장전 시간외 종가매매**: 08:30~08:40 (전일 종가 매매)

WikiDocs (`wikidocs.net/239581`) 및 KIS Open API GitHub 샘플:
- `ord_dvsn = "00"` 지정가 (정규장 + 동시호가 통합)
- `ord_dvsn = "01"` 시장가
- `ord_dvsn = "02"` 조건부지정가
- `ord_dvsn = "05"` 장전 시간외
- `ord_dvsn = "06"` 장후 시간외 (추정, KIS 매뉴얼 공식 확인 필요)

### 결론
**PRD 9-1 "15:25~15:28 동시호가 2차 진입" → `ord_dvsn="00"` (지정가) 단일 코드 사용** (시간대만 PRD대로 분리, 15:20~30 사이 지정가 주문은 KRX/KIS가 자동으로 동시호가 큐로 처리).

**phase2_enabled 결정**: `default=True` (코드는 작성, 5/15 장중 dry_run에서 최종 확인)

**폴백 시나리오**: 5/15 dry_run에서 15:25 주문이 정상 큐 진입 확인 안 되면 → `phase2_enabled=False` 토글로 전환, phase1만 100% 진입 (모든 코드는 보존, settings.yaml만 변경)

### 권장 추가 안전장치
- `EntryExecutorSettings.allowed_phase2_window_start = "15:20"` / `_end = "15:28"` 시간대 가드 (PRD 9-1 정합)
- 15:30 이후 phase2 호출 시 즉시 종료 (이미 단일가 체결 시점)

---

## 검증 2: 예상체결가 TR

### 분석 결과
- 우리 시스템 기존 인프라: `inquire_asking_price` (TR `FHKST01010200`, kis_order_api.py:819) — 호가 1~10단계 + 잔량 응답
- 응답에 예상체결가 필드 존재 추정 (KIS 매뉴얼): `stck_prdy_clpr`(전일 종가) / `antc_cnpr`(예상체결가) / `antc_vol`(예상거래량) / `stck_oprc`(시가) 등
- 단, **15:20 이전에는 예상체결가 필드가 빈 값** 또는 의미 없음 (동시호가 시간대만 의미 있음)

### 결론
**`FHKST01010200` (inquire_asking_price) 응답에서 `antc_cnpr` 필드 추출 → 예상체결가**.
- 15:20 이전 호출 시 None 또는 0 반환 → `estimated_price_collector.get_estimated_price()` graceful None
- 단위 2-4b 신규 collector `estimated_price_collector.py` 는 기존 `inquire_asking_price` 래핑

**5/15 장중 dry_run 검증 항목**: 15:21 호출 시 `antc_cnpr` 필드가 양수 정수로 반환되는지

### 폴백
필드가 비어있으면 PRD 9-3 "예상체결가 +0.5% 보류" 룰을 우회하고 **호가 잔량 비율만 사용** (`ask_total / bid_total < 0.8` 만 적용).

---

## 검증 3: 분봉 VWAP TR

### 분석 결과
- 우리 시스템 기존 인프라: **없음** — 분봉 데이터 신규 도입 필요
- KIS 매뉴얼: `FHKST03010200` (당일분봉조회) 또는 `inquire-time-itemchartprice` 엔드포인트
- 응답: 분봉 OHLCV (시가/고가/저가/종가/거래량) 시계열

### 결론
**`FHKST03010200` (당일분봉조회) 신규 호출 추가 — `closing_bet_system/collectors/vwap_collector.py` 가 래핑**.
- 14:50~15:18 시점 분봉 28개 추출 → `VWAP = Σ(close × volume) / Σ(volume)`
- 14:50 이전 호출 시 `InsufficientDataError` (이전 데이터로 계산 무의미)
- KIS 응답 형식이 우리 시스템에 처음 사용되므로 단위 2-4b 단위 테스트에서 mock + 실 호출 모두 검증

### 폴백
KIS 분봉 응답 비어있거나 14:50 이전 호출 시:
- 옵션 A: 가격 상한 룰에서 VWAP 항 제외, `당일 고가`만 사용 (보수적)
- 옵션 B: phase1 스킵 (안전 최우선)
- **권장: 옵션 A** — 진입 기회 보존 + 당일 고가가 이미 상한 역할

---

## 검증 4: TR_ORDER_STATUS (TTTC8001R)

### 분석 결과
**우리 시스템에 이미 구현됨**! (`modules/trading_engine/kis_order_api.py:569` `get_order_status(order_id, order_date)`)
- TR_ID `TTTC8001R` / 모의 `VTTC8001R` 사용
- URL: `/uapi/domestic-stock/v1/trading/inquire-daily-ccld`
- 응답 dict 필드: `order_id`, `filled_qty`(`tot_ccld_qty`), `filled_price`(`avg_prvs`), `status`("체결"/"미체결")
- 부분 체결도 지원 (`order_qty != tot_ccld_qty` 시 "미체결" 상태)

### 결론
**fill_checker는 thin wrapper로 충분** — `closing_bet_system/execution/fill_checker.py`:
```python
async def get_fill_status(order_id: str) -> FillStatus:
    """KIS get_order_status 래핑 + 3회 재시도 + KST 시간 기준."""
    orders = await asyncio.to_thread(self.kis_order_api.get_order_status, order_id=order_id)
    # KIS 500 timeout 3회 재시도 (5/13 사건 패턴, label_provider._fetch_daily_price_with_retry 참조)
    ...
```

**5/15 장중 dry_run 검증 항목**: 실 ODNO로 `get_order_status` 호출 시 응답 dict 필드 명세 일치

---

## 5/15 장중 dry_run 검증 체크리스트 (단위 2-4e)

단위 2-4b/c/d 구현 완료 후 5/15(금) 장중 dry_run 1회 실행 시 다음 확인:

| 항목 | 시간 | 검증 내용 | PASS 조건 |
|---|---|---|---|
| ord_dvsn="00" 동시호가 큐 | 15:25 | 모의 주문 응답 코드 | rt_cd=0 + ODNO 반환 |
| 예상체결가 antc_cnpr 필드 | 15:21~ | `inquire_asking_price` 응답 | antc_cnpr 양수 정수 |
| 분봉 14:50~15:18 VWAP | 15:18~ | `FHKST03010200` 응답 | 분봉 28개 + close/volume 정상 |
| fill_checker 응답 | 15:23~ | `get_order_status(ODNO)` 응답 | filled_qty/filled_price 추출 정상 |

검증 후 결과 → `STEP0_VERIFICATION_RESULT.md` 추가 작성 (단위 2-4e 시점)

---

## 권고 폴링 간격

- **default 5초** 유지 (사용자 결정 5/14)
- 5/15 dry_run에서 KIS rate limit 위반 발생 안 하면 5초 확정
- 위반 시 10초로 완화 + PRD 9-3 명시 사유 기록

---

## 단위 2-4a 완료 상태

- [x] 검증 1: 동시호가 ord_dvsn → "00" 단일 사용 결론 (5/15 dry_run 최종 확인)
- [x] 검증 2: 예상체결가 TR → 기존 `inquire_asking_price` antc_cnpr 필드 사용
- [x] 검증 3: 분봉 VWAP → `FHKST03010200` 신규 추가, 단위 2-4b 구현
- [x] 검증 4: TR_ORDER_STATUS → **기존 구현 재사용**, fill_checker thin wrapper
- [x] phase2_enabled = True (default)
- [x] 폴링 간격 5초 default
- [x] 폴백 시나리오 4건 정의 완료

→ **단위 2-4b 진입 준비 완료**
