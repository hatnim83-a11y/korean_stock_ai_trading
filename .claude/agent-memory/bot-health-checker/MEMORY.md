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

## DB Schema (updated 2026-02-25)
- portfolio: id, date, stock_code, stock_name, theme, weight, shares, buy_price, current_price, stop_loss, take_profit, profit_rate, profit_amount, status (holding/closed), created_at, updated_at
- trades: id, date, time, stock_code, stock_name, action (buy/sell), shares, price, amount, reason, profit_rate, profit_amount, order_id, created_at
- themes: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment, created_at (NO status/selected_date columns)
- NOTE: NO `timestamp` column in trades; use `created_at` for ordering
- NOTE: NO `buy_date` column in portfolio; use `date` field

## Stop Loss Calculation
- Uses ATR-based dynamic stop loss, NOT just DEFAULT_STOP_LOSS
- Clamped to range: MIN=-12%, MAX=-5% (see `calculators.py`)
- DEFAULT_STOP_LOSS in .env is -0.08 but actual SL varies per stock via ATR

## Known Issues (as of 2026-02-25)
- **CRITICAL: Partial sell not saved to DB**: `_execute_partial_sell` in `portfolio_monitor_v2.py` does NOT save sell trade to DB or update portfolio shares. Only full liquidation triggers `_close_position_in_db`. Confirmed on 2/23 with Y2Solution.
- **WebSocket orderbook parse error 'A'**: `_parse_orderbook_data` in `kis_websocket.py:509` fails with `int('A')`. Happens ONLY at market close (15:20-15:30 KST). KIS sends non-numeric chars in orderbook fields during closing auction. ~5000 errors/day. Non-critical (only orderbook display). Fix: add per-field try/except or filter non-numeric.
- **KIS API "Server disconnected"**: Intermittent `get_current_price` failures. ~48/day on 2/25. Stocks: 064760, 066570, 009240, 031980. HTTP keepalive timeout. Non-critical.
- **Dashboard CPU 5.9%**: Higher than expected. SSE polling every ~8s causes frequent DB connect/disconnect.
- **Log file date uses UTC**: loguru `{time:YYYY-MM-DD}` uses server UTC. 08:00-08:59 KST logs go to PREVIOUS day's file.

## Resolved Issues
- ~~**Service is disabled**~~: 2026-02-20 `systemctl enable` done.
- ~~**Morning filter stock name None**~~: 2026-02-20 fix (07cc814).
- ~~**Supply filter all zeros**~~: 2026-02-20 fix (8e55179).

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul)
- 08:00 Theme rotation check
- 08:30 Theme analysis (crawl + score + select themes)
- 09:05 Stock screening + AI verification + observation loop
- 09:25 Auto buy execution (new positions only in empty slots)
- 09:26 Monitoring start (trailing stop, stop loss)
- 15:30 Monitoring stop
- 15:35 Market close cleanup
- 16:00 Daily report

## Trading Parameters (.env)
- TOTAL_CAPITAL: 3,000,000 KRW, MAX_POSITIONS: 5, per_slot: 600,000
- DEFAULT_STOP_LOSS: -8% (actual ATR-based: -5%~-12%)
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days (config default)

## Health Check Patterns
- Process: PID from trading_system.pid, then `ps -p PID`
- API: KIS=500, Claude=405, Telegram=200 (getMe) = all REACHABLE
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Weekend: No logs = NORMAL (mon-fri CronTrigger)
- Orderbook parse errors: ONLY at market close 15:20-15:30 KST = NORMAL

## Recent Health Checks
- **2026-02-25 20:57 KST**: Wed. PID 1350780, up 6h, 0.1%CPU/55MB. Dashboard PID 1354814, 5.9%CPU/160MB. 5 holdings, +47,550 KRW (+2.0%). 2 restarts during market (13:56, 14:44 KST - manual). All jobs ran. 1 buy (HD한국조선해양). Hansem trailing L1 active (+7.5%). 4,970 orderbook errors (close-only), 48 disconnect errors. Disk 52%, Mem 2.7G/3.9G.
- **2026-02-23 11:01 KST**: Mon. 5 holdings +2.2%. 2 buys. Partial sell bug discovered.
- **2026-02-22**: Sun. 3 holdings. Theme 18 days old, rotation due Mon.
- **2026-02-20**: First day new account. 3 buys. VM reboot, service auto-restarted.
