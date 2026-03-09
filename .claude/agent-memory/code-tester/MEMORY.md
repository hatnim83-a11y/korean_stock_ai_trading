# Code Tester Agent Memory

## Key Patterns Found in This Codebase

### 지속적 주의 사항
- `date.today()` → UTC 서버에서 KST 날짜 불일치 (UTC 15:00 이후)
- `update` SQL 후 `cursor.rowcount` 미확인 → silent failure
- DB 작업: `Database()` → `connect()` → 작업 → `close()` 패턴에 finally 필요
- emoji in logger messages: 기존 코드에서 광범위 사용 (스타일 문제, 기능 무관)

### 잔존 기존 이슈 (미수정)
- `database.py` line 524: `save_trade()` date 기본값 `date.today()` UTC 버그
- `formatter.py` line 510: `date.today()` → `__main__` 블록에만 있음, 운영 경로 영향 없음
- `trading_engine.py` `_execute_stop_loss/take_profit`: sync `time.sleep(1)` 이벤트루프 블로킹
- `_close_position_in_db`/`_save_partial_sell_to_db`: DB `finally` 없음 (커넥션 누수 가능성)
- **2026-03-02 검증 완료**: telegram_notifier.py → now_kst() 수정 완료 (date.today() 없음 확인)

### is_trading_day() (config.py, 2026-03-02 추가)
- `holidays.KR`: 법정 공휴일 + 대체 공휴일 포함 (2026-03-02 3.1절 대체공휴일 정확히 감지)
- **미포함**: KRX 임시 특별 휴장일 (연말, 재난 등) — 알려진 한계, docstring 미언급
- `ImportError` fallback: 라이브러리 미설치 시 평일이면 거래일로 간주 (안전)

### _skip_on_holiday 데코레이터 (scheduler.py, 2026-03-02 추가)
- `@_skip_on_holiday` on class method: `functools.wraps` 보존됨 → `__name__`, coroutinefunction 인식 OK
- APScheduler와 완전 호환됨 (asyncio.iscoroutinefunction = True 확인)
- 휴장일에 None 반환 → APScheduler job error 없음 (설계 의도)

### crawlers.py naive/aware 캐시 처리 (2026-03-02)
- naive 타임스탬프 → `replace(tzinfo=timezone.utc)` 처리: 최대 9시간 오차 발생
- 7일 캐시 기준 실용적 영향: 경계 근처 9시간 편차로 만료 캐시를 유효로 간주 가능 (허용 수준)
- `from config import now_kst` 중복 import (line 29, 40) — 기능 무관, 스타일 이슈
- theme_rotator.py `get_history()` line 416: `fromisoformat` + `now_kst()` 비교
  - _load_history가 DB 미활성화(TODO) 상태이므로 naive 데이터 유입 경로 없음 → 현재 위험 없음
  - DB 활성화 시 naive 타임스탬프 처리 로직 추가 필요

### formatter.py (portfolio_optimizer, 2026-03-02 수정 완료)
- 운영 코드 lines 54, 185, 259: `now_kst().date()` 또는 `now_kst()` 사용으로 수정 완료
- line 510: `date.today()` 는 `__main__` 블록 내에만 존재 → 운영 경로 영향 없음
- `format_orders_for_telegram()`: `now_kst().date()` 사용 (line 259 기준)

### 매수/매도 구조적 버그 (2026-02-26, 수정 완료)
- partial_X_executed 재시작 이중 매도: DB position_state 저장으로 해결됨
- 3차 익절 profit_amount=0: `_close_position_in_db` 4번째 파라미터 추가로 해결됨
- execute_sell `_get_kis()` NameError → `_get_kis_api()`로 수정됨

### 테마 파이프라인 v2 (2026-03-03 개편)
- url 키 누락 버그: DB 복원 후 비월요일 재시작 시 스크리닝 종목 0개 — 수정 확인 필요
- main.py ISO week same_week: isocalendar() 비교로 수정 완료 (연말 edge case 해결)
- **수정 완료 (2026-03-06 스케줄 시뮬 검증)**: main.py `run_daily_theme_collection`
  - `ai_map = {r['theme_name']: r for r in ai_results}` + `ai_map[name].get('score', 0)` 패턴으로 수정됨
  - 수정 후: lines 1466-1472 정상 동작 확인
