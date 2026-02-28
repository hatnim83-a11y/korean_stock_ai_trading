# 테마 파이프라인 리뷰 (2026-02-27)

## 변경 사항 3가지 검증 결과

### 변경 1: DB 복원 시 "name", "theme", "score", "total_score" 4개 키 포함
- 이전: `{"theme": t["theme_name"], "score": t["score"]}` (2개 키)
- 이후: `{"name": t["theme_name"], "theme": t["theme_name"], "score": t["score"], "total_score": t["score"]}` (4개 키)
- 평가: screener.py가 `theme.get("name")`과 `t.get("total_score", t.get("score"))`를 모두 참조하므로 개선 맞음

### 변경 2: TOP_THEME_COUNT 4→5
- config.py default=5로 변경됨
- 하지만 .env line 118: `TOP_THEME_COUNT=4` — pydantic-settings가 .env 우선 적용
- 실제 `settings.TOP_THEME_COUNT = 4` (변경 미반영!)
- .env도 수정해야 변경 완료됨

### 변경 3: 주간 로테이션 (rolling 7일 → 매주 월요일)
- theme_rotator.py `should_review`: `days_held >= 7` → `now_kst().weekday() == 0`
- main.py 재사용 판정: `days_since < 7` → `not is_monday and same_week` (isocalendar 기반)
- isocalendar 엣지 케이스 검증 완료 (연도 경계도 ISO year 기준으로 올바름)
- 월요일 로테이션 흐름: 08:00 check_rotation_needed(True) → _last_date 리셋 안함 → 08:30 is_monday=True → 재크롤링 OK

## 심각 버그: DB 복원 후 재사용 시 url 키 누락

- **증상**: 재시작 후 같은 주 비월요일에 today_themes를 DB 복원으로 채움
  - normalized dict: `{"name":..., "theme":..., "score":..., "total_score":...}` — url 키 없음
  - 08:30 재사용 조건 충족 → 재크롤링 없이 return
  - 09:05 screener: `theme.get("url") = None` → `theme_stocks[name] = []` → 종목 0개 → 스킵
  - 결과: 후보 종목 0개 → 매수 0건

- **영향**: 서비스 재시작 후 같은 주 비월요일에 발생
  - 재시작 없이 계속 실행 중인 경우: 정상 (run_theme_analysis 실행 후 url 있음)
  - KRX 소스, predefined 소스 테마도 동일 문제 (url 없음)

- **수정 방안 (권장)**: main.py 재사용 판정에서 url 없으면 재크롤링 강제
  ```python
  # 재사용 조건에 url 체크 추가
  themes_have_url = all(t.get("url") for t in self.today_themes)
  if not is_monday and same_week and themes_have_url:
      # 재사용 경로
  ```

## 통과 항목
- selector.py `t["name"]` KeyError 없음 (모든 크롤링 소스가 name 키 사용)
- screener.py `screen_stocks_in_theme`: `theme.get("name", "Unknown")` 안전
- screener.py `screen_all_themes`: `theme_stocks.get(theme_name, [])` 안전
- check_rotation_needed(scored_themes): scored_themes의 "theme" 키 있음
- 월요일 긴급 트리거 vs 주간 로테이션 충돌 없음
- isocalendar same_week 로직 엣지 케이스 통과
- py_compile 4개 파일 모두 통과

---

## 전수 dict 키 불일치 리뷰 (2026-02-27 추가)

### 테마 dict 키 전체 흐름 맵

```
crawlers.py        → {"name", "url", "avg_change_rate", "stock_count", "source", ...}  ← "theme" 키 없음
scorer.py          → {**theme, "theme": theme_name, "total_score", "score", "momentum_score", ...}
                      ↑ "name" 키도 원본에서 상속, "theme" 키 새로 추가 (= name과 동일값)
selector.py        → 그대로 전달 (name, theme, url, score, total_score 모두 있음)
  ├→ DB 저장 경로 (selector.py line 282-291):
  │    {"theme": t["name"], "score": t.get("total_score", 0), ...}  ← url 저장 안 함
  └→ DB 저장 경로 (main.py line 389-402):
       {"theme": t.get("theme", t.get("name","")), "score": t.get("score",0), ...}  ← url 저장 안 함

DB themes 테이블   → 컬럼: id, date, theme_name, score, momentum, supply_ratio, news_count, ai_sentiment
                     ← url, avg_change_rate, stocks, stock_count 저장 없음

DB 복원 (main.py)  → {"name": t["theme_name"], "theme": t["theme_name"], "score": t["score"], "total_score": t["score"]}
                     ← url 없음, avg_change_rate 없음, stocks 없음, stock_count 없음
```

### 버그 B: theme_rotator 키 불일치 (주의)

```python
# theme_rotator.py check_rotation_needed line 218
if theme['theme'] == self.current_main_theme.theme_name:  # 직접 인덱싱
# theme_rotator.py check_rotation_needed line 227
self.update_main_theme_score(main_theme_data['score'])    # 직접 인덱싱
# theme_rotator.py select_new_main_theme line 310
sorted_themes = sorted(current_themes, key=lambda x: x['score'], reverse=True)
# theme_rotator.py select_new_main_theme line 323
self.set_main_theme(theme_name=new_theme['theme'], initial_score=new_theme['score'])
```

실제 입력 = scored_themes → "theme", "score" 키 모두 있음 → OK
이론적 위험: raw_themes (crawlers 결과) 직접 전달 시 "theme" 키 없음 → KeyError
    현재 코드에서는 항상 scored_themes가 전달되므로 실용상 안전.

### 버그 C: screener.py date.today() UTC 버그 (주의)

```python
# modules/stock_screener/screener.py line 483
db.save_screened_stocks(stocks_to_save, date.today())  # UTC 기준
```
수정: `now_kst().date()`

### 버그 D: screener.py datetime.now() (참고)

```python
# modules/stock_screener/screener.py line 420, 495
start_time = datetime.now()  # UTC, elapsed 계산에만 사용 → 무해
```

### 버그 E: morning_screener.py datetime.now() (참고)

```python
# modules/morning_filter/morning_screener.py line 208, 302
start_time = datetime.now()  # UTC, elapsed 계산에만 사용 → 무해
```

### dashboard_service.py get_themes_data (참고)

DB themes 테이블 컬럼: theme_name (NOT "name", NOT "theme")
current_themes의 키: theme_name
HTML 템플릿이 theme_name을 올바르게 사용해야 함 — 확인 필요

### 통과: screner.py run_daily_screening 내 url 처리

```python
# screener.py line 441-450
theme_url = theme.get("url")
if theme_url:
    stocks = crawl_naver_theme_stocks(theme_url)
    ...
else:
    theme_stocks[theme_name] = []  # url 없으면 빈 리스트
```
url 없을 때 빈 리스트 처리 → 해당 테마 스크리닝 스킵
재시작 후 DB 복원 테마(url 없음)가 09:05에 사용되면 전 테마 종목 0개 → 심각 버그 (기존 문서 참조)
