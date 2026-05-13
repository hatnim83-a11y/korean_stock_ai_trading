# CONTEXT — monitor_state.json 잔재 데이터 버그

## 변경 이유 (사건 로그)

### 2026-05-12 한화오션(042660) 사건 타임라인
```
09:25:11  매수 (BL 지정가) @ 126,950원 × 14주
          filled_price=126,950, slippage 0.0394%
09:26:00  모니터 add_position → _restore_trailing_state
          🛡️ BE 손절 복원: 한화오션 stop → 125,680원 (max 5.9%)  ← 4월 잔재
09:26:01  로그: 한화오션 126,700원 (-0.2%) 보유0일 최고136,800.0원
          ↑ 매수 0분만에 "최고136,800" 등장 — 4월 사이클 데이터 잔재 확정 증거
10:04:37  ⚠️ BE 손절: 한화오션 현재가 125,600 <= 손절가 125,680 (보유 0일)
10:04:38  매도 12주 @ 125,600원, profit_rate -1.063%, -16,200원, review_id=55
```

### 잔재 메커니즘
1. **4월 보유 사이클**: 한화오션 highest_price=136,800, max_profit_rate=5.9% 까지 도달
2. **4/27 매도**: `remove_position()` → DB position_state 삭제 ✓ / JSON 미정리 ✗
3. **JSON 덤프**: `_dump_monitor_state()` 는 self.positions 기반 전체 덮어쓰기인데, 매도 후 다음 dump 가 모니터 stop 으로 미실행되면 042660 키가 영구 잔존
4. **5/12 재매수**: 042660 신규 포지션 (buy_price=126,950)
5. **5/12 09:26 모니터 재시작**: `_restore_trailing_state()`
   - DB position_state 신규 포지션 빈 상태 → **JSON 폴백 진입** (라인 378-389)
   - JSON 에서 042660 키 발견 → `for code, pos in self.positions.items()` 통과 (같은 코드)
   - 잔재 `highest_price=136,800`, `max_profit_rate=0.3` 복원
   - BE 분기 (라인 443-449): `max_profit_rate × 100 = 30%` >= `trail_be_activate_pct=5%` → BE 활성화
   - `stop_loss_price = buy_price × (1 + trail_be_stop_pct) = 126,950 × 0.99 = 125,680`
6. **10:04 손절 발동**: 매수 0분 후 -1% 만 하락해도 즉시 청산

## 현재 코드 상태 (영향 라인)

### `modules/trading_engine/portfolio_monitor_v2.py`

#### `remove_position()` (라인 291-307)
```python
def remove_position(self, stock_code: str) -> None:
    if stock_code in self.positions:
        pos = self.positions[stock_code]
        logger.info(f"포지션 제거: {pos.stock_name} (보유 {pos.hold_days}일)")
        del self.positions[stock_code]
        
        db = None
        try:
            db = Database()
            db.connect()
            db.delete_position_state(stock_code)   # ✓ DB 삭제
        except Exception as e:
            logger.debug(f"position_state 삭제 실패: {e}")
        finally:
            if db:
                db.close()
        # ❌ JSON 미정리
```

#### `_execute_partial_sell()` 전량 익절 분기 (라인 1013-1088)
- 부분 매도 실행 후 `remaining_shares <= 0` 이 되면 `_close_position_in_db()` 호출
- **`remove_position()` 을 호출하지 않음** → JSON 정리 없음 → 30초 dump 사이클 안에 모니터 stop 시 잔재

#### `_restore_trailing_state()` (라인 357-461)
```python
# 라인 378-389: JSON 폴백 진입
if not state:
    state_path = Path(settings.DATABASE_PATH).parent / "monitor_state.json"
    try:
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)
    except Exception as e:
        logger.debug(f"JSON 폴백 실패: {e}")

if not state:
    return

# 라인 392: 반복 (이미 self.positions 기준)
for code, pos in self.positions.items():
    if code not in state:
        continue
    s = state[code]
    # 라인 422-426: max_profit_rate 단위 변환
    raw_rate = s.get("max_profit_rate", 0)
    if db_source:
        pos.max_profit_rate = raw_rate
    else:
        pos.max_profit_rate = raw_rate / 100  # %→비율
    
    # 라인 443-456: BE only 복원 분기
    saved_highest = s.get("highest_price", pos.buy_price)
    pos.highest_price = max(pos.highest_price, saved_highest)  # ❌ 잔재 highest 가 그대로 들어옴
    if self.enable_be_stop and pos.max_profit_rate >= self.trail_be_activate_pct:
        be_stop = pos.buy_price * (1 + self.trail_be_stop_pct)
        pos.stop_loss_price = be_stop
        logger.info(f"🛡️ BE 손절 복원: {pos.stock_name} stop → {be_stop:,.0f}원 (max {pos.max_profit_rate:.1%})")
```

#### `_dump_monitor_state()` (라인 596-644)
- `state = {}` 후 self.positions 만 dict 채워서 JSON 전체 덮어쓰기 + DB UPSERT
- 30초 주기로 호출되지만 **매도 직후 모니터 stop 시 미실행 가능**

### `web/dashboard_service.py` `_load_monitor_state()` (라인 190-209)
- DB 우선 → JSON 폴백 → 보유 외 키 필터링 없음 → 대시보드에 잔재 표시 가능

### `data/monitor_state.json` 현재 상태 (10:15 KST)
```json
{
  "448900": {...},   ← 한화오션 5/12 청산 후 잔재 (예상)
  "000270": {...},   ← 보유
  "007070": {...},   ← 보유
  "058470": {...}    ← 보유
}
```

## 핵심 스니펫 (참고)

### `database.py` 관련 함수
- `delete_position_state(stock_code)` 라인 1224-1243 — DB 행 삭제
- `get_all_position_states()` — DB primary 로드

### `config.py`
- `settings.DATABASE_PATH` — JSON 경로 도출 기준 (`Path(settings.DATABASE_PATH).parent / "monitor_state.json"`)
- `now_kst()` — KST 타임스탬프

## 과거 관련 버그 / 결정
- **2026-03-13 DB Schema v12**: position_state 테이블 도입, "DB가 primary source of truth, JSON은 대시보드 캐시용 병행" 정책 (CLAUDE.md / MEMORY.md)
- **2026-05-06 SellLock 도입**: 동시 매도 race 봉쇄 — `clear_all()` 은 15:30 stop_monitoring 에서 일괄 해제 (`memory/project_partial_profit_early_monitoring.md`)
- **2026-05-07 theme score zero fix**: DB 복원 시 박제값 자동 폴백 (DB 정합성 보완 패턴 참조 가능)

## 영향 범위
- **7일 이내 동일 종목 재매수 케이스 모두**: BE 손절 즉시 발동 위험
- **부분 매도 전량 익절 경로**: remove_position 미호출로 잔재 발생 가능
- **대시보드 UI**: 잔재 종목 표시 가능 (DB 우선이라 실제로는 보유 외 표시 거의 없음)
- **운영 안전**: 단발성 손실 -16,200원에 그쳤지만 잔재 highest_price 가 더 컸으면 더 빠른 청산 가능

## 에이전트 리뷰 결과 (v1 → v2)
- strategy-planner: 조건부 합격 — P1 가드 로직 재설계 요구
- code-tester: 조건부 합격 — P1 전량 익절 경로 누락 + 가드 로직 재설계 요구
- 양측 합의 후 v2 플랜에 모두 반영
