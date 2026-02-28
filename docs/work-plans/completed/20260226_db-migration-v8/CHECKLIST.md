# DB 스키마 v8 마이그레이션 체크리스트 (최종)

> 검증일: 2026-02-26 | 3차 독립 검증 완료

## A. 마이그레이션 인프라

- [x] A1. schema_version 테이블 존재
- [x] A2. v1~v8 모두 적용 (schema_version에 8건)
- [x] A3. 재실행 시 멱등 (version 유지, 중복 에러 없음)
- [x] A4. 마이그레이션 전 DB 백업 파일 생성 확인 **(WAL/SHM 포함으로 수정)**
- [x] A5. _has_column() 체크로 ALTER TABLE 중복 방지

## B. Phase 1: position_state

- [x] B1. position_state 테이블 DDL 확인 (PK=stock_code)
- [x] B2. upsert_position_state() — ON CONFLICT 동작 확인
- [x] B3. get_all_position_states() — 전체 조회 + dict 반환
- [x] B4. delete_position_state() — 포지션 청산 시 삭제
- [x] B5. _dump_monitor_state() — JSON + DB 동시 저장
- [x] B6. _restore_trailing_state() — DB 우선, JSON 폴백
- [x] B7. remaining_shares 복원 로직 (DB source에서)
- [x] B8. max_profit_rate 단위 왕복 (비율 <-> %) 정확성

## C. Phase 1: portfolio 컬럼

- [x] C1. 10개 신규 컬럼 존재 확인
- [x] C2. original_shares = shares 보정 (기존 holding) — **LG전자 분할매도 후 보정이므로 3주로 기록, 신규 매수부터 정확**
- [x] C3. buy_date = date 보정 (기존 holding)
- [x] C4. save_holding_position()에 original_shares, buy_date 전달
- [x] C5. update_portfolio_partial_state() — partial/trailing 업데이트
- [x] C6. 기존 save_portfolio() 하위 호환 (신규 컬럼 NULL 허용)

## D. Phase 1: trades 컬럼

- [x] D1. 4개 신규 컬럼 존재 확인 (buy_price, filled_price, slippage, remaining_shares)
- [x] D2. save_trade() 16개 파라미터 INSERT 확인
- [x] D3. save_trade() Optional[int] 반환 (lastrowid)
- [x] D4. 기존 호출부 하위 호환 (반환값 무시해도 OK)
- [x] D5. trading_engine._save_trades()에서 신규 필드 전달
- [x] D6. slippage 계산 로직 확인 (매도 시만)

## E. Phase 2: daily_snapshots

- [x] E1. daily_snapshots 테이블 DDL 확인
- [x] E2. save_daily_snapshot() UPSERT (동일 날짜 덮어쓰기)
- [x] E3. _save_daily_snapshot() — MDD, peak_value, 승률 계산 **(가격 최신화 추가)**
- [x] E4. get_daily_snapshots() — ORDER BY date DESC
- [x] E5. dashboard get_performance_data() — daily_snapshots 우선 폴백

## F. Phase 2: trade_reviews

- [x] F1. trade_reviews 테이블 DDL 확인 (FK: trade_id -> trades.id)
- [x] F2. _close_position_in_db()에서 trade_review 자동 생성
- [x] F3. _save_partial_sell_to_db()에서 trade_review 자동 생성
- [x] F4. _classify_strategy() 매도사유 -> 전략유형 매핑
- [x] F5. get_pending_trade_reviews() — ai_review IS NULL 필터
- [x] F6. update_trade_review_ai() — AI 평가 업데이트

## G. Phase 2: strategy_stats

- [x] G1. strategy_stats 테이블 DDL 확인 (UNIQUE: date + strategy_type)
- [x] G2. _aggregate_strategy_stats() — trade_reviews GROUP BY 집계
- [x] G3. save_strategy_stats() INSERT OR REPLACE

## H. Phase 3: screening_log

- [x] H1. screening_log 테이블 DDL 확인 (UNIQUE: date + stock_code + stage)
- [x] H2. save_screening_log() INSERT OR REPLACE

## I. 기존 시스템 호환성

- [x] I1. 기존 get_portfolio() 정상 작동 (신규 컬럼 포함)
- [x] I2. 기존 save_trade() 호출부 하위 호환 (dashboard_service, trading_engine)
- [x] I3. 기존 monitor_state.json 포맷 유지 — **remaining_shares 키 자동 추가됨**
- [x] I4. 대시보드 _load_monitor_state() DB 우선 조회
- [x] I5. 현재 실행 중인 서비스와 충돌 여부 — **WAL 모드 안전, 12-col INSERT 호환**
- [x] I6. remove_position()에서 position_state 삭제 시 DB 에러 방지

## J. 커넥션 안전성

- [x] J1. _close_position_in_db() try/finally 패턴
- [x] J2. _save_partial_sell_to_db() try/finally 패턴
- [x] J3. remove_position() try/finally 패턴 **(수정 완료)**
- [x] J4. _update_db_prices() try/finally 패턴 **(수정 완료)**
- [x] J5. _restore_trailing_state() try/finally 패턴 **(수정 완료)**
- [x] J6. _dump_monitor_state() DB 블록 try/finally 패턴 **(수정 완료)**
- [x] J7. _save_daily_snapshot() try/finally 패턴 **(수정 완료)**

## K. 시간대/날짜

- [x] K1. 신규 코드에서 datetime.now() / date.today() 사용하지 않음
- [x] K2. log_system_status()에서 now_kst().date() 사용
- [x] K3. _save_daily_snapshot()에서 now_kst() 사용

## L. 배포

- [x] L1. 5개 파일 py_compile 통과
- [ ] L2. systemctl restart 후 마이그레이션 자동 실행 (재시작 후 확인)
- [ ] L3. 재시작 후 position_state에 데이터 확인 (30초 후)
- [ ] L4. monitor_state.json과 position_state 테이블 데이터 일치 (30초 후)

## M. 3차 검증 추가 항목

- [x] M1. WAL/SHM 백업 포함 (database.py 수정)
- [x] M2. daily_snapshot 전 _update_db_prices() 호출 (main.py 수정)
- [x] M3. WAL 동시 읽기/쓰기 안전성 (20 스레드 테스트 통과)
- [x] M4. FK 제약 실제 강제 확인 (trade_reviews -> trades)
- [x] M5. NULL buy_price SUM/AVG 집계 안전 (SQLite NULL 무시)
- [x] M6. 구 12-col INSERT -> 신 16-col 테이블 하위 호환
