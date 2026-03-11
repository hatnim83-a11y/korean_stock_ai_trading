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

## Known Issues (as of 2026-03-10)
- **CRITICAL (fixed in code but affected today)**: 화요일 Step 4 URL 보충 -- commit 4b49bac 배포가 08:30 이후 → 구 코드(0245c33)에 Step 4 없어 스크리닝 0건. 서비스 14:07 재시작으로 수정 반영 완료. 다음 화요일부터 정상 예상.
- **WARNING: screener URL 폴백**: search_naver_theme + _search_naver_upjong이 해운/게임/조선/반도체/구제역 등에 모두 실패. 네이버 테마 페이지에서 해당 테마 검색 가능 여부 별도 확인 필요.
- **WARNING: Dashboard 24/7 SSE polling**: 5s interval portfolio query 24/7. ~17,280 queries/day.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError. Falls back to Naver-only.
- **WARNING: 8개 predefined 테마 네이버 미발견**: K-방산, 바이오, 로봇, 수소, 조선, 엔터테인먼트, 음식료, 철강 -- 17:05 크롤링 시 "기본값 사용" 경고.
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.
- **INFO: 2/27 themes data missing**: weekly_aggregator effectively uses 5 days instead of 6.

## Scheduler (KST, CronTrigger timezone=Asia/Seoul, _skip_on_holiday)
- 08:00 Theme rotation | 08:30 Theme analysis | 09:05 Screening | 09:25 Auto buy
- 09:26 Monitor start | 15:30 Monitor stop | 15:35 Close cleanup | 16:00 Report
- 16:10 Health check | 17:00 Post-trade | 17:05 Daily theme collection | Fri 17:30 Weekly review

## Theme System Architecture
- **08:30 화요일**: aggregate_weekly_scores(6영업일 가중평균) → select_themes_with_retention → Step 4 URL 보충(crawl_all_themes) → DB 저장
- **08:30 비화요일**: DB 복원 테마 재사용 or crawl_all_themes → score_themes → select_top_themes
- **09:05**: screener가 theme URL로 종목 수집 → 없으면 search_naver_theme → _search_naver_upjong 폴백
- **17:05**: crawl_all_themes(206개) → score_themes(상위30) → AI 분석(20) → DB 저장(30개)
- **Key insight**: 화요일 08:30 집계 데이터에는 URL이 없으므로 Step 4 보충이 필수

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,406,493 KRW, MAX_POSITIONS: 5
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Recent Health Checks
- **2026-03-10 23:10 KST**: Tue theme check. 08:30 weekly agg OK (101 themes). Step 4 URL not executed (old code). Screening 0 candidates. 17:05 collection OK (34 themes). DB accumulation normal since 03-04.
- **2026-03-09 10:55 KST**: Mon. 1 holding (Pearl Abyss). 5 candidates -> 1 bought. No errors.
- **2026-03-04 15:36 KST**: Tue. ALL 4 stopped out. P&L: -350K.
