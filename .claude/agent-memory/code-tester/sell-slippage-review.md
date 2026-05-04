---
name: 매도 슬리피지 측정 시스템 검증 결과
description: sell-slippage-tracking Phase 1~3 구현 검증 (trading_engine + monitor + main.py)
type: project
---

# 매도 슬리피지 측정 시스템 검증 (2026-05-04)

## 검증 결과: 배포 가능

### 핵심 설계 확인됨
- reference_price 폴백 순서: bid1 → current_price → fallback_price → (0, "none") — 안전
- slippage 공식: (filled - ref) / ref * 100, 음수=불리, 양수=유리 — 의미 올바름
- 중앙화 캡처: 진입점 3개(execute_sell_orders, execute_stop_loss, execute_take_profit)에서만 계산
- 외부 호출처(monitor 4함수, main.py 3경로): result.get("slippage") 추출만

### _save_trades 분기 안전성 확인
- is_sell=True: order.get("slippage") 우선 → None이면 reference_price 폴백 → order_price 폴백
- is_sell=False: slippage = None 강제 초기화 → expected_price 기반 매수 슬리피지 계산
- 매수 경로(execute_portfolio → _save_trades(is_sell=False)) 완전 무영향 확인

### 잔존 주의사항 (주의 수준)
1. execute_stop_loss/take_profit의 time.sleep(1) — 기존 이슈, 이번 구현 무관
2. _wait_for_fills의 filled_price는 전량 체결 시만 업데이트 — 부분 체결 타임아웃 시 slippage=None
   → 시장가 매도 특성상 실전 발생 가능성 낮음 (허용)
3. 가중평균 filled_price로 slippage 계산 시 개별 호가 대비 의미 흐려질 수 있음 — 설계상 허용

### 하드코딩 신규 추가 없음
- SELL_SLIPPAGE_WARN_THRESHOLD: config.py Field로 관리됨 (default=2.0%)
- timeout=5 (inquire_asking_price): 기존 코드와 동일한 값, 새로 추가 아님
- round(..., 4): 기존 slippage 패턴과 일관성

### 테스트 결과
- py_compile 4개 파일: PASS
- scripts/test_sell_slippage.py 11개 시나리오: 전체 PASS
  - S-A·E: 다종목 매도, bid1 reference 정상
  - S-A(stop/profit): execute_stop_loss/take_profit 경로 정상
  - S-B: bid1=0 → current_price 폴백 정상
  - S-C/C(exc): 전체 실패, 예외 발생 → fallback_price / none 폴백 정상
  - S-Compute: _compute_sell_slippage 엣지케이스 정상
  - S-D1/D2/D3: monitor 손절/분할 부분/분할 전량 경로 slippage 비-None 확인
  - S-Compat: slippage 인자 생략 시 None 저장 역호환 확인

**Why:** 매도 5경로에서 slippage 95% NULL 문제 해결. bid1을 reference로 사용하는 설계 검증됨.
**How to apply:** 이후 slippage 관련 수정 시 _save_trades의 is_sell 분기 구조 주의.
