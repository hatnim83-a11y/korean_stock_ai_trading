# CHECKLIST: API 폴백 및 None 방어 버그 수정

## 구현 항목
- [x] 1. performance_calculator.py: None profit_rate 방어 (305행)
- [x] 2. telegram_notifier.py: API 실패 시 DB current_price 폴백 (1063-1069행)
- [x] 3. dashboard_service.py: 동일 폴백 적용 (110-115행)
- [x] 4. screener.py: API retry 로직 추가 (189-199행, 3회 지수백오프)
- [x] 5. DB: NULL profit_rate/profit_amount/buy_price 수정 (26건)
- [x] 6. (추가) report_generator.py: profit_rate None 방어 일괄 적용
- [x] 7. (추가) telegram_notifier.py: profit_rate None 방어 일괄 적용 (501,506,576,582행)

## 검증 항목
- [x] py_compile 전체 통과 (5개 파일)
- [x] code-tester 에이전트 검증 (심각 0건, 배포 가능)

## 배포 항목
- [ ] systemd 재시작

## 문서 업데이트 항목
- [ ] 메모리 업데이트
