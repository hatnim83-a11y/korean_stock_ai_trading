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

## Known Issues (as of 2026-03-30)
- **BUG: LIG넥스원(079550) 매수 수량 0주**: 3/26 09:25 "주문수량을 확인하여 주십시요" 3회 재시도 실패. 주가(289,500원) 대비 할당금액 부족으로 수량=0 계산. 고가 종목 매수 수량 산정 방어 필요.
- **BUG: 일별 수집이 주간 선정 점수를 덮어씀**: database.py:574-596에서 selected=False 저장 시 기존 selected=1 행의 score/momentum/ai_sentiment를 일별 수집 값으로 UPDATE.
- **BUG: position_state 매도 후 잔존**: HJ중공업(097230) 3/26 매도 완료 후에도 position_state 테이블 + monitor_state.json에 데이터 잔존. _close_position_in_db에서 position_state DELETE 누락 추정.
- **WARNING: KRX theme index API broken**: `pykrx` '시장' KeyError. Falls back to Naver-only. 3/27, 3/30 연속 재현.
- **WARNING: predefined 테마 네이버 미발견 증가**: AI반도체, K-방산, 바이오, 로봇, 원자력, 수소, 조선, 철강 (3/30 기준 8~9개). 반도체, 원자력, 조선이 3/27부터 추가.
- **WARNING: 방위산업/전쟁및테러 테마 URL 빈값**: selected=1인데 url='' → 스크리닝 시 종목 수집 품질 저하 가능.
- **INFO: Telegram unreachable from GCP VM**: Persistent since 03-04.
- **INFO: Log file date uses UTC**: 08:00-08:59 KST logs → previous day's file.
- **INFO: 비철금속 테마 통과율 연속 저조**: 3/26 0%, 3/27 0%, 3/30 5%(1/20). 방위산업도 3/30 0%(0/18).
- **INFO: trade_reviews AI분석 미완료**: 3/23~3/24 매도 3건 ai_review=NULL. D+5가 비거래일(주말)과 겹쳐 데이터 수집 실패 가능.

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
- **2026-03-30 10:22 KST (월)**: 전 서비스/스케줄 정상. Market Crisis Guard CRISIS 연속 발동(KOSPI -3.70%, KOSDAQ -3.07%). 포트폴리오 0종목(3/26 이후 4거래일 빈 상태). 스크리닝 75종목 중 7통과(9.3%), AI 1통과(현대모비스 7.0/Yes). position_state 잔존 데이터 발견. 디스크 69%. 누적 P&L +185,881원(+4.22%), 승률 69.4%.
- **2026-03-27 10:28 KST (금)**: Market Crisis Guard CRISIS(KOSPI -3.68%, KOSDAQ -2.17%). 매수 스킵. 스크리닝 76종목 중 11통과. LIG넥스원 0주 매수 에러.
- **2026-03-17 19:30 KST (Phase 2.5 점검)**: AI감성분석 정상. 덮어쓰기 버그 재발.
- **2026-03-16 10:01 KST**: Mon. 5 holdings. 전 스케줄 정상.
- **2026-03-04 15:36 KST**: Tue. ALL 4 stopped out. P&L: -350K.
