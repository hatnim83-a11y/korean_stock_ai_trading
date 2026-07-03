# PLAN: 종가베팅 단위 2-5 morning_exit_manager (Phase 2 자동매도)

## 목표
종가베팅 자동매수(단위 2-4) 익일 09:00~10:30 KST 자동매도 모듈 구현.
PRD 10-1 시가 액션 매트릭스 6단계 정합. 단위 2-4f 활성화 게이트의 최대 블로커 해소.

## 배경
- 단위 2-4 EntryExecutor 인프라 완성 (5/15, commit `fe5f54c` push, 운영 봇 배포)
- settings.yaml `entry_executor.enabled=true / dry_run=true` 상태 — 5/18(월) 15:18부터 dry_run 자연 발화
- 단위 2-4f 실전 활성화 게이트 조건: **단위 2-5 완료 또는 수동 매도 SOP 확정** + 1주 dry_run + 사용자 승인
- 매수만 자동/매도 부재 시 보유 종목 리스크 노출 → 단위 2-5 우선 진행
- 5/14 walkforward 게이트 PASS (n=103, realistic EV +1.04% / Sharpe +1.57) — 자동매매 진입 결정 근거 확보됨

## PRD 10-1 시가 액션 매트릭스 (settings.yaml `exit:` 섹션 박제값)
| 분류 | 조건 | 액션 | 시점 | 시뮬 매핑 |
|---|---|---|---|---|
| **emergency_stop** | 시가 ≤ -1% (hard_stop_loss) | 즉시 전량 시장가 매도 | **09:01** (메인 봇 09:00 잡 race 회피) | prd_split_gapdown |
| **gap_up_high** | 시가 ≥ +2% (gap_up_high_threshold) | 50~70% 분할 매도 (partial_ratio=0.6) + 잔여 09:30 추적 | 09:30 | prd_split_gapup |
| **gap_up_low** | +0.5% ≤ 시가 < +2% | **09:30 시초가 100% 시장가 매도** (단순화, trailing 미적용) | 09:30 | prd_split_gapup |
| **flat** | -0.5% < 시가 < +0.5% | **09:30 시초가 100% 시장가 매도** (시뮬 일치) | 09:30 | prd_split_flat |
| **weak_gap_down** | -1% < 시가 ≤ -0.5% | 09:30 즉시 전량 시장가 매도 (반등 대기 X) | 09:30 | prd_split_flat |
| **trailing_stop** | (Phase 2-5 범위 외 — 단위 2-5g 후속) | - | - | - |

10:30 도달 시 미매도 잔량은 **strong force_close** (시장가 전량) 처리.

## 시뮬레이터 ↔ 실 매도 매트릭스 매핑 (P0-1 반영)
**walkforward EV +1.04% 정합성 보존 필수**. 시뮬레이터 3구간(`phase25_simulator.py` `_PRD_GAPUP_THRESHOLD=0.005`)과 실 매도 6단계 매핑:

- 시뮬 `prd_split_gapdown` (open ≤ -1%) ⇔ 실 `emergency_stop`
- 시뮬 `prd_split_gapup` (open ≥ +0.5%) ⇔ 실 `gap_up_high` + `gap_up_low` 합
- 시뮬 `prd_split_flat` (-1% < open < +0.5%) ⇔ 실 `flat` + `weak_gap_down` 합

**박제 결정**:
- 모든 6단계가 09:30 시초가 ~ 10:30 사이 단일 시장가 매도로 통일 → 시뮬 `open_pct` 가정과 정합
- `gap_up_high` 만 50% 분할 (시초가 50% + 10:30 시장가 50%, partial_ratio=0.6 적용)
- `trailing_stop` 은 본 단위 범위 외 (별도 단위 2-5g 분리, 09:30~10:30 폴링 루프 별도 설계 필요)

**완료 게이트** (단위 2-5e): 단위 2-5c 매도 액션 매트릭스 구현 후 `phase25_simulator.py` 의 매핑 정확성 재검증 (시뮬 EV vs 실 매도 가정 EV delta ≤ 0.1%).

## 단위 분할 (6단위)
- **2-5a** Step 0 사전 조사
  - KIS `get_current_price` 응답 open/high/low/price 필드 실호출 검증 (5/18 09:00~09:30 자연 검증 또는 dry_run 호출)
  - 매도 대상 SQL 쿼리 정확성 (status='entered' phase2 완료 + phase1 only 보유 식별)
  - **KIS 주문 취소 API 검증** (P1-4 force_close 미체결 취소 순서, `kis_order_api` 취소 엔드포인트 가능 여부)
  - 폴백 시나리오: 시가 조회 실패 / 현재가 조회 실패 / 부분 체결 잔량 처리
  - **sell_lock 결정 박제** (재사용 + owner 네임스페이스 `"closing_bet:emergency_stop|morning_exit|force_close"` 분리, 메인 봇 15:30 `clear_all()` 일괄 해제 보존)
- **2-5b** 시장 데이터 collector + 매도 대상 select + `log_exit` LookupError 해결 (P0-2)
  - `closing_bet_system/collectors/morning_price_collector.py` 신규 (async get_open_high_current)
  - `closing_bet_system/execution/exit_target_query.py` 신규 (보유 식별 SQL + `ExitTarget` dataclass)
  - **`closing_bet_system/storage/candidate_logger.py` 수정**: phase1 only 매도 대비 — `mark_entered_phase1_only(candidate_id)` 헬퍼 신규 (phase1_executed_price 그대로 entry_price 박제) OR `log_exit` 시그니처 `force_entry_price` 파라미터 확장. **옵션 A (헬퍼 신규) 권장** — 데이터 정합성 (entry_price 채워진 상태로 기록 보존)
