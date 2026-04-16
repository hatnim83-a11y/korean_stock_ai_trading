# CHECKLIST: 공격적 지정가 전환 작업 (Plan C)

## 구현 항목

### Phase 0: 사전 검증 ✅ 완료 (2026-04-16)
- [x] KIS OpenAPI 공식 문서에서 ORD_DVSN 스펙 확인
- [x] `scripts/verify_best_limit_margin.py` 작성
- [x] 실전 계정 READ-ONLY 검증 완료
- [x] 03 = 1.3배 증거금 확인 → Plan C 채택

### Phase 1: 인프라
- [ ] `config.py` TradingConfig에 7개 Field 추가
  - [ ] `ORDER_TYPE_DEFAULT: str` (default `"limit_aggressive"`)
  - [ ] `LIMIT_AGGRESSIVE_RETRY_TIMEOUT: int` (default `10`)
  - [ ] `LIMIT_AGGRESSIVE_POLL_INTERVAL: int` (default `3`)
  - [ ] `LIMIT_AGGRESSIVE_MAX_RETRIES: int` (default `12`)
  - [ ] `LIMIT_AGGRESSIVE_TOTAL_TIMEOUT: int` (default `120`)
  - [ ] `LIMIT_AGGRESSIVE_MARGIN_RATIO: float` (default `1.04`)
  - [ ] `LIMIT_AGGRESSIVE_CANCEL_DELAY: float` (default `0.5`)
  - [ ] `ABNORMAL_RETRY_WARN_THRESHOLD: int` (default `5`)
- [ ] `kis_order_api.py`: `inquire_asking_price(stock_code) -> dict` 신규 메서드
  - TR_ID `FHKST01010200`
  - 반환: `{"ask1": int, "bid1": int, "ask_volume1": int, "bid_volume1": int, "current_price": int, ...}`
- [ ] `kis_order_api.py`: `_rate_limit()`에 `threading.Lock` 추가
- [ ] `MockOrderApi` 확장
  - [ ] `inquire_asking_price()` 시뮬레이터 (expected_price ×1.001을 ask1로 반환)
  - [ ] `buy_limit_order()` 부분체결 시뮬레이션 (체결률 50~100% 랜덤)
  - [ ] `get_order_status()` 단건 조회
  - [ ] `cancel_order()` 정상/실패 시뮬레이션
- [ ] `modules/trading_engine/__init__.py`: 새 상수/메서드 export

### Phase 2: 재시도 루프
- [ ] `trading_engine.py`: `OrderErrorCategory(Enum)` — FATAL/RECOVERABLE/SIZE_ERROR/PRICE_ERROR
- [ ] `trading_engine.py`: `_classify_order_error(message: str) -> OrderErrorCategory`
- [ ] `trading_engine.py`: `_compute_aggressive_limit_price(stock_code, fallback_price) -> int`
  - [ ] 매도 1호가 조회 성공 시 반환
  - [ ] 실패 시 `fallback_price × 1.005`를 호가 단위에 맞게 반올림
- [ ] `trading_engine.py`: `_compute_weighted_avg_price(fills: list) -> int` 유틸
- [ ] `trading_engine.py`: `_place_aggressive_limit_with_retry()` 본체
  - [ ] 재시도 루프 (MAX_RETRIES + TOTAL_TIMEOUT 이중 상한)
  - [ ] 각 재시도마다 매도 1호가 재조회 (호가 변동 반영)
  - [ ] `buy_limit_order(price=매도1호가)` 호출
  - [ ] 3초 간격 폴링 × 3회 (총 9~10초)
  - [ ] 미체결 취소 → 0.5초 대기 → 재조회
  - [ ] 체결량/비용 누적
  - [ ] FATAL/SIZE_ERROR/RECOVERABLE 분기 처리
  - [ ] 이미 전량 체결 후 취소 요청 정상 흐름 처리
  - [ ] 결과 dict 반환 (quantity/requested_quantity/filled_price/remaining_shares/sub_order_ids 등)
- [ ] `scripts/test_aggressive_limit_order.py` 신규 작성
  - [ ] 시나리오 1: 100% 즉시 체결
  - [ ] 시나리오 2: 1차 70% + 2차 30% (재시도 1회)
  - [ ] 시나리오 3: 1차 50% + 2차 0% + 3차 50% (재시도 2회)
  - [ ] 시나리오 4: 최대 재시도 후 미체결 (포기)
  - [ ] 시나리오 5: 증거금 초과 → 수량 감축
  - [ ] 시나리오 6: 취소 직전 전량 체결 엣지케이스
  - [ ] 6개 시나리오 모두 PASS

