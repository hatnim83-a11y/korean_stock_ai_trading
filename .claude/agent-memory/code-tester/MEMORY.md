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

### 테마 파이프라인 하드코딩 목록 (2026-03-11 v11 리뷰)
- `screener.py:576` `stock_codes[:20]` — 크롤링 풀 크기, config 없음 (MAX_STOCKS_PER_THEME=10과 별개)
- `main.py:427` `raw_themes[:20]` — 비화요일 점수화 개수, config 없음
- `main.py:1533` `raw_themes[:30]` / `1541` `scored_themes[:20]` — 17:05 일별수집 대상, config 없음
- `scorer.py:119-122` `OVERHEAT_THRESHOLD=8.0/OVERHEAT_MAX=15.0/PENALTY_MAX=15.0` — 함수 로컬 상수
- `selector.py:43,46` `MIN_SELECTION_SCORE=30.0/RETENTION_SCORE=38.0` — 모듈 상수 (config 없음)

### aggregate_weekly_scores 결과 키 구조 (v11 확정)
- 포함: name, theme, total_score, score, momentum, momentum_score, news_count, ai_sentiment, category, days_found, daily_scores, selection_reason, grade
- 미포함: url, stock_count, avg_change_rate, news_score, bonus_score, overheat_penalty
- AI 재계산 식이 이 결과에 적용되면 news_score/bonus_score/overheat_penalty = 0 -> 주의
- 실제로는 17:05 score_themes 결과에만 AI 재계산 적용됨 (화요일 08:30은 DB 집계 점수 사용)

### scorer.py time import
- `from datetime import time` -> datetime.time 클래스, time(9,0)으로 사용 (표준 time 모듈 아님)
- now_kst().time() vs time(9,0) 비교: 올바른 사용

### 전체 검증 완료 파일 목록
- 상세: `review-history.md`
- 테마 파이프라인 상세: `theme-pipeline-review.md`
- 대시보드 상세: `dashboard-review.md`
- screener 폴백 상세: `screener-review.md`

### 검증 방법
- `py_compile` 구문 검사 후 런타임 import 테스트
- DB 작업: tempfile sqlite3 격리 테스트
- WAL 모드 DB 복사: `.db`, `.db-wal`, `.db-shm` 3파일 항상 확인
