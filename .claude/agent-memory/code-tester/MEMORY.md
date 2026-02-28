# Code Tester Agent Memory

## Key Patterns Found in This Codebase

### database.py
- `save_trade()`의 `date` 기본값이 `date.today()` (UTC 기준) — 기존 코드, 미수정 상태
  - 올바른 값: `now_kst().date()`
  - 위치: database.py line 524
  - UTC 15:00 이후 KST 날짜와 불일치 발생 가능
- `update_portfolio_shares()` silent failure 가능 — rowcount 체크 없음
  - 이론적으로 이중 holding row가 있으면 복수 row 업데이트됨 (실제 발생 가능성 낮음)

### portfolio_monitor_v2.py
- DB 작업 패턴: `Database()` → `connect()` → 작업 → `close()` — try/except로 감싸야 함
- `_close_position_in_db` 와 `_save_partial_sell_to_db` 가 동일 패턴 사용 (일관성 OK)
- `remaining_shares` 차감 후 DB 저장 순서 올바름 (line 695 이후 line 704 호출)

### telegram_notifier.py (명령어 리스너 추가, 2026-02-23)
- `_handle_portfolio_command()`: async 함수 내 동기 `KISApi.get_current_price()` 루프 호출 → 이벤트 루프 블로킹 (8종목 기준 ~3.3초)
  - 수정: `await asyncio.to_thread(lambda: kis.get_current_price(stock_code))` — 이후 수정 완료
- `_handle_portfolio_command()`: DB connect 후 exception 발생 시 `db.close()` 미호출 → try/finally 패턴으로 수정 완료
- `_handle_portfolio_command()`: `int(h.get('buy_price', 0))` — buy_price 컬럼이 REAL nullable이므로 None 반환 시 TypeError
  - 수정: `int(h.get('buy_price') or 0)` — 이후 수정 완료
- `send_daily_report()`: `date.today()` 사용 — UTC 기준 (기존 코드, 미수정)
- `stop_command_listener()`: `_listening=False`만 설정, task.cancel() 없음 → 종료 시 최대 30초 지연 (기존 이슈)
- main.py line 186: `asyncio.create_task()` 반환 Task 저장됨 (`self._listener_task`) — 이미 수정됨

### telegram_notifier.py + main.py (실현 손익 기능 추가, 2026-02-23)
- `send_daily_report()`: realized_trades, total_capital 파라미터 추가 — 기본값 있어 하위 호환 OK
- `_handle_portfolio_command()`: realized_pnl 표시 추가, DB try/finally 패턴 적용 OK
- 주의: `profit_amount` 컬럼이 REAL(float)이므로 `:,` 포맷 시 `62,500.0원` 으로 소수점 표시
  - `send_daily_report`와 `_handle_portfolio_command` 모두 `int()` 변환 없이 합산 후 포맷
  - 수정 권장: `int(realized_pnl)` 후 포맷
- `cash_remaining = total_capital - total_cost` — total_cost > total_capital 시 음수 가능 (주의)
- `database.py get_all_sell_trades()`: action 인덱스 없어 풀스캔이지만 레코드 수 적어 실용상 OK

### main.py + kis_api.py (상세 매수 리포트 기능 추가, 2026-02-24)
- `_send_buy_summary()`: market order는 price=0 → 손절/목표가 % 계산 스킵됨 (stop_loss/take_profit 절대값은 있음)
  - optimizer.py line 398: `"price": 0 if order_type == "market"` 확인됨
  - 수정 권장: `o.get('stop_loss', 0)` 절대값을 직접 표시하거나 `pos["price"]`를 order dict에도 포함하도록 optimizer 수정
- `get_company_overview()`: httpx.get()으로 네이버금융 직접 크롤링 — get_stock_name()과 동일 패턴 (OK)
  - `_rate_limit()` 미호출 (네이버 요청이므로 KIS rate limit 불필요, 정상)
  - div 중첩 시 (.*?) 패턴이 첫 번째 </div>에서 멈출 수 있음 — 실용상 허용 가능 (내용 일부만 잘려도 무방)
- `_morning_excluded` 아이템: `morning_screener._fetch_realtime_data()`에서 `code`, `name` 키 정규화됨 → `s.get('name', s.get('stock_name', ...))` 폴백 체인 OK
- `_slot_excluded` 아이템: `today_ai_analysis` 아이템, `code`/`name` 키 사용 → 폴백 체인 OK

