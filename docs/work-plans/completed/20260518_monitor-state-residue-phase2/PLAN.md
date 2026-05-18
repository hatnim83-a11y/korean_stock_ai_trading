# monitor_state.json 잔재 회귀 차단 강화 (Phase 2)

## 배경
- **2026-05-13 Phase 1 배포**: `_restore_trailing_state` JSON 폴백 경로에서 `state.highest_price > buy_price × 1.02` 시 잔재 스킵 (5/12 한화오션 BE 손절 즉시 활성화 사건)
- **2026-05-18 회귀 사건**: 기아(000270)
  - portfolio.status='closed' 인데 self.positions에 잔존
  - JSON에 `highest_price=184,200` 잔재 (buy_price=175,700 × 1.02 = 179,214 → ×1.02 임계 통과)
  - 09:10 `_execute_max_hold_sell` 트리거 → KIS "주문 가능 수량 초과" → `on_sell_failed` → 텔레그램 알림
  - **무한 루프**: 매도 실패 시 `remove_position` 호출이 없어 다음 모니터링 사이클에서 또 발사 → 텔레그램 도배

## 목표
무한 매도 실패 루프와 closed 종목의 모니터링 메모리 잔존을 **2단 방어**로 봉쇄한다.

1. **1차 방어 — DB 화이트리스트**: JSON 폴백 시 `portfolio.status='holding'` 종목만 복원 대상 (DB가 single source of truth)
2. **2차 방어 — 매도 실패 카운터**: 매도 진입점 4곳에 연속 실패 카운터, 임계(`MAX_SELL_FAILURES=3`) 초과 시 강제 `remove_position` + 텔레그램 1회 알림 → 도배 차단

## 변경 파일
| 파일 | 변경 |
|---|---|
| `config.py` | `MAX_SELL_FAILURES = 3` 상수 추가 |
| `modules/trading_engine/portfolio_monitor_v2.py` | `_restore_trailing_state` 화이트리스트 가드 / `Position` 또는 dict에 `sell_failure_count` / 매도 진입점 4곳 후처리 |
| `tests/test_monitor_state_residue.py` | 화이트리스트 케이스 + 실패 카운터 케이스 추가 |
| `memory/project_monitor_state_residue_fix.md` | Phase 2 사건/방어 기술 추가 |
| `memory/MEMORY.md` | 1줄 갱신 |
| `docs/improvements/change_log.md` | 변경 1줄 |

## 구현 단계
1. 3문서 작성 (이 디렉토리)
2. `_restore_trailing_state`: JSON 폴백 직전 `Database.get_portfolio('holding')`로 화이트리스트 추출, `state` 키 ∉ 화이트리스트면 즉시 제외 + WARN 로그
3. `Position` 모델에 `sell_failure_count: int = 0` 추가 (또는 monitor.sell_failure_counts dict — 영향 범위 작은 후자 선택)
4. 매도 진입점 4곳:
   - `_execute_stop_loss` / `_execute_partial_sell` / `_execute_trailing_stop` / `_execute_max_hold_sell`
   - 실패 시 카운터 증가 → 임계 도달 시 강제 `remove_position(code)` + `on_sell_failed(pos, sell_type, "연속 N회 실패 — 모니터링 제거")` 1회 + WARN 로그
   - 성공 시 카운터 리셋 (필요시)
5. `tests/test_monitor_state_residue.py`:
   - `test_restore_skips_residue_by_holding_whitelist`: closed 종목의 JSON 잔재가 ×1.02 우회해도 화이트리스트로 차단되는지
   - `test_max_sell_failures_force_remove`: KIS 매도 3회 연속 실패 시 강제 remove + 알림 1회
6. pytest 전체 실행 + code-tester 에이전트 검증
7. 메모리/change_log 갱신
8. CHECKLIST 완료 → main 머지 → archive

## 롤백 계획
- 단일 파일 변경(portfolio_monitor_v2.py + config.py)이므로 `git revert <commit>` 후 서비스 재시작
- 백업 monitor_state.json 보관(2026-05-18 cleanup 백업 존재)

## 완료 기준
- 화이트리스트: `pytest tests/test_monitor_state_residue.py` 전체 PASS (기존 4개 + 신규 2개)
- 실패 카운터: 3회 실패 시 텔레그램 1회 + 메모리 제거 단위테스트 PASS
- code-tester 에이전트: 심각/주의 이슈 0건
- `docs/improvements/change_log.md` 1줄 추가
