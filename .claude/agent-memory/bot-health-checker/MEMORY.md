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

## Known Issues (as of 2026-04-10)
- **RESOLVED: 삼성SDI 4/9 주중 교체 매도**: 수익 청산 +4.05%. 손절가 이상 문제는 더 이상 해당 없음.
- **BUG: 테마 DB 동기화 문제 (수정 완료 4/10)**: 4/9 테마 3→1개 유실. DB selected 마킹 및 비화요일 복원 로직 수정.
- **BUG: 손절가 비율 불균일**: HD한국조선해양 -8.47%, HPSP -9.52% (기준 -7%). 종목별 변동성 반영 또는 버그. HJ중공업 -6.39%는 정상 범위.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError 계속 재현 (3/27~4/10).
- **WARNING: predefined 테마 네이버 미발견**: AI반도체, 수소 (4/10 기준).
- **WARNING: KIS API 간헐적 연결 실패**: SSL EOF / Server disconnected 에러 반복 (장외 시간 대시보드 폴링 시). 자동 재시도로 복구됨.
- **WARNING: 대시보드 KIS API 토큰 403**: 재시작 시 메인 봇과 1분 내 중복 발급 충돌.
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.
- **INFO: Zombie processes (2 defunct)**: 4/9 이전 세션 좀비 프로세스 2개. 기능 무해하나 정리 필요.

## Scheduler (KST, CronTrigger timezone=Asia/Seoul, _skip_on_holiday)
- 08:00 Theme rotation | 08:30 Theme analysis | 09:05 Screening | 09:15 Hold period sells | 09:25 Auto buy
- 09:26 Monitor start | 15:30 Monitor stop | 15:35 Close cleanup | 16:00 Report
- 16:10 Health check | 17:00 Post-trade | 17:05 Daily theme collection | Fri 17:30 Weekly review
- `_skip_on_holiday` checks `is_trading_day()` (weekday + holidays lib); weekend/holiday skips

## Theme System Architecture
- **08:30 화요일**: aggregate_weekly_scores(6영업일 가중평균) → 유동성 보정 → select_themes_with_retention → Step 4 URL 보충(crawl_all_themes) → DB 저장
- **08:30 비화요일**: DB 복원 테마 재사용 (same_week=True if days_since_rotation < 7 and not Tuesday). 유동성 보정 미실행(설계상 의도 가능).
- **09:05**: screener가 theme URL로 종목 수집 → 없으면 search_naver_theme → _search_naver_upjong 폴백
- **17:05**: crawl_all_themes(200+개) → score_themes(상위30) → AI 분석(20) → DB 저장(30개, selected=False)

## Liquidity Check Feature (added ~2026-03-26)
- **THEME_LIQUIDITY_CHECK_ENABLED**: config.py:418, default True
- **calculate_liquidity_penalty**: scorer.py:491 — pass_rate < 10% 최대감점, < 20% 비례감점
- **get_theme_pass_rates**: database.py:688 — screening_log 기반 테마별 통과율 조회
- **호출 시점**: main.py:441-477, 화요일 가중 집계 경로 + 비화요일 score_themes 경로(pass_rates 인자)
- **비화요일 재사용 경로 (main.py:337-422)에서는 유동성 보정 미실행** — 이미 확정된 테마 재사용이므로 설계상 의도된 동작

## Trading Parameters (.env)
- TOTAL_CAPITAL: 4,406,493 KRW, MAX_POSITIONS: 5
- Trailing: L1(+8%, -5%), L2(+15%, -3%), L3(+25%, -2%)
- Theme rotation: 7 days

## Recent Health Checks
- **2026-04-10 09:10 KST (금)**: 테마 DB 버그 수정 후 재시작(09:04). 금융 1개 테마 운영. 09:05 스크리닝 20종목 중 7개 필터 통과→AI 검증 0건 통과(전부 Hold). 포트폴리오 4종목(클래시스 -1.31%, HD한국조선해양 -0.51%, HJ중공업 -2.14%, HPSP -2.31%). 누적 P&L +332,031원(+6.60%), 승률 70%. 디스크 65%. 좀비 프로세스 2개.
- **2026-04-06 10:13 KST (월)**: 전 서비스 정상. 포트폴리오 3종목. 삼성SDI 손절가 -11.1% 이상 발견. 누적 +167,031원(+3.26%), 승률 67.6%.
- **2026-04-05 20:47 KST (일)**: 전 서비스 정상 가동(4일째). 포트폴리오 2종목.
- **2026-03-30 10:22 KST (월)**: Market Crisis Guard CRISIS 연속. 포트폴리오 0종목. 누적 +185,881원(+4.22%), 승률 69.4%.
