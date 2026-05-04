# CHECKLIST: 공격적 지정가 전환 작업 (Plan C)

> **상태**: 모든 Phase 완료 (2026-04-16 배포, 2026-04-20 CASH_BUFFER 후속 튜닝). 본 CHECKLIST는 사후 정리·아카이브용 갱신본.

## 구현 항목

### Phase 0: 사전 검증 ✅ 완료 (2026-04-16)
- [x] KIS OpenAPI 공식 문서에서 ORD_DVSN 스펙 확인
- [x] `scripts/verify_best_limit_margin.py` 작성
- [x] 실전 계정 READ-ONLY 검증 완료
- [x] 03 = 1.3배 증거금 확인 → Plan C 채택

### Phase 1: 인프라 ✅ 완료
- [x] `config.py` TradingConfig에 8개 Field 추가 (FALLBACK_PREMIUM 포함)
  - [x] `ORDER_TYPE_DEFAULT: str` (default `"market"`, `.env`로 `limit_aggressive` 전환)
  - [x] `LIMIT_AGGRESSIVE_RETRY_TIMEOUT: int` (default `10`)
  - [x] `LIMIT_AGGRESSIVE_POLL_INTERVAL: int` (default `3`)
  - [x] `LIMIT_AGGRESSIVE_MAX_RETRIES: int` (default `12`)
  - [x] `LIMIT_AGGRESSIVE_TOTAL_TIMEOUT: int` (default `120`)
  - [x] `LIMIT_AGGRESSIVE_MARGIN_RATIO: float` (default `1.04`)
  - [x] `LIMIT_AGGRESSIVE_CANCEL_DELAY: float` (default `0.5`)
  - [x] `LIMIT_AGGRESSIVE_FALLBACK_PREMIUM: float` (default `1.005`) — 호가 조회 실패 폴백
- [x] `kis_order_api.py`: `inquire_asking_price(stock_code) -> dict` 신규 메서드 (TR_ID `FHKST01010200`)
- [x] `kis_order_api.py`: `_rate_limit()`에 `threading.Lock` 추가
- [x] `MockOrderApi` 확장 (`inquire_asking_price`, `get_order_status`, `cancel_order`, `buy_limit_order` 부분체결 시뮬레이터)
- [x] `modules/trading_engine/__init__.py`: 새 상수/메서드 export

### Phase 2: 재시도 루프 ✅ 완료
- [x] `trading_engine.py`: `OrderErrorCategory(Enum)` — FATAL/RECOVERABLE/SIZE_ERROR/PRICE_ERROR
- [x] `trading_engine.py`: `_classify_order_error(message: str) -> OrderErrorCategory`
- [x] `trading_engine.py`: `_compute_aggressive_limit_price(stock_code, fallback_price) -> int`
- [x] `trading_engine.py`: `_compute_weighted_avg_price(fills: list) -> int` 유틸
- [x] `trading_engine.py`: `_place_aggressive_limit_with_retry()` 본체
- [x] `scripts/test_aggressive_limit_order.py` 신규 작성 (6개 시나리오)
- [x] 6개 시나리오 모두 PASS

### Phase 3: trading_engine 통합 ✅ 완료
- [x] `_execute_buy_orders()` 분기 추가 (`order_type == "limit_aggressive"`)
- [x] `price == 0` 시장가 분기 조건 정리
- [x] 증거금 사전 검증 3갈래 분리 (market×1.3 / limit_aggressive×1.04 / limit×1.0)
- [x] `_save_trades()`: 매수 슬리피지 계산 추가 (expected_price 대비)
- [x] `_save_trades()`: `filled_price or order_price` 폴백 제거, 체결량=0 skip
- [x] `_save_positions()`: 가중평균 체결가 반영
- [x] 기존 시장가 경로는 그대로 보존 (긴급 롤백용)

