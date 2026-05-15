# CHECKLIST: 종가베팅 단위 2-4 entry_executor

## 단위 2-4a: Step 0 KIS 사전 조사
- [x] ~~probe 스크립트 작성~~ → 인프라/매뉴얼 분석으로 대체 (실 probe는 5/15 dry_run에서 자연 검증)
- [x] 검증 1: 동시호가 ord_dvsn 코드 → "00" 단일 사용 결론 (15:20~30 자동 동시호가 큐 진입)
- [x] 검증 2: 예상체결가 TR → `inquire_asking_price` (FHKST01010200) `antc_cnpr` 필드 사용
- [x] 검증 3: 분봉 VWAP TR → `FHKST03010200` 신규 추가 (`kis_api.get_minute_price`)
- [x] 검증 4: TR_ORDER_STATUS (TTTC8001R) → 기존 `KISOrderApi.get_order_status` 재사용
- [x] STEP0_KIS_RESEARCH.md 작성 (결과 + 폴링 5초 + 폴백 4건)
- [x] phase2_enabled True 결정 (default, 5/15 dry_run 최종 확인 후 토글)

## 단위 2-4b: collectors + fill_checker + price_utils
- [x] `closing_bet_system/collectors/vwap_collector.py` 신규 (async)
- [x] `closing_bet_system/collectors/estimated_price_collector.py` 신규
- [x] `closing_bet_system/collectors/kis_orderbook_collector.py` 폴링 메서드 추가 (`poll_asking_price`)
- [x] `closing_bet_system/execution/fill_checker.py` 신규 (3회 재시도)
- [x] `closing_bet_system/execution/price_utils.py` 신규 (호가 단위 정렬)
- [x] 단위 테스트 신규 29건 (VWAP 6 / EP 5 / POLL 4 / FILL 8 / TICK 6) — PASS 누적 89건
- [x] code-tester 호출 — 심각 0 / 주의 2 (수정 완료) / 참고 1 (5/15 dry_run 확인 항목)

## 단위 2-4c: EntryExecutor + DB v2
- [x] `closing_bet_system/storage/db.py` v3 마이그레이션 (+6 컬럼, idempotent, `_has_column` 가드, 인덱스는 v1에 기존 존재)
- [x] `closing_bet_system/execution/entry_executor.py` 신규 (EntryExecutor + EntryExecutorSettings + Phase1Result/Phase2Result + CandidateOrder)
- [x] `closing_bet_system/notification/entry_notifier.py` 신규 (텔레그램 포맷: phase1/phase2/pipeline_summary/market_guard_skip)
- [x] `closing_bet_system/collectors/vwap_collector.py` `get_snapshot` 메서드 + `VWAPSnapshot` dataclass 추가 (high 동반 반환)
- [x] PRD 9-2 가격 상한 + align_to_tick 통합 (vwap×1.005 / high / estimated_price×1.002)
- [x] PRD 9-3 보류(예상체결가 +0.5%) / 취소(ask/bid < 0.8) 로직
- [x] market_guard Phase 0.5 통합 (CRISIS 전체 스킵 / DANGER+CAUTION → ratio × 0.5)
- [x] fund_guard 거부 시 `mark_rejected_by_filter('fund_guard:...')` 로깅
- [x] dry_run 토글 분기 (KIS 호출 직전, subclass 패턴 X)
- [x] mark_entered 옵션 A (`_finalize_mark_entered` — phase2 완료 시 가중 평균 1회 호출)
- [x] 단위 테스트 신규 30건 (EE-1~30) — 30/30 PASS
- [x] code-tester 호출

## 단위 2-4d: APScheduler 통합
- [x] `closing_bet_system/main_orchestrator.py` `run_entry_pipeline` 추가 (단일 async 잡 + `_sleep_until_kst` 헬퍼)
- [x] `register_jobs()` 15:18 cron trigger 등록 (mon-fri Asia/Seoul, misfire_grace_time=120, coalesce, enabled 가드는 함수 진입 시 즉시 skip)
- [x] `closing_bet_system/config/settings.yaml` `entry_executor` 섹션 신규 (10개 키, Phase 2 기본 비활성)
- [x] EntryExecutor lazy property (의존성 주입 + idempotent + settings.yaml 매핑)
- [ ] `run_daily_summary` 현행화 (옵션 C 3점 비교 표) ← 단위 2-4f 활성화 직전 추가 (현 시점 비운영)

## 단위 2-4e: 통합 검증
- [x] 단위 테스트 누적 136건 PASS (phase25 회귀 60 + 2-4b 29 + 2-4c 31 + orchestrator 16, 마스터플랜 119건 초과)
- [x] py_compile 0 에러 (entry_executor / entry_notifier / db.py / vwap_collector / main_orchestrator)
- [x] dry_run 통합 단발 (`run_entry_pipeline` enabled=False 가드 → 즉시 skip; settings.yaml 기본값 dry_run=true 검증)
- [x] code-tester 종합 호출 — 단위 2-4c 심각 0/주의 3 (즉시 수정 완료), 단위 2-4d 심각 0/주의 2 (테스트 커버리지 보강 완료)
- [x] orchestrator 회귀 보강 (test_register_jobs 5건 + entry_pipeline 트리거 검증 + ENTRY 상수 4건 추가)
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
