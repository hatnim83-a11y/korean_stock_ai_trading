# Bot Health Checker - Agent Memory

## Key File Locations
- Logs: `/home/hatni/korean_stock_ai_trading/logs/` (system_YYYY-MM-DD.log, error_YYYY-MM-DD.log, trading_YYYY-MM-DD.log)
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
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at
- trade_reviews: id, trade_id, stock_code, stock_name, buy_date, **sell_date**, buy_price, sell_price, shares, hold_days, profit_rate, profit_amount, sell_reason, strategy_type, trailing_level, max_profit_during_hold, theme, ai_review, lesson_learned, created_at (NO 'date' column - use sell_date)
- position_state: stock_code (PK), current_price, highest_price, trailing_active, trailing_level, trailing_stop_price, max_profit_rate, partial_1/2/3_executed, remaining_shares, last_updated
- **IMPORTANT**: trades.profit_rate unit is inconsistent! Old (id<=20): ratio. New (id>=21): percent.

## Stop Loss Calculation
- ATR-based dynamic, clamped to MIN=-12%, MAX=-5% (see `calculators.py`)

## Known Issues (as of 2026-03-04)
- **CRITICAL: Telegram API unreachable from GCP VM**: DNS resolves but TCP fails. Command listener and send_message all error. GCP firewall or Telegram geo-block.
- **WARNING: Theme scores dropped 03-03(~66) to 03-04(~36)**: New daily collection + weighted aggregation. May need tuning.
- **WARNING: Dashboard creates new KISApi every SSE poll**: Still present (dashboard 5+ days uptime).
- **WARNING: Dashboard excessive portfolio polling**: 83 zero-result queries in ~6min after close.
- **KIS API "Server disconnected"**: Intermittent, non-critical.
- **Log file date uses UTC**: 08:00-08:59 KST logs go to PREVIOUS day's file.

## Resolved Issues
- ~~**screening_log table always empty**~~: Fixed by 2026-03-04. Now has 85 rows.
- ~~**Theme data key mismatch**~~: Fixed `da6276b`.
- ~~**Partial sell not saved to DB**~~: Fixed `5a336fe`.
- ~~**Dashboard manual sell full close instead of partial**~~: Fixed `4f43344`.

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul)
- 08:00 Theme rotation check | 08:30 Theme analysis | 09:05 Screening | 09:25 Auto buy
- 09:26 Monitoring start | 15:30 Monitoring stop | 15:35 Close cleanup | 16:00 Daily report
- 17:00 Post-trade analysis | 17:05 Daily theme collection | Fri 17:30 Weekly trade review

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,406,493 KRW, MAX_POSITIONS: 5, per_slot: cash/slots
- DEFAULT_STOP_LOSS: -8% (actual ATR-based: -5%~-12%)
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Health Check Patterns
- Process: PID from trading_system.pid, then `ps -p PID`
- API: KIS=200, Claude=405 = REACHABLE; Telegram=000 = UNREACHABLE (as of 2026-03-04)
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Service restart during market hours: auto-resume via `_resume_monitoring_if_needed`

## Recent Health Checks
- **2026-03-04 15:36 KST**: Tue. BEARISH (KOSPI -3.12%, KOSDAQ -3.35%). ALL 4 positions stopped out. P&L: -350,000. Telegram DOWN. Theme scores dropped to ~36. screening_log now working (85 rows). New schedules (17:00, 17:05) registered. 2 service restarts for code deploy (12:14, 12:23 KST). Dashboard 5d+ uptime.
- **2026-03-03 12:58 KST**: Mon. 3 holdings. +140,900 realized. Techwing trailing L2.
- **2026-02-26 14:24 KST**: Screener 0 candidates (theme key mismatch). CRITICAL.
- **2026-02-25 20:57 KST**: 5 holdings +2.0%.
