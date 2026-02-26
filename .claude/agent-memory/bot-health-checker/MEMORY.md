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

## DB Schema (updated 2026-02-26)
- portfolio: id, date, stock_code, stock_name, theme, weight, shares, buy_price, current_price, stop_loss, take_profit, profit_rate, profit_amount, status (holding/closed), created_at, updated_at + v8 columns (original_shares, buy_date, partial_1/2/3_executed, trailing_active/level/stop, highest_price, max_profit_rate)
- trades: id, date, time, stock_code, stock_name, action (buy/sell), shares, price, amount, reason, profit_rate, profit_amount, order_id, created_at + v8 columns (buy_price, filled_price, slippage, remaining_shares)
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at (NO status/selected_date columns)
- position_state: stock_code (PK), current_price, highest_price, trailing_active, trailing_level, trailing_stop_price, max_profit_rate, partial_1/2/3_executed, remaining_shares, last_updated
- **IMPORTANT**: trades.profit_rate unit is inconsistent! Old data (id<=20): ratio (0.07=7%). New data (id>=21): percent (7.0=7%).
- NOTE: NO `timestamp` column in trades; use `created_at` for ordering

## Stop Loss Calculation
- Uses ATR-based dynamic stop loss, NOT just DEFAULT_STOP_LOSS
- Clamped to range: MIN=-12%, MAX=-5% (see `calculators.py`)
- DEFAULT_STOP_LOSS in .env is -0.08 but actual SL varies per stock via ATR

## Known Issues (as of 2026-02-26)
- **CRITICAL: Theme data key mismatch** (found 2026-02-26): `main.py:176` creates `{"theme": name, "score": val}` on DB restore, but `screener.py:441` expects `{"name": name, "url": url, "total_score": val}`. Causes screener to find 0 stocks because `theme.get("name")` = None. Fresh analysis works; DB restore fails. Fix: normalize keys in main.py or screener.py.
- **CRITICAL: profit_rate unit inconsistency in trades table**: Old data ratio, new data percent.
- **WARNING: DB portfolio trailing columns never updated at runtime**: portfolio.trailing_active=0 always. position_state has correct data. Bot uses position_state for restore.
- **WARNING: Dashboard creates new KISApi every SSE poll**: 2195 inits/5h = ~7/min. Should cache.
- **WebSocket orderbook parse error 'A'**: Market close only. Non-critical.
- **KIS API "Server disconnected"**: Intermittent. Non-critical.
- **Log file date uses UTC**: 08:00-08:59 KST logs go to PREVIOUS day's file.

## Resolved Issues
- ~~**Partial sell not saved to DB**~~: Fixed `5a336fe`.
- ~~**Dashboard manual sell full close instead of partial**~~: Fixed `4f43344`.
- ~~**Service is disabled**~~: 2026-02-20.
- ~~**Morning filter stock name None**~~: 2026-02-20.
- ~~**Supply filter all zeros**~~: 2026-02-20.

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul)
- 08:00 Theme rotation check | 08:30 Theme analysis | 09:05 Screening | 09:25 Auto buy
- 09:26 Monitoring start | 15:30 Monitoring stop | 15:35 Close cleanup | 16:00 Daily report

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,000,000 KRW, MAX_POSITIONS: 5, per_slot: 800,000
- DEFAULT_STOP_LOSS: -8% (actual ATR-based: -5%~-12%)
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Health Check Patterns
- Process: PID from trading_system.pid, then `ps -p PID`
- API: KIS=500, Claude=405, Telegram=200 (getMe) = all REACHABLE
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Weekend: No logs = NORMAL (mon-fri CronTrigger)
- Orderbook parse errors: ONLY at market close 15:20-15:30 KST = NORMAL

## Recent Health Checks
- **2026-02-26 14:24 KST**: Thu. PID 39777 (11min), 0.4%CPU/62MB. Dashboard PID 458, 1.2%CPU/113MB. 3 holdings (TCK+9%, LG+10%, HD조선-2.3%). TCK/LG trailing L1 active. Screener 0 candidates (theme key mismatch). 52 bot inits (code changes). 20 errors, 9 warnings. FOREIGN KEY error 1x. Theme mismatch CRITICAL for tomorrow.
- **2026-02-25 20:57 KST**: 5 holdings +2.0%. 1 buy (HD조선). Hansem trailing L1.
- **2026-02-23 11:01 KST**: 5 holdings +2.2%. Partial sell bug discovered.
