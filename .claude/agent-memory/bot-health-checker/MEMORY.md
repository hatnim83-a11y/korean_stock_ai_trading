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

## Phase A 매수 필터 (2026-04-24 배포, DB v14)
- **screener.py:134** `_get_market_regime_rsi()` — KOSPI 전일 등락률 → RSI 상한 동적(BULL ≥+1% =75 / NORMAL =70 / BEAR ≤-1% =65)
- **screener.py:189** `_apply_theme_min_slot(min_score, safety_floor)` — 테마별 최상위 1개가 ≥THEME_SAFETY_FLOOR(25점)면 MIN_FINAL_SCORE 컷 면제, protected_codes set 반환
- **screener.py:721-734** Phase A 3단 컷: ① 슬롯 보장 → ② min_score 컷 → ③ max_total 컷(보장 종목 우선). screening_log.theme_slot_protected=1로 반영.
- **config.py:394-426** 신규 파라미터 7개: RSI_DYNAMIC_ENABLED=True, RSI_BULL_THRESHOLD=1.0, RSI_BEAR_THRESHOLD=-1.0, RSI_UPPER_BULL=75, RSI_UPPER_NORMAL=70, RSI_UPPER_BEAR=65, THEME_MIN_SLOT_ENABLED=True, THEME_SAFETY_FLOOR=25.0
- **screening_log v14 컬럼**: rsi_at_screen, theme_slot_protected (집계 가능)

## Daily Health Check 16:10 (확장 2026-05-07)
**main.py:run_daily_health_check** (스케줄러 `daily_health_check` 잡, 평일 16:10 KST `_skip_on_holiday`).
- 기존 7개 항목 + 신규 헬퍼 11개 = 18개 모니터링
- 헬퍼 메서드 `_hc_*` (모두 `{"info": str|None, "issue": str|None}` 반환):
  - `_hc_theme_score_pinning`: 테마 0 박제(zero=issue), NULL(info 폴백 대기), 정상 분리
  - `_hc_screening_log_stages`: filter/gap_filter/ai_verify 3-stage 적재
  - `_hc_phase_a_metrics`: theme_slot_protected 발동 + RSI 평균/최대
  - `_hc_midweek_replacement`: midweek 교체 발생 시 알림
  - `_hc_makeup_reselection`: 보정 재선정 발화 여부 (5/6 도입 검증)
  - `_hc_trade_review_coverage`: 오늘 SELL vs trade_reviews 카운트
  - `_hc_slippage_coverage`: 매도 slippage NULL 비율
  - `_hc_systemd_dashboard`: trading_dashboard active 검증
  - `_hc_disk_usage`: shutil.disk_usage, 90% 초과 시 issue
  - `_hc_closing_bet_universe`: closing_bet.db candidates 오늘 건수
  - `_hc_sell_lock_residual`: sell_lock.snapshot()로 잔존 락 카운트 (15:30 clear_all 검증)
- 공휴일 false alarm 수정: yesterday 영업일 체크는 `is_trading_day(yesterday_date)` 사용
- 텔레그램 메시지 ~534자 운영 범위 (4096 한계 여유)
- 단위 테스트: tests/test_health_check.py 21건 PASS (운영 DB 시뮬레이션 + Stub 인스턴스)

## Closing Bet System (data/closing_bet.db)
- **테이블**: candidates(22컬럼), candidate_features(20), candidate_labels(9), orderbook_snapshots, flow_data_reliability, schema_version, sqlite_sequence
- **candidate_labels 컬럼**: candidate_id, next_open_pct, next_morning_high_pct, next_morning_low_pct, label_gap_up, label_morning_exit, label_stop_risk, label_net_ev_positive, labeled_at
- **누적 통계 (5/11 기준)**: 4개일자 79건 후보 (recommended 69 / rejected 10), gate_threshold=30 이미 돌파 — 자동매매 게이트 통과 상태이나 Phase 1 정책상 알림형 유지
- **일자별**: 5/4 19건, 5/7 18건, 5/8 19건, 5/11 23건
- **라벨링 현황**: 5/4(19/19, 5/5 10:00 자동), 5/7(18/18, 5/8 manual_backfill+자동 혼합), 5/8(0/19 누락!), 5/11(미도래)

## BUG: T+1 라벨링 — 월요일 영업일 미반영 (2026-05-11 발견, 심각)
- **위치**: `closing_bet_system/main_orchestrator.py:419-420` `run_label_yesterday`
- **현상**: `yesterday = today - timedelta(days=1)` — 단순 캘린더 -1일 사용
- **결과**: 월요일 10:00 라벨링 잡이 일요일을 조회 → 금요일 후보 영구 미라벨
- **5/11 검증**: 5/8 후보 19건 모두 labeled_at=NULL, 잡 로그 "라벨링 완료 — 0/0 저장"
- **수정 방향**: `is_trading_day()` 기반 직전 영업일 탐색 (config.py 함수 활용)
- **5/7 → 5/8 manual_backfill 흔적**: 사람이 수동 보정한 적 있음 → 운영 부담

## Recent Health Checks
- **2026-05-11 15:36 KST (월)**: 종가베팅 점검. 5/11 15:10 파이프라인 정상(universe 94→23, recommended 20/rejected_filter 3 모두 atr_overheat>1.8). 5/8 19건 라벨링 누락(영업일 -1 버그, 위 BUG 항목). 누적 라벨 37건 중 EV+ 20건(54.1%). 5/4 84% 단일 결과는 비대표적, 5/7 EV+ 22%로 저조. 5/4 "005930 (테스트)" 라벨 1건 잔존 — 정합성 점검 필요.
- **2026-04-27 10:50 KST (월)**: Phase A 첫 실전일. uptime 2일 정상. 09:05 스크리닝 78종목 중 12개 필터 통과(슬롯 보장 5건 첫 기록), AI 검증 5종목 → Yes 1건(한화오션 7.5점)/Hold 4건. 한화오션은 보유 종목이라 후보풀에서 제외 → 매수 후보 0개.
- **2026-04-10 09:10 KST (금)**: 테마 DB 버그 수정 후 재시작(09:04). 금융 1개 테마 운영. 09:05 스크리닝 20종목 중 7개 필터 통과→AI 검증 0건 통과(전부 Hold). 포트폴리오 4종목(클래시스 -1.31%, HD한국조선해양 -0.51%, HJ중공업 -2.14%, HPSP -2.31%). 누적 P&L +332,031원(+6.60%), 승률 70%. 디스크 65%. 좀비 프로세스 2개.
- **2026-04-06 10:13 KST (월)**: 전 서비스 정상. 포트폴리오 3종목. 삼성SDI 손절가 -11.1% 이상 발견. 누적 +167,031원(+3.26%), 승률 67.6%.
- **2026-04-05 20:47 KST (일)**: 전 서비스 정상 가동(4일째). 포트폴리오 2종목.
- **2026-03-30 10:22 KST (월)**: Market Crisis Guard CRISIS 연속. 포트폴리오 0종목. 누적 +185,881원(+4.22%), 승률 69.4%.
