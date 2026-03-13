# 테마 분석 파이프라인 개선

## 목표
테마 점수 정확도를 높이기 위해 3가지 결함 수정:
1. AI 감성분석이 항상 5.0점 → 뉴스 텍스트 전달하여 실제 분석
2. 모멘텀 부정확 → 종목 매핑으로 KIS API 5일 수익률 계산
3. 화요일 08:30 실시간 검증 없음 → 실시간 보강 로직 추가

## 구현 단계

### Phase 1: 17:05 일별 수집 강화
- [x] Step 1-1: crawlers.py — `crawl_theme_news()` + `_clean_news_html()` 추가
- [x] Step 1-2: scorer.py — `_collect_news_data()` 교체, score_themes 반환값에 news 키 추가
- [x] Step 1-3: scorer.py — `_enrich_theme_stocks()` 종목 매핑 함수 추가
- [x] Step 1-4: main.py 17:05 경로 확인 (추가 수정 불필요)

### Phase 2: 화요일 08:30 실시간 보강
- [x] Step 2-1: database.py — DB v12 (themes.url 컬럼)
- [x] Step 2-2: database.py — save_theme_scores() url 저장
- [x] Step 2-3: weekly_aggregator.py — url 반환 추가
- [x] Step 2-4: main.py — 화요일 08:30 실시간 보강 로직

## 변경 파일 목록
| 파일 | 변경 내용 |
|------|----------|
| modules/theme_analyzer/crawlers.py | crawl_theme_news(), _clean_news_html() |
| modules/theme_analyzer/scorer.py | _collect_news_data(), _enrich_theme_stocks() |
| modules/theme_analyzer/__init__.py | crawl_theme_news export |
| main.py | 화요일 08:30 실시간 보강 로직 |
| database.py | DB v12 url 컬럼, save_theme_scores url |
| modules/theme_analyzer/weekly_aggregator.py | url 반환 |

## 롤백 계획
- 모든 신규 경로는 실패 시 기존 동작 폴백
- DB v12는 컬럼 추가만 (기존 데이터 영향 없음)
- git revert로 원복 가능

## 완료 기준
- crawl_theme_news() → count + text 반환
- score_themes() → news/stocks 키 포함
- AI 분석 → 5.0이 아닌 실제 점수 반환
- DB v12 마이그레이션 정상
- py_compile 통과 + code-tester 검증
