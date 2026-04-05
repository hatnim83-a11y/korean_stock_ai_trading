# CONTEXT — 3월 분석 기반 수익률 개선

## 변경 이유
3월 실전 데이터에서 손절(-453,200원)이 익절(+471,450원)을 거의 상쇄. 손절 6건 중 5건이 매수 후 0~1일 만에 발동, 이후 D+5일 내 +15~36% 반등.

## 핵심 코드 위치
- `config.py:235~242` — GRACE_PERIOD_*, TRAIL_LEVEL1_PCT
- `config.py:211~220` — MAX_STOCKS_PER_THEME/SECTOR
- `config.py:487~524` — MAX_GAP_DOWN_PERCENT, MIN_STRENGTH
- `portfolio_monitor_v2.py:806~840` — `_check_stop_loss()` 보호기간 분기
- `main.py:1155~1195` — Phase 5.5 테마/섹터 분산 필터

## 코더 에이전트 발견사항
- `STOP_LOSS_FAST(-7%)`는 데드 코드 — 실제 손절은 ATR 기반(-5%~-8%)
- 전략 4(모멘텀 소진 5일/3%)는 기존 `MAX_HOLD_DAYS_LOSS=5`와 동일 → 제거
- L1 활성화 +10% 상향은 최대수익 9.9% 종목 L1 미발동 위험 → 취소

## 영향 범위
- 직접: config.py, portfolio_monitor_v2.py, main.py
- 간접: 모닝필터(config 참조), 포트폴리오 옵티마이저(분산 필터)
