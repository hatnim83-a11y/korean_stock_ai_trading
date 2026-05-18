# CONTEXT — Phase 2 잔재 차단 강화

## 5/18 09:10 기아(000270) 사건 재현

### 로그 발췌
```
00:10:58 WARNING  | _execute_max_hold_sell:1340 | ⏰ 최대 보유 기간 도달: 기아
00:10:58 WARNING  | _execute_max_hold_sell:1341 |    보유 일수: 5일
00:10:59 ERROR    | _place_order:483 | ❌ 매도 주문 실패: 000270 - 주문 가능한 수량을 초과했습니다.
00:11:01 WARNING  | _execute_max_hold_sell:1340 | ⏰ 최대 보유 기간 도달: 기아  ← 2초 후 재발사
...
```

### 상태 스냅샷
| 항목 | 값 |
|---|---|
| portfolio.status | closed |
| portfolio.shares | 11 (재고는 안 지움) |
| portfolio.buy_price | 175,700 |
| monitor_state.json[000270].highest_price | 184,200 |
| ×1.02 sanity 임계 | 175,700 × 1.02 = 179,214 |
| 결과 | highest(184,200) > 179,214 → **잔재 못 거름** |

### 자가 원인
1. `_load_positions`가 어느 시점에 `000270`을 holding으로 로드 → self.positions 입성
2. 외부 경로(혹은 5/15~17 사이 매도 시도)에서 status='closed' 갱신, but self.positions에서는 안 빠짐
3. 5/18 09:10 max_hold_sell → KIS 잔량 0 → 실패 → on_sell_failed (텔레그램)
4. 메모리에 그대로 남아 다음 사이클에 또 발사 (3초 간격 도배)

## 코드 위치
- `modules/trading_engine/portfolio_monitor_v2.py`
  - L41 `_RESIDUE_SANITY_RATIO = 1.02` (Phase 1 임계값)
  - L376~510 `_restore_trailing_state` — JSON 폴백 sanity check (이번에 화이트리스트 추가)
  - L947 `_execute_stop_loss` — `on_sell_failed` (실패 카운터 적용 대상)
  - L1055 `_execute_partial_sell` — `on_sell_failed`
  - L1254 `_execute_trailing_stop` — `on_sell_failed`
  - L1338 `_execute_max_hold_sell` — `on_sell_failed` (이번 도배의 발사점)
  - L256 `add_position` / L294 `remove_position` (포지션 라이프사이클)
- `database.py` L1021 `get_portfolio(status='holding')` (화이트리스트 추출에 재사용)
- `config.py` (새 상수 `MAX_SELL_FAILURES` 추가 위치)

## 영향 범위
- 모니터링/매도 경로 전체. 단 변경은 **방어 추가**만이라 정상 경로 동작에는 영향 없음.
- 화이트리스트는 JSON 폴백 경로 전용 (DB 경로는 `_dump_monitor_state`로 30초 동기화되어 잔재 가능성 낮음).
- 실패 카운터는 메모리 dict (`self.sell_failure_counts: dict[str,int]`) — 재시작 시 0으로 리셋(안전).

## 과거 사건/문서
- [[project_monitor_state_residue_fix]] (2026-05-13 Phase 1)
- `memory/project_monitor_state_residue_fix.md`
- `tests/test_monitor_state_residue.py` (기존 4 케이스)

## 핵심 가정
- `Database.get_portfolio('holding')`는 `_restore_trailing_state` 시점에 진실의 source of truth (매도 즉시 `_close_position_in_db`로 status='closed' 갱신)
- 매도 실패는 **잘못된 상태**(KIS 잔량 0인데 모니터 잔존)일 때만 반복 발생 — 정상 케이스에서는 1~2회 재시도로 회복
- `MAX_SELL_FAILURES=3`은 일시적 네트워크 실패를 허용하는 최소 임계 (필요시 5까지 조정 검토)
