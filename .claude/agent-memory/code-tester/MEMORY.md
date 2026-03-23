# Code Tester Agent Memory

## Key Patterns Found in This Codebase

### 지속적 주의 사항
- `date.today()` → UTC 서버에서 KST 날짜 불일치 (UTC 15:00 이후)
- `update` SQL 후 `cursor.rowcount` 미확인 → silent failure
- DB 작업: `Database()` → `connect()` → 작업 → `close()` 패턴에 finally 필요
- emoji in logger messages: 기존 코드에서 광범위 사용 (스타일 문제, 기능 무관)

### 잔존 기존 이슈 (미수정)
- `database.py` line 524: `save_trade()` date 기본값 `date.today()` UTC 버그
- `trading_engine.py` `_execute_stop_loss/take_profit`: sync `time.sleep(1)` 이벤트루프 블로킹
- `_close_position_in_db`/`_save_partial_sell_to_db`: DB `finally` 없음 (커넥션 누수 가능성)

### is_trading_day() (config.py)
- `holidays.KR`: 법정 공휴대 + 대체 공휴일 포함
- 미포함: KRX 임시 특별 휴장일 (연말, 재난 등)
- `ImportError` fallback: 평일이면 거래일로 간주

### crawlers.py 주요 패턴
- naive 타임스탬프 → `replace(tzinfo=timezone.utc)` 처리: 최대 9시간 오차 (허용 수준)
- `_PREDEFINED_THEMES`: 모듈 로드 시 1회 정의, `THEME_NAME_MAP` 자동 생성
- `classify_theme_category(None)` → AttributeError (None 방어 없음, 실사용 경로 낮음)

### DB 관련
- 실제 DB 경로: `data/trading.db` (루트의 `trading.db`는 빈 파일)
- `daily_snapshots` 컬럼명: `total_capital` (total_value 아님)
- `get_balance()` 반환 키: `cash`, `total_value`, `total_profit`, `profit_rate`, `positions`
- WAL 모드 DB 복사: `.db`, `.db-wal`, `.db-shm` 3파일 항상 확인
- datetime.utcnow() in JWT create_token: jose exp은 UTC 기준으로 올바름 (now_kst 교체 금지)

### 테마 배점 구조 v2 확정 (2026-03-09)
- 실제 최대 점수: 25(모멘텀)+0(과열없음)+15(뉴스)+10(AI)+5(보너스)+10(기본) = **65점**
- 등급 기준: S=58, A=48, B=38(=RETENTION_SCORE), C=30(=MIN_SELECTION_SCORE)
- S등급 실제 달성 가능 최대: 65점 (배점 개편 후)
- `calculate_overheat_penalty`: 8%→감점시작, 15%→-15점(최대), 급가속(avg3/avg5>=0.8)→추가 -3점
- float 정밀도: `accel_ratio=9.6/12.0=0.79999` → 급가속 -3점 미적용 (경미)

### 테마 연장(retention) 패턴
- `select_themes_with_retention()`: selector.py, RETENTION_SCORE=38.0
- score_map/previous_themes 키: `t.get('theme', t.get('name', ''))` 듀얼 폴백

### same_week 버그 (2026-03-09 발견, 수정됨)
- `main.py`: `same_week = (days_since_rotation < 7) and (today.weekday() != 1)` 으로 수정됨

### pause/resume 기능
- `trading_paused` bool 플래그: asyncio 단일 스레드 → 원자적, 스레드 안전
- `/pause`/`/resume`/`/status` 핸들러: sync `def`, start_command_listener 내 직접 호출 (정상)

### run_daily_health_check + send_daily_report 버그 (미수정)
- `db.get_trades_by_date(today_str)` → 메서드 미존재
- `s.get("total_value", ...)` → 컬럼명은 `total_capital`
- `bal.get("available_cash", 0)` → 키는 `cash`

### URL 보충 로직 (두 경로, 2026-03-10 검증)
- aggregate_weekly_scores 결과: url 키 없음 (None)
- crawled_map = {t["name"]: t}: crawl_all_themes의 normalize된 이름이 키
- DB 저장명 = normalize(name), 이름 매칭 체인 일관성 확인됨
- url="" 저장 케이스: crawled_map에 있으나 url 없는 predefined_default 경우 발생
  -> run_stock_screening에서 not any("") = True -> 불필요 재분석 트리거 (지연만 발생)
- run_stock_screening 재분석 조건: "하나도 없을 때만" (부분 실패는 screener 폴백)
- asyncio.to_thread 내 dict side-effect: today_themes에 정상 반영됨 (의도된 동작)
- 두 경로 모두 폴백 추가 확인됨: crawlers.search_naver_theme → screener._search_naver_upjong
- 비-화요일 경로(line 348-352): crawled_map hit + url="" → 폴백 없이 "✓ 보충 완료" 로그 출력
  → 화요일 경로와 달리 url 유효성 검증 없음 (주의사항, 09:05 screener 폴백으로 보완됨)

