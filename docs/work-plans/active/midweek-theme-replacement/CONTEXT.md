# CONTEXT: 주중 테마 교체

## 변경 이유
주중에 테마 모멘텀이 꺾여도 다음 화요일까지 교체 불가 → 해당 테마 종목이 최대 14일간 슬롯을 차지하며 더 좋은 기회를 놓침.

## 현재 코드 상태

### check_theme_rotation() — main.py:1336
- 08:00 긴급 트리거(-20%/+15%) 로직만 존재
- 주중 일별 점수 비교 로직 없음

### run_theme_analysis() — main.py:303
- 08:30 same_week 체크 (line 326-331): today_themes + _last_theme_rotation_date 기준
- 주중 교체 시 today_themes만 갱신하면 자동으로 재사용됨

### run_stock_screening() — main.py:668
- 09:05 스크리닝: today_themes 기반
- 손실 종목 재평가 로직 없음

### scheduler.py
- 12개 스케줄 (0~10번, 9번 중복)
- 09:00, 09:10 슬롯 없음

### database.py
- get_top_themes(): selected=1 조회
- 전일 일별 점수(selected=0) 조회 함수 없음

### selector.py
- select_top_themes(), select_themes_with_retention() 존재
- 비활성 테마에서 교체 후보 선정 함수 없음

### portfolio_monitor_v2.py:260
- remove_position(stock_code) 이미 존재

## 핵심 코드 스니펫

### 테마 same_week 재사용 (main.py:326-331)
```python
if self.today_themes and self._last_theme_rotation_date:
    today = now_kst().date()
    days_since_rotation = (today - self._last_theme_rotation_date).days
    is_tuesday = (today.weekday() == 1)
    same_week = (days_since_rotation < 7) and not is_tuesday
    if same_week:
        # 기존 테마 유지 → return
```

### 교체 판단 기준
- 절대 하한: 전일 일별 점수 <= 38점 (B등급 이하)
- 상대 열세: 비활성 최상위 후보와의 점수 차이 >= 15점
- 안전장치: 하루 최대 1개, 2영업일 보호, 화요일 스킵, 후보 최소 35점

## 영향 범위
- 직접: main.py, scheduler.py, config.py, database.py, selector.py
- 간접: portfolio_monitor_v2.py (remove_position 호출), dashboard.html (시각화)
- 무영향: trading_engine, morning_filter, reporter
