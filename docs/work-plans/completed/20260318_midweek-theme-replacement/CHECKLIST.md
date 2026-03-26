# CHECKLIST: 주중 테마 교체

## 구현 항목
- [x] config.py: MIDWEEK_REPLACEMENT_* 설정 상수 6개 추가
- [x] database.py: get_daily_theme_scores() 메서드 추가
- [x] selector.py: select_replacement_candidate() 함수 추가
- [x] scheduler.py: 콜백 선언 + add_job + _run_* 메서드 추가
- [x] main.py: 상태 변수 4개 추가
- [x] main.py: check_theme_rotation() 주중 교체 로직
- [x] main.py: _execute_midweek_profit_sells() 09:00
- [x] main.py: _execute_midweek_loss_sells() 09:10
- [x] main.py: run_stock_screening() 손실 종목 재평가
- [x] main.py: _setup_scheduler_callbacks() 콜백 등록
- [x] main.py: run_theme_analysis() 교체 정보 반영
- [x] dashboard.html: 테마 점수 트렌드 차트
- [x] dashboard.html: 테마 카드 delta 표시

## 검증 항목
- [x] py_compile 전체 수정 파일 통과
- [x] code-tester 에이전트 검증 통과 (심각 0건, 주의 3건→2건 수정)
- [ ] 수동 테스트 (python main.py --manual --test --real)
- [ ] 서비스 재시작 정상 작동

## 배포 항목
- [ ] sudo systemctl restart trading_system
- [ ] 로그 확인 (교체 발생/미발생 로깅)

## 문서 업데이트 항목
- [x] memory/MEMORY.md — 주중 교체 기능 추가 기록
- [ ] active/ → completed/ 아카이브