### 보안 취약점 개선 (2026-02-24)
- `config.py`: `validate_stock_code()` 추가 — 6자리 숫자 regex, strip 포함, ValueError 발생
  - `get_stock_name`/`get_company_overview`: 캐시 확인 전에 validate 호출 (올바른 순서)
  - `get_current_price`는 validate 없음 — API 응답으로 자연 필터 (의도적)
- `fetch_stock_news` / `fetch_dart_disclosures`: validate 호출 후 ValueError 전파
  - verifier.py 호출부에 `try/except Exception` 감싸기 있어 안전
- `_is_authorized`: `chat_id == int(self.chat_id)` — int/str 타입 불일치 방어, ValueError/TypeError 처리
- `_is_rate_limited`: dict 메모리 누수 없음 — 미인가 사용자는 authorized 검사에서 먼저 차단됨
- `html.unescape`: 함수 내 지역 import (`import html as html_mod`) — 기능 정상, 사소한 성능 비용만 있음
- `pickle.load`: 보안 주석 추가 (로컬 캐시 전용 명시) — 실질 위험 없음
- `kis_api.py` `get_financial_info`: `float(output.get(...) or 0)` 패턴 — 기존 코드, `_safe_float()` 미사용 (미수정)

### web/ 대시보드 (신규, 2026-02-24)
- `web/app.py` PUBLIC_PATHS startswith 버그: `/api/v1/auth/loginXXX`, `/staticmalicious` 가 미들웨어 우회
  - 실질 위험 없음: api_router/sse_router에 `Depends(require_auth)` 이중 방어 있음
  - 수정: PUBLIC_PATHS에 exact match + `/static/` prefix 분리
- `web/api_routes.py`: `validate_stock_code()` ValueError → HTTP 500 (400이어야 함)
  - /actions/sell, /news 엔드포인트 영향
- `web/dashboard_service.py` `execute_sell()`: sync `sell_market_order()` 를 async context에서 직접 호출 → 이벤트루프 블로킹
  - 수정: `await asyncio.to_thread(order_api.sell_market_order, stock_code, quantity)`
- `web/sse_routes.py`: 지속 오류 시 SSE 루프 유지 (매 5초 오류 이벤트 전송, 서킷브레이커 없음)
- `web/dashboard_service.py` `execute_sell_all()`: 개별 실패 무관하게 항상 `success: True` 반환
- `.env` DASHBOARD_SECRET_KEY: `kst-dashboard-jwt-secret-key-change-me` (change-me 포함, 보안 취약)
- `web/templates/dashboard.html` line 295: 변수 `d` 미사용 (dead code)
- `web/auth.py`: TOKEN_EXPIRE_HOURS=24, MAX_ATTEMPTS=5, WINDOW_SECONDS=60 하드코딩 (.env 미참조)
- `database.py save_trade()` date 기본값 UTC 버그: execute_sell에서 date 미전달 → 기존 버그 영향

### kis_websocket.py + dashboard_service.py (2026-02-25)
- `_safe_int()` 모듈 레벨 추가 — `_parse_orderbook_data()`에 적용됨 (OK)
- `_parse_price_data()` 내 `int(data_fields[N])` 직접 호출 6곳 미교체 — 체결가는 장중에만 수신되어 'A' 플래그 없음 (수용 가능)
  - 인덱스: [2] 현재가, [4] 전일대비, [7] 시가, [8] 고가, [9] 저가, [13] 누적거래량
  - 방어적 코딩 관점에서 _safe_int 교체 권장 (참고 수준)
- `dashboard_service.py` `_get_db()` 싱글턴 패턴 — async gather 환경에서 안전 확인됨 (테스트 통과)
  - SQLite WAL 모드 + check_same_thread=False → 거래봇과 동시 접근 안전
  - uvicorn workers=1 (기본값) → 프로세스간 싱글턴 공유 없음
  - `app.py` lifespan에 `_db_instance.close()` 호출 없음 — OS가 프로세스 종료 시 정리 (실용상 OK)
  - `get_cursor()` 예외 발생 후 conn 생존 확인됨 (rollback 후 conn 유지)

