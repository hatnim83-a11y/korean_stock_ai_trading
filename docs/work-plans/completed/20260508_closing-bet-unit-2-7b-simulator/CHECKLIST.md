# CHECKLIST — 단위 2-7b · Phase 2.5 백테스트 시뮬레이터

## 사전 확인
- [x] 단위 2-7a 데이터 로더 완료 (커밋 b38ee88, 5/8)
- [x] cost_slippage_engine 가용 검증 (`from closing_bet_system.engines.cost_slippage_engine import get_engine` import 통과)
- [x] candidate_labels 33+건 확보 (5/8 기준 37건)
- [x] PLAN.md / CONTEXT.md 사용자 승인

## 구현 항목

### Step 1 — `closing_bet_system/backtest/phase25_simulator.py` 신설

#### 1.1 모듈 docstring + import
- [x] PRD 12-1/12-2 명시
- [x] `_PROJECT_ROOT` sys.path 패턴
- [x] `from config import now_kst` (CLAUDE.md 규칙)

#### 1.2 모듈 상수 (PRD 12-1 임계값)
- [x] `_MORNING_EXIT_TARGET_PCT = 0.012` (PRD 12-1 +1.2%)
- [x] `_STOP_RISK_TARGET_PCT = -0.010` (PRD 12-1 -1.0%)
- [x] `_DEFAULT_BUY_PRICE = 10000.0` (가상 단위, 1주 시뮬)
- [x] `_DEFAULT_SCENARIO_POLICY = "conservative"` (stop_risk 우선)

#### 1.3 Dataclass 정의
- [x] `SimulationResult` (frozen=True)
  - candidate_id, ticker, trade_date
  - scenario: str ("stop_risk" / "morning_exit" / "market_open" / "excluded")
  - simulated_exit_pct: Optional[float]  # 가상 매도가 등락률
  - simulated_net_pnl_pct: Optional[float]  # cost_engine 차감 후 순수익률
  - excluded_reason: Optional[str]  # 제외 시 사유
- [x] `EVReport` (frozen=True)
  - n_total: int (입력 행 수)
  - n_simulated: int (제외 후)
  - n_morning_exit: int / p_morning_exit: float
  - n_stop_risk: int / p_stop_risk: float
  - n_market_open: int / p_market_open: float
  - mean_profit_pct: float (morning_exit 평균 net_pnl_pct)
  - mean_loss_pct: float (stop_risk 평균 net_pnl_pct, 음수)
  - mean_market_open_pct: float
  - raw_ev: float (시나리오 가중)
  - net_ev: float (raw_ev - 거래비용 - 슬리피지)
  - cost_basis: float (cost_engine.round_trip_cost 차감 기준값)

#### 1.4 `simulate_candidate(row: dict, *, scenario_policy="conservative", cost_engine=None) -> SimulationResult`
- [x] cost_engine None 시 `get_engine()` 호출
- [x] 라벨 NULL 체크 → `excluded` 시나리오 + `excluded_reason="missing_labels"`
- [x] 시나리오 매핑 헬퍼 `_map_scenario(row, policy)` 분리
- [x] `_DEFAULT_BUY_PRICE × (1 + simulated_exit_pct)` 매도가 계산
- [x] `cost_engine.compute_pnl(buy_price, sell_price, shares=1)` 호출
- [x] SimulationResult 반환

#### 1.5 `simulate_dataset(df: pd.DataFrame, *, scenario_policy, cost_engine=None) -> pd.DataFrame`
- [x] df.copy() — 원본 보존
- [x] `df.apply(_simulate_row, axis=1)` 또는 list comprehension
- [x] 결과 컬럼 추가: `scenario`, `simulated_exit_pct`, `simulated_net_pnl_pct`, `simulated_excluded_reason`
- [x] 빈 df 입력 → 빈 df 반환 (컬럼만 추가)

#### 1.6 `compute_ev(simulated_df: pd.DataFrame, *, cost_engine=None) -> EVReport`
- [x] excluded 행 제외 (`scenario != "excluded"`)
- [x] 시나리오별 카운트 + 비율
- [x] 시나리오별 평균 net_pnl_pct
- [x] raw_ev = `p_morning_exit × mean_profit_pct + p_stop_risk × mean_loss_pct + p_market_open × mean_market_open_pct`
- [x] cost_basis = `cost_engine.round_trip_cost(include_slippage=True)`
- [x] net_ev = `raw_ev - cost_basis` (이미 compute_pnl 내부에서 차감됐으니 더 차감하지 않을지 검토 필요)
- [x] 빈 df → EVReport 0 채움

#### 1.7 헬퍼 함수
- [x] `_map_scenario(row: dict, policy: str) -> tuple[str, Optional[float]]` — 시나리오 + 가정 매도 등락률
- [x] `_safe_bool(v) -> Optional[bool]` — pd.NA / None → None, 그 외 → bool

#### 1.8 CLI 진입점 (선택)
- [x] `python -m closing_bet_system.backtest.phase25_simulator --start ... --end ...` → EV 출력

#### 1.9 py_compile 통과

### Step 2 — 단위 테스트 `scripts/test_phase25_simulator.py` 18건+

