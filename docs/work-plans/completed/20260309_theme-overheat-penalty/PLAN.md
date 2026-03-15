# PLAN: 테마 점수 과열 감점 + 배점 조정 (Phase 1)

## 목표
테마 점수가 수익률과 역상관하는 문제 해결. 과열 테마(이미 급등한 테마) 감점 로직 추가.

## 배경
- 실전 13건 분석: 70점+ 테마 → 평균 -0.15% 손실
- 손절 4건 전부 적자 (-10.48% 평균)
- 근본 원인: 모멘텀 높은 테마 = 고점 매수 → 조정 시 손절

## 구현 단계

### Step 1: 배점 상수 변경 (scorer.py:33-38)
- [x] MAX_MOMENTUM_SCORE: 40→25
- [x] MAX_NEWS_SCORE: 20→15
- [x] MAX_AI_SCORE: 15→10
- [x] MAX_SIZE_BONUS: 10→5
- [x] BASE_SCORE: 15→10

### Step 2: calculate_overheat_penalty() 추가 (scorer.py)
- [x] 5일 수익률 +8% 이상 → 감점 시작, +15%에서 -15점(최대)
- [x] 3일/5일 비교: 급가속 추가 -3점

### Step 3: score_themes() 통합 수정 (scorer.py:491)
- [x] overheat_penalty 계산 및 total에 반영
- [x] scored_theme dict에 "overheat_penalty" 키 추가

### Step 4: 모멘텀 clamping 범위 조정 (scorer.py:475)
- [x] -15~+15 → -10~+10

### Step 5: 종목수 보너스 조정 (scorer.py:487-488)
- [x] stock_count * 2 → stock_count * 1

### Step 6: MIN_SELECTION_SCORE 변경 (selector.py:43)
- [x] 15.0 → 30.0

### Step 7: 독스트링/주석 업데이트
- [x] scorer.py 파일 상단 배점 설명 갱신
- [x] score_themes() 독스트링 갱신
- [x] calculate_momentum_score() 독스트링 갱신

## 변경 파일
| 파일 | 변경 |
|------|------|
| `modules/theme_analyzer/scorer.py` | 배점 상수, overheat 함수, score_themes, clamping, 종목수 |
| `modules/theme_analyzer/selector.py` | MIN_SELECTION_SCORE |

## 롤백 계획
- git revert로 즉시 원복 가능 (2파일만 변경)

## 완료 기준
- py_compile 통과
- calculate_overheat_penalty 단위 테스트 통과
- code-tester 에이전트 검증 통과