### portfolio_monitor_v2.py (buy_date 폴백 + _restore_trailing_state 추가, 2026-02-26)
- portfolio 테이블에 buy_date 컬럼 없음, date 컬럼만 존재 — 폴백 로직 필수
- buy_date 파싱: `_dt.strptime(buy_date, "%Y-%m-%d").replace(tzinfo=now_kst().tzinfo)` — KST aware 정확
- `_restore_trailing_state()` line 336: `f"스탑 {pos.trailing_stop:,.0f}원"` — trailing_stop=None 시 TypeError
  - JSON 손상/수동 편집 시만 발생 가능 (정상 경로에서는 trailing_active=True이면 항상 float)
  - 수정 권장: `f"스탑 {pos.trailing_stop:,.0f}원" if pos.trailing_stop else "스탑 미설정"`
- `load_positions_from_db()`: DB connect 후 for loop 도중 예외 시 db.close() 미호출 — try/finally 필요
  - 기존 _close_position_in_db, _save_partial_sell_to_db도 동일 패턴 (기존 이슈)
- 함수 내부 `from datetime import datetime as _dt` — 모듈 상단에 이미 `from datetime import datetime` 있음 (중복)
  - 기능 정상, 스타일 이슈만

### trading_engine.py + portfolio_monitor_v2.py (실제 체결가 조회 추가, 2026-02-26)
- `execute_stop_loss()` / `execute_take_profit()`: 시장가 체결 후 1초 대기 + `get_order_status()` 조회 → `sell_price` 키로 result.update()
  - `result.get('order_id')` 빈문자열 falsy guard 있음 → 당일 전체 조회 방지됨 (올바름)
  - `orders and orders[0].get('filled_price', 0) > 0` guard → 빈 리스트 / 미체결 시 current_price 폴백 (올바름)
  - `import time` 중복: 모듈 상단(line 20) + 함수 내부(line 386, 438) — 기능 무관, 스타일 이슈
- **주의**: `execute_stop_loss()` / `execute_take_profit()`은 동기 함수 — 내부 `time.sleep(1)`이 asyncio 이벤트 루프를 블로킹
  - 호출 경로: async `_execute_stop_loss` → sync `execute_stop_loss` (1초 블로킹) → 모니터링 루프 1초 지연
  - 수정: `await asyncio.to_thread(self.trading_engine.execute_stop_loss, ...)` 패턴 사용 권장
  - 현재 포지션이 1~8개이므로 실용적 영향은 제한적 (허용 가능 수준)
- `_execute_stop_loss` line 669: `actual_sell_price` 콜백 직전 재할당 → success=False 시에도 안전 (중복이지만 안전한 패턴)
- `_execute_trailing_stop` line 962: ternary `actual_sell_price if result.get('success') else pos.current_price` → Python lazy evaluation으로 UnboundLocalError 없음 (직접 테스트 확인됨)
- `MockOrderApi`에 `get_order_status()` 메서드 없음 — `execute_stop_loss`/`execute_take_profit` 내부에서 `self.order_api.get_order_status()` 호출 시 `use_mock_api=True` 환경에서 AttributeError 발생
  - 단, `result.get('order_id')` falsy guard가 mock에서도 order_id를 반환하므로 조건 진입 가능 → AttributeError 위험
  - 실전 환경에서는 KISOrderApi 사용 → 정상 동작, mock 환경 한정 이슈
- `portfolio_monitor.py` (구버전): `on_stop_loss(pos, pos.current_price)` — actual_sell_price 미반영, 구버전은 main.py에서 미사용 (확인됨)
- `_close_position_in_db`의 profit_amount: `pos.profit`은 `pos.current_price` 기준, actual_sell_price와 슬리피지 차이만큼 오차 발생 (수용 가능)

### 매수/매도 전수 검토 (2026-02-26) — 심각 3건, 주의 4건, 참고 4건 — 즉시 수정 필요
#### 심각 버그 (이 봇의 핵심 구조적 문제)
1. **partial_X_executed 재시작 후 초기화 → 이중 매도**: DB/state.json에 partial 상태 저장 없음
   - monitor_state.json에 `partial_1_executed`, `partial_2_executed` 필드 추가 필요
   - _restore_trailing_state()에서 복원 로직 추가 필요
   - 현재 LG전자(066570) 1차 익절 완료 상태로 재시작 시 즉시 이중 매도 발생 확인됨
2. **3차 익절 profit_amount=0 버그**: remaining_shares 차감 후 _close_position_in_db 호출
   - _execute_partial_sell line 768 이후: remaining_shares-=sell_shares 먼저, 그 다음 _close_position_in_db
   - _close_position_in_db 내부: `(sell_price - buy_price) * pos.remaining_shares` → 0
   - 수정: _close_position_in_db 호출 전 sell_shares를 별도 변수로 보존하거나 파라미터 추가
