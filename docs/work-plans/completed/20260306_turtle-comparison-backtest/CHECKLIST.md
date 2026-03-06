# CHECKLIST: 터틀 트레이딩 동일 조건 백테스트 비교

## 구현 항목
- [x] backtest_live_logic.py에 Low_10, High_20 지표 추가
- [x] 터틀 청산 로직 구현 (10일 저가 이탈 + 2xATR 손절)
- [x] --turtle-exit 플래그 추가
- [x] --no-max-hold 플래그 추가
- [x] 순수 터틀 진입 모드 구현 (20일 고가 돌파 + ATR 사이징)
- [x] --pure-turtle 플래그 추가
- [x] --compare-turtle 플래그로 4전략 비교 실행 모드 추가
- [x] 결과 테이블 출력 (수익률, CAGR, MDD, Sharpe, 승률, 손익비)

## 검증 항목
- [x] py_compile 통과
- [x] 기존 전략(A) 결과가 +337% 근처인지 확인 → +336.92% OK
- [x] 기존 --compare 등 플래그가 정상 동작하는지 확인 (--help OK)
- [x] 4전략 모두 동일 데이터/기간/자본으로 실행되는지 확인

## 배포 항목
- [x] 백테스트 실행 완료
- [x] 결과 로그 확인

## 문서 업데이트 항목
- [x] docs/backtest_turtle_results.md 결과 업데이트
