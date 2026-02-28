# DB 스키마 v8 마이그레이션 - 결정 맥락 문서

## 핵심 결정 1: position_state를 별도 테이블로 분리한 이유

**결정**: portfolio 컬럼 추가 + 별도 position_state 테이블 병행

**배경**:
- `portfolio_monitor_v2.py:495-516` — 30초마다 _dump_monitor_state()로 JSON 저장
- `portfolio_monitor_v2.py:311-360` — 재시작 시 JSON에서 복원
- 문제: JSON 파일만으로는 원자성 보장 불가, 파일 손실 위험

**왜 portfolio 테이블에만 넣지 않았나**:
- portfolio는 매수 시점 기록 (원본 상태) + 현재 상태가 혼재
- 30초마다 UPDATE하면 `updated_at` 타임스탬프가 매번 변경되어 이력 추적 방해
- position_state는 "모니터링 런타임 상태"를 위한 별도 영역

**왜 JSON도 유지하나**:
- 대시보드가 파일 읽기로 빠른 캐시 접근 (DB 락 없이)
- DB가 primary source of truth, JSON은 캐시

**관련 코드**:
- `database.py:165-213` — position_state DDL
- `database.py:576-615` — upsert/get_all/delete CRUD
- `portfolio_monitor_v2.py:495-554` — _dump_monitor_state() DB+JSON 동시 저장
- `portfolio_monitor_v2.py:311-410` — _restore_trailing_state() DB 우선 복원

## 핵심 결정 2: save_trade() 반환값을 Optional[int]로 변경한 이유

**결정**: save_trade()가 lastrowid(trade_id)를 반환하도록 변경

**배경**:
- trade_reviews 테이블이 trades.id를 FK로 참조
- 매도 시 trade 저장 직후 trade_review를 생성해야 하므로 ID가 필요

**영향 분석**:
- 기존 호출부: `db.save_trade(trade)` — 반환값 무시하므로 하위 호환
- 신규 호출부: `trade_id = db.save_trade(trade)` — FK 연결에 사용
- `web/dashboard_service.py:355-364` — execute_sell()에서 save_trade() 호출, 반환값 미사용 → 무해

**관련 코드**:
- `database.py:619-658` — save_trade() 변경
- `portfolio_monitor_v2.py:598-638` — _close_position_in_db()에서 trade_id 사용
- `portfolio_monitor_v2.py:652-721` — _save_partial_sell_to_db()에서 trade_id 사용

## 핵심 결정 3: max_profit_rate 단위 변환 (% vs 비율)

**결정**: DB에는 비율(0.12), JSON에는 %(12.0)으로 저장

**배경**:
- Position 객체 내부: `max_profit_rate`는 비율(0.12)
- 기존 JSON: `round(pos.max_profit_rate * 100, 2)` → %(12.0)으로 저장
- 기존 복원: `s.get("max_profit_rate", 0) / 100` → 비율로 변환

**DB 저장 시**:
- `_dump_monitor_state()` → DB에 `max_profit_rate / 100` (JSON의 %를 비율로 변환해서 저장)
- `_restore_trailing_state()` → DB source면 그대로 사용, JSON source면 /100

**왜 이렇게 결정**:
- DB는 코드와 동일 단위(비율)로 통일 → 변환 실수 감소
- JSON은 기존 대시보드 호환성 유지 → 변경하면 대시보드도 수정 필요

**관련 코드**:
- `portfolio_monitor_v2.py:528-531` — JSON 저장 시 *100
- `portfolio_monitor_v2.py:541-548` — DB 저장 시 /100
- `portfolio_monitor_v2.py:388-392` — 복원 시 소스별 분기

## 핵심 결정 4: daily_snapshots를 performance 대신 신규 테이블로 만든 이유

**결정**: performance 테이블은 유지하되 daily_snapshots를 별도 생성

**배경**:
- performance 테이블 0건 (사용되지 않음)
- 하지만 get_performance_history()를 호출하는 코드가 여러 곳에 존재
- 기존 코드 호환성을 깨지 않으면서 새 스키마로 전환

**전략**:
- daily_snapshots가 더 풍부한 컬럼 (realized_pnl, MDD, positions_json 등)
- dashboard_service.py에서 daily_snapshots 우선 조회, 없으면 performance 폴백
- 기존 save_performance() 메서드는 유지 (다른 곳에서 호출할 수 있으므로)

**관련 코드**:
- `database.py:687-726` — daily_snapshots DDL + save/get
- `main.py:1193-1267` — _save_daily_snapshot() 장 마감 시 저장
- `web/dashboard_service.py:194-244` — get_performance_data() 폴백 로직

## 핵심 결정 5: _classify_strategy() 매도사유→전략유형 매핑

**결정**: 매도 사유 문자열에서 전략 유형을 추출하는 정적 메서드

**배경**:
- SellReason enum 값: "손절", "1차 익절", "트레일링L1" 등
- strategy_stats는 전략 유형별 집계가 목적
- 한글 매도 사유를 영문 전략 코드로 변환

**매핑 규칙**:
- "손절" → stop_loss
- "트레일링" → trailing_stop
- "익절" → take_profit
- "보유" → max_hold
- "수급" → supply_exit
- 기타 → manual

**관련 코드**:
- `portfolio_monitor_v2.py:640-653` — _classify_strategy()
- `portfolio_monitor_v2.py:40-51` — SellReason enum (원본 매도 사유)

## 핵심 결정 6: 기존 데이터 보정 전략

**결정**: 마이그레이션 v2에서 기존 portfolio 데이터 보정

**실행**:
```sql
UPDATE portfolio SET original_shares = shares WHERE original_shares IS NULL;
UPDATE portfolio SET buy_date = date WHERE buy_date IS NULL;
```

**결과 확인** (실제 DB):
- 3개 holding 종목 모두 original_shares = shares로 보정 완료
- buy_date도 date 컬럼 값으로 보정 완료

**미보정 항목**:
- portfolio.trailing_* 컬럼: 마이그레이션 시점에는 0/NULL (정상)
  → 서비스 재시작 후 _dump_monitor_state()가 30초 내 채움
- trades.buy_price: 기존 24건은 NULL 유지 (과거 데이터 복구 불필요)
  → 신규 거래부터만 채워짐

**관련 코드**:
- `database.py:225-230` — _migrate_v2()의 UPDATE 문
- 위 쿼리 결과: 3개 holding 모두 정합성 확인됨
