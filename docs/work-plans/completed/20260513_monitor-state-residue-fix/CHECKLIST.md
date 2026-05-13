# CHECKLIST — monitor_state.json 잔재 데이터 버그 수정

> 단위별 진행. 각 단위 완료 시 사용자 확인 후 다음 단위로 진행.

## 구현 항목

### 단위 1 — `portfolio_monitor_v2.py` 패치 (장중 가능)
- [ ] `remove_position()` 라인 291-307: JSON 키 동기 삭제 추가
  - [ ] 함수 내 로컬 `import json` 패턴 사용
  - [ ] 예외 처리: 파일 없음/파싱 실패 시 logger.debug 만, 매도 차단 금지
- [ ] `_execute_partial_sell()` 라인 1013-1088: 전량 익절 분기 (`remaining_shares <= 0`) 에 JSON 정리 추가
  - [ ] 결정: `self.remove_position(stock_code)` 호출로 1-1 패치 상속 (단순)
- [ ] `_restore_trailing_state()` 라인 357-461: buy_price sanity check 가드
  - [ ] JSON 폴백 경로(`db_source=False`) 에서, `state[code].highest_price > pos.buy_price × 1.02` 면 `continue` + warning 로그
  - [ ] 로그 포맷: `🚮 JSON 잔재 무시: {code} highest={x} > buy_price×1.02={y}`
- [ ] `stop_monitoring()` 마지막 `_dump_monitor_state()` 호출 여부 확인 — 없으면 1줄 추가
- [ ] `python -m py_compile modules/trading_engine/portfolio_monitor_v2.py` 통과

### 단위 2 — `web/dashboard_service.py` 폴백 가드 (장중 가능)
- [ ] `_load_monitor_state()` 라인 190-209: JSON 폴백 직후 `portfolio.status='holding'` 코드 셋 쿼리
- [ ] JSON dict 에서 보유 외 키 필터링 제외
- [ ] `python -m py_compile web/dashboard_service.py` 통과

### 단위 3 — `scripts/cleanup_monitor_state_json.py` 신규 (장중 가능)
- [ ] 첫 줄에 `systemctl is-active --quiet trading_system` 가드 — 실행 중이면 stderr 출력 후 exit(1)
- [ ] `data/monitor_state.json` 백업 → `.bak_{now_kst().strftime('%Y%m%d_%H%M%S')}`
- [ ] DB `portfolio` 테이블에서 `status='holding'` 코드 셋 로드
- [ ] JSON 에서 보유 외 키 제거 후 쓰기
- [ ] 정화 전후 키 개수 + 제거 키 목록 콘솔 출력
- [ ] `python -m py_compile scripts/cleanup_monitor_state_json.py` 통과

### 단위 4 — `tests/test_monitor_state_residue.py` 신규 (장중 가능)
- [ ] `test_remove_position_clears_json_key` — 매도 시 JSON 키 즉시 삭제
- [ ] `test_partial_sell_full_exit_clears_json` — 전량 익절 경로 JSON 삭제
- [ ] `test_restore_skips_residue_by_buy_price` — sanity check 가드 검증
- [ ] `test_dashboard_json_fallback_filters_residue` — 대시보드 폴백 필터링
- [ ] `pytest tests/test_monitor_state_residue.py -v` 전체 통과

### 단위 5 — code-tester 에이전트 검증 (장중 가능)
- [ ] 4개 파일 (`portfolio_monitor_v2.py`, `dashboard_service.py`, `cleanup_monitor_state_json.py`, `test_monitor_state_residue.py`) 검증
- [ ] 심각 이슈 0건, 주의 이슈는 즉시 픽스

## 검증 / 배포 항목

### 단위 6 — 배포 (반드시 15:30 이후 또는 다음 거래일 08:30 이전)
- [ ] `sudo systemctl stop trading_system`
- [ ] `ps aux | grep main.py | grep -v grep` — 잔존 프로세스 없는지 확인
- [ ] 정화 스크립트 실행: `source venv/bin/activate && python scripts/cleanup_monitor_state_json.py`
- [ ] JSON 키 = `holding` 종목 셋 일치 확인
- [ ] `.bak_YYYYMMDD_HHMMSS` 백업 파일 생성 확인
- [ ] git commit (4개 파일 + 1개 신규 스크립트 + 1개 신규 테스트 — 단일 커밋)
- [ ] `sudo systemctl start trading_system` → 30초 후 status active 확인
- [ ] **회귀 시뮬레이션**: JSON 에 더미 잔재 키 `000000` 삽입 → restart → 로그에 `🚮 JSON 잔재 무시` 또는 다음 dump 사이클에서 자동 정화 확인
- [ ] `sudo journalctl -u trading_system --since "5 minutes ago" | grep -E "BE 손절 복원"` — 비정상 BE 손절가 복원 로그 부재 확인
- [ ] SQLite MCP 로 `position_state` 키 셋 = JSON 키 셋 = `portfolio.status='holding'` 일치 확인
- [ ] 대시보드 접속 — 보유 종목만 표시 확인

## 문서 업데이트 항목 (단위 7)
- [ ] `CLAUDE.md` — "monitor_state.json 정합성 규칙" 섹션 추가 (DB primary + remove_position JSON 정리 의무 + sanity check 가드 설명)
- [ ] `memory/project_monitor_state_residue_fix.md` 신규 — 사건 / 원인 / 패치 / 검증 결과 요약
- [ ] `memory/MEMORY.md` — 신규 메모리 1줄 추가
- [ ] `docs/improvements/change_log.md` — 1줄 추가 (before: JSON 잔재 → BE 손절 오발동 / after: 3중 동기화 + sanity check)
- [ ] 작업 폴더 `active/monitor-state-residue-fix` → `completed/20260512_monitor-state-residue-fix` 이동

## 단위별 사용자 확인 게이트
각 단위 종료 시:
1. 변경 사항 요약 보고
2. 사용자 OK 확인
3. 다음 단위 진행

단위 6 은 장 마감 시간 (15:30 KST) 이후에 진입 — 현재 시각 10:30 KST 기준 단위 1~5 는 장중 가능, 단위 6 만 대기.
