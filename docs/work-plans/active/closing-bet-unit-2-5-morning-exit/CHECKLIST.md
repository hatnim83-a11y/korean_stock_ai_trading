# CHECKLIST: 종가베팅 단위 2-5 morning_exit_manager

## 단위 2-5a: Step 0 사전 조사
- [ ] probe 스크립트 — `scripts/probe_kis_morning_exit.py` 단발 (인증 후 5/18 09:00~10:00 자연 검증)
- [ ] 검증 1: `KISApi.get_current_price(stock_code)` 응답 open/high/low/price 필드 정확성
- [ ] 검증 2: 매도 대상 SQL 쿼리 (`status='entered' OR phase1 only`) — 5/15 candidates 테스트 row 직접 query
- [ ] 검증 3: `KISOrderApi.sell_market_order` 응답 rt_cd / ODNO 필드 (entry_executor 와 동일 가정)
- [ ] 검증 4: 부분 체결 잔량 처리 — phase2 부분 체결 시 `total_shares = phase1_shares + COALESCE(phase2_executed_shares, 0)` 정확성
- [ ] **검증 5 (P1-4)**: KIS 주문 취소 API 엔드포인트 가능 여부 (force_close 미체결 취소 순서용)
- [ ] **검증 6 (P0-3)**: 메인 봇 09:00 잡 (`monitoring_start_early` + `midweek_sell_profit`) 실행 시간 측정 → 09:01 emergency_stop 시점 충분성 확인
- [ ] STEP0_MORNING_EXIT_RESEARCH.md 작성 (결과 + 권고 폴링 간격 + 폴백 결정)
- [x] **sell_lock 결정 박제** (P1-5): 재사용 + owner 네임스페이스 분리 (`"closing_bet:*"`) — CONTEXT.md 박제 완료

## 단위 2-5b: collectors + 매도 대상 select + log_exit phase1 only 해결 (P0-2)
- [ ] `closing_bet_system/collectors/morning_price_collector.py` 신규 (async get_open_high_current)
- [ ] `closing_bet_system/execution/exit_target_query.py` 신규 (보유 식별 SQL 캡슐화 + `ExitTarget` dataclass)
- [ ] **`closing_bet_system/storage/candidate_logger.py` 수정** (P0-2): `mark_entered_phase1_only(candidate_id)` 헬퍼 신규 — phase1_executed_price/_shares 를 entry_price/_amount/_time 로 박제 (status는 'recommended' 유지, log_exit 호출 가능 상태로 전환)
- [ ] 단위 테스트 신규 12건 (COL 4 + QUERY 6 + P1ONLY 2 — phase1 only / phase2 완료 / 이미 exit / 빈 결과 + mark_entered_phase1_only 정상 / NULL 가드)
- [ ] **v4 마이그레이션 필요 여부 결정** — default 불필요 (phase1+phase2 합으로 잔량 계산), exit_action 컬럼은 단위 2-5g 후속 분리
- [ ] code-tester 호출

## 단위 2-5c: ExitExecutor + 매도 액션 매트릭스 (P0-1/P0-2/P1-1~5 반영)
- [ ] `closing_bet_system/execution/exit_executor.py` 신규 (**~600~700줄**, entry_executor 684줄 실측 기준)
  - [ ] `ExitExecutor` 클래스 + `ExitExecutorSettings` frozen dataclass
  - [ ] `ExitAction` Enum (**5단계** — trailing_stop은 단위 2-5g 분리: emergency_stop / gap_up_high / gap_up_low / flat / weak_gap_down)
  - [ ] `ExitResult` dataclass (per-ticker + summary)
  - [ ] `map_action(open_price, entry_price, exit_cfg)` 헬퍼 — 매트릭스 분기
  - [ ] `execute_emergency_stop(trade_date)` (09:01 잡용 — hard_stop_loss만, **발주 직후 exit_in_progress 플래그 박제 P1-2**)
  - [ ] `execute_morning_exit(trade_date)` (09:30 잡용 — 나머지 4단계, exit_in_progress 종목 제외)
  - [ ] `execute_force_close(trade_date)` (10:30 잡용 — **순서: 미체결 주문 취소 → 취소 확인 → 시장가 재발주 P1-4**)
  - [ ] **phase1 only 매도 시 mark_entered_phase1_only 선행 호출 (P0-2)**
- [ ] `closing_bet_system/notification/exit_notifier.py` 신규 (3종 메서드 시그니처 통일: `send_emergency_stop_result(result, dry_run)` / `send_morning_exit_result` / `send_force_close_result`)
- [ ] dry_run 토글 분기 (P1-3 entry_executor 일관성): KIS sell + log_exit 모두 건너뜀 + simulated_exit 로그 dict + 텔레그램 "[DRY-RUN] would have sold"
- [ ] **sell_lock 재사용 + owner 네임스페이스 분리 (P1-5)**: `"closing_bet:emergency_stop|morning_exit|force_close"`
- [ ] `candidate_logger.log_exit()` 호출 (cost_engine 비용 분해, phase1 only 는 mark_entered_phase1_only 선행)
- [ ] `_save_exit_order_id` 발주 직후 ODNO 즉시 박제 (idempotency)
- [ ] 단위 테스트 신규 25~30건 (EX-1~30):
  - 5단계 매트릭스 분기 5건
  - dry_run 정책 (KIS X + log_exit X + simulated_exit 발화) 3건
  - phase1 only 매도 (mark_entered_phase1_only 선행) 3건
  - 부분 체결 잔량 처리 2건
  - 09:01 → 09:30 idempotency (exit_in_progress 플래그) 2건
  - 10:30 force_close 미체결 취소 → 시장가 재발주 순서 3건
  - sell_lock owner 네임스페이스 분리 2건
  - 09:30 KIS 500 재시도 / submit_fail 2건
  - 매도 대상 0건 graceful / 텔레그램 비활성 graceful 3건
