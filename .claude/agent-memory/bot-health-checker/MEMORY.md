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

## DB Schema (updated 2026-03-04)
- portfolio, trades, themes, trade_reviews, position_state, screening_log, daily_snapshots, strategy_stats
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at + category(v10)
- screening_log: id, date, stock_code, stock_name, theme, stage, passed, score, reject_reason, details_json, created_at
- position_state: stock_code(PK), current_price, highest_price, trailing_active, trailing_level, trailing_stop_price, max_profit_rate, partial_1/2/3_executed, remaining_shares, last_updated (NO stock_name column)

## Known Issues (as of 2026-03-15)
- **WARNING: 수동 테스트 selected=1 오염**: 3/15 수동 테스트로 5개 테마가 selected=1로 DB 저장됨 (id 280-284). 서비스 재시작 시 get_last_theme_analysis_date()가 3/15를 반환하여 잘못된 테마 복원 가능. 현재 서비스는 메모리에 3/11 테마 로드 상태라 정상.
- **WARNING: KIS API 500 에러 (장외 시간)**: 주말/장외 시간에 현재가 조회 시 500 에러 반복. 대시보드 SSE 폴링(5s)이 원인.
- **WARNING: Dashboard 24/7 SSE polling**: 5s interval portfolio query. 오늘(일요일) 4,677회 발생. 시스템 로그 80%+ 차지.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError. Falls back to Naver-only.
- **WARNING: 8개 predefined 테마 네이버 미발견**: 2차전지, K-방산, 바이오, 로봇, 수소, 금융, 철강, 화학 -- 크롤링 시 "기본값 사용" 경고.
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.
- **INFO: 토큰 403 에러**: 장외시간 토큰 발급 시도 → 403 (정상 동작)

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
- **2026-03-15 22:33 KST**: Sun. 3 holdings (두산에너빌리티, JYP Ent., 한국항공우주). 수동 테스트 DB 오염 발견 (3/15 selected=1). KIS 500 에러(장외 시간 정상). Dashboard polling 4,677회. 내일(월) 3/11 테마 유지 예정 (days_since=5 < 7).
- **2026-03-10 23:10 KST**: Tue theme check. 08:30 weekly agg OK. Step 4 URL not executed (old code). 17:05 OK.
- **2026-03-09 10:55 KST**: Mon. 1 holding. 5 candidates -> 1 bought.
- **2026-03-04 15:36 KST**: Tue. ALL 4 stopped out. P&L: -350K.
