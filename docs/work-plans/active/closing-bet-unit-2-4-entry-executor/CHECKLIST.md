# CHECKLIST: 종가베팅 단위 2-4 entry_executor

## 단위 2-4a: Step 0 KIS 사전 조사
- [ ] probe 스크립트 작성 (`scripts/probe_kis_entry_executor.py`)
- [ ] 검증 1: 동시호가 ord_dvsn 코드 ("00" 자동 / "05/06/07" 별도)
- [ ] 검증 2: 예상체결가 TR (FHKST01010100 antc_cnpr / 별도 TR)
- [ ] 검증 3: 분봉 VWAP TR (FHKST03010200)
- [ ] 검증 4: TR_ORDER_STATUS (TTTC8001R) 응답 필드 (체결가/수량/잔량)
- [ ] STEP0_KIS_RESEARCH.md 작성 (결과 + 권고 폴링 간격 + 폴백 결정)
- [ ] phase2_enabled True/False 결정

## 단위 2-4b: collectors + fill_checker + price_utils
- [ ] `closing_bet_system/collectors/vwap_collector.py` 신규 (async)
- [ ] `closing_bet_system/collectors/estimated_price_collector.py` 신규
- [ ] `closing_bet_system/collectors/kis_orderbook_collector.py` 폴링 메서드 추가
- [ ] `closing_bet_system/execution/fill_checker.py` 신규 (3회 재시도)
- [ ] `closing_bet_system/execution/price_utils.py` 신규 (호가 단위 정렬)
- [ ] 단위 테스트 신규 29건 (VWAP 6 / EP 5 / POLL 4 / FILL 8 / TICK 6)
- [ ] code-tester 호출

## 단위 2-4c: EntryExecutor + DB v2
- [ ] `closing_bet_system/storage/db.py` v2 마이그레이션 (+6 컬럼 + 인덱스, idempotent)
- [ ] `closing_bet_system/execution/entry_executor.py` 신규 (EntryExecutor + Settings + Phase1/2Result)
- [ ] `closing_bet_system/notification/entry_notifier.py` 신규 (텔레그램 포맷)
- [ ] PRD 9-2 가격 상한 + align_to_tick 통합
- [ ] PRD 9-3 보류/취소 로직
- [ ] market_guard Phase 0.5 통합
- [ ] fund_guard 거부 시 mark_rejected 로깅
- [ ] dry_run 토글 분기
- [ ] mark_entered 옵션 A (phase2 완료 시 1회, 가중 평균)
- [ ] 단위 테스트 신규 30건 (EE-1~30)
- [ ] code-tester 호출

## 단위 2-4d: APScheduler 통합
- [ ] `closing_bet_system/main_orchestrator.py` `run_entry_pipeline` 추가 (단일 async 잡)
- [ ] `register_jobs()` 15:18 cron trigger 등록 (enabled 토글 가드)
- [ ] `closing_bet_system/config/settings.yaml` `entry_executor` 섹션 신규
- [ ] `run_daily_summary` 현행화 (옵션 C 3점 비교 표 — phase1f에서 본격 운영)

## 단위 2-4e: 통합 검증
- [ ] 단위 테스트 누적 119건 PASS (회귀 60 + 신규 59)
- [ ] py_compile 0 에러
- [ ] dry_run 통합 단발 (KIS 미발주 + 알림 발화 검증)
- [ ] code-tester 종합 호출 — 심각 0건 + 주의 ≤3건
- [ ] 메인 워크트리 머지 + push (사용자 승인 후)

## 단위 2-4f: 실전 활성화 (별도 세션)
- [ ] 단위 2-5 morning_exit_manager 완료 OR 수동 매도 SOP 확정
- [ ] 사용자 명시 승인
- [ ] `settings.yaml enabled=true / dry_run=false`
- [ ] systemctl restart trading_system
- [ ] 1주 모니터링 (옵션 C 3점 비교 + weekly_loss_limit)

## 문서 업데이트 (단위 2-4e 완료 시)
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] `memory/project_closing_bet_followups.md` 갱신
- [ ] active/ → completed/ 아카이브