3. **trading_engine._save_trades/positions: date.today() UTC 버그**
   - line 485, 516: `today = date.today()` → UTC 기준
   - 수정: `from config import now_kst; today = now_kst().date()`

#### 주의
- portfolio_monitor._close_position_in_db / _save_partial_sell_to_db: 'date' 키 없음 → save_trade() date.today() UTC 폴백
- trading_engine._execute_buy_orders: 타임아웃 실패 시 재시도 → KIS API 실제 체결된 주문 중복 재전송 가능
- DB에 shares 컬럼이 update_portfolio_shares()로 갱신되어 재시작 후 pos.shares가 원본이 아닌 잔여 수량이 됨
  → 재시작 후 1/2차 익절 비율 계산이 왜곡됨 (소수주 보정으로 실용적 영향 제한)

### 검증된 파일 목록
- database.py: update_portfolio_shares() 추가 (2026-02-23) — 주의 1건 (silent failure)
- database.py: get_all_sell_trades() 추가 (2026-02-23) — 통과
- portfolio_monitor_v2.py: _save_partial_sell_to_db() + _execute_partial_sell() 수정 (2026-02-23) — 통과
- telegram_notifier.py: 명령어 리스너 추가 (2026-02-23) — 심각 2건, 주의 3건, 수정 후 배포
- telegram_notifier.py + main.py: 실현 손익 기능 추가 (2026-02-23) — 주의 1건(float 포맷), 참고 1건, 배포 가능
- main.py + kis_api.py: 상세 매수 리포트 기능 추가 (2026-02-24) — 주의 1건(market order price=0), 참고 2건, 배포 가능
- 보안 취약점 개선 (2026-02-24) — 주의 2건, 참고 2건, 배포 가능
- web/ 대시보드 신규 (2026-02-24) — 주의 5건, 참고 4건, 수정 후 배포
- kis_websocket.py + dashboard_service.py (2026-02-25) — 참고 2건, 배포 가능
- portfolio_monitor_v2.py: buy_date 폴백 + _restore_trailing_state 추가 (2026-02-26) — 주의 2건, 참고 1건, 수정 후 배포
- trading_engine.py + portfolio_monitor_v2.py: 실제 체결가 조회 추가 (2026-02-26) — 주의 1건(이벤트루프 블로킹), 참고 1건, 배포 가능
- trading_engine.py + portfolio_monitor_v2.py: 체결가 조회 호환성 전면 리뷰 (2026-02-26) — 주의 2건, 참고 3건, 배포 가능
- 매수/매도 전수 검토 (2026-02-26) — 심각 3건, 주의 4건, 참고 4건 — 즉시 수정 필요
- DB 스키마 마이그레이션 전수 검토 (2026-02-26) — 주의 2건, 참고 2건, 배포 가능

### DB 스키마 마이그레이션 (2026-02-26)
- `database.py` `log_system_status()`: `date.today()` 사용 — UTC 기준 (기존 이슈, 신규 함수도 미수정)
- `_close_position_in_db` / `_save_partial_sell_to_db`: try/except 내 `db.close()`가 finally 없이 try 블록 마지막에 위치 → 예외 발생 시 DB 커넥션 누수 (기존 패턴, 실용상 영향 낮음)
- `_migrate_v2` + `_migrate_v3`: `_has_column()` raw cursor를 `get_cursor()` 블록 내에서 호출 — SQLite 허용, 테스트 통과
- `save_trade()` 반환값 `Optional[int]` 확인: `_close_position_in_db`/`_save_partial_sell_to_db`가 `trade_id`를 `save_trade_review`에 전달 — trade_id=None 시 SQLite NULL FK 허용 (FK 무결성 약함)
- `_migrate` 멱등성 확인: schema_version 체크 후 미적용 버전만 실행 → 재실행 안전 확인됨
- `_dump_monitor_state` 단위 변환: JSON에 `max_profit_rate * 100` (%), DB에 `% / 100` (비율) → `_restore_trailing_state`에서 DB source: 비율 그대로, JSON source: /100 → 왕복 변환 정확 확인됨
- 3차 익절 profit_amount=0 버그: `_close_position_in_db(pos, reason, price, sell_shares)` 4번째 파라미터 추가로 수정됨 — 이전 메모리 업데이트
- MDD 계산: `peak_value = max(prev_peak, current_total)` → mdd = (current - peak) / peak — 항상 <= 0 (올바름)
- `dashboard_service.py` `max_profit_rate`: DB source 0.12 (비율) 반환, HTML 템플릿 미사용 → 표시 이슈 없음