- crawlers.py `_PREDEFINED_THEMES`: 모듈 로드 시 1회 정의, `THEME_NAME_MAP` 자동 생성
- scorer.py `include_ai`/`include_news` 플래그: 크롤링 신규 수집 제어용이지만, theme 딕셔너리에 이미 값이 있으면 플래그와 무관하게 항상 반영됨 (설계 의도이나 docstring 미언급)
- scorer.py `calculate_theme_total_score`: supply_score(25) 포함, BASE_SCORE/overheat 미포함 → score_themes와 최대 점수 구조 불일치 (score_themes 최대 65점 vs 이 함수 최대 75점)
- scorer.py line 304: `calculate_ai_sentiment_score` 내 주석 `(0~10 → 0~15)` → MAX_AI_SCORE=10으로 변경 후 `(0~10 → 0~10)`으로 수정 필요
- scorer.py line 332/335: `calculate_theme_total_score` docstring `모멘텀 0~60`, `AI 0~25` 구 배점 잔존
- weekly_aggregator.py: DAILY_WEIGHTS=[0.25,0.20,0.18,0.15,0.12,0.10] 합=1.0 확인됨
- aggregate_weekly_scores: 연결된 db 객체 필요 (connect() 선행), score/momentum 컬럼 읽음
- DB themes 테이블: UNIQUE(date, theme_name), INSERT OR REPLACE → 멱등 안전
- 08:30 run_theme_analysis + 17:05 run_daily_theme_collection 이중 저장: 17:05가 덮어씀 (설계 의도)

### DB 스키마 v9 추가 (2026-03-03 post_trade_analyzer)
- `post_trade_prices` 테이블: review_id FK, UNIQUE(review_id, check_date)
- `get_reviews_ready_for_analysis`: `julianday('now')` — UTC 기준이나 D+5 체크에서 실질 영향 없음
- `update_trade_review_ai`, `save_post_trade_prices`, `get_post_trade_prices` 추가됨
- **주의**: price_tracker.py `_fetch_yfinance()` 내 `int(row.get('volume', 0))` — volume이 NaN이면 ValueError
  - `pd.isna()` 가드 없음, 수정 필요 (🔴 심각)

### post_trade_analyzer 패턴 (2026-03-03)
- `analyzer.py` line 261: `__import__('datetime').timedelta` — 동작은 하나, 가독성 낮음
  - `from datetime import date, datetime, timedelta`로 교체 권장
- `DEFAULT_MODEL = 'claude-sonnet-4-6'` (fallback) vs 실제 `settings.CLAUDE_MODEL` 사용 — 충돌 없음
- `max_tokens=2000` (주간 요약) 하드코딩 — `MAX_TOKENS` 상수 미사용, 참고 수준
- `sys.path.insert(0, ...)` 중복: price_tracker.py + analyzer.py 각각 있으나 기능 무관
- `import json` in main.py `run_weekly_trade_review` 내부 inline — top-level로 이동 권장

### DB 스키마 v8 주요 구조
- WAL 백업 불완전: `_migrate()` `.db-wal` 미포함 — 서비스 중단 없이 마이그레이션 시 위험
- KRX 12/31 임시 휴장: is_trading_day()에서 감지 안됨 (holidays.KR 미포함)
- `_save_daily_snapshot` 가격 최대 5분 지연 (DB current_price 사용)

### 검증 방법
- `py_compile` 구문 검사 후 런타임 import 테스트
- DB 작업: tempfile sqlite3 격리 테스트
- WAL 모드 DB 복사: `.db`, `.db-wal`, `.db-shm` 3파일 항상 확인
- datetime.utcnow() in JWT create_token: jose exp은 UTC 기준으로 올바름 (now_kst 교체 금지)

### pause/resume 기능 (2026-03-04 초기, 2026-03-06 최종 확인)
- `trading_paused` bool 플래그: asyncio 단일 스레드 → GIL 하에서 bool 대입은 원자적, 스레드 안전
- `_system_ref` 설정 순서: `start()` 내 scheduler.start() → `_system_ref = self` → listener task 순서 OK
- **수정 완료 (2026-03-06)**: `_handle_status_command` → `Database()` 별도 인스턴스 사용 확인
- **수정 완료 (2026-03-06)**: SQL `status='holding'` 사용 확인 (이전 'active' 버그 수정됨)
- `/pause`/`/resume`/`/status` 핸들러가 sync `def` → `start_command_listener` 내 `await` 없이 직접 호출 (정상)

