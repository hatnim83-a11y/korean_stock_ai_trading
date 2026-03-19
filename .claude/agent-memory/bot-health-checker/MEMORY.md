# Bot Health Checker - Agent Memory

## Key File Locations
- Logs: `/home/hatni/korean_stock_ai_trading/logs/` (system_YYYY-MM-DD.log, error_YYYY-MM-DD.log, trading_YYYY-MM-DD.log)
- Logs are compressed (.gz) after rotation at UTC midnight; current day only has uncompressed .log
- Database: `/home/hatni/korean_stock_ai_trading/data/trading.db`
- PID file: `/home/hatni/korean_stock_ai_trading/trading_system.pid`
- Config: `/home/hatni/korean_stock_ai_trading/config.py`, `.env`
- Main entry: `/home/hatni/korean_stock_ai_trading/main.py`
- Scheduler: `/home/hatni/korean_stock_ai_trading/scheduler.py`
- Systemd service: `/etc/systemd/system/trading_system.service`
- Dashboard service: `/etc/systemd/system/trading_dashboard.service`
- Telegram notifier: `modules/reporter/telegram_notifier.py`
- Monitor state: `data/monitor_state.json`

## API Class Names
- Order API: `KISOrderApi` -- init params: `(app_key, app_secret, account_no, is_mock)` NOT `is_real`
- Screener API: `KISApi` (in `modules/stock_screener/kis_api.py`)
- Token sharing: `KISApi._shared_token` class variable, reused by `KISOrderApi`
- 1-minute cooldown on token issuance per app key

## DB Schema (updated 2026-03-16)
- portfolio, trades, themes, trade_reviews, position_state, screening_log, daily_snapshots, strategy_stats
- **trades columns**: id, date, time, stock_code, stock_name, action, shares, price, amount, reason, profit_rate, profit_amount, order_id, created_at, buy_price, filled_price, slippage, remaining_shares
- **portfolio columns**: id, date, stock_code, stock_name, theme, weight, shares, buy_price, current_price, stop_loss, take_profit, profit_rate, profit_amount, status, created_at, updated_at, original_shares, buy_date, partial_1/2/3_executed, trailing_active, trailing_level, trailing_stop, highest_price, max_profit_rate
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at + category(v10) + selected(v11) + url(v12)
- screening_log: id, date, stock_code, stock_name, theme, stage, passed, score, reject_reason, details_json, created_at
- position_state: stock_code(PK), current_price, highest_price, trailing_active, trailing_level, trailing_stop_price, max_profit_rate, partial_1/2/3_executed, remaining_shares, last_updated (NO stock_name column)

## Known Issues (as of 2026-03-17)
- **BUG: 일별 수집이 주간 선정 점수를 덮어씀**: database.py:574-596에서 selected=False 저장 시 기존 selected=1 행의 score/momentum/ai_sentiment를 일별 수집 값으로 UPDATE. 3/17 스페이스X 53.0→40.0 재확인(Phase2.5점검). 일별수집 30개 중 기존 selected=1 행과 겹치는 테마가 UPDATE됨 — created_at 미변경으로 판별 어려움. 수정 방법: selected=1 행은 UPDATE를 skip하거나, score/momentum/ai_sentiment를 제외하고 url만 갱신.
- **BUG: LS머트리얼즈 매수 실패(주문가능금액 초과)**: 시장가 주문 시 상한가 증거금 문제 재발(commit 572d4ea에서 수정했으나 여전히 발생). 3/17 09:25 KST.
- **WARNING: 로그 로테이션 에러**: UTC midnight에 system_2026-03-16.log가 이미 압축/삭제되어 FileNotFoundError. loguru _file_sink의 ctime 조회 실패.
- **WARNING: KIS API 500 에러 (장외 시간)**: 8,968회/일. 대시보드 SSE 폴링이 원인.
- **WARNING: Dashboard 24/7 SSE polling**: 장중 ~6초, 장외 30초 DB poll. 에러 로그 대부분 차지.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError. Falls back to Naver-only.
- **WARNING: 8개 predefined 테마 네이버 미발견**: K-방산, 바이오, 로봇, 원자력, 수소, 건설, 게임, 철강.
- **WARNING: httpx AsyncClient 이벤트 루프 에러**: asyncio event loop closed 후 aclose() 호출 시 RuntimeError. 기능 영향 없음(경고성).
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04. 로그에 발송 시도 흔적 없음.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.

## Scheduler (KST, CronTrigger timezone=Asia/Seoul, _skip_on_holiday)
- 08:00 Theme rotation | 08:30 Theme analysis | 09:05 Screening | 09:25 Auto buy
- 09:26 Monitor start | 15:30 Monitor stop | 15:35 Close cleanup | 16:00 Report
- 16:10 Health check | 17:00 Post-trade | 17:05 Daily theme collection | Fri 17:30 Weekly review
- `_skip_on_holiday` checks `is_trading_day()` (weekday + holidays lib); weekend/holiday skips

## Theme System Architecture
- **08:30 화요일**: aggregate_weekly_scores(6영업일 가중평균) → select_themes_with_retention → Step 4 URL 보충(crawl_all_themes) → DB 저장
- **08:30 비화요일**: DB 복원 테마 재사용 (same_week=True if days_since_rotation < 7 and not Tuesday)
- **08:30 비화요일 (재사용)**: URL 없으면 crawl_all_themes + search_naver_theme + _search_naver_upjong 폴백
- **09:05**: screener가 theme URL로 종목 수집 → 없으면 search_naver_theme → _search_naver_upjong 폴백
- **17:05**: crawl_all_themes(206개) → score_themes(상위30) → AI 분석(20) → DB 저장(30개, selected=False)
- **Theme restoration on restart**: get_last_theme_analysis_date() → MAX(date) WHERE selected=1 → get_top_themes(date)

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,406,493 KRW, MAX_POSITIONS: 5
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Recent Health Checks
- **2026-03-17 19:30 KST (Phase 2.5 점검)**: AI감성분석 정상(20/20, 2.5~7.5범위). 모멘텀 KIS API 정상(15/15매핑, 27종distinct). 화요일 실시간보강 정상(모멘텀11건+AI7건 보정). 덮어쓰기 버그 재발(스페이스X 53.0→40.0).
- **2026-03-17 (화) 테마 점검**: 주간 선정 정상 실행(08:30 KST). 5개 테마(유지3+신규2). 신규: 5G, SOFC. 탈락: 항공기부품, HBM. LS머트리얼즈 매수 실패. 한국항공우주 2차 익절 1주. 보유4종목(항공기부품 테마 탈락 3종목 주의). 일별 수집→selected=1 점수 덮어쓰기 버그 발견.
- **2026-03-16 10:01 KST**: Mon. 5 holdings. 전 스케줄 정상. 신규 매수 2건, 1차 익절 1건.
- **2026-03-15 22:33 KST**: Sun. 3 holdings. 수동 테스트 DB 오염 발견→해결.
- **2026-03-10 23:10 KST**: Tue theme check. 08:30 weekly agg OK.
- **2026-03-04 15:36 KST**: Tue. ALL 4 stopped out. P&L: -350K.
