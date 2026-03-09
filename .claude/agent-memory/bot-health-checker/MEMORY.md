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
- portfolio: id, date, stock_code, stock_name, theme, weight, shares, buy_price, current_price, stop_loss, take_profit, profit_rate, profit_amount, status (holding/closed), created_at, updated_at + v8 columns
- trades: id, date, time, stock_code, stock_name, action (buy/sell), shares, price, amount, reason, profit_rate, profit_amount, order_id, created_at + v8 columns
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at + category(v10)
- trade_reviews: id, trade_id, stock_code, stock_name, buy_date, **sell_date**, buy_price, sell_price, shares, hold_days, profit_rate, profit_amount, sell_reason, strategy_type, trailing_level, max_profit_during_hold, theme, ai_review, lesson_learned, created_at (NO 'date' column - use sell_date)
- position_state: stock_code (PK), current_price, highest_price, trailing_active, trailing_level, trailing_stop_price, max_profit_rate, partial_1/2/3_executed, remaining_shares, last_updated
- **IMPORTANT**: trades.profit_rate unit is inconsistent! Old (id<=20): ratio. New (id>=21): percent.

## Stop Loss Calculation
- ATR-based dynamic, clamped to MIN=-12%, MAX=-5% (see `calculators.py`)

## Known Issues (as of 2026-03-09)
- **WARNING: Dashboard 24/7 SSE polling**: 5-sec interval portfolio query even on weekends/nights. Source: dashboard SSE endpoint. Creates ~17,280 queries/day + KISApi init each time. CPU: 1h26m over ~3 days.
- **WARNING: portfolio.highest_price/max_profit_rate not synced with position_state**: position_state tracks correctly (63500, 7.08%) but portfolio row has (None, 0.0). Only cosmetic if monitor reads from position_state.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError. Falls back to Naver-only data.
- **WARNING: Theme scores low (~32-43)**: Since 03-04 switch to daily collection + weighted avg. news_count=0, ai_sentiment=0 in 03-09 data (only momentum contributes).
- **INFO: Telegram still unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs go to PREVIOUS day's file.

## Resolved Issues
- ~~**screening_log table always empty**~~: Fixed by 2026-03-04.
- ~~**Theme data key mismatch**~~: Fixed `da6276b`.
- ~~**Partial sell not saved to DB**~~: Fixed `5a336fe`.
- ~~**Dashboard manual sell full close instead of partial**~~: Fixed `4f43344`.

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul, all have _skip_on_holiday)
- 08:00 Theme rotation check | 08:30 Theme analysis | 09:05 Screening | 09:25 Auto buy
- 09:26 Monitoring start | 15:30 Monitoring stop | 15:35 Close cleanup | 16:00 Daily report
- 16:10 Daily health check | 17:00 Post-trade analysis | 17:05 Daily theme collection | Fri 17:30 Weekly trade review
- NOTE: day_of_week='mon-fri' on all jobs, but _skip_on_holiday only checks holidays lib (not KRX closures)
- NOTE: 08:30 theme analysis runs EVERY weekday (not just Tue). On non-Tue, it still selects themes from existing DB data.

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,406,493 KRW, MAX_POSITIONS: 5, per_slot: cash/slots
- DEFAULT_STOP_LOSS: -8% (actual ATR-based: -5%~-12%)
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Health Check Patterns
- Process: PID from trading_system.pid, then `ps -p PID`
- API: KIS=200, Claude=405 = REACHABLE; Telegram=000 = UNREACHABLE
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Service restart during market hours: auto-resume via `_resume_monitoring_if_needed`
- Dashboard SSE is a separate logging stream - `journalctl -u trading_dashboard` to see its logs
- System logs from previous days: `zcat logs/system_YYYY-MM-DD.*.log.gz`

## Theme Data Accumulation Pattern
- 08:30 KST: Crawl + score themes, select top 5 for today's trading (saves to themes table)
- 17:05 KST: Daily theme collection (30 themes with full scoring: momentum + news + AI sentiment)
- Weekly aggregation for Tue reselection uses last 6 business days weighted average
- 03-02/03-03: Only 5 themes (before 17:05 schedule existed). 03-04+: 30-35 themes/day.

## Recent Health Checks
- **2026-03-09 10:55 KST**: Mon. BEARISH (KOSPI -7.28%, KOSDAQ -5.78%). 1 holding (Pearl Abyss 263750, buy 59300, +2.19%). 5 candidates -> 4 gap-filtered -> 1 bought. No errors. Dashboard SSE 24/7 polling confirmed. Theme data 6 biz days accumulated for Tue reselection. Service uptime 2d+.
- **2026-03-04 15:36 KST**: Tue. ALL 4 positions stopped out. P&L: -350,000.
- **2026-03-03 12:58 KST**: Mon. 3 holdings. +140,900 realized. Techwing trailing L2.
