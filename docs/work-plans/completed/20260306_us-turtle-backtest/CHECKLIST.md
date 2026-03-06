# CHECKLIST: 미국 주식 터틀 트레이딩 백테스트

## 구현 항목
- [x] S&P 500 티커 리스트 확보 (200개 섹터별 선별)
- [x] backtest_us_turtle.py 작성 (데이터 로드 + 지표 계산)
- [x] 순수 터틀 전략 구현 (20일 돌파 + 10일 이탈 + ATR 손절)
- [x] 터틀+모멘텀필터 전략 구현 (MA200 + 거래량)
- [x] Buy & Hold SPY 벤치마크 구현
- [x] 3전략 비교 테이블 출력

## 검증 항목
- [x] py_compile 통과
- [x] 백테스트 실행 완료
- [x] SPY Buy & Hold 수익률 확인 (+100.8%, 5년간 실제와 유사)

## 배포 항목
- [x] 결과 로그 확인

## 문서 업데이트 항목
- [x] docs/backtest_us_turtle_results.md 작성
