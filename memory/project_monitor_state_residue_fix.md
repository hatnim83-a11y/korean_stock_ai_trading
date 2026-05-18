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

---

# Phase 2 (2026-05-18) — Phase 1 sanity 우회 차단 + 무한 매도 루프 봉쇄

## 사건
- **2026-05-18 09:10 KST** 기아(000270) 11주 @ 175,700원 매수(5/11) 후 portfolio.status='closed' 인데 self.positions 메모리/`monitor_state.json` 잔존
- 09:10 `_execute_max_hold_sell` → KIS "주문 가능한 수량을 초과했습니다." 에러 → `on_sell_failed` 콜백 → **2~3초 간격 텔레그램 알림 도배**
- Phase 1 sanity (`highest > buy × 1.02`) 우회: buy=175,700 × 1.02 = 179,214 < highest=184,200 (장중 +5% 갭상승 이력) → 잔재 판정 못 함

## 근본 원인 (Phase 1과의 차이)
- Phase 1은 "갭 ≤ +2% 정상 사이클" 경계만 보호 — +2% 초과 후 매도된 closed 종목은 보호 못 함
- 매도 실패 후 `remove_position` 호출이 없어 다음 모니터링 사이클에서 또 발사 → 영원히 반복

## 패치
**2단 방어 구조**:

### 1차 — DB 화이트리스트 (`_restore_trailing_state` JSON 폴백)
- `_filter_state_by_holding_whitelist(state)` 신규 헬퍼: `Database.get_portfolio("holding")`로 화이트리스트 추출 → state 키 ∉ 화이트리스트 즉시 제거 + WARN 로그
- JSON 폴백 진입 직후 적용. DB 경로(`_dump_monitor_state` 30초 갱신)는 면제
- DB 조회 실패 시 state 그대로 반환 — Phase 1 ×1.02 sanity가 2차 폴백

### 2차 — 매도 실패 카운터 (`_record_sell_failure` 헬퍼)
- `self.sell_failure_counts: dict[str, int]` (재시작 시 0 리셋)
- 매도 진입점 3곳(`_execute_stop_loss` / `_execute_trailing_stop` / `_execute_max_hold_sell`)의 실패 분기에서 호출
- 카운터 증가 → `settings.MAX_SELL_FAILURES`(=3) 도달 시 강제 `remove_position` + `on_sell_failed` 1회만 발사 → 도배 봉쇄
- 분할 익절(`_execute_partial_sell`)은 **카운터 미적용** — 트리거 조건(+10/15/20%) 재도달 필요해 도배 위험 낮고, 익절 기회 보존
- `remove_position` 내부에서 `sell_failure_counts.pop` 동시 정리 (예외 분기에서도 명시 정리)

## 변경 파일
- `config.py`: `MAX_SELL_FAILURES = 3` 신규 상수
- `modules/trading_engine/portfolio_monitor_v2.py`:
  - `__init__` self.sell_failure_counts dict 추가
  - `remove_position` 마지막에 카운터 pop
  - `_record_sell_failure` 신규 헬퍼
  - `_filter_state_by_holding_whitelist` 신규 헬퍼
  - `_restore_trailing_state` JSON 폴백 직후 화이트리스트 호출
  - 매도 진입점 3곳(_execute_stop_loss/_execute_trailing_stop/_execute_max_hold_sell) 실패 분기 `_record_sell_failure` 교체
- `tests/test_monitor_state_residue.py`: 신규 케이스 4개 추가 (총 10/10 PASS)
- `memory/MEMORY.md`: 본 메모리 갱신 표시

## 검증
- `pytest tests/test_monitor_state_residue.py -v` 10/10 PASS (기존 6 + 신규 4)
- code-tester 에이전트: 심각 1건은 false alarm(콜백 위치 재정의 흐름) / 주의 2건 반영(분할익절 카운터 미적용 + remove_position 예외 시 카운터 정리)
- 실사건 즉시 조치: `scripts/cleanup_monitor_state_json.py`로 잔재 1건(000270) 제거 + systemctl restart → 도배 즉시 멈춤 확인

## 미해결 / 후속
- ×1.02 sanity는 폴백 안전장치로 유지 (DB 조회 실패 시)
- `partial_sell` 실패 카운터 적용 여부는 1주 운영 데이터 후 재평가
