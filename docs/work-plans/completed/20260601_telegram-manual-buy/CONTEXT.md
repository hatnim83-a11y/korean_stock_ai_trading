# CONTEXT — 텔레그램 `/buy` 수동 매수

**작성일**: 2026-06-01

## 변경 이유
v17 분할진입 안정화 후 수동 매수 수단 부재. 텔레그램 매도 구조 재사용으로 부담 낮음.
DB holding 저장 → 모니터링 자동 편입 구조라 핵심은 (1) 안전한 진입점 (2) 테마 로테이션 제외.

## 핵심 파일 & 라인 (현재 코드 상태)
### 텔레그램 명령어
- `modules/reporter/telegram_notifier.py:76~79` — _pending_sell/_pending_sell_all/_pending_sell_expires
- `:883` _handle_sell_command (패턴 참조) / `:989` _handle_confirm_command (buy 분기 추가) / `:1062` _handle_cancel_command
- 장시간 체크 함수 **없음** → config의 is_trading_day + now_kst로 `_is_valid_buy_time()` 신설
- rate-limit: chat_id 단위 공유 (sell/buy 공유 시 연속 명령 차단 주의 — 참고사항)

### 매수 파이프라인 (대칭 참조)
- `web/dashboard_service.py:472` execute_sell — execute_buy 구조 모델, :510~519 체결가 폴백 패턴
- `modules/trading_engine/trading_engine.py:1024` _save_positions — v17 필드 저장 패턴
- `modules/trading_engine/trading_engine.py:1048~1062` filled_price≤0 skip + ATR 박제 (to_thread 안에서만 동기 호출 안전)
- `database.py:1143` save_holding_position — is_manual 추가
- `modules/trading_engine/kis_order_api.py:293` buy_market_order

### 슬롯 사이징 (추출 대상)
- `main.py:1267~1279` 현금/총자산 폴백:
  - available_cash = get_orderable_cash(); ≤0이면 balance["cash"]
  - total_capital = balance["total_value"]; ≤0이면 max(settings.TOTAL_CAPITAL, available_cash)
- `main.py:1379~1407` swing_capital_pool = total_capital×SWING_CAPITAL_RATIO(0.9) /
  max_per_stock = pool//MAX_POSITIONS / per_slot = min(cash//slots, max_per_stock) /
  TRANCHE_ENTRY_ENABLED 시 ×TRANCHE_FIRST_RATIO(0.5)

### 모니터 등록
- `portfolio_monitor_v2.py:326` add_position (v17 필드 10개)
- `portfolio_monitor_v2.py:471` load_positions_from_db (재시작 자동 복원)
- `portfolio_monitor_v2.py:748~749` positions 비면 즉시 종료 (장외 매수 시 다음날까지 미감시)
- `portfolio_monitor_v2.py:786` stop_monitoring 직전 _dump_monitor_state
- `main.py:1682~1716` start_monitoring = stop+새 인스턴스+load_positions_from_db (WebSocket 재구독 위해)

### 동시성 잠금
- `modules/trading_engine/buy_lock.py` buy_lock.acquire(code, owner) / release (발주 후 try/finally 즉시)
- `modules/trading_engine/sell_lock.py` sell_lock.is_locked(code) — 수동 매수 전 체크

### 로테이션 매도 (skip 가드 위치)
- `main.py:≈1332` 화요일 재선정 루프: `h_theme = h.get("theme",""); if h_theme and h_theme not in selected_themes:` → 진입 전 is_manual 가드
- `main.py:≈2164` _check_midweek_replacement: `if h_theme != dropped_name: continue` → is_manual 가드
- `main.py:2268` run_hold_period_sells: buy_date 기반, theme 무관 → 가드 안 함

### DB 마이그레이션
- `database.py _migrate()` idempotent + auto-backup. 현재 최신 v18 → v19 추가

## 핵심 설계 결정 근거
- **is_manual 컬럼**: theme="수동"은 화요일 `not in selected_themes` 패턴에 걸려 자동 청산. 단일 컬럼이 안전.
- **start_monitoring stop+start**: WebSocket 동적 구독 불가(:1683~1688 docstring). add_position만으론 실시간 가격 미수신.
- **헬퍼 추출**: SWING_CAPITAL_RATIO/TRANCHE_FIRST_RATIO/MAX_POSITIONS 의존 비즈니스 로직 중복 금지.
- **ATR to_thread**: compute_atr는 pykrx/KIS 동기 HTTP. 텔레그램 핸들러는 메인 이벤트루프(main.py:278 create_task)라 직접 호출 시 전체 블로킹.

## 과거 버그 교훈
- **5/27 삼현**: add_position v17 필드 누락 → second_tranche_pending=False default → 2차 진입 영구 차단.
  execute_buy에서 v17 필드 10개 명시 전달 필수.
- **5/12 한화오션**: monitor_state.json 잔재 → BE 손절 즉시 활성화. 신규 매수는 잔재 없으나
  당일 매도 후 재매수는 주의 → 당일 매도 종목 /buy 거부로 봉쇄.

## 영향 범위
- closing_bet_system: 영향 없음 (별도 DB/계좌)
- 수동 종목 2차 진입(불타기): _check_and_execute_pyramid_in 그대로 적용. 종가베팅 가드 A(15:15)·
  B'(swing_pool)는 is_manual 무관 자동 작동 (수동 1차분도 swing_used에 포함)