### screener.py 폴백 로직 (2026-03-10 추가, 상세: screener-review.md)
- 3단계 폴백: 직접 URL → search_naver_theme → _search_naver_upjong(업종)
- 업종 URL로 crawl_naver_theme_stocks 호출 시: 종목 코드 수집 OK, price/change_rate 오염
  → screener는 KIS API로 재조회하므로 실질 영향 없음
- theme["url"] = theme_url 직접 수정 → today_themes side-effect (재호출 시 업종 URL 사용)
- stock_codes[:20]: 크롤링 풀 크기 하드코딩 (MAX_STOCKS_PER_THEME=10과 별개 상수)
- _search_naver_upjong: requests 사용 (crawlers.py는 httpx → 혼용, 기능 무관)
- 기존 동작(URL 있는 테마) 완전 무영향 확인

### selected 컬럼 버그 (v11, 2026-03-11 수정 완료)
- 수정 내용: `save_theme_scores(selected=False)` 시 기존 selected=1 행은 UPDATE만, INSERT OR REPLACE 건너뜀
- `get_last_theme_analysis_date()`: `WHERE selected=1` 필터 추가
- `get_top_themes()`: `selected=1` 우선 조회, 없으면 폴백
- `_migrate_v11`: `ALTER TABLE themes ADD COLUMN selected BOOLEAN DEFAULT 0`
- main.py: 주간 선정(line 516) `selected=True`, 일별 수집(line 1582) `selected=False`
- 격리 테스트 통과 확인 (2026-03-11)
- 잔존 설계 주의: init_tables DDL에 selected 컬럼 없음 (_migrate_v11 이전 호출 불가 구조라 무해)
- 잔존 설계 주의: 주간 재선정 시 같은 날짜만 selected=1 초기화 → 이전 주 selected=1 행 누적 (기능 무관)

### 테마 파이프라인 하드코딩 목록 (v12 업데이트, 2026-03-13)
- `screener.py:576` `stock_codes[:20]` — 크롤링 풀 크기, config 없음 (MAX_STOCKS_PER_THEME=10과 별개)
- `main.py:429` `raw_themes[:20]` — 비화요일 점수화 개수, config 없음
- `main.py:1651` `raw_themes[:30]` / `1659` `scored_themes[:20]` — 17:05 일별수집 대상, config 없음
- `scorer.py:666` `themes[:15]` (_enrich_theme_stocks) / `scorer.py:633` `themes[:30]` (_collect_news_data) — 하드코딩
- `scorer.py:119-122` `OVERHEAT_THRESHOLD=8.0/OVERHEAT_MAX=15.0/PENALTY_MAX=15.0` — 함수 로컬 상수
- `selector.py:43,46` `MIN_SELECTION_SCORE=30.0/RETENTION_SCORE=38.0` — 모듈 상수 (config 없음)

### crawl_theme_news 주의사항 (2026-03-19 업데이트)
- `days` 파라미터: **이제 API 경로에서도 사용됨** (display=100 items의 pubDate 파싱 후 cutoff 필터)
- `cutoff = now_kst() - timedelta(days=days)`: timezone-aware 비교 (pubDate %z도 aware → 정상)
- `count = data.get("total", 0)` 제거됨 → items 순회 + cutoff 필터로 실제 건수 카운트
- 파싱 실패 시 `count += 1` (보수적 포함 처리, 허용 수준)
- `text_parts` 수집: 날짜 필터와 독립적으로 상위 max_items개 수집 (sort=date 내림차순 의존)
  → sort=date이므로 실전에서 최신 기사가 text_parts에 들어오는 구조 (코드 수준 보장 없음)
- `display=100`: Naver API 최대값, Rate Limit 무관 (호출 횟수 변경 없음)
- `_crawl_theme_news_count_scrape`: 변경 없음, now_kst() 사용 확인
- API 키 없을 때만 _crawl_theme_news_count_scrape(days 사용)로 폴백

### _enrich_tuesday_themes 패턴 (2026-03-13)
- main.py:1517 정의, main.py:420 호출 (run_theme_analysis 내 화요일 경로)
- 섹션 헤더 '17:05 일별 테마 데이터 수집' 아래에 위치하지만 08:30 화요일 경로용 메서드
- BASE_SCORE import(line 1533): _enrich_tuesday_themes 내부에서 미사용 (dead import)
- top_15 = scored_themes[:15]: 슬라이스이므로 dict 수정이 원본에 반영됨 (의도된 side-effect)
- asyncio.to_thread 대상 함수: 모두 동기 함수 (확인됨)
- 모멘텀 보정: delta*1.5 적용, 하한 없음 (실용 범위 내 허용)

