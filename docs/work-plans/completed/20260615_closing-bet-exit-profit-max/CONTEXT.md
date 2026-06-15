# CONTEXT — 종가베팅 청산/필터 수익 극대화

## 변경 이유 (데이터 근거)
실거래 5건 실현 평균 **−3.00%**, 합계 −14.98%, 승률 20%. 같은 5건 라벨은 양호(익일시가갭 +0.90%, 아침고가 +3.02%). 청산가가 익일 시가보다 **−1.78%~−6.30%** 아래 체결 → 시장가가 시가 아닌 오전 dip 저점에 투매. 교과서 사례 HPSP 6/9: emergency_stop −4.08% 손절 직후 아침고가 +4.56% 반등.

## 현재 코드 상태 (검증된 위치)
### closing_bet_system/execution/exit_executor.py
- `ExitExecutorSettings`(frozen dataclass) 57~77: `hard_stop_loss=-0.01`, `polling_interval_sec=5.0`, `fill_check_deadline_sec=60.0`, `cancel_confirm_deadline_sec=30.0`, `order_submit_sleep_sec=0.5`.
- `map_action(gap_rate, settings)` 118~128: 시가갭 → 5단계(EMERGENCY/GAP_UP_HIGH/GAP_UP_LOW/FLAT/WEAK_GAP_DOWN).
- `_process_emergency_stop` 314~351: emergency만, `_execute_market_sell` 호출. **(Phase 1 변경 안 함)**
- `_process_morning_exit` 353~402: emergency 제외 4단계. GAP_UP_HIGH만 50%(`gap_up_high_partial_ratio=0.6`) 분할, 나머지 전량. **(Phase 1 변경 지점)**
- `_process_force_close` 404~471: 미체결 cancel→시장가 재발주. **(변경 안 함)**
- `_execute_market_sell` 475~549: **공용 단일 시장가 발주**. dry_run 분기 504~510. 실발주 `sell_market_order(ticker, qty)` 513. `_pending_exit_orders[ticker]=(order_id,qty)` 523. `_poll_fill` 527 → `log_exit`(가중평균 아님, 단일 fill) 536~543.
- `_wait_cancel_confirm` 551~570, `_poll_fill` 572~591: Phase 1 폴백에서 재사용.
- `_resolve_entry_price` 595~: entered면 entry_price, phase1 only면 phase1_executed_price.

### 핵심 스니펫 — 현행 단일 시장가 (Phase 1이 분기 추가할 지점)
```python
# _execute_market_sell:512
sell_result = await asyncio.to_thread(
    self.kis_order_api.sell_market_order, target.ticker, quantity,
)
```

### closing_bet_system/main_orchestrator.py 322~347
- `yaml_settings = _load_settings().get("morning_exit", {})` → ExitExecutorSettings 매핑.
- `exit_cfg = _load_settings().get("exit", {})` → 임계값.
- **신규 토글 2키는 `morning_exit:` 섹션에서 읽어 `ee_settings`에 매핑** (enabled/dry_run과 동일 패턴).

### modules/trading_engine/kis_order_api.py
- `sell_limit_order(stock_code, quantity, price:int)` 377 (실제 API 존재).
- `sell_market_order(stock_code, quantity)` 354.
- **MockOrderApi(976~1264): `sell_market_order`(1040)·`cancel_order`(1205) 있으나 `sell_limit_order` 없음** → Phase 1에서 추가 필요. `buy_limit_order`(1137)가 `scenario_fill_ratio` 부분체결 시뮬 지원 → 패턴 복사.

### closing_bet_system/collectors/morning_price_collector.py
- `MorningPriceSnapshot`: `open_price:int`(stck_oprc), high, low, current_price 31~41.
- `get_snapshot()`: `open_price<=0`이면 `None` 반환(82~86) → **지정가 가격 방어 이미 collector 레벨에서 보장**.

## 사전 리뷰 발견 (strategy-planner + code-tester, 2026-06-15) — Phase 1 설계 선반영
1. **[심각] 부분체결 폴백 집계**: `_execute_market_sell` 단일발주 가정 깨짐. 별도 `_execute_limit_sell_with_fallback`로 분리. cancel 후 **최종 체결수량 재조회**, 잔량=`total−executed`만 시장가, `log_exit` **정확히 1회**(가중평균가·합산수량). `total_shares`로 재발주 금지(없는 수량 과매도).
2. **[심각] MockOrderApi.sell_limit_order 미존재**: 부분체결 테스트 불가 → 추가 필수.
3. **[주의] emergency_stop 지정가화 제외**: 60초 지연이 긴급손절 취지 위반 → morning_exit만 적용.
4. **[주의] 시간예산**: 09:02 morning_exit + 폴백이 09:05 스윙매수 전 수렴해야 함 → `limit_fill_deadline_sec=30초` 보수 설정. 폴백 시장가는 즉시 체결.
5. **[주의] dry_run/기존 테스트**: dry_run 로그에 LIMIT/MARKET 구분. EX-8/9에 `sell_limit_order.assert_not_called()` 추가. 토글 default False라 기존 테스트(토글 미설정)는 영향 없음.
6. **[경미] frozen 신규 필드**: default값 추가 안전. main_orchestrator keyword 매핑이라 기존 인스턴스화 무영향.
7. **[경미] _pending_exit_orders**: 지정가 ODNO → 취소확정 후 시장가 ODNO 교체 순서 보장. force_close cancel race 방지.

## 관련 과거 버그/설계
- P0-1(주석): "모든 액션 시초가 시장가 매도로 통일 → phase25_simulator open_pct 가정 일치". **시가 지정가는 체결가가 시가 근처라 오히려 open_pct 가정에 더 부합** → 시뮬 문서 동기 필요.
- 5/13 사건: KIS 500 → fill_checker 3회 재시도(5/10/15초 백오프). 폴백도 이 재시도 경로 통과.
- sell_lock: owner `closing_bet:morning_exit`, release는 15:30 clear_all 또는 force_close 강제. 폴백 2회 발주는 동일 함수 내라 owner 충돌 없음.

## 영향 범위
- Phase 1: 종가베팅 morning_exit 청산 경로만. 스윙/메인봇 무관. 토글 default False라 미설정 시 현행 동작 100% 유지.
- DB: candidates UPDATE(log_exit) 경로 동일, 스키마 변경 없음.

## 작업 중 발견 사항
- (구현 중 갱신)
