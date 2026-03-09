# CONTEXT: 테마 카테고리 자동 분류

## 현재 코드 상태
- `crawlers.py:577-620` — predefined 20개 테마에만 category 있음
- `crawlers.py:837-971` — crawl_all_themes()에서 predefined 매칭 시만 category 보강
- `database.py:537-563` — save_theme_scores()에서 `theme.get('category', '기타')` 기본값
- `selector.py:247-290` — _select_with_diversity() MAX_THEMES_PER_CATEGORY=2

## 영향 범위
- 직접: crawlers.py만 수정 (분류 함수 + 적용)
- 간접: selector.py 다양성 로직 활성화, DB 저장 시 실제 카테고리 반영
- 간접: weekly_aggregator.py의 category 기반 집계 활성화
