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

## Known Issues (as of 2026-03-03)
- **CRITICAL: screening_log table always empty**: `screener.py:486` says "DB 저장 완료" but 0 rows. Save logic may be broken or saving to wrong table/DB.
- **CRITICAL: profit_rate unit inconsistency in trades table**: Old data (id<=20) ratio, new data (id>=21) percent.
- **WARNING: DB portfolio trailing columns never updated at runtime**: portfolio.trailing_active=0 always. position_state has correct data.
- **WARNING: Dashboard creates new KISApi every SSE poll**: 42 inits in ~7h = ~6/min. Should cache.
- **WARNING: Dashboard log rotation error**: Tries to write to previous date's log file (e.g. `system_2026-02-28.log`) which doesn't exist. FileNotFoundError.
- **WARNING: KRX theme crawl fails**: `crawl_krx_themes:390` error `'시장'` key missing from response.
- **KIS API "Server disconnected"**: Intermittent. Non-critical.
- **Log file date uses UTC**: 08:00-08:59 KST logs go to PREVIOUS day's file.

## Resolved Issues
- ~~**Theme data key mismatch**~~: Fixed `da6276b` (2026-03-02). Screener now works on DB restore.
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
- **2026-03-03 12:58 KST**: Mon. PID 1560800 (14h), 0.1%CPU/137MB. Dashboard PID 100599 (4d), 0.6%CPU/149MB. 3 holdings (HD조선-3.1%, 이오텍+3.7%, HPSP-1.6%). Realized today: +140,900 (4 sells, all profit). Theme: CCTV&DVR(66.1)+반도체장비(65.2)+LED+홈쇼핑+SI. Screener 8 candidates, 4 bought. Techwing: partial 1+2+trailing L2, excellent. Hanamaicron trailing L1. screening_log empty. Dashboard KIS init 42x/7h.
- **2026-02-26 14:24 KST**: Thu. Screener 0 candidates (theme key mismatch). CRITICAL.
- **2026-02-25 20:57 KST**: 5 holdings +2.0%. 1 buy (HD조선).
- **2026-02-23 11:01 KST**: 5 holdings +2.2%. Partial sell bug discovered.
