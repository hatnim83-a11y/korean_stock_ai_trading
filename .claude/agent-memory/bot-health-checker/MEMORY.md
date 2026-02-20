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

## Known Issues (as of 2026-02-20)
- **PID file conflict with systemd**: When nohup process is running, PID file blocks systemd starts.
- **KIS API 403 errors**: Caused by multiple token requests within 1-minute cooldown.
- **Log file date uses UTC**: loguru `{time:YYYY-MM-DD}` uses server UTC. 08:00-08:59 KST logs go to PREVIOUS day's file.
- **nrcvb_buy_amt vs ord_psbl_cash**: get_orderable_cash returns nrcvb amount. ~15K less than deposit on 3M.
- ~~**Service is disabled**~~: 2026-02-20 `systemctl enable` 완료. 자동 시작됨.
- ~~**Morning filter stock name None**~~: 2026-02-20 수정 (07cc814). `_fetch_realtime_data`에서 키 정규화 추가.
- ~~**Supply filter all zeros**~~: 2026-02-20 수정 (8e55179). API 반환 키 `foreign_net`/`institution_net`과 코드의 `foreign_net_buy`/`institution_net_buy` 불일치 → 수정 완료.

## 내일 (2026-02-21) 필수 확인 사항
- [ ] **수급 필터 데이터 검증**: 09:25 모닝 필터 로그에서 외국인/기관 수급이 **0.0억이 아닌 실제 값**으로 나오는지 확인
  - 정상 예시: `✅ [티씨케이] 수급 양호 (외국인 15.2억, 기관 8.3억) - 통과`
  - 여전히 0이면: `kis_api.get_investor_trading()` 반환값 직접 확인 필요
  - 관련 커밋: 8e55179 (키 불일치 수정)
- [ ] **종목명 표시 검증**: 모닝 필터 결과 로그에서 종목명이 `None`이 아닌 실제 이름으로 나오는지 확인
  - 정상 예시: `1. 티씨케이 (갭 +0.46%, 강도 50%, 수급 12.5억)`
  - 관련 커밋: 07cc814 (키 정규화)
- [ ] **테마 상세 보고 검증**: 08:30 텔레그램 메시지가 새 포맷(유지/신규/탈락 분류)으로 나오는지 확인
  - 오늘이 2일차이므로 "기존 테마 유지 (2일차/7일)" 형태 예상
  - 관련 커밋: 8f41c6d (테마 보고 개선)
- [ ] **수급 기반 탈락 여부**: 수급 필터가 실제로 종목을 걸러내는지 (이전에는 전원 통과였음)

## Scheduler (KST, all CronTrigger with timezone=Asia/Seoul)
- 08:00 Theme rotation check
- 08:30 Theme analysis (crawl + score + select themes)
- 09:05 Stock screening + AI verification + observation loop
- 09:25 Auto buy execution (new positions only in empty slots)
- 09:26 Monitoring start (trailing stop, stop loss)
- 15:30 Monitoring stop
- 15:35 Market close cleanup
- 16:00 Daily report

## Trading Parameters (.env, updated 2026-02-19)
- TOTAL_CAPITAL: 3,000,000 KRW
- MAX_POSITIONS: 5
- per_slot_capital: 600,000 KRW
- DEFAULT_STOP_LOSS: -8%
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)

## Health Check Patterns
- Process: Read PID from trading_system.pid, then `ps -p PID -o pid,%cpu,%mem,etime,args`
- Bot restarts: grep "system start complete" in system log
- Market hours (KST): 09:00-15:30; Server UTC; KST = UTC+9
- Screening: ~90sec for 4 themes (~70 stocks). AI verification: ~15-30sec.
- Telegram: send_message logs at DEBUG level (not visible in journalctl). Success = no ERROR after send.
- VM Power key reboot: GCP can trigger "Power key pressed" -> clean shutdown. Service must be `enabled` to survive.

## DB Schema (key tables)
- portfolio: shares (NOT quantity), buy_price, status (holding/closed)
- trades: shares, action (buy/sell), price, reason

## Recent Health Checks
- **2026-02-20**: New account first full day. All jobs ran correctly (08:00~16:00). 3 stocks bought (TCK, Hansem, PSK). Day return -0.95%. VM rebooted at 20:04 KST via Power key; service not auto-restarted (disabled). 2 minor KIS API disconnects (non-impacting).
