# CHECKLIST: 종가베팅 단위 2-5 morning_exit_manager

## 단위 2-5a: Step 0 사전 조사
- [ ] probe 스크립트 — `scripts/probe_kis_morning_exit.py` 단발 (인증 후 5/18 09:00~10:00 자연 검증)
- [ ] 검증 1: `KISApi.get_current_price(stock_code)` 응답 open/high/low/price 필드 정확성
- [ ] 검증 2: 매도 대상 SQL 쿼리 (`status='entered' OR phase1 only`) — 5/15 candidates 테스트 row 직접 query
- [ ] 검증 3: `KISOrderApi.sell_market_order` 응답 rt_cd / ODNO 필드 (entry_executor 와 동일 가정)
- [ ] 검증 4: 부분 체결 잔량 처리 — phase2 부분 체결 시 `total_shares = phase1_shares + phase2_executed_shares` 정확성
- [ ] STEP0_MORNING_EXIT_RESEARCH.md 작성 (결과 + 권고 폴링 간격 + 폴백 결정)
- [ ] sell_lock 재사용 vs 신규 잠금 결정

## 단위 2-5b: collectors + 매도 대상 select
- [ ] `closing_bet_system/collectors/morning_price_collector.py` 신규 (async get_open_high_current)
- [ ] `closing_bet_system/execution/exit_target_query.py` 신규 (보유 식별 SQL 캡슐화 + `ExitTarget` dataclass)
- [ ] 단위 테스트 신규 10건 (COL 4 + QUERY 6 — phase1 only / phase2 완료 / 이미 exit / 빈 결과 등)
- [ ] code-tester 호출

## 단위 2-5c: ExitExecutor + 매도 액션 매트릭스
- [ ] `closing_bet_system/execution/exit_executor.py` 신규 (~500줄)
  - [ ] `ExitExecutor` 클래스 + `ExitExecutorSettings` frozen dataclass
  - [ ] `ExitAction` Enum (6단계: emergency_stop / gap_up_high / gap_up_low / flat / weak_gap_down / trailing_stop)
  - [ ] `ExitResult` dataclass (per-ticker + summary)
  - [ ] `map_action(open_price, entry_price, exit_cfg)` 헬퍼 — 매트릭스 분기
  - [ ] `execute_emergency_stop(trade_date)` (09:00 잡용 — hard_stop_loss만)
  - [ ] `execute_morning_exit(trade_date)` (09:30 잡용 — 나머지 5단계)
  - [ ] `execute_force_close(trade_date)` (10:30 잡용 — 잔량 시장가 전량)
- [ ] `closing_bet_system/notification/exit_notifier.py` 신규 (텔레그램 알림: emergency_stop / morning_exit / force_close 3종)
- [ ] dry_run 토글 분기 (KIS 호출 직전, subclass 패턴 X)
- [ ] sell_lock 적용 (race 봉쇄)
- [ ] `candidate_logger.log_exit()` 호출 (cost_engine 비용 분해)
- [ ] 단위 테스트 신규 20~25건 (EX-1~25 — 6단계 분기 + dry_run + 부분 체결 + 잔량 force_close + idempotency)
- [ ] code-tester 호출

## 단위 2-5d: APScheduler 통합
- [ ] `closing_bet_system/main_orchestrator.py` 메서드 3개 추가:
  - [ ] `run_emergency_stop_check` (09:00 cron, hard_stop_loss 즉시 손절)
  - [ ] `run_morning_exit` (09:30 cron, 매도 액션 매트릭스)
  - [ ] `run_morning_force_close` (10:30 cron, 잔량 시장가)
- [ ] `register_jobs()` 잡 3건 추가 (mon-fri Asia/Seoul, misfire_grace_time=120)
- [ ] 잡 등록 로그 "5건 → 8건" 변경
- [ ] `closing_bet_system/config/settings.yaml` `morning_exit:` 섹션 신규 (enabled=false / dry_run=true / emergency_stop_enabled / use_sell_lock 등 5~8개 키)
- [ ] ExitExecutor lazy property (idempotent)
- [ ] orchestrator 회귀 테스트 갱신 (잡 8건 + 신규 3개 상수 검증)

## 단위 2-5e: 통합 검증
- [ ] 단위 테스트 누적 누적 165+건 PASS (회귀 136 + 신규 30~40)
- [ ] py_compile 0 에러
- [ ] dry_run 통합 단발 (KIS 매도 미발주 + 알림 발화 검증)
- [ ] code-tester 종합 호출 — 심각 0건 + 주의 ≤3건
- [ ] 메인 워크트리 머지 + push (사용자 승인 후)

## 단위 2-5f: 실전 활성화 (단위 2-4f와 묶어서, 별도 세션)
- [ ] dry_run 데이터 1주+ 누적 검증 (5/18~5/24)
- [ ] walkforward 실측 vs realistic vs optimistic 3점 비교 (단위 2-4f run_daily_summary 현행화 후)
- [ ] 사용자 명시 승인 (매수+매도 짝 활성화)
- [ ] `settings.yaml entry_executor.dry_run=false` + `morning_exit.enabled=true / dry_run=false`
- [ ] systemctl restart trading_system
- [ ] 1주 모니터링 (옵션 C 3점 비교 + weekly_loss_limit + 매도 슬리피지)

## 문서 업데이트 (단위 2-5e 완료 시)
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] `memory/project_closing_bet_followups.md` 갱신
- [ ] active/ → completed/ 아카이브
- [ ] `CLAUDE.md` 종가베팅 운영 규칙에 morning_exit 잡 시간 추가

## 사전 리뷰 (strategy-planner + strategy-coder, 코딩 전)
- [ ] strategy-planner 호출 — 매도 매트릭스 6단계 분기 정합성 / 옵션 A 인터페이스 계약 정확성 / 09:00 + 09:30 race 위험 / 부분 체결 잔량 처리
- [ ] strategy-coder 호출 — async 패턴 / sell_lock 재사용 vs 신규 / log_exit 호출 위치 / idempotency / 메인 봇 09:00~09:25 매수와 시점 충돌
- [ ] P0/P1 발견 시 PLAN.md / CONTEXT.md 갱신 후 코딩 시작

## 비고
- 본 CHECKLIST는 상위 단위 — 단위 2-5a 진입 전 사전 리뷰 필수 (CLAUDE.md feedback_plan_review_process)
- 단위 2-5c ExitAction 매핑이 가장 위험 — strategy-planner 사전 리뷰 P0 후보
- 단위 2-5b SQL 쿼리 — 단위 2-4 옵션 A 인터페이스 계약 정확 매핑 검증 필수
- 컨텍스트 크기 주의 — 본 작업은 entry_executor 와 비슷 규모, 한 세션에 2-5a~e 전체 진행은 컨텍스트 한계 초과 가능 → 단위 2-5a/b 한 세션, 2-5c 별도 세션, 2-5d/e 또 별도 세션 권장
