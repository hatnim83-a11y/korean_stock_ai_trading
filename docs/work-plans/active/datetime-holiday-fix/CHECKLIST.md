# CHECKLIST: datetime 버그 수정 + 휴장일 체크

## 구현 항목
- [x] crawlers.py datetime 수정 (3곳 + 캐시 호환)
- [x] verifier.py date.today() 수정 (1곳)
- [x] report_generator.py date.today() 수정 (1곳)
- [x] performance_calculator.py date.today() 수정 (3곳)
- [x] optimizer.py date.today() 수정 (3곳)
- [x] rebalancer.py date.today() 수정 (2곳)
- [x] holidays 설치 + requirements.txt
- [x] config.py is_trading_day() 추가
- [x] scheduler.py _skip_on_holiday 데코레이터 + 적용
- [x] portfolio_monitor_v2.py _is_market_hours() 개선

## 검증 항목
- [x] py_compile 전 파일 통과
- [x] is_trading_day() 동작 확인 (3/2 대체공휴일=False, 3/3=True, 설날=False)
- [x] code-tester 에이전트 검증 (심각 0건, 배포 가능)

## 배포 항목
- [ ] systemctl restart trading_system (내일 장 전)

## 문서 업데이트 항목
- [x] memory/MEMORY.md 업데이트
