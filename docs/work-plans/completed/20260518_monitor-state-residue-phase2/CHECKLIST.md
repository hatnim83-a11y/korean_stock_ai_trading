# CHECKLIST — Phase 2 잔재 차단 강화 (완료)

## 구현
- [x] `config.py`에 `MAX_SELL_FAILURES = 3` 추가
- [x] `portfolio_monitor_v2.py` `_restore_trailing_state` JSON 폴백 경로에 holding 화이트리스트 가드 추가
- [x] `portfolio_monitor_v2.py` 클래스에 `self.sell_failure_counts: dict[str,int] = {}` 추가
- [x] `_execute_stop_loss` 실패 분기: `_record_sell_failure` 헬퍼로 교체
- [x] `_execute_partial_sell` 실패 분기: 카운터 미적용 (트리거 재도달 필요해 도배 위험 낮음 + 익절 기회 보존). 기존 on_sell_failed 직접 호출 유지 + 예외 가드 추가
- [x] `_execute_trailing_stop` 실패 분기: `_record_sell_failure` 헬퍼로 교체
- [x] `_execute_max_hold_sell` 실패 분기: `_record_sell_failure` 헬퍼로 교체
- [x] `remove_position` 마지막에 `sell_failure_counts.pop` 추가 (재진입 시 0부터 시작)

## 검증
- [x] `python -m py_compile modules/trading_engine/portfolio_monitor_v2.py config.py` — OK
- [x] `pytest tests/test_monitor_state_residue.py -v` — 10/10 PASS (기존 6 + 신규 4)
- [x] 신규 케이스 추가:
  - [x] `test_restore_skips_residue_by_holding_whitelist` — closed 종목 JSON 잔재가 ×1.02 우회해도 화이트리스트로 차단
  - [x] `test_restore_keeps_holding_when_whitelist_passes` — holding 종목 정상 복원
  - [x] `test_max_sell_failures_force_remove` — 3회 연속 매도 실패 시 강제 remove + 리셋 확인
  - [x] `test_sell_failure_below_threshold_keeps_position` — 임계 미만 유지
- [x] code-tester 에이전트 — 심각 1건 false alarm 확인 / 주의 2건 반영(partial 카운터 미적용 + remove_position 예외 카운터 정리)

## 배포
- [x] worktree → main 머지 (커밋/푸쉬 수행)
- [ ] `sudo systemctl restart trading_system` (보유 종목 0 확인 후, 사용자가 운영 타이밍에 맞춰 실행)
- [x] `docs/improvements/change_log.md` 1줄 추가

## 문서 업데이트
- [x] `memory/project_monitor_state_residue_fix.md` — Phase 2 회귀 사례 + 화이트리스트/카운터 기술
- [x] `memory/MEMORY.md` 해당 줄 갱신 (Phase 1+2 누적 표시)
- [x] `CLAUDE.md` — 새 규칙 없음(기존 규칙 강화라 변경 없음)
- [x] `active/` → `completed/20260518_monitor-state-residue-phase2/` 아카이브 (커밋 시점에 이동)
