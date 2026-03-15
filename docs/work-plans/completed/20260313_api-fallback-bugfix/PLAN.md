# PLAN: API 폴백 및 None 방어 버그 수정

## 목표
KIS API 실패 시 수익률 0% 표시, 일일 리포트 TypeError, 스크리닝 전면 실패 등 5개 버그 일괄 수정

## 배경
- 2026-03-13 KIS REST API 500 에러로 스크리닝 전면 실패 (retry 없음)
- 포트폴리오 조회 시 API 실패 → current_price = buy_price 폴백 → 수익률 0%
- 3/9부터 일일 리포트 미발송 (profit_rate=NULL → TypeError)

## 구현 단계

### Step 1: performance_calculator.py None 방어
- [ ] `calculate_win_rate()` 305행: `profit_rate = trade.get("profit_rate") or 0`
- [ ] 동일 패턴 검색하여 다른 곳도 방어

### Step 2: telegram_notifier.py DB 폴백
- [ ] KIS API 실패 시 DB의 current_price 사용
- [ ] API 실패 표시 추가

### Step 3: dashboard_service.py DB 폴백
- [ ] 동일 패턴 적용

### Step 4: screener.py API retry 로직
- [ ] `get_stock_full_info` 호출부에 retry 추가 (3회, 지수 백오프)
- [ ] 전체 실패 감지 시 로깅 강화

### Step 5: DB 정리
- [ ] trades 테이블 NULL profit_rate 2건 → 0으로 업데이트

## 변경 파일 목록
1. `modules/reporter/performance_calculator.py`
2. `modules/reporter/telegram_notifier.py`
3. `web/dashboard_service.py`
4. `modules/stock_screener/screener.py`
5. `data/trading.db` (SQL UPDATE)

## 롤백 계획
- git revert로 코드 변경 롤백 가능
- DB는 수동 복구 불필요 (NULL→0은 안전한 변경)

## 완료 기준
- performance_calculator에서 None profit_rate에도 TypeError 미발생
- API 실패 시 DB 가격으로 수익률 표시
- 스크리닝 시 API 일시 장애에 retry 수행