### Phase 4: optimizer/calculators 전환 ✅ 완료
- [x] `calculators.py`: `LIMIT_AGGRESSIVE_MARGIN_RATIO = 1.04` 상수
- [x] `calculators.py`: `calculate_position_size()` 시그니처 확장 (`order_type: str`)
- [x] `calculators.py`: `market_order: bool` 파라미터 하위호환 유지 (deprecated alias)
- [x] `optimizer.py`: order dict `order_type = settings.ORDER_TYPE_DEFAULT`
- [x] `optimizer.py`: order dict `expected_price = stock['price']` 필수 채움

### Phase 5: 실전 관찰 ✅ 완료 (2026-04-16 배포 ~ 2026-04-23 안정화)
- [x] `.env` 설정: `ORDER_TYPE_DEFAULT=limit_aggressive`
- [x] `sudo systemctl restart trading_system` (장 마감 후)
- [x] 매일 매수 후 MCP SQLite로 결과 집계 확인
- [x] 후속 튜닝: CASH_BUFFER 0.05 → 0.02 (2026-04-20, optimizer.py:51) — 슬롯 활용률 86% → 89%+

## 검증 항목

### 단위 검증
- [x] `python -m py_compile` 모든 수정 파일 통과
- [x] `scripts/test_aggressive_limit_order.py` 6개 시나리오 모두 PASS
- [x] `python main.py --manual --test --real` 모의투자 모드 1회 체결 확인

### 통합 검증
- [x] code-tester 에이전트로 수정 파일 4개 검증 (kis_order_api, trading_engine, calculators, optimizer)
- [x] code-tester 지적 심각/주의 이슈 0건 또는 즉시 수정
- [x] MockOrderApi 경로로 기존 시장가 테스트도 PASS (하위호환 확인)

### 실전 검증 (1주)
- [x] 실전 운영 1주 이상 안정 동작 확인 (배포 2026-04-16, 후속 튜닝 2026-04-20, 본 정리 시점 2026-05-01)
- [ ] **매도 슬리피지는 미측정** — 매도 경로가 시장가 + monitor 우회로 slippage 95% NULL
  - 후속 작업 권고: `docs/work-plans/active/sell-slippage-tracking/` 신규 (2026-05-01 monthly 분석 식별)
- [x] 슬롯당 실제 투자금 비율 ≥ 89% (CASH_BUFFER 0.02 후속 튜닝으로 확보)

## 배포 항목

- [x] systemd 재시작 전 선행 체크 (이중 실행 / PID 파일 / `.env` 활성 계정)
- [x] 장 마감(15:30) 이후 `sudo systemctl restart trading_system`
- [x] `sudo systemctl status trading_system` 정상 기동 확인
- [x] 최초 09:25 매수 시점 로그 실시간 관찰 (`[BL]` 프리픽스)
- [x] 첫 영업일 체결 결과 MCP SQLite로 확인
- [x] 롤백 트리거 없음 — 운영 지속

## 문서 업데이트 항목

- [x] `memory/MEMORY.md` 인덱스에 `project_aggressive_limit_order.md` 추가
- [x] `memory/project_aggressive_limit_order.md` 신규 작성 — 전환 내용, 파라미터, 실측 결과, 교훈
- [x] `memory/project_strategy.md`에 주문 방식 변경 기록
- [x] 프로젝트 `CLAUDE.md` 관련 규칙 업데이트
- [x] 3문서 (PLAN/CONTEXT/CHECKLIST) `active/` → `completed/20260501_best-limit-order-conversion/` 이동 (본 정리 시점)

## 완료 게이트 (선언 전 체크)

- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 전부 `[x]` (매도 슬리피지는 별도 작업으로 분리)
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 전부 `[x]`
- [x] `active/` → `completed/` 아카이브 완료

## 후속 작업 (별도 트래킹)

- **`sell-slippage-tracking`** (신규 권고, 2026-05-01 monthly 분석에서 식별):
  - 매도 55건 중 52건(95%) slippage NULL — 시장가 매도 + monitor 우회 경로 모두 trading_engine 우회
  - Phase 5 효과 평가의 핵심 지표가 빠진 상태 → Plan-Driven 신규 작업 권장
  - 참고: `memory/project_followup_investigations_2026_05_01.md`