## Common Anti-Patterns to Check
- `date.today()` → UTC 서버에서 KST 날짜 불일치 (15:00 UTC 이후)
- `update` SQL 후 `cursor.rowcount` 미확인 → silent failure
- emoji in logger messages: 기존 코드에서 광범위하게 사용 중 (스타일 문제, 기능 무관)

### DB 마이그레이션 v8 체크리스트 결과 (2026-02-26)
- A1~A5, B1~B8, C1~C6, D1~D6, E1~E5, F1~F6, G1~G3, H1~H2, I1~I6, J1~J3, K1~K3, L1 모두 PASS
- L2~L4: 배포 후 검증 필요 (재시작 전에는 확인 불가)
- 발견된 주의 사항:
  - C2: LG전자 original_shares=3 (실제 4주 매수 → 마이그레이션 전 partial sell로 3주 됨) — 기존 데이터 한정, 신규 매수는 정확
  - J3: remove_position DB 삭제 실패 시 try/except만 (finally 없음) — minor connection leak
  - _save_daily_snapshot: db.close()가 finally 없이 try 블록 끝에 위치 — 예외 발생 시 누수 (기존 패턴)
  - I4: position_state 현재 비어있음 → 재시작 후 30초 내 _dump_monitor_state가 채움 (설계 의도)
  - FK enforcement=0 (SQLite 기본값) → trade_reviews FK 미강제 (실용상 무관)

### DB 스키마 v8 — 3차 런타임 시나리오 리뷰 (2026-02-26)
- **WAL 백업 불완전 버그 (경고)**: `_migrate()` line 181에서 `shutil.copy2(.db)` 만 복사 → `.db-wal` 미포함
  - 현재 data/trading.db-wal = 543KB (DB 159KB의 340%) — WAL 체크포인트 안된 데이터 존재
  - 서비스 중단 없이 마이그레이션 시 `.bak` 파일에 커밋된 데이터 누락 가능
  - 수정: `.db-wal`, `.db-shm`도 동시 복사하거나 sqlite3 backup API 사용
- **FK 실제 강제 확인**: `PRAGMA foreign_keys=ON` 실제 작동됨 — trades 삭제 시 FOREIGN KEY constraint failed
  - 단, 기존 FK=0 DB(구버전 코드)에는 orphaned trade_reviews가 이미 존재할 수 있음
- **_save_daily_snapshot 가격 최대 5분 지연**: DB current_price 사용 (in-memory pos 아님)
  - `_update_db_prices()`는 5분 간격 → 15:35 스냅샷에 15:25 가격 사용 가능
  - 수정 권장: `run_market_close()`에서 snapshot 전 `_update_db_prices()` 1회 추가 호출
- **old code 12-col INSERT on new 16-col trades table**: SQLite 허용 (NULL fill), 기능 무관
- **Dashboard simultaneous sell**: bot이 자동 매도 + 사용자가 동시 수동 매도 → KIS API 이중 주문 가능
  - DB 수준: 두 번째 close_position()은 0 rows UPDATE (무해), 하지만 KIS는 양쪽 처리
  - 구조적 이슈 (해결책: 매도 플래그 또는 bot ↔ dashboard 통신 채널 필요)
- **_update_db_prices, _restore_trailing_state, _dump_monitor_state: finally 없음** (DB 커넥션 누수 가능)
  - `_update_db_prices` line 513, `_restore_trailing_state` line 333, `_dump_monitor_state` line 581
  - SQLite Python driver GC 회수로 실용상 누수 없음, 하지만 패턴 불일치
- **_dump_monitor_state 780 cycles/day 성능**: 6.38ms/cycle → 하루 총 ~5초 오버헤드 (수용 가능)
- **position_state 고아 행**: 매도 실패 시 delete_position_state()가 미호출되어 orphan 가능
  - restore 시 실보유 종목만 state 참조 → 고아 행은 무해 (메모리만 낭비)
- **3-stage 분할 매도 원자성 없음**: 12개 commits, 4개 connections 사용
  - close_position() 커밋 후 save_trade() 실패 시 trades 레코드 없이 포지션 closed 상태
  - WAL + timeout=30s로 실제 발생 확률 매우 낮음
