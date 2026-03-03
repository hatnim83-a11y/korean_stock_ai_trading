# Context: 테마 선정 개선

## 변경 이유
방산, AI 등 현재 시장에서 핫한 테마가 선정되지 않는 문제 해결.
근본 원인: 모멘텀(과거 데이터)만 사용, 뉴스/AI 감성(미래 지향) 미활용.
일별 데이터 누적 없이 선정 시점 1회 크롤링만 수행.

## 현재 코드 상태

### 테마 크롤링 (crawlers.py)
- `get_predefined_themes()` (L552-615): 20개 핵심 테마 정의, naver_aliases 포함
- `_match_theme_name()` (L618-628): 부분 매칭 → 거짓양성 위험
- `crawl_all_themes()` (L795-919): 네이버+KRX+predefined 통합, 중복 제거(이름 기준)
- `crawl_theme_news_count()` (존재하지만 호출 안 됨)

### 테마 점수 (scorer.py)
- `score_themes()` (L441-524): 모멘텀(60)+종목수(10)+고정(30)=100
- `calculate_news_score()` (L180-230): 존재, 미사용
- `calculate_ai_sentiment_score()` (L235+): 존재, 미사용, MAX=25
- 상수: MAX_MOMENTUM_SCORE=60, MAX_SIZE_BONUS=10, BASE_SCORE=30

### AI 분석 (ai_analyzer.py)
- `analyze_theme_sentiment()`, `analyze_themes_batch()`: 존재, 미사용
- 모델명: claude-sonnet-4-5-20250929 (업데이트 필요)

### 테마 선정 (selector.py)
- `select_top_themes()` (L52-132): 블랙리스트/점수/종목수 필터 + 카테고리 다양성
- `_select_with_diversity()` (L135-178): theme.get("category") 참조 (메모리만)

### 테마 로테이션 (theme_rotator.py)
- `should_review` (L68-70): `weekday() == 0` (월요일 체크)

### DB (database.py)
- themes 테이블: date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment
- UNIQUE(date, theme_name)
- category 컬럼 없음
- `save_theme_scores()` (L527-552): category 미저장

### 스케줄 (scheduler.py)
- 08:00 on_theme_check (긴급 체크)
- 08:30 on_theme_analysis (테마 분석 + 선정)
- 17:00 on_daily_theme_collection 없음 (추가 필요)

## 영향 범위
- 직접: crawlers.py, scorer.py, ai_analyzer.py, selector.py, theme_rotator.py, database.py, scheduler.py, main.py
- 간접: screener.py (테마 결과 소비), telegram_notifier.py (메시지 표시)
- DB: themes 테이블 구조 변경 (category 추가)

## 작업 중 발견 사항
- screening_log UNIQUE(date, stock_code, stage)로 인해 다중 테마 종목 정보 손실 → 이번 스코프 외
- 네이버 크롤링 종목코드 검증(re.match r'^\d{6}$') 이미 적용됨
- KRX 크롤러 예외 처리 이미 적용됨

## 작업 완료 결과 (2026-03-03)

### 구현 완료
- **Step 0**: 테마명 정규화 (`normalize_theme_name()` + `THEME_NAME_MAP` 25건), `_match_theme_name()` 정확 매칭, DB v10 category 컬럼
- **Step 1**: 점수 체계 변경 (모멘텀40+뉴스20+AI15+종목수10+기본15), `score_themes(include_news, include_ai)` 플래그 추가
- **Step 2**: `weekly_aggregator.py` 신규 생성 — 6일 가중 평균 (0.25~0.10)
- **Step 3**: 로테이션 월→화 변경 (theme_rotator.py, main.py)
- **Step 4**: scheduler.py 17:05 일별 수집 스케줄, main.py 콜백 연결 + 화요일 분기 로직
- **Step 5**: 텔레그램 메시지 화요일 반영

### 수정된 버그 (code-tester 발견)
- `main.py`: AI 결과 타입 오류 — `analyze_themes_sync()` 반환값 `list[dict]`를 `dict`로 처리, `sentiment_score` → `score` 키명 오류 → `ai_map` 변환 적용
- `scorer.py`: docstring/log "60" → "40" 하드코딩 수정

### 배포
- systemd 재시작 완료, DB v10 마이그레이션 적용 확인, 17:05 스케줄 등록 확인