### 종목 목록 보충 로직 (2026-03-06, main.py line 334-352)
- `crawl_all_themes()` 반환 키: `name`, `url`, `stock_count`, `avg_change_rate` 등 — `stocks` 키 없음
- `matched.get("stocks", [])` → 항상 `[]` 반환, 로그에 "0종목 보충" 표시 (기능 무관, 오해 소지)
- `run_daily_screening`은 `theme.get("url")`에서 직접 `crawl_naver_theme_stocks(url)` 호출
  → screener는 `stocks` 키를 사용하지 않으므로 기능 정상
- `asyncio.to_thread(crawl_all_themes)` 올바름 (동기 함수 확인됨, Python 3.10)
- import path `from modules.theme_analyzer import crawl_all_themes` → `__init__.py`에 export 확인됨
- 조건 `if not any(t.get("url") or t.get("stocks") for t in self.today_themes)`: `stocks` 조건 불필요 (항상 없음)
  → 실질적으로 `if not any(t.get("url") for t in ...)` 와 동일

### same_week 버그 (2026-03-09 발견, 수정 필요)
- `main.py` line 329: `same_week = (days_since_rotation < 7)`
  - 화요일에도 `days_since=6 < 7` → `same_week=True` → `aggregate_weekly_scores()` 호출 안 됨
  - **수정**: `same_week = (days_since_rotation < 7) and (today.weekday() != 1)`
  - 재시작 없이 화요일 도달, 17:05 이전/이후 재시작 시 모두 재선정 실패
  - DB `MAX(date) in themes`가 일별 수집(17:05)으로 갱신되어 화요일 전날 날짜 반환 → 재선정 차단
- `main.py` line 1259-1262: `isocalendar()` 잔존 코드 (08:00 로테이션 체크 표시 목적)
  - 기능 영향 없음(표시용), but `last=03-09(월), today=03-10(화)` 케이스에서 '보유 1일' 오표시
  - 실제 재선정 트리거는 아니므로 severity=주의 수준

### 전체 스케줄 시뮬레이션 (2026-03-06 검증 완료)
- py_compile: scheduler.py + main.py 모두 통과
- 14개 모듈 import 전체 통과 (venv 기준)
- 11개 CronTrigger 전부 timezone=_KST_TZ='Asia/Seoul' 포함 확인
- 11개 스케줄 콜백 → 11개 TradingSystem async 메서드 정확히 매핑 확인
- DB 메서드 13개 호출지점 전부 존재 확인
- pause 체크: run_theme_analysis / run_stock_screening / execute_buy_orders 3개만 guard (설계 의도)
- 데이터 흐름: screener('code') -> verifier({**stock}+'code') -> optimizer('stock_code') -> morning_filter('stock_code') 체인 확인
- 주요 미해결 이슈:
  - score_themes(raw_themes[:20]): 20 하드코딩 (config 상수 없음)
  - run_daily_theme_collection score_themes(raw_themes[:30]): 30 하드코딩
  - run_daily_theme_collection 총점 + 15.0: scorer.BASE_SCORE 미참조 (배점 변경 후 10.0으로 수정 필요, overheat_penalty도 누락)
  - available_cash < 100_000: 현금 최소값 하드코딩 (MIN_CASH_THRESHOLD 설정값 없음)
  - execute_buy_orders timeout=30: asyncio.wait_for 하드코딩

### run_daily_health_check + send_daily_report 버그 (2026-03-06, 수정 필요)
- `db.get_trades_by_date(today_str)` → 메서드 미존재, `db.get_trades(now_kst().date())`로 교체 필요
- `s.get("total_value", ...)` → daily_snapshots 컬럼명은 `total_capital`, 성과 지표 전체 오류
- `bal.get("available_cash", 0)` → get_balance() 반환 키는 `cash`
- `db.conn.cursor()` 직접 접근 → `with db.get_cursor() as cursor:` 패턴으로 교체 필요 (3회)
- 테마 수집 체크(16:10 실행)는 항상 오탐: themes는 17:05에 저장되므로 issues가 아닌 info 처리 필요
- get_balance() 반환 키: `cash`, `total_value`, `total_profit`, `profit_rate`, `positions`

