# PLAN — 단위 2-7c · Phase 2.5 walk-forward 분석 리포트

## 목표
단위 2-7b 시뮬레이터 출력을 입력으로, 시간 분할 walk-forward EV 안정성 + 점수 구간별 EV 분포 + Sharpe 비율 + 시나리오 정책 옵션 비교 분석을 수행하는 리포트 생성기를 신설한다. **단위 2-8 100건 자동화 게이트 합격선 판정 근거**.

## 배경
- 단위 2-7a 데이터 로더 완료 (5/8, 커밋 b38ee88)
- 단위 2-7b 시뮬레이터 완료 (5/8, 커밋 5b79a8c) — **단일 기간 EV 측정 완료**
- 통합 검증에서 **시장 변동성에 EV 매우 민감 정량 확인** (-1.43%p delta)
  → 단일 기간 EV는 시장 환경 의존성 높음 → walk-forward로 안정성 측정 필수
- candidate_labels 누적 37건 (5/11 라벨링 후 56건 도달 예정)
- **자동매매 코드 0줄** (Phase 1 알림형 안전 유지)

## PRD 12-2 / 단위 2-8 게이트 기준

| 지표 | 합격 기준 |
|---|---|
| 평균 EV | ≥ +0.5% (PRD 12-2 "강한 진입") |
| Win/Loss ratio | ≥ 1.3 (mean_profit / abs(mean_loss)) |
| 월간 Sharpe | ≥ 1.0 |
| 표본 수 | ≥ 100건 |

## 핵심 분석 5종

### 1. Walk-Forward EV 안정성
- **목적**: 시간 흐름에 따른 EV 일관성 측정
- **방법**: 학습 윈도우 5일 → 테스트 1일 슬라이딩
  - 단, 5/8 시점 데이터 33건 (5/4 19 + 5/7 18)으로는 윈도우 부족
  - **본 단위는 인프라만 구축, 실측 결과는 100건 누적 후**
- **출력**: 윈도우별 EV 시계열 + 평균 / 표준편차

### 2. 점수 구간별 EV 분포
- **목적**: total_score 0/1/2/3 구간별 EV 차이 측정 (점수 모델 유효성 검증)
- **방법**: groupby total_score → 시뮬 → EVReport
- **출력**: 구간별 EV 표 + "고득점일수록 EV 높은가?" 답

### 3. Sharpe 비율 / 변동성
- **목적**: 단순 EV가 아닌 risk-adjusted return 측정
- **방법**: 일별 평균 net_pnl_pct → mean / std → Sharpe (월간 환산)
- **출력**: Sharpe / Sortino / Max Drawdown

### 4. 시나리오 정책 옵션 비교
- **목적**: 단위 2-7b "conservative"(stop_risk 우선) vs 다른 옵션 비교
- **방법**:
  - Option A: stop_risk 우선 (현재, 보수)
  - Option B: morning_exit 우선 (낙관)
  - Option C: 50/50 분할 (중립)
- **출력**: 옵션별 EV 비교 + 어느 정책이 최적인지

### 5. 라벨 분포 + 시뮬 일치도
- **목적**: 데이터 누적 추세 모니터링
- **방법**: 일별 라벨 분포 + 시뮬 시나리오 분포 시계열
- **출력**: 시계열 차트 (matplotlib) + CSV

## 모듈 신설
- `closing_bet_system/backtest/phase25_walkforward.py` (신규, ~350줄 예상)
  - Public:
    - `walkforward_analysis(start_date, end_date, *, train_days=5, test_days=1, ...) -> WalkforwardReport`
    - `score_bucket_analysis(simulated_df) -> dict[int, EVReport]`
    - `compute_sharpe(simulated_df, *, annualize=12) -> SharpeMetrics`
    - `compare_scenario_policies(df) -> dict[str, EVReport]`
    - `generate_report(start_date, end_date, *, output_path=None) -> str` (md 리포트 통합)
  - Dataclass: `WalkforwardReport`, `SharpeMetrics`
- `scripts/test_phase25_walkforward.py` (신규, ~300줄, 단위 테스트 15건+)
- (선택) `scripts/run_phase25_report.py` 단발 실행 + md 출력

