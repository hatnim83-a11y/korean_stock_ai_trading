# PLAN: 주중 테마 교체 (Midweek Theme Replacement)

## 목표
매일 08:00에 활성 테마의 전일 점수를 모니터링하여, 후보 대비 크게 떨어진 테마 1개를 교체하고, 해당 테마 보유 종목을 정리 후 새 테마 종목으로 슬롯을 채움.

## 배경
현재 시스템은 화요일 08:30에 테마를 선정하고 다음 화요일까지 1주간 유지. 주중에 테마 모멘텀이 꺾여도 교체 불가 → 해당 테마 종목이 최대 14일간 슬롯을 차지하며 더 좋은 기회를 놓침.

## 구현 단계

### Step 1: config.py — 설정 상수 추가
- [x] Settings 클래스에 MIDWEEK_REPLACEMENT_* Field 6개 추가

### Step 2: database.py — 전일 점수 조회 함수
- [x] `get_daily_theme_scores(target_date)` 메서드 추가 (selected=0 조회)

### Step 3: selector.py — 교체 후보 선정 함수
- [x] `select_replacement_candidate()` 함수 추가

### Step 4: scheduler.py — 스케줄 슬롯 추가
- [x] 콜백 선언 2개, add_job 2건, _run_* 메서드 2개
- [x] main.py 콜백 등록 추가

### Step 5: main.py — 핵심 교체 로직
- [x] 상태 변수 4개 추가
- [x] check_theme_rotation() 확장 (주중 교체 판단)
- [x] _execute_midweek_profit_sells() (09:00)
- [x] _execute_midweek_loss_sells() (09:10)
- [x] run_stock_screening() 확장 (손실 종목 재평가)

### Step 6: dashboard.html — 테마 점수 트렌드 차트
- [x] 라인 차트 추가
- [x] 테마 카드에 전일 대비 delta 표시

## 변경 파일 목록
1. `config.py` — 설정 상수
2. `database.py` — DB 조회 함수
3. `modules/theme_analyzer/selector.py` — 교체 후보 선정
4. `scheduler.py` — 스케줄 슬롯
5. `main.py` — 핵심 로직
6. `web/templates/dashboard.html` — 대시보드

## 롤백 계획
- `MIDWEEK_REPLACEMENT_ENABLED=False` 설정으로 즉시 비활성화
- 코드 변경은 기존 로직에 분기 추가 형태이므로 git revert 가능

## 완료 기준
- py_compile 전체 통과
- code-tester 에이전트 검증 통과
- 서비스 재시작 정상 작동
