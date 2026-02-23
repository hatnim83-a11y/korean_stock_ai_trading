# Bot Health Checker - Agent Memory

## Key File Locations
- Logs: `/home/hatni/korean_stock_ai_trading/logs/` (system_YYYY-MM-DD.log, error_YYYY-MM-DD.log, trading_YYYY-MM-DD.log)
- Database: `/home/hatni/korean_stock_ai_trading/data/trading.db`
- PID file: `/home/hatni/korean_stock_ai_trading/trading_system.pid`
- Config: `/home/hatni/korean_stock_ai_trading/config.py`, `.env`
- Main entry: `/home/hatni/korean_stock_ai_trading/main.py`
- Scheduler: `/home/hatni/korean_stock_ai_trading/scheduler.py`
- Systemd service: `/etc/systemd/system/trading_system.service`
- Telegram notifier: `modules/reporter/telegram_notifier.py`

## API Class Names
- Order API: `KISOrderApi` -- init params: `(app_key, app_secret, account_no, is_mock)` NOT `is_real`
- Screener API: `KISApi` (in `modules/stock_screener/kis_api.py`)
- Token expiry: `api.token_expired_at` (value 0 = not yet acquired; token is on-demand)
- Both APIs read from `settings` (which loads `.env`), so changing `.env` is sufficient

## Account Management (updated 2026-02-20)
- Active account: 44037660 (new), old: 43975058 (commented out in .env)
- KIS_CANO is the 8-digit account number; KIS_ACCOUNT_NO has `-01` suffix
- Token sharing: `KISApi._shared_token` class variable, reused by `KISOrderApi`
- 1-minute cooldown on token issuance per app key

## DB Schema (updated 2026-02-22)
- portfolio: id, date, stock_code, stock_name, theme, weight, shares, buy_price, current_price, stop_loss, take_profit, profit_rate, profit_amount, status (holding/closed), created_at, updated_at
- trades: id, date, time, stock_code, stock_name, action (buy/sell), shares, price, amount, reason, profit_rate, profit_amount, order_id, created_at
- NOTE: NO `timestamp` column in trades; use `created_at` for ordering
- NOTE: NO `buy_date` column in portfolio; use `date` field

## Stop Loss Calculation
- Uses ATR-based dynamic stop loss, NOT just DEFAULT_STOP_LOSS
- Clamped to range: MIN=-12%, MAX=-5% (see `calculators.py` lines 41-42)
- This is why different stocks have different stop loss percentages
- DEFAULT_STOP_LOSS in .env is -0.08 but actual SL varies per stock via ATR

## Known Issues (as of 2026-02-23)
- **CRITICAL: Partial sell not saved to DB**: `_execute_partial_sell` in `portfolio_monitor_v2.py` updates `pos.remaining_shares` in memory but does NOT save sell trade to DB or update portfolio shares. Only full liquidation (`remaining_shares <= 0`) triggers `_close_position_in_db`. On 2/23, 19 shares of Y2Solution were sold at +10% but DB still shows 66 shares. Needs fix in `_execute_partial_sell` around line 667-680.
- **PID file conflict with systemd**: When nohup process is running, PID file blocks systemd starts.
- **KIS API 403 errors**: Caused by multiple token requests within 1-minute cooldown.
- **Log file date uses UTC**: loguru `{time:YYYY-MM-DD}` uses server UTC. 08:00-08:59 KST logs go to PREVIOUS day's file.
- **2/19 positions closed without sell trades**: 다우기술/대한항공 marked `closed` in DB but no sell trade records. Rebalancing logic marks old positions as closed when new day starts.
- **THEME_REVIEW_DAYS not in .env**: Uses config.py default (7 days). Not a problem but differs from documentation.
- **WebSocket keepalive drops**: Recurring ~3min disconnects, auto-reconnects successfully (1/5 retries). Seen 2/23.

## Resolved Issues
- ~~**Service is disabled**~~: 2026-02-20 `systemctl enable` done. Auto-starts on reboot.
- ~~**Morning filter stock name None**~~: 2026-02-20 fix (07cc814). Key normalization in `_fetch_realtime_data`.
- ~~**Supply filter all zeros**~~: 2026-02-20 fix (8e55179). API key name mismatch fixed.

## 2026-02-23 Verification Results
- [x] **Supply filter data**: WORKING. Morning filter shows real values (Y2Sol: 63.7B foreign, LG: 130.9B foreign + 367B institution)
- [x] **Stock name display**: WORKING. Names display correctly (와이투솔루션, LG전자, etc.)
- [x] **Theme rotation**: WORKING. 08:00 detected "메인 테마 미설정", 08:30 ran full analysis, selected 4 new themes
- [x] **Supply-based filtering**: WORKING. Supply filter passed 2/2 candidates (no rejections today, but filter ran correctly)
- [ ] **Partial sell DB bug**: NEW - discovered 2/23. See Known Issues.

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul)
- 08:00 Theme rotation check
- 08:30 Theme analysis (crawl + score + select themes)
- 09:05 Stock screening + AI verification + observation loop
- 09:25 Auto buy execution (new positions only in empty slots)
- 09:26 Monitoring start (trailing stop, stop loss)
- 15:30 Monitoring stop
- 15:35 Market close cleanup
- 16:00 Daily report

## Trading Parameters (.env, updated 2026-02-22)
- TOTAL_CAPITAL: 3,000,000 KRW
- MAX_POSITIONS: 5
- per_slot_capital: 600,000 KRW
- DEFAULT_STOP_LOSS: -8% (actual SL: ATR-based, range -5%~-12%)
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days (config default, not in .env)

## Health Check Patterns
- Process: Read PID from trading_system.pid, then `ps -p PID -o pid,%cpu,%mem,etime,args`
- Process start time: `ps -p PID -o lstart`
- Bot restarts: grep "system start complete" in system log
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Weekend: No logs generated (all jobs are mon-fri CronTrigger) -- this is NORMAL
- API connectivity: KIS=500 (POST without body=normal), Claude=405 (no POST), Telegram=404 (no bot token=normal) all = REACHABLE
- Telegram bot check: `/getMe` endpoint

## Recent Health Checks
- **2026-02-23 11:01 KST**: Monday check. PID 387599, uptime 14h, 0.1% CPU, 4.4% MEM (178MB). 5 holdings (+2.2% overall). All scheduled jobs ran (08:00 theme check -> 08:30 analysis -> 09:05 screening -> 09:25 buy -> 09:26 monitor). 2 new buys (Y2Solution 66@8615, LG Electronics 4@132500). 1차 익절 triggered for Y2Solution (19 shares at +10%). **BUG: partial sell not saved to DB**. WebSocket 2 disconnects, both auto-recovered. No ERROR logs. Disk 46%, Mem 1.3G/3.9G.
- **2026-02-22**: Sunday evening check. Service running 1d22h (PID 28996, 0.0% CPU, 1.1% MEM = 39.8M). 3 holdings (TCK -1.4%, Hansem +0.2%, PSK -1.8%). All APIs reachable. Bug fixes (07cc814, 8e55179) confirmed loaded in running process. Theme is 18 days old (selected 02-04), rotation due Monday 08:00. Disk 45%, Mem 1.1G/3.9G used.
- **2026-02-20**: New account first full day. All jobs ran correctly (08:00~16:00). 3 stocks bought (TCK, Hansem, PSK). Day return -0.95%. VM rebooted at 20:04 KST via Power key; service auto-restarted (enabled).