### aggregate_weekly_scores 결과 키 구조 (v12 업데이트, 2026-03-13)
- 포함: name, theme, total_score, score, momentum, momentum_score, news_count, ai_sentiment, category, days_found, daily_scores, selection_reason, grade, **url** (신규)
- url: COALESCE(url,'') 쿼리로 DB NULL→빈문자열 변환, 가장 최근 날짜 우선 보존
- 미포함: stock_count, avg_change_rate, news_score, bonus_score, overheat_penalty
- AI 재계산 식이 이 결과에 적용되면 news_score/bonus_score/overheat_penalty = 0 -> 주의
- 실제로는 17:05 score_themes 결과에만 AI 재계산 적용됨 (화요일 08:30은 DB 집계 점수 사용)

### scorer.py time import
- `from datetime import time` -> datetime.time 클래스, time(9,0)으로 사용 (표준 time 모듈 아님)
- now_kst().time() vs time(9,0) 비교: 올바른 사용

### 텔레그램 수동 매도 기능 (2026-03-12, 2026-03-13 검증)
- `/sell`, `/sellall`, `/confirm`, `/cancel` 핸들러: `telegram_notifier.py` 추가
- `execute_sell(reason=)` / `execute_sell_all(reason=)`: `dashboard_service.py` reason 파라미터 추가
- `_pending_sell_all = False` 초기화: line 952에서 await 전에 배치됨 (수정 완료, 2026-03-13 확인)
- `_pending_sell = None` 초기화: line 981에서 await 전에 배치됨 (두 경로 모두 정상)
- 미사용 import: `telegram_notifier.py`의 `datetime`/`date` (기능 무관)
- 30초 TTL: `_confirm_timeout_sec = 30` 인스턴스 변수로 관리됨 (상수 추출 불필요)

### profit_rate None 방어 패턴 주의사항 (2026-03-13)
- `p.get("profit_rate") or 0` 는 정렬/표시용으로는 올바름
- `p.get("profit_rate") or 0 < 0` 는 연산자 우선순위 버그: `or`가 `<`보다 낮아 `(profit_rate) or (0 < 0)` 로 파싱됨
- 올바른 형태: `(p.get("profit_rate") or 0) < 0`
- 버그 발생 위치: `report_generator.py:328` (any() 조건), `telegram_notifier.py:506` (리스트컴프리헨션 필터)
- 수정 완료: 2026-03-13

### 주중 테마 교체 기능 (2026-03-18 1회차 검증)
- 수정 파일: config.py, database.py, selector.py, scheduler.py, main.py, dashboard.html
- `execute_sell_orders(save_to_db=False)` 호출 후 직접 `db.close_position` + `db.save_trade` 패턴 (정상)
- `kis_order_api._place_order` 반환값: stock_code, quantity 포함 → `order.get("stock_code")` 안전
- `themes` 테이블에 `stock_count` 컬럼 없음 → `select_replacement_candidate`에서 항상 0 반환 → 필터 스킵 (기능은 정상, 종목수 필터 비활성 상태)
- `MIDWEEK_MIN_HOLD_DAYS=2`: 달력일 기준 비교 (`_last_theme_rotation_date`가 None이면 체크 스킵)
  → None 케이스: today_themes가 있으면 교체 판단 실행 (잠재적 부작용, 발동 조건은 희소)
- `from datetime import timedelta` 내부 중복 import: 상단에 이미 import됨, 기능 무관
- `_check_midweek_replacement`에서 `Database()` 새 인스턴스 생성 (self.db로 대체 가능, 비효율적이나 무해)
- `_rescore_midweek_loss_stocks`에서 `screen_stocks_in_theme` 반환 시그니처: `tuple[list, list]` (passed, failed) 정상 호출
- 대시보드 Chart.js `borderDash` 위치: dataset 직속 속성으로 올바름
- 09:00 스케줄이 기존 09:05 스크리닝보다 먼저 실행: 시계열 순서 정상
- `profit_rate` 단위: DB 저장값은 소수(0.05=5%), `:+.1%` 포맷팅과 일치 (정상)

