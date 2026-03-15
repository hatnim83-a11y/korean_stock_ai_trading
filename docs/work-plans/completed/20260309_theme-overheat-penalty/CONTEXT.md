# CONTEXT: 테마 점수 과열 감점

## 변경 이유
실전 매매 13건 분석 결과 고점수 테마가 손실. 모멘텀 높은 테마 = 이미 급등 → 고점 매수 → 손절.

## 현재 코드 상태
- `modules/theme_analyzer/scorer.py:33-38` — 배점 상수 (40+20+15+10+15=100)
- `modules/theme_analyzer/scorer.py:475` — clamping -15~+15
- `modules/theme_analyzer/scorer.py:487-488` — size_bonus = stock_count * 2
- `modules/theme_analyzer/scorer.py:491` — total 계산식
- `modules/theme_analyzer/selector.py:43` — MIN_SELECTION_SCORE = 15.0

## 영향 범위
- 직접: scorer.py (점수 계산), selector.py (필터링)
- 간접: weekly_aggregator.py (가중 평균은 DB 저장 점수 사용 → 새 배점으로 저장됨)
- 간접: 대시보드 (점수 표시 → 배점 변경으로 절대값 달라짐, 상대 순위는 유지)
