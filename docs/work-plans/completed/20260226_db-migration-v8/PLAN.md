# DB 스키마 v8 마이그레이션 계획서

## 1. 목적

재시작 시 메모리 상태 휘발로 인한 이중 매도 위험 제거, 모든 거래/분석 이력 축적, 성과 추적 체계 구축.

## 2. 현황 (마이그레이션 전)

| 항목 | 상태 | 위험도 |
|------|------|--------|
| partial_1/2/3_executed | 메모리 + JSON에만 존재 | **치명적** - 재시작 시 이중 매도 |
| trailing 상태 | monitor_state.json에만 존재 | **높음** - 파일 손실 시 복구 불가 |
| remaining_shares | DB 부분 반영 (portfolio.shares만) | 중간 |
| profit_rate/profit_amount | current_price 기준 기록 | 중간 - 부정확 |
| daily_snapshots | performance 테이블 0건 | 낮음 - 성과 추적 불가 |
| 매매 복기 | 데이터 없음 | 낮음 - 학습 불가 |

## 3. 실행 계획

### Phase 1: 핵심 안정성 (즉시 적용)

1. `position_state` 테이블 신규 생성 → monitor_state.json의 DB 대체
2. `portfolio` 테이블에 10개 컬럼 추가 (original_shares, buy_date, partial_*, trailing_*)
3. `trades` 테이블에 4개 컬럼 추가 (buy_price, filled_price, slippage, remaining_shares)

### Phase 2: 성과 추적 (기능 확장)

4. `daily_snapshots` 테이블 → 장 마감 시 자산/수익/MDD 스냅샷
5. `trade_reviews` 테이블 → 매도 시 자동 복기 레코드 생성
6. `strategy_stats` 테이블 → 일일 리포트에서 전략별 집계

### Phase 3: 학습 데이터 (장기)

7. `screening_log` 테이블 → 스크리닝 단계별 기록 (향후 연동)
8. 인덱스 추가

## 4. 마이그레이션 안전장치

- `schema_version` 테이블로 버전 관리
- 각 버전은 멱등 (재실행 시 스킵)
- 실행 전 DB 파일 자동 백업 (`trading.db.bak.{timestamp}`)
- `_has_column()` 체크로 ALTER TABLE 멱등성 보장
- 기존 데이터 보정: `original_shares = shares`, `buy_date = date` (NULL 방어)

## 5. 수정 파일 목록

| 파일 | 변경 내용 | 위험도 |
|------|-----------|--------|
| `database.py` | 마이그레이션 + 신규 CRUD 메서드 | 높음 (핵심) |
| `modules/trading_engine/portfolio_monitor_v2.py` | DB 연동 (dump/restore/close/partial) | 높음 (실시간 매매) |
| `modules/trading_engine/trading_engine.py` | trades 신규 컬럼 전달 | 중간 |
| `main.py` | daily_snapshots + strategy_stats | 중간 |
| `web/dashboard_service.py` | DB에서 trailing 상태 조회 | 낮음 |

## 6. 롤백 계획

- 마이그레이션 실행 전 자동 백업된 `trading.db.bak.*` 파일로 복원
- 신규 컬럼/테이블은 기존 코드와 충돌하지 않음 (하위 호환)
- 최악의 경우: git revert + DB 백업 복원
