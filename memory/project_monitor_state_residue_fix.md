# project_monitor_state_residue_fix (2026-05-13)

## 사건
- **2026-05-12 09:25** 한화오션(042660) 14주 @ 126,950원 매수 (BL 지정가, slippage 0.0394%)
- **2026-05-12 10:04** BE 손절 발동 @ 125,600원 → 12주 청산, -1.06%, -16,200원 (trade_id=120, review_id=55)
- 매수 직후 정상 가격 변동인데도 BE 손절가가 125,680원으로 즉시 활성화

## 근본 원인
4월 한화오션 1차 보유 사이클(highest_price=136,800, max_profit_rate=5.9%)이 종료될 때:
1. `remove_position()` → `delete_position_state()` 호출로 DB는 정상 삭제
2. `data/monitor_state.json` 의 042660 키는 **미정리** (다음 `_dump_monitor_state()` 사이클이 stop/restart 사이에 미실행)
3. 5/12 재매수 후 09:26 모니터 재시작 → `_restore_trailing_state()` DB 빈 결과 → **JSON 폴백 진입** (portfolio_monitor_v2.py:378-389)
4. `for code, pos in self.positions.items()` 루프가 같은 stock_code 매칭으로 통과 → BE only 분기(라인 443-449)에서 `pos.max_profit_rate = 0.3` 복원 → `pos.max_profit_rate(0.3) >= trail_be_activate_pct(0.05)` → `stop_loss_price = 126,950 × 0.99 = 125,680원` 활성화

결정적 로그 증거:
```
09:26:01 한화오션 126,700원 보유0일 최고136,800.0원   ← 매수 0분만에 4월 최고가
09:26:00 🛡️ BE 손절 복원: 한화오션 stop → 125,680원 (max 5.9%)
10:04:37 ⚠️ BE 손절: 한화오션 현재가 125,600 <= 손절가 125,680
```

## 패치 (commit bf8105d, 2026-05-13 KST 12:25 배포)

### portfolio_monitor_v2.py 4지점
1. **`remove_position()` 라인 291-323**: DB 삭제 직후 `monitor_state.json` 키 동기 삭제 (try/except + logger.debug, 매도 차단 X)
2. **`_execute_partial_sell()` 전량 익절 분기 ~라인 1116**: `_close_position_in_db` 직후 `self.remove_position(pos.stock_code)` 호출 추가 — 콜백 호출 외 메모리/JSON 정리를 자체 수행하지 않던 맹점 봉쇄
3. **`_restore_trailing_state()` 라인 391-408**: JSON 폴백 경로(`db_source=False`)에서 `state[code].highest_price > pos.buy_price × 1.02` 면 `🚮 JSON 잔재 무시` warning + `continue`. DB 경로는 30초 갱신으로 잔재 가능성 없어 면제
4. **`stop_monitoring()` 라인 539-549**: 정지 직전 `self._dump_monitor_state()` 호출 추가 (sell_lock.clear_all 직전 마지막 dump 보장)

### web/dashboard_service.py
- `_load_monitor_state()` JSON 폴백 시 `portfolio.status='holding'` 코드 셋으로 필터링 → 대시보드에 잔재 노출 차단

### 신규 자산
- `scripts/cleanup_monitor_state_json.py`: systemctl is-active 가드 + KST 백업(`*.bak_YYYYMMDD_HHMMSS`) + 잔재 키 제거 + 롤백 명령 출력
- `tests/test_monitor_state_residue.py`: 6 테스트 (한화오션 시나리오 재현 차단 + 정당 데이터 통과 회귀 방지 포함)

## 검증
- pytest: 6/6 PASS (test_restore_skips_residue_by_buy_price 가 5/12 사건 직접 재현 차단 검증)
- code-tester 에이전트: 심각 0건 / 주의 3건(모두 즉시 수정 불요) / 참고 4건
- 장중 systemctl stop → cleanup dry-run → 현재 잔재 없음(JSON=holding=1종목 기아) → restart → 모니터링 V2 정상 재개

## 미해결 / 후속 작업
- **하드코딩 추출** (참고 사항, 후속 별도 commit):
  - `1.02` 임계값 → `_RESIDUE_SANITY_RATIO` 모듈 상수
  - `"monitor_state.json"` 5곳 분산 → `MONITOR_STATE_FILENAME` 상수 (`portfolio_monitor_v2.py` 라인 314, 397, 658 / `web/dashboard_service.py` 라인 206 / `scripts/cleanup_monitor_state_json.py` 라인 70)
- **`import json` 모듈 상단 통합** (코드 스타일)
- **JSON 폐기 마이그레이션 (옵션 C)** — 장기 후보. DB primary 정책 강화 시 `_dump_monitor_state` JSON 쓰기 제거 + 대시보드 캐시는 별도 path

## 관련 메모리
- [[project_partial_profit_early_monitoring]] — SellLock 동시성 (09:00 조기 모니터링)
- [[project_theme_score_zero_fix]] — DB 박제값 자동 폴백 패턴 (유사한 정합성 보완)
- [[feedback_plan_review_process]] — strategy-planner + code-tester 병렬 리뷰 프로세스 (이번 작업 본격 적용)
