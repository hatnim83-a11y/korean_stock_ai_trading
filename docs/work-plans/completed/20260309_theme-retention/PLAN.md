# PLAN: 테마 연장(유지) 기능

## 목표
화요일 재선정 시 기존 테마 점수가 여전히 좋으면 교체하지 않고 유지. 불필요한 테마 교체 방지.

## 배경
- 현재: 매주 화요일 전체 5개 테마를 무조건 새로 선정
- 문제: 수익 중인 테마를 불필요하게 교체 → 거래 비용 증가, 연속성 상실
- 해결: 기존 테마가 새 주간 점수에서도 B등급(38점) 이상이면 슬롯 유지

## 구현 단계

### Step 1: selector.py에 `select_themes_with_retention()` 함수 추가
- 기존 테마 리스트 + 새 scored_themes를 입력받음
- 기존 테마 중 새 점수 >= RETENTION_SCORE인 것을 "유지"
- 남은 슬롯을 새 테마에서 채움 (이미 유지된 테마 제외)
- 유지/교체 사유를 로깅

### Step 2: main.py `run_theme_analysis()` 화요일 로직 수정
- 기존 `select_top_themes()` 대신 `select_themes_with_retention()` 호출
- `self._previous_themes`를 기존 테마로 전달
- 텔레그램 리포트에 유지/교체 정보 포함

### Step 3: selector.py `format_theme_report()`에 유지/신규 표시 추가

## 변경 파일
| 파일 | 변경 |
|------|------|
| `modules/theme_analyzer/selector.py` | `select_themes_with_retention()` 추가, `RETENTION_SCORE` 상수 |
| `main.py` | 화요일 선정 로직에서 retention 함수 호출 |

## 롤백 계획
- `select_themes_with_retention()`은 기존 `select_top_themes()`를 내부에서 호출하므로, 기존 동작 유지 가능
- retention 기능만 제거하면 원복

## 완료 기준
- py_compile 통과
- 시뮬레이션: 기존 5개 중 3개 유지, 2개 교체 시나리오 테스트
- code-tester 에이전트 검증