- **포지션 복원 정상 동작 확인**: DB position_state에 partial_1=True → 재시작 후 올바르게 복원됨
  - "첫 30초 내 재시작" 시나리오: DB가 이미 partial 상태를 보존하고 있음
  - JSON 폴백도 partial_1_executed 포함 (이전 fix로 해결됨)

### 대시보드 전체 리뷰 (2026-02-26) — 상세: dashboard-review.md
- execute_sell save_trade: stock_name=stock_code, price=0, amount=0, profit_amount=NULL → 실현손익 합산 누락
- check_rate_limit: 성공/실패 모두 카운트 → 정상 로그인 5회 후 1분 잠금
- execute_sell: get_portfolio 중복 조회 (quantity=None 시 2번)
- close_position 후 delete_position_state 미호출 → 고아 row (무해)
- auth.py TOKEN_EXPIRE_HOURS/MAX_ATTEMPTS/WINDOW_SECONDS 하드코딩
- datetime.utcnow() in create_token: jose JWT exp은 UTC 기준 → 올바름 (now_kst로 교체 금지)

### 테마 파이프라인 리뷰 (2026-02-27) — 상세: theme-pipeline-review.md
- 심각: DB 복원 후 재사용 경로에서 url 키 없음 → 09:05 스크리닝 종목 0개
  - 재시작 후 같은 주 비월요일에만 발생 (지속 실행 중인 경우 정상)
  - 수정: 재사용 판정에 `all(t.get("url") for t in self.today_themes)` 조건 추가
- 주의: .env TOP_THEME_COUNT=4 미수정 → config.py default=5 변경 미반영
- 통과: key 구조 일관성, 월요일 로테이션 로직, 긴급 트리거 충돌 없음
- crawlers.py line 351-352: `datetime.now()` 사용 (KRX 날짜 조회, 날짜만 사용하므로 실용상 무해)

### execute_sell 개선 검증 (2026-02-26)
- 심각 버그: `_get_kis()` 미정의 — 파일 내 함수명은 `_get_kis_api()` (line 407 NameError)
  - py_compile은 통과하지만 런타임 체결가 폴백 경로에서 NameError 발생
  - 수정: line 407 `_get_kis()` → `_get_kis_api()`
- 참고: sell_price=0 최종 저장 시 경고 로그 없음 → price=0, amount=0 조용히 저장됨
- 참고: execute_sell_all 내 execute_sell 루프 → N+1 get_portfolio 호출 (기존 패턴, 미수정 상태)

### 테마 로테이션 월요일 기반 전환 (2026-02-27)
- config.py TOP_THEME_COUNT: 4→5 변경, THEME_REVIEW_DAYS 설명 업데이트
- selector.py line 54: count 기본값 4→5 변경. run_daily_theme_analysis_sync 기본값 여전히 4 (main.py에서 미호출, 무관)
- selector.py docstring line 18-19: `count=4` 예시 주석 미업데이트 (참고 수준)
- theme_rotator.py should_review: `days_held >= THEME_REVIEW_DAYS` → `now_kst().weekday() == 0` 변경
  - now_kst import 확인됨 (line 31)
  - display_status() line 456, 462: `review_days`일 표시 잔재 — 기능 무관, 참고용 출력만
- main.py ISO week same_week 체크: `rotation_date.year == today.year` 사용
  - 연말 edge case: 12/29(월) 선정 시 1/1(목) 체크에서 same_week=False → 월요일 아님에도 재분석
  - 원인: ISO W1이 12/29에서 시작하면 캘린더 연도가 달라짐 (2025 vs 2026)
  - 수정: `rotation_date.isocalendar().year == today.isocalendar().year` 사용 권장
  - 영향: 연 1회, 비파괴적 (추가 테마 분석만 발생)
- main.py line 318: `days_since` 변수 계산 후 미사용 (dead variable, 이전 로직 잔재)
- main.py normalized dict: `{"name": ..., "theme": ..., "score": ..., "total_score": ...}` — screener/rotator 양쪽 만족. OK
- 키 스키마: 크롤러 "name" → 스코어러 "theme" 추가 → 둘 다 today_themes에 존재 → 하위 호환성 유지

## Test Approach
- `py_compile` 로 구문 검사 후 런타임 import 테스트 병행
- DB 작업은 tempfile sqlite3로 격리 테스트
- `remaining_shares` 순서는 git diff로 변경 범위 먼저 확인 후 로직 추적
- WAL 모드 DB 복사 시 항상 `.db`, `.db-wal`, `.db-shm` 3파일 모두 확인
