# CONTEXT: 미국 주식 터틀 트레이딩 백테스트

## 변경 이유
- 한국 시장에서 순수 터틀(+136.8%)이 기존 전략(+336.9%) 대비 열위
- 종목 풀 부족(38개)이 주요 원인 — 미국 S&P 500(~500개)로 해결 가능
- yfinance로 미국 데이터 무료 조회 가능 (추가 API 불필요)

## 참조 코드
- 한국 터틀 백테스트: scripts/backtest_live_logic.py --compare-turtle
- 터틀 청산 로직: backtest_live_logic.py 라인 583-602 (check_exits 내 turtle_exit 분기)
- 순수 터틀 진입: backtest_live_logic.py 라인 477-558 (execute_pure_turtle_entry)

## 한국 vs 미국 터틀 비교 포인트
- 종목 수: 38 → ~500 (13배)
- 돌파 신호 빈도: 286건/3년 → 예상 수천건
- 추세 지속성: 미국이 장기 추세 더 뚜렷
- 수수료: 한국 0.015% → 미국 0 (Alpaca 등)

## 영향 범위
- 신규 파일만 생성, 기존 시스템에 영향 없음

## 작업 중 발견 사항
(구현 중 업데이트)