- [ ] code-tester 호출

## 단위 2-5d: APScheduler 통합 (P0-3 + P1-5 잡별 misfire 정책)
- [ ] `closing_bet_system/main_orchestrator.py` 메서드 3개 추가:
  - [ ] `run_emergency_stop_check` (**09:01 cron**, hard_stop_loss 즉시 손절, misfire_grace_time=60, coalesce=True)
  - [ ] `run_morning_exit` (09:30 cron, 매도 액션 매트릭스, misfire_grace_time=300, coalesce=True)
  - [ ] `run_morning_force_close` (10:30 cron, 잔량 시장가, misfire_grace_time=120, coalesce=True)
- [ ] **상수 박제** (P0-3): `EMERGENCY_STOP_SCHEDULE_HOUR=9, _MINUTE=1` / `MORNING_EXIT_HOUR=9, _MINUTE=30` / `MORNING_FORCE_CLOSE_HOUR=10, _MINUTE=30`
- [ ] `register_jobs()` 잡 3건 추가 (mon-fri Asia/Seoul, 잡별 misfire 정책 반영)
- [ ] 잡 등록 로그 "5건 → 8건" 변경
- [ ] `closing_bet_system/config/settings.yaml`:
  - [ ] `morning_exit:` 섹션 신규 (enabled=false / dry_run=true / emergency_stop_enabled=true / use_sell_lock=true 등 5~8개 키)
  - [ ] `schedule.emergency_stop_start: "09:00" → "09:01"` 갱신 (P0-3 race 회피, 주석 명시)
- [ ] ExitExecutor lazy property (idempotent)
- [ ] orchestrator 회귀 테스트 갱신 — 잡 8건 + 신규 3개 상수 검증 + 09:01 race 회피 검증 + 잡별 misfire/coalesce 설정 검증

## 단위 2-5e: 통합 검증
- [ ] 단위 테스트 누적 165+건 PASS (회귀 136 + 신규 30~40)
- [ ] py_compile 0 에러
- [ ] dry_run 통합 단발 (KIS 매도 미발주 + 텔레그램 "[DRY-RUN] would have sold" 알림 발화 + log_exit 미호출 검증)
- [ ] **시뮬레이터 정합성 게이트 (P0-1)**: `phase25_simulator.py` `prd_split_realistic` 정책 매핑이 단위 2-5c 실 매도 매트릭스와 일치하는지 재검증
  - 시뮬 `prd_split_gapdown` (≤ -1%) ⇔ 실 emergency_stop
  - 시뮬 `prd_split_gapup` (≥ +0.5%) ⇔ 실 gap_up_high + gap_up_low 합
  - 시뮬 `prd_split_flat` (-1% < open < +0.5%) ⇔ 실 flat + weak_gap_down 합
  - delta(시뮬 EV - 실 매도 가정 EV) ≤ 0.1% 합격
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

## 사전 리뷰 (strategy-planner + strategy-coder, 코딩 전) — 2026-05-15 완료
- [x] strategy-planner 호출 — P0 2건 (시뮬 정합성 P0-1 + log_exit P0-2) + P1 5건 (GAPUP 임계값 / idempotency / trailing 모니터링 / force_close 취소 / sell_lock)
- [x] strategy-coder 호출 — P0 2건 (log_exit P0-2 + 09:00 race P0-3) + P1 5건 (sell_lock 재사용 / align_to_tick 검증 / dry_run 정책 / 파일 크기 / 잡별 misfire)
- [x] P0 3건 + P1 9건 PLAN.md / CONTEXT.md / CHECKLIST.md 반영 완료 (이 commit)

## P0/P1 반영 결과 요약
**P0-1** (시뮬 vs 실 매도 매트릭스 불일치): PLAN.md 매핑표 박제 + 5단계 매트릭스 통일 (trailing_stop 단위 2-5g 분리) + 단위 2-5e 정합성 게이트 추가
**P0-2** (phase1 only log_exit LookupError): `mark_entered_phase1_only` 헬퍼 신규 (candidate_logger.py 수정), ExitExecutor 가 매도 발주 전 선행 호출
**P0-3** (09:00 메인 봇 잡 race): emergency_stop 09:00 → **09:01** 오프셋, 상수 박제, settings.yaml schedule.emergency_stop_start 갱신
**P1-1** (GAPUP 임계값 불일치): 5단계 모두 시초가 시장가 매도로 통일 → 시뮬 정합
**P1-2** (idempotency): 09:01 발주 직후 exit_in_progress 플래그 즉시 박제
**P1-3** (trailing 모니터링): 단위 2-5 범위 외, **단위 2-5g** 별도 분리
**P1-4** (force_close 취소 순서): 미체결 취소 → 확인 → 시장가 재발주, 단위 2-5a Step 0 검증 추가
**P1-5** (sell_lock): 재사용 + owner 네임스페이스 분리 (`"closing_bet:*"`), CONTEXT 박제

## 비고
- 본 CHECKLIST는 상위 단위 — 사전 리뷰 P0/P1 반영 완료 후 단위 2-5a 진입 가능
- 컨텍스트 크기 주의 — 본 작업은 entry_executor 와 비슷 규모, 한 세션에 2-5a~e 전체 진행은 컨텍스트 한계 초과 가능 → 단위 2-5a/b 한 세션, 2-5c 별도 세션, 2-5d/e 또 별도 세션 권장
- `trailing_stop` 분리에 따라 단위 2-5g 신설 — 09:30~10:30 폴링 루프 (별도 비동기 잡 또는 2분 cron 추가)
