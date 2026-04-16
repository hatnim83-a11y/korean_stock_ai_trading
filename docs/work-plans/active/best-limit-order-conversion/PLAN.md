# PLAN: 공격적 지정가(ORD_DVSN=00 + 매도 1호가) + 10초 재시도 루프

> **작업명 유지 이유**: 폴더명 `best-limit-order-conversion`은 원 목표("최유리지정가로 전환")를 반영. Phase 0 검증 결과 KIS가 ORD_DVSN=03을 시장가와 동일한 1.3배 증거금으로 처리함을 확인하여, 같은 효과(즉시 체결)를 **일반 지정가 + 매도 1호가**로 에뮬레이션하는 방향으로 전환.

## 목표
시장가(01) 주문을 **일반 지정가(00) + 매도 1호가 지정** 방식으로 전환하여 슬롯당 실제 투자금을 **73% → 91%**로 확대한다. KIS가 지정가 주문에만 1.0배 증거금을 적용함을 실측 확인(2026-04-16).

## 배경
- **2026-04-16 실측** (`scripts/verify_best_limit_margin.py`):
  - 시장가(01) + UNPR=0: 16주 (1.30배 증거금)
  - 최유리지정가(03) + UNPR=0: 16주 (1.30배 — 예상과 달리 시장가와 동일)
  - 최유리지정가(03) + UNPR=현재가: 16주 (1.30배 — Plan B도 무효)
  - **일반 지정가(00) + UNPR=현재가: 20주 (1.04배)** ← 유일한 돌파구
- Plan A(03) / Plan B(03+UNPR) 모두 무효 → Plan C(00 + 공격적 가격) 채택

## 구현 단계 (Phase별)

### Phase 0: 사전 검증 ✅ 완료 (2026-04-16)
- [x] KIS OpenAPI 문서에서 `ORD_DVSN` 스펙 확인
- [x] `scripts/verify_best_limit_margin.py` 작성 및 실전 계정 READ-ONLY 검증
- [x] 03 증거금 1.3배 확인 → Plan A/B 기각, Plan C 채택 결정
- **Gate 통과**: Plan C 전환 확정

### Phase 1: 인프라 (반나절)
- [ ] `config.py`: 7개 Field 추가 (LIMIT_AGGRESSIVE_*)
- [ ] `kis_order_api.py` (또는 `kis_api.py`): `inquire_asking_price(stock_code)` 신규 메서드 — 매도 1호가/매수 1호가 조회 (TR_ID `FHKST01010200`)
- [ ] `_rate_limit()`에 `threading.Lock` 추가 (병렬화 대비)
- [ ] `MockOrderApi` 확장: `inquire_asking_price`, `get_order_status`, `cancel_order`, `buy_limit_order` 부분체결 시뮬레이터
- [ ] `__init__.py` export 동기화

### Phase 2: 재시도 루프 (1일)
- [ ] `trading_engine.py`: `OrderErrorCategory(Enum)` — FATAL/RECOVERABLE/SIZE_ERROR/PRICE_ERROR
- [ ] `trading_engine.py`: `_classify_order_error(message)` 분류기
- [ ] `trading_engine.py`: `_compute_aggressive_limit_price(stock_code, fallback_price)` — 매도 1호가 조회 + 호가 단위 폴백
- [ ] `trading_engine.py`: `_place_aggressive_limit_with_retry()` 핵심 루프
  - 매도 1호가 조회 → `buy_limit_order()` → 10초 타임아웃 → 취소 → 새 호가 재조회 → 재주문
  - 가중평균 체결가 누적
- [ ] 가중평균 유틸 `_compute_weighted_avg_price()`
- [ ] `scripts/test_aggressive_limit_order.py` 6개 시나리오

### Phase 3: trading_engine 통합 (반나절)
- [ ] `_execute_buy_orders()` 분기 추가 (`order_type == "limit_aggressive"`)
- [ ] **`price == 0` 시장가 분기 조건 제거** (라인 221)
- [ ] 증거금 사전 검증 3갈래: market(×1.3) / limit_aggressive(×1.04) / limit(×1.0)
- [ ] `_save_trades()` 매수 슬리피지 기록, 체결량=0 skip
- [ ] `_save_positions()` 가중평균 체결가 반영
- [ ] 기존 시장가 경로 유지 (긴급 롤백용)

### Phase 4: optimizer/calculators 전환 (반나절)
- [ ] `calculators.py`: `LIMIT_AGGRESSIVE_MARGIN_RATIO = 1.04`
- [ ] `calculate_position_size()` 시그니처 확장 (`order_type: str = "limit_aggressive"`)
- [ ] `optimizer.py`: order dict `order_type = settings.ORDER_TYPE_DEFAULT`, `expected_price` 필수화

### Phase 5: 실전 관찰 (1주)
- [ ] `.env`: `ORDER_TYPE_DEFAULT=limit_aggressive` 적용 + systemctl restart
- [ ] 일일 지표 수집: 체결률, 평균 재시도, 슬리피지, 종목당 소요시간
- [ ] 이상치 탐지 로그 집계

## 변경 파일 목록

| 파일 | 변경 규모 | 종류 |
|---|---|---|
| `config.py` | 소 (7개 Field) | 수정 |
| `modules/trading_engine/kis_order_api.py` | 중 (`inquire_asking_price` 신규 + Mock 확장) | 수정 |
| `modules/trading_engine/trading_engine.py` | 대 (핵심 루프 신규) | 수정 |
| `modules/trading_engine/__init__.py` | 소 (export) | 수정 |
| `modules/portfolio_optimizer/calculators.py` | 소 (상수+시그니처) | 수정 |
| `modules/portfolio_optimizer/optimizer.py` | 소 (order dict) | 수정 |
| `scripts/test_aggressive_limit_order.py` | 중 (6 시나리오) | 신규 |
| `scripts/verify_best_limit_margin.py` | - | 유지 (재검증용 참고) |

## 접근 방식
- **호가 기반 즉시체결 에뮬레이션**: 매도 1호가 = 시장 참여자 최저 매도 희망가 = 지정가로 주문 시 즉시 체결
- **점진적 도입**: 기존 시장가 경로 그대로 유지 + config 스위치로 즉시 롤백
- **하위호환**: `calculate_position_size(market_order=True)` alias 유지
- **검증 우선**: Phase 0 완료 (증거금 차이 실측). Phase 2의 Mock 기반 6 시나리오 전 통과 후 Phase 3 진입
- **Phase별 사용자 확인**: 각 Phase 완료 시 진행 여부 확인

## 롤백 계획
- **즉시 롤백**: `.env`에서 `ORDER_TYPE_DEFAULT=market` 후 `sudo systemctl restart trading_system`
- **부분 롤백**: Phase별 독립 커밋 → `git revert <hash>`
- **롤백 트리거**:
  - 완전체결률 < 90%
  - 평균 슬리피지 > 1.0% (매도 1호가 대비)
  - 매수 루프 전체 완료 시간 > 5분

## 완료 기준 (1주 관찰 후)

| 지표 | 목표 |
|---|---|
| 완전체결률 (remaining_shares=0) | ≥ 95% (최유리지정가보다 약간 보수적) |
| 평균 재시도 횟수 | ≤ 2회 |
| 평균 매수 슬리피지 (expected_price 대비) | ≤ +0.5% |
| 종목당 평균 체결 소요시간 | ≤ 30초 |
| **슬롯당 실제 투자금 비율** | **≥ 89%** (이론 91%, 변동성 감안) |
| 호가 조회 실패율 | ≤ 1% |
| TOTAL_TIMEOUT(120s) 도달 | 0건/일 |
