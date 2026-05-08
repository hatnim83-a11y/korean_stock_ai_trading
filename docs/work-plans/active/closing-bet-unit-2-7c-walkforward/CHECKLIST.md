# CHECKLIST — 단위 2-7c · Phase 2.5 walk-forward 분석 리포트

## 사전 확인
- [ ] 단위 2-7a 데이터 로더 완료 (커밋 b38ee88, 5/8)
- [ ] 단위 2-7b 시뮬레이터 완료 (커밋 5b79a8c, 5/8)
- [ ] candidate_labels 누적 50+건 확보 (5/11 라벨링 후 56건 예상)
- [ ] PLAN.md / CONTEXT.md 사용자 승인

## 구현 항목

### Step 1 — 단위 2-7b 정책 옵션 B/C 추가

#### 1.1 phase25_simulator.py 수정
- [ ] `_VALID_SCENARIO_POLICIES = ("conservative", "aggressive", "split")` 확장
- [ ] `_map_scenario` 분기 추가:
  - `"aggressive"`: morning_exit 우선
  - `"split"`: 50/50 — 두 시나리오 결과 평균 (또는 확률적)
- [ ] 모듈 docstring 업데이트 (옵션 3종 명시)

#### 1.2 옵션 B/C 단위 테스트 추가 (test_phase25_simulator.py)
- [ ] **SC-9**: policy="aggressive", 양립 라벨 → morning_exit 우선
- [ ] **SC-10**: policy="split", 양립 라벨 → 평균 또는 확률적 처리
- [ ] **SC-11**: 옵션 A/B/C 동일 단일 라벨 → 동일 결과 (회귀)

### Step 2 — `closing_bet_system/backtest/phase25_walkforward.py` 신설

#### 2.1 모듈 docstring + import
- [ ] PRD 12-2 / 단위 2-8 게이트 기준 명시
- [ ] `_PROJECT_ROOT` sys.path 패턴
- [ ] 단위 2-7a / 2-7b import

#### 2.2 모듈 상수
- [ ] `_DEFAULT_TRAIN_DAYS = 5`
- [ ] `_DEFAULT_TEST_DAYS = 1`
- [ ] `_DEFAULT_ANNUALIZE_FACTOR = 12`  # 월간 환산 default
- [ ] 단위 2-8 게이트: `_GATE_EV_THRESHOLD = 0.005`, `_GATE_WIN_LOSS_RATIO = 1.3`, `_GATE_SHARPE = 1.0`, `_GATE_MIN_SAMPLES = 100`

#### 2.3 Dataclass
- [ ] `WalkforwardReport` (frozen)
  - n_windows: int
  - window_evs: list[float]  # 윈도우별 EV
  - mean_window_ev: float
  - std_window_ev: float
  - stable_windows_pct: float  # EV 양수 윈도우 비율
- [ ] `SharpeMetrics` (frozen)
  - daily_mean: float
  - daily_std: float
  - sharpe: float (annualize 적용)
  - sortino: float (downside std)
  - max_drawdown: float
  - n_days: int

#### 2.4 `score_bucket_analysis(simulated_df) -> dict[int, EVReport]`
- [ ] groupby total_score → simulate_dataset 호출 → compute_ev
- [ ] 빈 버킷 graceful 처리

#### 2.5 `compute_sharpe(simulated_df, *, annualize=12) -> SharpeMetrics`
- [ ] 일별 mean / std
- [ ] Sharpe / Sortino / Max Drawdown
- [ ] n_days < 5 시 신뢰도 낮음 warning 로그

#### 2.6 `compare_scenario_policies(df) -> dict[str, EVReport]`
- [ ] 3 옵션 동일 데이터셋 적용 → EVReport 비교
- [ ] 각 옵션 differential 출력

#### 2.7 `walkforward_analysis(start_date, end_date, *, train_days, test_days, ...) -> WalkforwardReport`
- [ ] 학습 윈도우 → 테스트 1일 슬라이딩
- [ ] 데이터 부족 시 윈도우 스킵 + n_windows 카운트
- [ ] 빈 결과 graceful

#### 2.8 `generate_report(start_date, end_date, *, output_path=None) -> str`
- [ ] 5종 분석 통합 → markdown 출력
- [ ] 단위 2-8 게이트 자동 판정 ("PASS" / "FAIL: <사유>")
- [ ] output_path 미지정 시 `docs/improvements/phase25_ev_report_YYYYMMDD.md`

#### 2.9 CLI 진입점
- [ ] `python -m closing_bet_system.backtest.phase25_walkforward --start ... --end ... --output ...`

#### 2.10 py_compile 통과

### Step 3 — 단위 테스트 `scripts/test_phase25_walkforward.py` 15건+

