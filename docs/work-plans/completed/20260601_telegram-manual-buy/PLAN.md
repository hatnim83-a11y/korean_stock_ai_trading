# PLAN — 텔레그램 `/buy` 수동 매수 기능

**작성일**: 2026-06-01 | **상태**: 진행 중 | **우선순위**: P1

## 1. 목표
텔레그램 `/buy [종목코드]`로 사용자가 종목을 지정하면 자동 슬롯 사이징 + 분할진입 룰(1차 50%) +
시장가로 매수하고, 이후 기존 모니터링(트레일링/분할익절/손절/보유기간)으로 자동 청산한다.
테마 로테이션 매도(화요일 재선정 + midweek 교체)에서는 제외한다.

## 2. 배경
09:25 자동 매수만 존재. 시장 급반등·개별 이벤트 대응용 수동 개입 수단 부재. 텔레그램 매도
(/sell, /confirm, 30초 TTL)를 대칭 재사용. DB holding 저장 시 모니터링 자동 편입 구조 활용.

## 3. 확정 요구사항 (사용자)
- 인터페이스: 텔레그램 `/buy [종목코드]`
- 수량: 자동 슬롯 사이징 (종목만 지정)
- 진입: 분할진입 룰(1차 50%) + 시장가
- 슬롯: MAX_POSITIONS 준수, 만석 시 거부

## 4. 부가 정책 (기본값 확정)
- 매수 시간: 09:00~15:10 (15:10 이후 차단 — 종가베팅 보호)
- RSI/AI 게이트 없음 (수동 = 사용자 판단 우선)
- 확인 메시지: 예상 수량/금액/손절가 표시
- 보유기간 매도: 수동 종목도 적용. 테마 로테이션 매도만 제외

## 5. 핵심 설계 결정 (병렬 리뷰 반영)
1. **수동 종목 식별 = `portfolio.is_manual` 컬럼 (DB v19)**. theme="수동" 마커는 화요일
   재선정(`h_theme and h_theme not in selected_themes`)에 걸려 자동 청산되므로 기각.
2. **모니터 편입 = `start_monitoring()` stop+start 재사용**. KIS WebSocket 동적 구독 불가 →
   add_position()만으론 실시간 가격 미수신. 09:26 재시작과 동일 패턴(WebSocket 재구독 +
   position_state DB/JSON 트레일링 상태 복원). 5~10초 공백은 기존 09:26 재시작과 동일 트레이드오프.
3. **슬롯 사이징 = 공용 헬퍼 추출**. `modules/trading_engine/capital_utils.py`에
   `compute_per_slot_capital()` 신설(순환 import 회피). main.py 인라인(1379~1407) +
   현금/총자산 폴백(1267~1279) 포함. 자동/수동 동일 계산 공유.
4. **ATR = `await asyncio.to_thread(compute_atr, ...)`** — 이벤트루프 블로킹 방지.
5. **pending 격리** — `_pending_buy`/`_pending_buy_expires` 별도. 새 명령 시 이전 pending
   전체 초기화. /confirm 핸들러 buy/sell 우선순위 명시.
6. **체결가 폴백** — buy_market_order 후 미체결/filled_price≤0이면 get_order_status 재조회
   (execute_sell:510~519 패턴). 그래도 실패면 DB·모니터 미저장 + 오류 응답.

## 6. 구현 단계
### Phase 1 — DB + 헬퍼 + 토글
- database.py `_migrate()` v19: `ALTER TABLE portfolio ADD COLUMN is_manual BOOLEAN DEFAULT 0`
- database.py `save_holding_position()` is_manual 파라미터, `get_portfolio()` 반환 포함
- `modules/trading_engine/capital_utils.py` 신설: compute_per_slot_capital()
- main.py: 인라인 슬롯 사이징 → 헬퍼 호출 교체 (before/after 회귀 비교)
- config.py: `MANUAL_BUY_ENABLED: bool = Field(default=True)`

### Phase 2 — 로테이션 skip 가드
- main.py 화요일 재선정 루프(≈1332): `if h.get("is_manual"): continue` + INFO 로그
- main.py `_check_midweek_replacement()`(≈2164): 동일 가드
- run_hold_period_sells()(2268): 가드 추가 안 함 (의도적)

### Phase 3 — execute_buy() 신설 (web/dashboard_service.py)
execute_sell 대칭. 사이징→ATR(to_thread)→손절가→sell_lock/buy_lock→buy_market_order→
체결가 폴백→save_holding_position(is_manual=True)→save_trade→v17 필드 전체 전달.

### Phase 4 — 텔레그램 핸들러 (modules/reporter/telegram_notifier.py)
_pending_buy 상태 + _handle_buy_command + confirm/cancel 분기 + 라우터 /buy.
검증 순서: 6자리→토글→시간→holding중복→2차대기→당일매도→sell_lock→슬롯→종목명/현재가→확인.

## 7. 변경 파일
database.py / modules/trading_engine/capital_utils.py(신설) / main.py / config.py /
web/dashboard_service.py / modules/reporter/telegram_notifier.py / tests/test_manual_buy.py(신설)

## 8. 완료 기준
- py_compile 6파일 통과 + tests/test_manual_buy.py 통과
- code-tester 재검증 심각/주의 0건
- DB v19 자동 마이그레이션 + restart active 확인
- 수동 통합 시나리오 (정상 매수 + 거부 케이스들) 확인
- 문서 갱신 (CLAUDE.md / MEMORY.md / change_log.md)

## 9. 롤백
`MANUAL_BUY_ENABLED=false` + restart. is_manual=1 포지션은 로테이션 skip 보호 유지.