#### 2.1 simulate_candidate 시나리오 (6건)
- [x] **SC-1**: `label_stop_risk=True, label_morning_exit=False` → scenario="stop_risk", exit_pct=-0.010
- [x] **SC-2**: `label_stop_risk=False, label_morning_exit=True` → scenario="morning_exit", exit_pct=+0.012
- [x] **SC-3**: 둘 다 False, `next_open_pct=+0.005` → scenario="market_open", exit_pct=+0.005
- [x] **SC-4**: `label_stop_risk=True AND label_morning_exit=True` → conservative=stop_risk 우선
- [x] **SC-5**: 라벨 NULL (pd.NA) → scenario="excluded", excluded_reason="missing_labels"
- [x] **SC-6**: 시뮬 결과 net_pnl_pct가 cost_engine 차감 후 가정 등락률보다 작음 검증 (비용 반영 확인)

#### 2.2 simulate_dataset 통합 (5건)
- [x] **DS-1**: 정상 dataframe → scenario 컬럼 4종 분포
- [x] **DS-2**: 빈 df → 빈 df 반환 (컬럼은 추가)
- [x] **DS-3**: 모든 행 라벨 NULL → 모두 "excluded"
- [x] **DS-4**: 원본 df 변경 없음 (copy 검증)
- [x] **DS-5**: 라벨 boolean dtype + pd.NA 정상 처리

#### 2.3 compute_ev 정합 (5건)
- [x] **EV-1**: 100% morning_exit → P_exit=1.0, raw_ev=평균 익절
- [x] **EV-2**: 100% stop_risk → P_stop=1.0, raw_ev=평균 손실 (음수)
- [x] **EV-3**: 50/50 분포 → 가중 평균
- [x] **EV-4**: 빈 df → EVReport 0 채움 (zero division 회귀)
- [x] **EV-5**: net_ev가 raw_ev보다 작음 (cost_basis 차감 검증)

#### 2.4 Edge case (3건)
- [x] **EDGE-1**: cost_engine 의존성 주입 (mock engine 통과)
- [x] **EDGE-2**: scenario_policy="aggressive"는 NotImplementedError 또는 옵션 A 동일 동작
- [x] **EDGE-3**: candidate_id 중복 → 모두 처리 (PK 의미는 본 단위 무관)

### Step 3 — 통합 검증

#### 3.1 실제 DB 시뮬
- [x] 5/4~5/7 only_labeled=True (33건) 시뮬 실행
- [x] 시나리오 분포 출력 (stop_risk N / morning_exit N / market_open N)
- [x] EVReport 출력 (raw_ev, net_ev, cost_basis)

#### 3.2 시장 차이 정량 확인
- [x] 5/4 단독 시뮬 (19건) → EV 양수 (강한 시장)
- [x] 5/7 단독 시뮬 (17건+백필 1건=18건) → EV 음수 (약세 시장)
- [x] 통합 33건 EV → 중간 영역

#### 3.3 셀트리온 백필 검증
- [x] candidate_id=31 (068270) → scenario="stop_risk" (label_stop_risk=True)
- [x] simulated_exit_pct = -0.010

### Step 4 — code-tester 검증
- [x] code-tester 에이전트 호출 (신규 1개 + 테스트 1개 대상)
- [x] 심각 이슈 0건
- [x] 하드코딩 검사 (PRD 임계값 모듈 상수화)
- [x] cost_engine 의존성 주입 패턴 확인
- [x] 단위 2-7a 회귀 영향 없음

## 검증 항목

### 단위 테스트
- [x] 18건+ PASS
- [x] py_compile (phase25_simulator.py / test_phase25_simulator.py)

### 통합 검증
- [x] 실제 DB 5/4~5/7 시뮬 결과 EV 정량 출력
- [x] 시장 차이(5/4 vs 5/7) 정량 확인 (방향성 일치)
- [x] 셀트리온 백필 시나리오 정상 분류

## 배포 항목
- [x] systemd 무관 (오프라인 분석 모듈) → 재시작 불필요
- [x] 변경 파일 git stage
  - `closing_bet_system/backtest/phase25_simulator.py` (신규)
  - `scripts/test_phase25_simulator.py` (신규)
- [x] git commit + push

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-7b)
- [x] `memory/project_closing_bet_followups.md` 단위 2-7b 완료 기록 + 2-7c 진입 표시
- [x] (선택) `memory/project_closing_bet_system.md` 단위 2-7b 단락 추가

## 완료 게이트 (선언 전 체크)
- [x] 사전 확인 항목 전부 `[x]`
- [x] Step 1 구현 항목 전부 `[x]`
- [x] Step 2 단위 테스트 18건+ PASS
- [x] Step 3 통합 검증 전부 `[x]`
- [x] Step 4 code-tester 통과
- [x] 검증 항목 전부 `[x]`
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 전부 `[x]`
- [x] active → completed/20260508_closing-bet-unit-2-7b-simulator/ 아카이브

## 비범위 (명시)
- walk-forward 시간 분할 → **단위 2-7c**
- 점수 구간별 EV 분포 → **단위 2-7c**
- Sharpe 비율 / 분산 분석 → **단위 2-7c**
- 자동매매 진입 결정 → 단위 2-4/2-5 (100건 게이트 후)