### 테마 선정 파이프라인 배점 구조 (2026-03-09 전체 테스트 완료)
- scorer.py score_themes 최대 총점: 25(모멘텀)+0(과열)+15(뉴스)+10(AI)+5(보너스)+10(기본) = **65점**
- S등급 기준 80점: **현재 배점 구조에서 S등급 도달 불가능** (최대 65점)
  → 등급 기준 또는 BASE_SCORE 상향 필요 (설계 의도 확인 필요)
- calculate_theme_total_score: BASE_SCORE/overheat_penalty 미포함, supply_score 포함 → score_themes와 구조 불일치
- score_themes docstring `= 최대 65~100` 표현 혼란 (실제 최대 65점)
- three_day_rate=None → dict.get() None 반환(기본값 무시) → 급가속 체크 스킵 (정상 동작)
- three_day_rate=0.0(크롤링 기본값) → accel_ratio=0 → 급가속 없음 (정상 동작)
- overheat_penalty(8.0%) = -0.0 → `p < 0` False → selection_reason 미표시 (실질 영향 없음)
- format_theme_report: 뉴스점수/AI점수 미표시 (모멘텀+보너스+기본+과열만 표시)
- 단위 테스트 78건 중 77건 통과 (S등급 도달 불가능 1건 FAIL - 구조적 설계 이슈)
- 과열 감점이 score_themes 총점 + weekly_aggregator DB score에 정상 반영됨 확인

### 과열 감점 (Phase 1, 2026-03-09 검증)
- `calculate_overheat_penalty(avg5, avg3)`: 8%→감점시작, 15%→-15점(최대), 급가속(avg3/avg5>=0.8)→추가 -3점
- 최대 감점 clamp: `max(MAX_OVERHEAT_PENALTY, penalty)` → -15점 이하 불가 (확인됨)
- **음수 총점 불가**: 과열감점은 avg5>=8%일 때만 발생, avg5=8%→모멘텀=22.5+과열≥-15+기본10=17.5점 이상 보장
- **float 정밀도 버그 (주의)**: `accel_ratio >= 0.8` → `9.6/12.0 = 0.79999` → False (급가속 -3점 미적용)
  - 수정: `accel_ratio >= 0.8 - 1e-9` 권장 (현재 미수정, 경미한 영향)
- `overheat(8.0)` = -0.0: score_themes에서 `p < 0` False → 선정이유 미표시 (설계 의도에 맞음)
- main.py AI 재계산(line 1494): `momentum + overheat_penalty + news + ai + bonus + BASE_SCORE` — 키 정확히 일치 확인됨

### classify_theme_category (crawlers.py, 2026-03-09 추가 검증)
- `None` 입력 → `AttributeError: 'NoneType'.upper()` — None 방어 없음 (주의 수준, crawl_all_themes 경로에서 None 유입 가능성 낮음)
- `K-방산` → `"기타"` — "방산" 키워드가 `_CATEGORY_KEYWORDS`에 없음 (방위 키워드만 있음)
- `AI반도체` → `"반도체"` — predefined 테마는 `category="반도체"` 명시이므로 자동분류 미적용 → 기능 정상
- 네이버 전용 "AI반도체" 이름 테마는 "반도체"로 분류됨 (predefined AI반도체와 동일 category → OK)
- `"SMR"` 단어 포함 테마 → `"기타"` (원전/SMR 키워드 `_CATEGORY_KEYWORDS`에 없음)
- predefined 14개 중 자동분류와 카테고리명 불일치 (예: 신성장/헬스케어/모빌리티/에너지/금융/통신/IT서비스/소재 등 predefined에만 존재하는 카테고리) — 설계 의도 (predefined는 자동분류 미적용)
- `score_themes({**theme,...})` → category 키 보존됨 (선정 다양성 로직 정상 동작 확인)
- `crawl_all_themes` 분류 순서: `normalize_theme_name()` → `classify_theme_category()` [정상]

