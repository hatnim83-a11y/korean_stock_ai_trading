# CONTEXT: 봇 점검 이슈 5건 수정

## 변경 이유
- 2026-03-03 봇 건강점검에서 발견된 5개 이슈 수정
- DB 스키마 v8 설계대로 데이터 저장 확인

## 현재 코드 상태

### screening_log (screener.py:462-486)
- save_screened_stocks()만 호출, save_screening_log()는 미호출
- database.py:1099에 save_screening_log() 함수 존재하지만 사용 안됨

### KRX 크롤러 (crawlers.py:344)
- `stock.get_index_ticker_list(market='테마')` → KeyError('시장') 발생
- pykrx 버전 변경으로 인한 API 불일치 추정

### AI 검증 (verifier.py:37)
- `EXCLUDE_RECOMMENDATIONS = ["No"]` → "Hold"도 통과됨
- 429줄 모의검증도 동일 문제

### 대시보드 KISApi (dashboard_service.py:39-46)
- _get_kis_api(), _get_order_api() 매 호출 시 새 인스턴스
- _get_db()는 싱글톤인데 KIS는 아님

### 종목코드 (crawlers.py:289-307)
- config.py에 validate_stock_code() 존재 (6자리 숫자 검증)
- 크롤러에서는 미사용 → "0015G0" 같은 무효 코드 유입

## DB 스키마 점검 결과
- 13개 테이블 모두 존재, v1-v8 마이그레이션 완료
- portfolio: v2 컬럼(trailing_*, partial_*) 정상 동작
- trades: v3 컬럼(buy_price, filled_price, slippage) 정상
- position_state: 현재 3종목 추적 중 (정상)
- trade_reviews: 8건 저장, 자동 생성 확인 (정상)
- strategy_stats: 3건 저장 (정상)
- daily_snapshots: 2건, 컬럼명 total_capital (total_value 아님) — 대시보드 쿼리 확인 필요
- screening_log: 0건 (Fix 1로 해결)
- performance: 0건 (별도 이슈, 이번 범위 아님)

## 영향 범위
- 직접: 스크리닝 이력 추적, 테마 분석 정확도, 매수 품질, 리소스 효율
- 간접: 없음 (기존 로직 변경 아닌 추가/강화)