### Phase 3: trading_engine 통합
- [ ] `_execute_buy_orders()` 분기 추가 (`order_type == "limit_aggressive"`)
- [ ] **`price == 0` 시장가 분기 조건 제거** (라인 221)
- [ ] 증거금 사전 검증 3갈래 분리 (market×1.3 / limit_aggressive×1.04 / limit×1.0)
- [ ] `_save_trades()`: 매수 슬리피지 계산 추가 (expected_price 대비)
- [ ] `_save_trades()`: `filled_price or order_price` 폴백 제거, 체결량=0 skip
- [ ] `_save_positions()`: 가중평균 체결가 반영
- [ ] 기존 시장가 경로는 그대로 보존 (긴급 롤백용)

### Phase 4: optimizer/calculators 전환
- [ ] `calculators.py`: `LIMIT_AGGRESSIVE_MARGIN_RATIO = 1.04` 상수
- [ ] `calculators.py`: `calculate_position_size()` 시그니처 확장 (`order_type: str = "limit_aggressive"`)
- [ ] `calculators.py`: `market_order: bool` 파라미터 하위호환 유지 (deprecated alias)
- [ ] `optimizer.py`: order dict `order_type = settings.ORDER_TYPE_DEFAULT`
- [ ] `optimizer.py`: order dict `expected_price = stock['price']` 필수 채움

### Phase 5: 실전 관찰
- [ ] `.env` 설정: `ORDER_TYPE_DEFAULT=limit_aggressive`
- [ ] `sudo systemctl restart trading_system` (장 마감 후)
- [ ] 매일 매수 후 MCP SQLite로 결과 집계 확인

## 검증 항목

### 단위 검증
- [ ] `python -m py_compile` 모든 수정 파일 통과
- [ ] `scripts/test_aggressive_limit_order.py` 6개 시나리오 모두 PASS
- [ ] `python main.py --manual --test --real` 모의투자 모드 1회 체결 확인

### 통합 검증
- [ ] code-tester 에이전트로 수정 파일 4개 검증 (kis_order_api, trading_engine, calculators, optimizer)
- [ ] code-tester 지적 심각/주의 이슈 0건 또는 즉시 수정
- [ ] MockOrderApi 경로로 기존 시장가 테스트도 PASS (하위호환 확인)

### 실전 검증 (1주)
- [ ] 완전체결률 ≥ 95%
- [ ] 평균 재시도 횟수 ≤ 2회
- [ ] 평균 매수 슬리피지 ≤ +0.5%
- [ ] 종목당 평균 체결 소요시간 ≤ 30초
- [ ] **슬롯당 실제 투자금 비율 ≥ 89%**
- [ ] 호가 조회 실패율 ≤ 1%
- [ ] TOTAL_TIMEOUT(120s) 도달 0건/일

## 배포 항목

- [ ] **systemd 재시작 전 선행 체크**
  - [ ] `ps aux | grep main.py | grep -v grep` 이중 실행 없음 확인
  - [ ] `trading_system.pid` 잔여 파일 정리
  - [ ] `.env` 활성 계정 확인
- [ ] 장 마감(15:30) 이후 `sudo systemctl restart trading_system`
- [ ] `sudo systemctl status trading_system` 정상 기동 확인
- [ ] 최초 09:25 매수 시점 로그 실시간 관찰 (`tail -f logs/*.log`)
- [ ] 첫 영업일 체결 결과 MCP SQLite로 확인
- [ ] 이상 시 즉시 롤백: `.env`에서 `ORDER_TYPE_DEFAULT=market` 후 재시작

## 문서 업데이트 항목

- [ ] `memory/MEMORY.md` 인덱스에 `project_aggressive_limit_order.md` 추가
- [ ] `memory/project_aggressive_limit_order.md` 신규 작성 — 전환 내용, 파라미터, 실측 결과, 교훈
- [ ] `memory/project_strategy.md`에 주문 방식 변경 1줄 기록
- [ ] 프로젝트 `CLAUDE.md`에 "주문 유형은 `ORDER_TYPE_DEFAULT` 설정으로 제어" 규칙 추가 (선택)
- [ ] 3문서 (PLAN/CONTEXT/CHECKLIST) `active/` → `completed/20260YMMDD_best-limit-order-conversion/` 이동
- [ ] CHECKLIST의 모든 항목 `[x]` 확인 후에만 완료 선언

## 완료 게이트 (선언 전 체크)

- [ ] 구현 항목 전부 `[x]`
- [ ] 검증 항목 전부 `[x]`
- [ ] 배포 항목 전부 `[x]`
- [ ] **문서 업데이트 항목 전부 `[x]`**
- [ ] `active/` → `completed/` 아카이브 완료
