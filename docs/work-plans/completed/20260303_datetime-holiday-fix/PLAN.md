# PLAN: datetime 버그 수정 + 휴장일 체크 로직 추가

## 목표
1. 프로젝트 전반의 `datetime.now()` / `date.today()` → `now_kst()` 변환
2. 한국 증시 공휴일 체크 로직 추가 (`holidays` 라이브러리)

## 구현 단계

### Part 1: datetime 버그 수정
- [ ] crawlers.py: 351-352, 529, 827 수정
- [ ] verifier.py:384 수정
- [ ] report_generator.py:73 수정
- [ ] performance_calculator.py:368-369, 456 수정
- [ ] optimizer.py:261, 343, 436 수정
- [ ] rebalancer.py:133, 186 수정

### Part 2: 휴장일 체크 로직
- [ ] holidays 설치 + requirements.txt 추가
- [ ] config.py에 is_trading_day() 추가
- [ ] scheduler.py에 _skip_on_holiday 데코레이터 + 8개 함수 적용
- [ ] portfolio_monitor_v2.py의 _is_market_hours() 개선

### 검증
- [ ] py_compile 전체 통과
- [ ] is_trading_day() REPL 테스트
- [ ] code-tester 에이전트 실행

## 변경 파일 목록
requirements.txt, config.py, scheduler.py, crawlers.py, verifier.py,
report_generator.py, performance_calculator.py, optimizer.py, rebalancer.py,
portfolio_monitor_v2.py (총 10개)

## 롤백 계획
git stash 또는 git revert로 원복 가능

## 완료 기준
- 모든 datetime.now()/date.today() 운영 코드에서 제거
- 휴장일에 스케줄러 작업 스킵 확인
- py_compile 통과