## 단위 2-7b 옵션 B/C 추가
단위 2-7b의 `_VALID_SCENARIO_POLICIES = ("conservative",)` 를 확장:
- `"aggressive"`: morning_exit 우선
- `"split"`: 50/50 분할 (확률적, 또는 두 결과 평균)

옵션 B/C는 본 단위 2-7c에서 활성화 (테스트 + 비교 분석에 필요).

## 변경 파일
| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `closing_bet_system/backtest/phase25_simulator.py` | 수정 | 옵션 B/C 정책 추가 (~30줄 추가) |
| `closing_bet_system/backtest/phase25_walkforward.py` | 신규 | ~350줄 |
| `scripts/test_phase25_simulator.py` | 수정 | 옵션 B/C 테스트 추가 (~5건) |
| `scripts/test_phase25_walkforward.py` | 신규 | ~300줄, 15건+ |
| `scripts/run_phase25_report.py` | 신규 (선택) | CLI 단발 실행 + md 출력 |

## 구현 단계

### Step 1. 단위 2-7b 정책 옵션 B/C 추가
- `_map_scenario` 분기 확장
- _VALID_SCENARIO_POLICIES = ("conservative", "aggressive", "split")
- 단위 테스트 SC-9, SC-10 추가

### Step 2. score_bucket_analysis 구현
- groupby total_score → simulate_dataset → compute_ev
- 단위 테스트 BUCKET-1~3

### Step 3. compute_sharpe 구현
- 일별 mean / std → Sharpe / Sortino
- annualize=12 (월간 환산), 5 (주간), 252 (연간) 옵션
- 단위 테스트 SHARPE-1~3

### Step 4. compare_scenario_policies 구현
- 3 옵션 동일 데이터셋에 적용 → EVReport 비교
- 단위 테스트 POLICY-1~2

### Step 5. walkforward_analysis 구현
- train/test 윈도우 슬라이딩
- 데이터 부족 시 graceful 처리 (n_simulated=0 윈도우 스킵)
- 단위 테스트 WF-1~3

### Step 6. generate_report (md 리포트)
- 5종 분석 통합 → markdown 출력
- 단위 2-8 게이트 기준 자동 판정 ("PASS" / "FAIL: EV < 0.5%")
- 단위 테스트 REPORT-1~2

### Step 7. CLI + 통합 검증
- 5/4~5/7 33건 분석 실행 (단위 2-8 게이트 미달 예상)
- md 리포트 생성 → docs/improvements/phase25_ev_report_YYYYMMDD.md

## 완료 기준
1. py_compile 통과 (신규 모듈)
2. 단위 테스트 15건+ PASS
3. code-tester 심각 이슈 0건
4. 통합 검증: 실제 DB 5/4~5/7 walk-forward 리포트 md 생성 + 단위 2-8 게이트 판정 출력
5. 옵션 A/B/C 비교에서 데이터 인사이트 1건 이상 도출 (예: "약세 시장에서는 옵션 A가 보수적이라 손실 적음")

## 롤백
- 단위 2-7b 정책 추가는 _VALID_SCENARIO_POLICIES 튜플만 되돌리면 회귀
- 단위 2-7c 신규 모듈 단독 — git revert 즉시
- 시스템 영향 0 (오프라인 분석 모듈)

## 위험
- **매우 낮음** — 읽기 전용 분석, 자동매매 X
- 데이터 부족(33건 < 100건) → walk-forward 윈도우 비어있을 가능성. graceful 처리 필요
- Sharpe 일별 계산 시 표본 수 적으면 신뢰도 낮음 → 신뢰구간 표시

## 다음 단위 (별도)
- **단위 2-8** 100건 자동화 게이트 — 본 리포트 산출물 기반 자동매매 진입 결정 (2026-05-28+ 예상)
- **단위 2-4/2-5** entry_executor / morning_exit_manager — 단위 2-8 통과 후

## 비범위 (본 단위 X)
- 자동매매 진입 결정 → 단위 2-8 (100건 게이트 + 사용자 승인)
- ML 학습 train/test split → Phase 3+
- regime_detector 시장 레짐 분류 → Phase 3 (단순 시계열만 본 단위)
