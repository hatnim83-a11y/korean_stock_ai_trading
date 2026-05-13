# PLAN: monitor_state.json 잔재 데이터 버그 근본 수정

> 글로벌 plan 파일: `/home/hatni/.claude/plans/purring-waddling-storm.md`
> 작업일: 2026-05-12 / 작업자: hatnim83-a11y + Claude

---

## 목표
`data/monitor_state.json` 잔재 데이터로 인한 BE 손절 오발동 버그(5/12 한화오션 사건) 근본 수정.

## 배경
- **2026-05-12 09:25**: 한화오션(042660) 14주 @ 126,950원 매수
- **2026-05-12 10:04**: BE 손절 발동 @ 125,600원 (-1.06%, -16,200원)
- **원인**: 4월 매도 시 DB position_state 는 삭제됐으나 JSON 잔재(highest_price=136,800, max_profit_rate=0.3) 가 보관되어, 5/12 모니터 재시작 시 `_restore_trailing_state()` JSON 폴백이 잔재로 BE 손절가(125,680원)를 즉시 복원

## 의도된 결과
1. 매도 시 DB/JSON/메모리 3중 동기화 — 매도된 종목 키 즉시 소거 (전량 익절 경로 포함)
2. 잔재가 남더라도 `buy_price` 기반 sanity check 로 무시 (서로 다른 매수 사이클 식별)
3. 1회용 정화 스크립트로 누적된 잔재 일괄 제거

## 구현 단계 (단위 분할)

### 단위 1 — `portfolio_monitor_v2.py` 패치 (장중 가능)
1. `remove_position()` 라인 291-307: JSON 키 동기 삭제
2. `_execute_partial_sell()` 라인 1013-1088 (전량 익절 분기): JSON 정리 추가
3. `_restore_trailing_state()` 라인 357-461: `highest_price > buy_price × 1.02` sanity check 가드
4. `stop_monitoring()` 마지막 dump 보장 검증 (필요 시 1줄 추가)
5. `python -m py_compile` 통과

### 단위 2 — `web/dashboard_service.py` 폴백 가드 (장중 가능)
- `_load_monitor_state()` 라인 190-209: JSON 폴백 직후 portfolio.status='holding' 필터링

### 단위 3 — `scripts/cleanup_monitor_state_json.py` 신규 (장중 가능)
- systemctl is-active 가드 + KST 타임스탬프 백업 + dry-run

### 단위 4 — 단위 테스트 `tests/test_monitor_state_residue.py` 신규 (장중 가능)
- 4개 케이스: remove_position / partial_sell 전량 익절 / sanity check / 대시보드 필터

### 단위 5 — code-tester 에이전트 검증 (장중 가능)
- 4개 파일 검증, 심각/주의 이슈 0건 확인

### 단위 6 — 배포 (장 마감 후 15:30 이후 필수)
- systemctl stop → 정화 스크립트 → git commit → systemctl start → 회귀 시뮬레이션

### 단위 7 — 문서 업데이트
- CLAUDE.md, MEMORY.md, change_log.md, active → completed/ 이동

## 변경 파일 목록

| 파일 | 변경 |
|---|---|
| `modules/trading_engine/portfolio_monitor_v2.py` | 3개 함수 패치 + 1개 검증 |
| `web/dashboard_service.py` | 폴백 가드 |
| `scripts/cleanup_monitor_state_json.py` | 신규 |
| `tests/test_monitor_state_residue.py` | 신규 |
| `CLAUDE.md` | JSON 정합성 정책 항목 추가 |
| `memory/MEMORY.md` + `memory/project_monitor_state_residue_fix.md` | 신규 |
| `docs/improvements/change_log.md` | 1줄 추가 |

## 롤백 계획
1. **코드**: `git revert <commit>` 1회
2. **JSON**: `cp data/monitor_state.json.bak_YYYYMMDD_HHMMSS data/monitor_state.json`
3. **서비스**: `sudo systemctl restart trading_system`

## 완료 기준
CHECKLIST.md 의 모든 항목 `[x]` + active → completed/20260512_monitor-state-residue-fix 이동