### 테마 연장(retention) 기능 (2026-03-09 검증 완료)
- `select_themes_with_retention()`: selector.py 신규 추가, `RETENTION_SCORE=38.0`
- score_map 키: `t.get('theme', t.get('name', ''))` 듀얼 폴백 → aggregate_weekly_scores 결과와 호환
- previous_themes 키: `prev.get('theme', prev.get('name', ''))` 듀얼 폴백 → DB 복원 정규화 dict 호환
- retained > count 트리밍: remaining_slots<=0 else 분기에서 처리 → 정상
- format_theme_report retained 태그: `True→유지, False→신규, None/없음→태그없음` (3-way 분기)
- main.py 430: `is_tuesday and self._previous_themes` 가드 → prev=[] 시 select_top_themes 경로
- select_themes_with_retention 내부도 `if not previous_themes:` 폴백 → 이중 방어
- 주의: select_top_themes 호출 시 min_score 파라미터 미전달(기본 30.0) → 신규 진입 기준 30점, 유지 기준 38점 비대칭 (설계 의도)
- aggregate_weekly_scores 반환에 stock_count 없음 → select_top_themes 필터에서 stock_count=0으로 처리 → 필터 스킵 (정상 설계)
- 통합 테스트: py_compile 3파일 통과, 런타임 import 통과, 실제 데이터 구조로 mock 실행 통과

### 배점 구조 v2 확정 (2026-03-09 Phase 1 검증, 2026-03-09 종합테스트 완료)
- 실제 최대 점수: 25(모멘텀)+0(과열없음)+15(뉴스)+10(AI)+5(보너스)+10(기본) = **65점**
- 등급 기준: S=58, A=48, B=38(=RETENTION_SCORE), C=30(=MIN_SELECTION_SCORE), D=30미만
- RETENTION_SCORE=38 = B등급 하한선과 동일 (설계 의도)
- 음수 총점 불가능 증명: avg5>=8% 시 momentum>=22.5 → total=22.5+(-overheat)+10>=20 (항상 양수)
- `calculate_overheat_penalty` export: __init__.py에 없으나 main.py가 scorer에서 직접 import → 문제 없음
- **등급 기준 통일 완료 (2026-03-09)**: weekly_aggregator._grade + scorer.py score_themes + calculate_theme_total_score + dashboard.html JS 모두 58/48/38/30으로 일치 확인
- TOTAL_MAX_SCORE=65.0, debug log `/65`, docstring `최대 65점` 수정 완료 확인
- **잔존 docstring 불일치 (참고)**:
  - scorer.py `calculate_theme_total_score` Returns 예시: `total_score=87.5, grade='A'` → 실제는 S (old data)
  - scorer.py `calculate_theme_total_score` Example 결과: `81.55` → 실제 실행시 58.0 (supply_score 구조 차이)
  - selector.py `__main__` 테스트 데이터: 82.3/79.1/78.5/75.0/72.0점 → grade='A' hardcoded (실제 S) — 테스트용, 운영 무관
  - screener.py `__main__` 테스트 데이터: 82.3/79.1점 grade='A' (실제 S) — 테스트용, 운영 무관
  - 저점수테마 30점 grade='D' hardcoded (실제 C) — 테스트용, 운영 무관
- `calculate_theme_total_score`: supply_score 포함, BASE_SCORE/overheat 미포함 → score_themes와 구조 불일치 (설계상 별도 함수)
- **구/신 배점 혼재 전환 기간 (약 6영업일)**: weekly_aggregator 가중 평균이 실제보다 높게 나올 수 있음 (경미)

### 테마 관련 종합 검증 완료 (2026-03-09)
- py_compile: scorer/selector/crawlers/__init__/weekly_aggregator 5파일 모두 통과
- category E2E: crawl_all_themes→score_themes(보존)→DB→weekly_aggregator→select_themes_with_retention ✓
- AI 재계산 키 참조 (main.py 1494): momentum/overheat_penalty/news_score/ai_score/bonus_score/BASE_SCORE 전부 정확 ✓
- DB 복원 테마 흐름: name/theme 양쪽 키 정합성 확인 ✓
- score/total_score 동기화 (AI 재계산 후): ✓
- classify_theme_category: 대부분 정확, 'AI반도체'(독립 테마명)는 '반도체' 분류 (predefined에선 무관)

### 상세 히스토리
- 파일: `.claude/agent-memory/code-tester/review-history.md`
