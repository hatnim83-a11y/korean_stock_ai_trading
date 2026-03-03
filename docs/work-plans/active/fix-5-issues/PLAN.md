# PLAN: 봇 점검 이슈 5건 수정 + DB 스키마 점검

## 목표
봇 건강점검에서 발견된 5개 이슈를 수정하고, DB 스키마가 설계대로 동작하는지 확인

## 구현 단계

### Fix 1: screening_log 저장 로직 추가
- screener.py의 screen_stocks_in_theme() 루프에서 모든 종목(통과/거부) screening_log 저장
- database.py의 save_screening_log() 함수는 이미 존재, 호출만 추가

### Fix 2: KRX 크롤러 에러 핸들링 강화
- crawlers.py:344 `stock.get_index_ticker_list(market='테마')` 에러 방어
- pykrx API 변경에 대응하는 try-except + 대안 호출

### Fix 3: AI 검증 "Hold" 필터링
- verifier.py:37 `EXCLUDE_RECOMMENDATIONS = ["No"]` → `["No", "Hold"]`
- verifier.py:429 모의검증도 동일 수정

### Fix 4: 대시보드 KISApi 싱글톤 캐싱
- dashboard_service.py:39-46 `_get_kis_api()`, `_get_order_api()` 싱글톤 패턴

### Fix 5: 종목코드 유효성 검증 추가
- crawlers.py:301 네이버 크롤링 시 validate_stock_code() 적용

## 변경 파일 목록
1. modules/stock_screener/screener.py (screening_log 저장)
2. modules/theme_analyzer/crawlers.py (KRX 에러 + 코드 검증)
3. modules/ai_verifier/verifier.py (Hold 필터)
4. web/dashboard_service.py (KISApi 싱글톤)

## 롤백 계획
git stash / git revert 가능

## 완료 기준
- py_compile 전 파일 통과
- code-tester 에이전트 검증 통과
- 서비스 재시작은 사용자가 직접 수행
