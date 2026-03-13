# 테마 분석 파이프라인 개선 - Context

## 변경 이유
- AI 감성분석: ai_analyzer.py:237에서 `len(news_text) < 50` 가드 → 뉴스 텍스트 미전달로 항상 5.0 반환
- 모멘텀: scorer.py:454 `if kis and theme.get("stocks")` — stocks 키가 없어 항상 False → 크롤링 avg_change_rate(당일 1일치)만 사용
- 화요일 보강: weekly_aggregator.py에서 DB 가중평균만 반환, 실시간 시장 변화 미반영

## 현재 코드 상태
- crawlers.py: `crawl_theme_news_count()` (line 406) — display=1, 건수만 반환
- scorer.py: `_collect_news_counts()` (line 612) — 건수만 dict[str, int] 반환
- scorer.py: `score_themes()` (line 491) — news 텍스트 미포함, stocks 미매핑
- ai_analyzer.py: `analyze_themes_batch()` (line 367) — `theme.get("news")` 참조하지만 데이터 없음
- database.py: themes 테이블 — url 컬럼 없음 (v11까지)
- weekly_aggregator.py: `aggregate_weekly_scores()` — url 미반환

## 핵심 데이터 흐름
```
17:05: crawl_all_themes() → score_themes(include_news=True) → analyze_themes_sync(top_20) → DB 저장
08:30 화: aggregate_weekly_scores(DB) → select_themes_with_retention() → URL 보충 → 스크리닝
```

## 영향 범위
- 직접: 17:05 일별 수집, 화요일 08:30 테마 선정
- 간접: 09:05 종목 스크리닝 (선정된 테마 기반)
