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
| 분류 | 조건 | 액션 | 시점 |
|---|---|---|---|
| **emergency_stop** | 시가 ≤ -1% (hard_stop_loss) | 즉시 전량 시장가 매도 | 09:00~09:30 |
| **gap_up_high** | 시가 ≥ +2% (gap_up_high_threshold) | 50~70% 분할 매도 (partial_ratio=0.6) + 잔여 09:30 추적 | 09:00~09:30 |
| **gap_up_low** | +0.5% ≤ 시가 < +2% | trailing 모니터링 + 09:30~10:30 시간 매도 | 09:30~10:30 |
| **flat** | -0.5% < 시가 < +0.5% | 시간 매도 (09:30~10:30 균분 또는 10:30 일괄) | 09:30~10:30 |
| **weak_gap_down** | -1% < 시가 ≤ -0.5% | 09:30 즉시 전량 시장가 매도 (반등 대기 X) | 09:30 |
| **trailing_stop** | 강세장 진입 후 -1.5% 도달 | 즉시 전량 매도 | 09:30~10:30 |

10:30 도달 시 미매도 잔량은 **strong force_close** (시장가 전량) 처리.

## 단위 분할 (6단위)
- **2-5a** Step 0 사전 조사
  - KIS `get_current_price` 응답 open/high/low/price 필드 실호출 검증 (5/18 09:00~09:30 자연 검증 또는 dry_run 호출)
  - 매도 대상 SQL 쿼리 정확성 (option A: status='entered' phase2 완료 + phase1 only 보유 식별)
  - 폴백 시나리오: 시가 조회 실패 / 현재가 조회 실패 / 부분 체결 잔량 처리
- **2-5b** 시장 데이터 collector + 매도 대상 select
  - `closing_bet_system/collectors/morning_price_collector.py` 신규 (async get_open_high_current)
  - `closing_bet_system/execution/exit_target_query.py` 신규 (보유 식별 SQL + dataclass)
- **2-5c** ExitExecutor 클래스 + 매도 액션 매트릭스 매핑
  - `closing_bet_system/execution/exit_executor.py` 신규 (~500줄)
  - `ExitExecutorSettings` frozen + `ExitAction` Enum + `ExitResult` dataclass
  - dry_run 토글 / 부분 체결 잔량 처리 / log_exit 호출
- **2-5d** APScheduler 잡 통합 (3개 잡)
  - `run_emergency_stop_check` (09:00 cron) — hard_stop_loss 즉시 손절
  - `run_morning_exit` (09:30 cron) — 매도 액션 매트릭스 실행
  - `run_morning_force_close` (10:30 cron) — 미매도 잔량 시장가 전량
- **2-5e** 단위 테스트 30~40건 + dry_run 통합 단발
- **2-5f** 단위 2-4f와 묶어서 단발 실전 활성화 (별도 세션)

## 변경 파일 요약 (신규 + 수정)
**신규 4개**:
- `closing_bet_system/execution/exit_executor.py`
- `closing_bet_system/execution/exit_target_query.py`
- `closing_bet_system/collectors/morning_price_collector.py`
- `closing_bet_system/notification/exit_notifier.py`
- `scripts/test_exit_executor.py`

**수정 3개**:
- `closing_bet_system/main_orchestrator.py` (run_emergency_stop_check + run_morning_exit + run_morning_force_close + 잡 3건 등록 + lazy property)
- `closing_bet_system/config/settings.yaml` (`morning_exit:` 섹션 추가 — emergency_stop_enabled / dry_run / use_market_order / partial_sell_retry 등)
- `closing_bet_system/storage/db.py` (필요 시 v4 — candidates +N 컬럼: exit_action / exit_executed_phase / first_exit_price 등 — 단위 2-5b 설계 시 결정)

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
