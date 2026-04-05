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

## Known Issues (as of 2026-04-05)
- **BUG: position_state 매도 후 잔존 (재확인)**: HJ중공업(097230) 3/26 매도 완료, 4/5 현재도 position_state에 잔존 (last_updated=3/25). 오이솔루션(138080) 4/2 매도 후에는 정상 정리됨 → 일관성 없는 정리 로직.
- **BUG: 주중 교체 테마 DB selected 미마킹**: 4/2 주중 교체로 '건설'이 '오가노이드' 대체 진입했으나, themes DB에서 건설의 selected=0 유지. 스크리닝은 메모리 기반으로 정상 작동하나 DB/대시보드 정합성 문제.
- **BUG: LIG넥스원(079550) 매수 수량 0주**: 3/26 발생. 고가 종목 매수 수량 산정 방어 필요.
- **BUG: 일별 수집이 주간 선정 점수를 덮어씀**: database.py에서 selected=False 저장 시 기존 selected=1 행 UPDATE.
- **WARNING: asyncio Event loop closed 에러**: 4/3 00:28 KST 09:28, httpx AsyncClient.aclose() → RuntimeError('Event loop is closed'). 텔레그램 notifier 관련 추정. 기능 영향 없으나 로그 오염.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError 계속 재현 (3/27, 3/30, 4/3).
- **WARNING: predefined 테마 네이버 미발견 8개**: K-방산, 바이오, 로봇, 수소, 엔터테인먼트, 게임, 화장품, 철강 (4/3 기준). 화장품이 활성 테마인데 미발견 → 종목 수집 품질 저하 가능.
- **WARNING: 오이솔루션 당일 매수→당일 손절**: 4/2 09:25 매수(43,200원) → 10:26 손절(-3.6%, 보유0일). 모닝 필터 통과 직후 급락. 매수 직후 보호 기간 검토 필요.
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.
- **INFO: trade_reviews 오이솔루션 ai_review 미완료**: 4/2 매도, D+5 = 4/7(화)에 분석 예정.

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
- **2026-04-05 20:47 KST (일)**: 전 서비스 정상 가동(4일째). 포트폴리오 2종목(클래시스 -0.94%, 삼성SDI -3.72%). 4/3 MarketGuard NORMAL(KOSPI +2.56%). 주중 교체 정상 발동(오가노이드→건설). position_state HJ중공업 잔존 재확인. asyncio 에러 1건(비치명적). 디스크 69%. 누적 P&L +167,031원(+3.26%), 승률 67.6%.
- **2026-03-30 10:22 KST (월)**: Market Crisis Guard CRISIS 연속. 포트폴리오 0종목. 누적 +185,881원(+4.22%), 승률 69.4%.
- **2026-03-27 10:28 KST (금)**: CRISIS(KOSPI -3.68%). 매수 스킵.
- **2026-03-17 19:30 KST**: Phase 2.5 점검. AI감성분석 정상.
- **2026-03-16 10:01 KST**: 5 holdings. 전 스케줄 정상.
