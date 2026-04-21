# CONTEXT — 테마 재선정 회전문 방지

## 변경 이유 (근본 원인)

2026-04-21 화요일 08:30 재선정에서 네 가지 설계 결함이 **상호 증폭**되어 비합리적 결과 도출:

**A. 모멘텀 ±10% clamp (scorer.py:50-97)**
- 5일 수익률을 -10~+10%로 clamp 후 0~25점 선형 매핑
- +8% 이상 테마가 모두 20~25점대에 집중 → DB 저장값이 상한 포화

**B. 실시간 보강 증폭 계수 ×1.5 (main.py:2519)**
- `adjustment = momentum_delta * 1.5`
- delta -22.8pp → -34.2점 음수 보정 폭증 (통신 사례)

**C. 상위 15개만 실시간 보강 (main.py:2490)**
- `top_15 = scored_themes[:15]`
- 상위만 냉각, 중위권 DB 원본 유지 → 순위 역전

**D. retention 48 vs 신규 30의 18점 격차 + 쿨다운 부재 (selector.py:43, 46)**
- 드롭된 테마가 신규 기준 30점은 넘어 즉시 재진입 허용

## DB 재검증 결과 (사용자 요청으로 수행)

기준일 2026-04-20, 6영업일 가중치 `[0.25, 0.20, 0.18, 0.15, 0.12, 0.10]` 재현:

| 테마 | DB 가중평균 | 실시간 보강 | 최종 | 로그값 | 일치 |
|---|---|---|---|---|---|
| 통신 | 83.66 | 모멘텀 -34.2 | 49.46 | 49.5 | ✓ |
| 금융 | 41.42 | +4.5 | 45.92 | 45.9 | ✓ |
| 아이폰 | 38.35 | (top15 밖) | 38.35 | 38.4 | ✓ |
| 조선 | 37.63 | (top15 밖) | 37.63 | 37.6 | ✓ |
| CXL | 36.15 | (top15 밖) | 36.15 | 36.1 | ✓ |

**결론**: 버그 없음. 설계대로 동작. 다만 설계가 상황과 충돌.

## 현재 코드 상태

### main.py:2470-2583 `_enrich_tuesday_themes`
```python
top_15 = scored_themes[:15]  # L2490 — 상위 15개만 보강
...
if abs(momentum_delta) > 3.0:
    adjustment = momentum_delta * 1.5  # L2519 — 증폭
    t["total_score"] = round(t.get("total_score", 0) + adjustment, 2)
```

### modules/theme_analyzer/selector.py:43-52 상수
```python
MIN_SELECTION_SCORE = 30.0       # 신규 기준
RETENTION_SCORE = 48.0           # 유지 기준
MAX_THEMES_PER_CATEGORY = 2
```

### modules/theme_analyzer/selector.py:138-244 `select_themes_with_retention`
- 기존 유지 판별 → 남은 슬롯 신규 채움 → 병합
- **현재 dropped 이름 필터 없음** → 회전문 발생

### modules/theme_analyzer/weekly_aggregator.py:17-19
```python
DAILY_WEIGHTS = [0.25, 0.20, 0.18, 0.15, 0.12, 0.10]  # 합계 1.00
```

## 영향 범위

- **직접 영향**: `main.py` (보강 로직), `selector.py` (선정 로직), `config.py` (상수)
- **간접 영향 없음**: DB 스키마·대시보드·REST API·텔레그램 (로그 추가만)

## 관련 과거 사항 (MEMORY 참조)

- **Midweek Replacement** (2026-03-18): 평일 08:00 부진 테마 교체 시스템. 화요일은 스킵(`_check_midweek_replacement`). 이번 변경은 화요일 경로만 다루고 월요일 미드위크 경로는 건드리지 않음.
- **Theme Selection System** (2026-03-03, 2026-03-13, 2026-03-26): 점수 체계 진화. 모멘텀 clamp ±10%는 2026-03-26 도입.
- **Theme 연장** (2026-03-09): 화요일 재선정 시 기존 테마 38점 이상이면 유지. 이후 48점으로 상향됨.

## 리뷰 반영 기록

- Plan 에이전트 리뷰: min_new vs 쿨다운 중복 지적 → min_new 제거
- strategy-coder 리뷰: `db_score` 신규 필드 불필요 지적 → 제거, 기존 score 재활용도 Phase 3로 연기
- 공통: 전체 테마 AI 확장 비용 위험 → top30 일괄로 축소
