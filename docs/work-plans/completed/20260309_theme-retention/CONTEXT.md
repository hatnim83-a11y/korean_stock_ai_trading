# CONTEXT: 테마 연장 기능

## 변경 이유
매주 화요일 전체 교체는 성과 좋은 테마를 불필요하게 버림. 점수 기준 유지/교체 판단 필요.

## 현재 코드 상태
- `main.py:414-432` — 화요일 선정: `select_top_themes(scored_themes)` 호출, 무조건 전체 교체
- `main.py:120-126` — `self.today_themes`, `self._previous_themes`, `self._last_theme_rotation_date`
- `selector.py:52-132` — `select_top_themes()`: 블랙리스트/점수/종목수 필터 + 다양성 로직
- `weekly_aggregator.py:48-164` — `aggregate_weekly_scores()`: 6영업일 가중 평균

## 핵심 코드 스니펫

### main.py 화요일 선정 (라인 414-432)
```python
themes = select_top_themes(scored_themes, count=settings.TOP_THEME_COUNT)
self.today_themes = themes  # 전체 교체
self._last_theme_rotation_date = now_kst().date()
```

### 기존 테마 참조 가능한 변수
```python
self._previous_themes  # 직전 테마 (비교용으로 이미 존재)
self.today_themes      # 현재 활성 테마
```

## 영향 범위
- 직접: selector.py (신규 함수), main.py (화요일 로직)
- 간접: 텔레그램 리포트 (유지/교체 정보 추가)
- 간접 없음: screener.py (today_themes 그대로 사용), portfolio_monitor (보유 종목 독립)
