# CHECKLIST — 매수↔모니터 재시작 레이스 근본 수정

## 구현
- [x] Step 1: `monitoring_minute` 간격 확대(early_buy 6→10) + 주석 근거 명시 (scheduler.py:193)
- [x] Step 2: `execute_buy_orders()` 말미 `start_monitoring()` 체이닝 (try/except 격리, bought_count>0 & not test_mode 가드)
- [x] Step 2: 고정 cron 재시작을 멱등 안전망으로 유지 (제거 안 함) — 주석으로 역할 명시
- [x] Step 3: holding ∖ self.positions 차집합 누락 편입 헬퍼(`_sweep_missing_positions`) + 텔레그램 1회 경보(on_position_recovered 콜백)
- [x] Step 3: 토글 상수(config) MONITOR_MISSING_SWEEP_ENABLED(기본 True)/MONITOR_MISSING_SWEEP_INTERVAL_SEC(60)
- [x] Step 4: `start_monitoring` 이중 트리거 멱등성 점검 (stop+start로 _running=False → 구 루프 종료, 신규 인스턴스만 동작)
- [x] `status='holding'` 엄격 가드(get_portfolio(status="holding") 차집합) + load_positions_from_db 재사용(v17 필드 복원) + remove 시 dedup set 정리

## 검증
- [x] py_compile 통과 (scheduler.py, main.py, portfolio_monitor_v2.py, config.py + 수정 테스트 2개)
- [x] code-tester 이슈 수정: 심각#1(_recovered_alerted hasattr 가드 + 테스트 헬퍼 set 초기화), 심각#2(스윕 only_codes 경로로 기존 포지션 트레일링 무손상), 주의#1(monitoring_time_str 09:06→09:10 + test_early_buy minute 6→10), 경미(create_task → self._monitor_task)
- [ ] 매수 루프 인위 지연(mock ~90초) → 모든 체결 종목 편입(누락 0) 재현 테스트
- [ ] 이중 트리거(매수완료 + cron 09:06) → 예외/중복 WebSocket 구독 없음
- [ ] holding 수 == 모니터 포지션 수 == monitor_state.json keys 수 정합 확인
- [x] 기존 회귀 테스트 통과 (test_monitor_state_residue.py + test_early_buy_schedule.py 합산 13 passed)

## 배포
- [x] 토글 기본값 안전 확인 (`MONITOR_MISSING_SWEEP_ENABLED=True` 코드 기본값, `.env` 추가 불필요)
- [x] 머지 worktree-buy-monitor-restart-race-fix → main (2026-06-10 장 마감 후)
- [x] `sudo systemctl restart trading_system` (2026-06-10 22:0x KST, 장 마감 후)
- [ ] 익일(2026-06-11) 09:05~09:10 로그로 정상 편입 실측 확인 ← 익일 모니터링
- [x] `docs/improvements/change_log.md` — monitoring_minute 6→10 + 매수완료 체이닝 1줄 기록

## 문서 업데이트
- [x] `CLAUDE.md` — "매수↔모니터 재시작 정합" 규칙 섹션 추가
- [x] `memory/MEMORY.md` — 본 사건/수정 1줄 포인터
- [x] `memory/` 신규 파일(project_buy_monitor_restart_race_fix.md) 작성
- [x] active/ → completed/20260610_buy-monitor-restart-race-fix/ 아카이브

## 결정 결과
- Step 2 채택, 고정 cron 재시작은 **멱등 안전망으로 유지**(제거 안 함)
- Step 1+2+3 **한 번에 배포**(권장안 그대로)