- **2-5c** ExitExecutor 클래스 + 매도 액션 매트릭스 매핑
  - `closing_bet_system/execution/exit_executor.py` 신규 (**~600~700줄** — entry_executor 684줄 실측 + ExitAction 6단계)
  - `ExitExecutorSettings` frozen + `ExitAction` Enum (5단계: emergency_stop / gap_up_high / gap_up_low / flat / weak_gap_down — trailing_stop 분리) + `ExitResult` dataclass
  - dry_run 토글 (entry_executor 일관성: KIS sell 호출 + log_exit 모두 건너뜀, 별도 simulated_exit 로그 dict + 텔레그램 "[DRY-RUN] would have sold" 발송)
  - 부분 체결 잔량 처리 (`total_shares = phase1_executed_shares + phase2_executed_shares`)
  - `_save_exit_order_id` 발주 직후 ODNO 즉시 박제 (idempotency)
  - **emergency_stop 발주 직후 `exit_time` 또는 별도 `exit_in_progress` 플래그 즉시 박제** (09:30 morning_exit이 같은 종목 재발주 방지, P1-2)
  - phase1 only 매도 시 `mark_entered_phase1_only` 선행 호출 → `log_exit` 호출
- **2-5d** APScheduler 잡 통합 (3개 잡, 시점 결정 P0-3 반영)
  - `run_emergency_stop_check` (**09:01 cron** — 메인 봇 09:00 monitoring_start_early + midweek_sell_profit race 회피) — misfire_grace_time=60, coalesce=True
  - `run_morning_exit` (09:30 cron) — misfire_grace_time=300, coalesce=True (10:30 force_close 안전망)
  - `run_morning_force_close` (10:30 cron) — misfire_grace_time=120, coalesce=True. **미체결 주문 취소 → 취소 확인 → 시장가 재발주 순서** (P1-4)
  - 상수 박제: `EMERGENCY_STOP_SCHEDULE_HOUR=9, _MINUTE=1` / `MORNING_EXIT_HOUR=9, _MINUTE=30` / `MORNING_FORCE_CLOSE_HOUR=10, _MINUTE=30`
- **2-5e** 단위 테스트 30~40건 + dry_run 통합 단발 + **시뮬레이터 정합성 재검증** (단위 2-5c 매트릭스 vs phase25_simulator 매핑 delta ≤ 0.1%)
- **2-5f** 단위 2-4f와 묶어서 단발 실전 활성화 (별도 세션)
- **2-5g (분리)** trailing_stop 모니터링 루프 (09:30~10:30 폴링 또는 2분 cron 추가 잡, 별도 단위로 분리)

## 변경 파일 요약 (신규 + 수정)
**신규 5개**:
- `closing_bet_system/execution/exit_executor.py` (~600~700줄)
- `closing_bet_system/execution/exit_target_query.py`
- `closing_bet_system/collectors/morning_price_collector.py`
- `closing_bet_system/notification/exit_notifier.py` (3종 메서드: `send_emergency_stop_result / send_morning_exit_result / send_force_close_result(result, dry_run)`)
- `scripts/test_exit_executor.py` + `scripts/test_morning_price_collector.py` + `scripts/test_exit_target_query.py`

**수정 4개**:
- `closing_bet_system/main_orchestrator.py` (run_emergency_stop_check + run_morning_exit + run_morning_force_close + 잡 3건 등록 + lazy property, 잡 로그 "5건 → 8건")
- `closing_bet_system/config/settings.yaml` (`morning_exit:` 섹션 추가 — enabled / dry_run / emergency_stop_enabled / use_sell_lock 등 5~8개 키. 시점은 `schedule.emergency_stop_start: "09:00"` → `"09:01"` 갱신)
- `closing_bet_system/storage/candidate_logger.py` (`mark_entered_phase1_only(candidate_id)` 헬퍼 신규, P0-2)
- `closing_bet_system/storage/db.py` (**v4 마이그레이션 default 불필요** — phase1+phase2 합으로 잔량 계산 가능. exit_action 컬럼 추가는 단위 2-5g 후속으로 분리)

**회귀 테스트 갱신**:
- `scripts/test_closing_bet_orchestrator.py` (잡 8건 + 신규 3개 상수 + 09:01 race 회피 검증)
- `scripts/test_closing_bet_candidate_logger.py` (`mark_entered_phase1_only` 단위 테스트 추가)

## 롤백
- 워크트리 격리 작업, 단위별 commit
- 실전 활성화 시 `settings.yaml morning_exit.enabled=false` + systemctl restart

## 완료 기준 (단위 2-5e 종료 시점)
- 단위 테스트 신규 30~40건 PASS (회귀 누적 170+건)
- code-tester 심각 0건 / 주의 ≤3건
- dry_run 통합 단발 성공 (KIS 매도 미발주 + 텔레그램 알림 발화)
- 단위 2-4f + 2-5f 묶어서 활성화 게이트 조건 충족 (1주 dry_run 데이터 + 사용자 승인)

## 의존 단위
- **단위 2-4** EntryExecutor (완료, 5/15 commit `fe5f54c`)
- **단위 2-5b**가 단위 2-4 candidates 컬럼(entry_phase1/2_executed_*) 직접 select
- **PRD 10-1 시가 액션 매트릭스 박제값** settings.yaml exit:* (변경 시 백테스트 walkforward 재실행 필요)

## 비고
- 본 PLAN은 큰 단위 — 본 세션에서 3문서만 작성, 구현은 별도 세션에서 strategy-planner + strategy-coder 병렬 사전 리뷰 거친 후 진입
- 단위 2-5c 의 ExitAction Enum 매핑 정확성이 핵심 — strategy-planner 사전 리뷰 필수