### 주중 교체 청산·진입 흐름 심층 검증 (2026-03-18 2회차)
- **close_position SQL**: reason 파라미터가 로그에만 출력되고 DB 저장 안 됨 (portfolio 테이블에 reason 컬럼 없음) — 기능 무관
- **profit_rate=0.0 경계**: `profit_rate > 0`이므로 정확히 0인 종목은 loss_queue에 배치됨 — 의도 여부 불명확, 재평가 대상으로 간주 (허용 수준)
- **test_mode DB 불일치**: test_mode=True 시 매도 스킵 → DB portfolio 여전히 holding → execute_buy_orders에서 슬롯 소진처럼 인식 → 교체 후 신규 매수 안 됨 (test_mode 한계)
- **_execute_midweek_loss_sells clear() 위치**: `except` 블록 밖에 있어 예외 발생 시에도 큐가 초기화됨 (의도된 동작, 재시도 방지 목적)
- **_execute_midweek_profit_sells clear() 위치**: 동일하게 except 밖에 위치 — 예외 후에도 초기화
- **position_state vs portfolio 이중 삭제 없음**: `close_position()`은 portfolio 테이블만, `remove_position()`은 position_state 테이블만 수정 — 독립적
- **09:26 모니터 재로드 문제 없음**: `load_positions_from_db()`는 status='holding'만 조회, close_position 후 'closed'로 변경되므로 재로드 안 됨
- **screener 반환 구조 정합성**: `screen_stocks_in_theme` 반환 candidates 각 항목에 `code`(kis_api.py 확인), `final_score`(filters.py 확인) 키 존재 — `passed_map = {p["code"]: p.get("final_score", 0)}` 패턴 안전
- **execute_sell_orders 반환 orders 구조**: `_place_order` 반환값에 `stock_code`, `quantity` 포함 + `execute_sell_orders`에서 `stock_name`, `buy_price` 추가 + `_wait_for_fills`에서 `filled_price` 추가 — main.py의 `order.get("filled_price", order.get("price", 0))` 폴백 패턴 안전
- **09:00~09:26 사이 monitor=None**: `_execute_midweek_profit_sells`에서 `if self.monitor: remove_position()` 가드 있음 — NoneType 오류 없음, 단 position_state 잔류 (09:26 load_positions_from_db에서 portfolio 기반으로 재로드하므로 실질 문제 없음)
- **중복 저장 없음**: `save_to_db=False` 시 `_save_trades()` 호출 안 됨, 이후 `db.close_position + db.save_trade` 직접 호출 — 중복 없음 확인
- **주요 미발견 이슈**: `_execute_midweek_profit_sells`에서 `exception` 발생 시 `sold_count`는 0이지만 `_midweek_sell_queue.clear()` 호출됨 → 매도 실패한 종목 재시도 불가 (주의)

### 주중 교체 평가·시각화·엣지케이스 검증 (2026-03-18 3회차)
- **점수 기준 정합성**: MIDWEEK_ABS_FLOOR(38) = RETENTION_SCORE(48)과 다름 (의도적 비대칭)
  → 이전 MEMORY에 RETENTION_SCORE=38.0으로 잘못 기록됨. **실제 현재값은 48.0** (selector.py line 46)
- **자기 자신 교체 불가 확인**: active_names에서 제외되므로 select_replacement_candidate에서 스킵됨
- **하루 1개만 교체 확인**: break 후 return 패턴 — 복수 탈락 시에도 1개만 교체됨
  → 다음 날 가장 낮은 점수 순 교체 가능 (의도된 설계)
- **대시보드 38 하드코딩**: dashboard.html line 442 등급 기준(38, 48, 58), line 543 기준선 38 — config에서 로드 안 함 (주의)
- **activeThemes fallback**: `selected_themes`가 비면 `current_themes`로 폴백 — current_themes가 None이면 undefined 전달 (renderThemeCards가 방어 처리함)
- **delta 계산**: hist[0]이 가장 최근(내림차순 정렬), hist.length < 2이면 delta 미표시 — 정상
- **금요일 교체 후 월요일 재시작**: `get_last_theme_analysis_date()`는 selected=1 기준 → 금요일 교체로 추가된 테마는 DB에 저장됨 (line 562 selected=True) → 복원 가능
  → 단 교체 테마가 same_week 로직(days_since_rotation < 7)으로 "기존 유지" 경로로 진입 — 정상
- **RETENTION_SCORE 수정**: selector.py:46 현재 48.0, MEMORY의 38.0 오기 수정됨

### 전체 검증 완료 파일 목록
- 상세: `review-history.md`
- 테마 파이프라인 상세: `theme-pipeline-review.md`
- 대시보드 상세: `dashboard-review.md`
- screener 폴백 상세: `screener-review.md`

### 검증 방법
- `py_compile` 구문 검사 후 런타임 import 테스트
- DB 작업: tempfile sqlite3 격리 테스트
- WAL 모드 DB 복사: `.db`, `.db-wal`, `.db-shm` 3파일 항상 확인