#### 3.1 score_bucket_analysis (3건)
- [ ] **BUCKET-1**: 정상 dataframe → 점수별 EVReport dict
- [ ] **BUCKET-2**: 빈 버킷 graceful (모든 행이 score=3) → score 0/1/2 빈 EVReport
- [ ] **BUCKET-3**: total_score 컬럼 누락 → ValueError 또는 graceful

#### 3.2 compute_sharpe (3건)
- [ ] **SHARPE-1**: 정상 시계열 → Sharpe 양수
- [ ] **SHARPE-2**: 모두 손실 → Sharpe 음수
- [ ] **SHARPE-3**: n_days=1 → 표준편차 0 → Sharpe inf 가드

#### 3.3 compare_scenario_policies (2건)
- [ ] **POLICY-1**: 양립 라벨 없는 데이터 → 옵션 A/B/C 동일 결과 (회귀)
- [ ] **POLICY-2**: 양립 라벨 있는 데이터 → 옵션 A/B 결과 다름

#### 3.4 walkforward_analysis (3건)
- [ ] **WF-1**: 정상 윈도우 → 윈도우 시계열 EV 출력
- [ ] **WF-2**: 데이터 부족 → 윈도우 0 graceful
- [ ] **WF-3**: 윈도우별 stable_windows_pct 정확

#### 3.5 generate_report (2건)
- [ ] **REPORT-1**: md 리포트 5섹션 포함 (walkforward / bucket / sharpe / policy / labels)
- [ ] **REPORT-2**: 단위 2-8 게이트 판정 자동 ("PASS" / "FAIL: <사유>")

#### 3.6 옵션 B/C 추가 단위 2-7b 테스트 (3건, 위 1.2 참조)

### Step 4 — code-tester 검증
- [ ] code-tester 에이전트 호출 (수정 1개 + 신규 2개 대상)
- [ ] 심각 이슈 0건
- [ ] 단위 2-7b 옵션 B/C 추가 회귀 영향 없음
- [ ] Sharpe 계산 numpy 정확도 검증
- [ ] groupby 분기 NaN 처리 검증

## 검증 항목

### 단위 테스트
- [ ] 15건+ PASS (BUCKET 3 + SHARPE 3 + POLICY 2 + WF 3 + REPORT 2 + 옵션 B/C 3 = 16건+)
- [ ] py_compile (phase25_walkforward.py / 수정된 phase25_simulator.py)

### 통합 검증 (단발)
- [ ] 5/4~5/7 33건 분석 실행
- [ ] 옵션 A vs B 차이 정량 출력
- [ ] 점수 구간별 EV 분포 출력
- [ ] Sharpe 신뢰구간 (n_days 적음 명시)
- [ ] md 리포트 생성 → `docs/improvements/phase25_ev_report_20260508.md`

### 단위 2-8 게이트 모의 판정
- [ ] 5/4~5/7 33건 → 게이트 미달 출력 ("FAIL: 표본 부족 33<100" 등)
- [ ] 5/11 후 56건 → 데이터 누적 후 재실행 (별도 단계)

## 배포 항목
- [ ] systemd 무관 (오프라인 분석 모듈) → 재시작 불필요
- [ ] 변경 파일 git stage
  - `closing_bet_system/backtest/phase25_simulator.py` (수정)
  - `closing_bet_system/backtest/phase25_walkforward.py` (신규)
  - `scripts/test_phase25_simulator.py` (수정)
  - `scripts/test_phase25_walkforward.py` (신규)
  - (선택) `scripts/run_phase25_report.py`
  - `docs/improvements/phase25_ev_report_20260508.md` (생성된 리포트)
- [ ] git commit + push

## 문서 업데이트 항목
- [ ] `docs/improvements/change_log.md` 1줄 추가 (단위 2-7c)
- [ ] `memory/project_closing_bet_followups.md` 단위 2-7c 완료 + 단위 2-8 진입 표시
- [ ] (선택) `memory/project_closing_bet_system.md` 2-7c 단락 추가

## 완료 게이트 (선언 전 체크)
- [ ] 사전 확인 항목 전부 `[x]`
- [ ] Step 1~3 구현 항목 전부 `[x]`
- [ ] 단위 테스트 15건+ PASS
- [ ] code-tester 통과
- [ ] 통합 검증 + md 리포트 생성 전부 `[x]`
- [ ] 배포 항목 전부 `[x]`
- [ ] 문서 업데이트 항목 전부 `[x]`
- [ ] active → completed/YYYYMMDD_closing-bet-unit-2-7c-walkforward/ 아카이브

## 비범위 (명시)
- 자동매매 진입 결정 → **단위 2-8** (100건 게이트 + 사용자 승인)
- ML 학습 train/test split → Phase 3+
- regime_detector 시장 레짐 분류 → Phase 3
- 실시간 모니터링 → 단위 2-6 dashboard (완료)
