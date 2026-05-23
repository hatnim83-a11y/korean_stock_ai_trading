# PLAN — 자본 배분 동적 분리 (스윙 90% + 종가베팅 동적 풀)

## 목표
스윙과 종가베팅 자금 풀을 명시적으로 분리하고, 종가베팅이 스윙 유휴자본을 동적으로 흡수하여 유연한 자본 활용 + 명확한 한도 관리.

5단계 룰:
1. 스윙 90% (총자본 × 0.9)
2. 종가베팅 기본 10%
3. 종가베팅 진입 시점에 `풀 = 10% + 스윙 유휴자본` (동적)
4. 풀 ÷ 4 = 1종목, score 순 top 4
5. T+1 매도 → KIS 자동 환원 + 로그 명시

## 마스터 Plan
`/home/hatni/.claude/plans/gleaming-orbiting-truffle.md` 참조 (사용자 승인 완료)

## Phase 0 선행 검증 결과 (완료)
- 213건 라벨 분석 → score≥3 win rate 75.9%, score≥4 91.7%
- 매일 score≥2 후보 6~15건 → top 4 채우기 충분
- LIMIT 4 + score_threshold=2 유지 결정
- 분석 출력: `docs/improvements/score_ev_distribution_20260523.md`

## 변경 파일 (6개)
1. `closing_bet_system/config/settings.yaml` — fund 섹션 신규 키 5개
2. `closing_bet_system/infra/swing_db_reader.py` — `get_swing_used_value()` 신규
3. `closing_bet_system/infra/fund_guard.py` — `compute_capital_limit()` SoT + GuardConfig 확장
4. `closing_bet_system/execution/entry_executor.py` — `_compute_order_amount` 동적 한도 + LIMIT 4
5. `closing_bet_system/execution/exit_executor.py` — 환원 로그
6. `main.py:1340` — SWING_CAPITAL_RATIO 적용

## 신규 파일
- `scripts/test_dynamic_capital_allocation.py` — DC-1~9 + 5-B + 6-B + external_risk = 12 케이스

## 사용자 결정
- 작업 시점: 5/24(일) 내 완료 (실발주 D+1 함께 적용)
- cap: 50% (사용자 원안)
- score별 EV 검증: 선행 완료 (Phase 0)

## 에이전트 리뷰 반영 (심각 8건 + 주의 10건)
- strategy-planner: top 4 EV 검증 / CRISIS 흡수 역설
- strategy-coder: SoT compute_capital_limit / per_stock_limit 하드코딩 / SQL 스키마
- code-tester: 두 경로 동기화 / swing_used > pool / KeyError 방어

## 완료 기준
- 12개 단위 테스트 PASS
- code-tester 심각 0건
- 기존 fund_guard 테스트 회귀 X
- 5/24 22:30 KST systemctl restart 후 capital_limit 동적 로그 확인
- 5/25 15:18 첫 실발주 종목별 entry_amount 동적 사이즈 검증

## 롤백
1단: settings.yaml `absorb_swing_idle: false` + restart → 기존 동작
2단: `SWING_CAPITAL_RATIO=1.0` env → 스윙 100% 복귀
3단: git revert + restart
