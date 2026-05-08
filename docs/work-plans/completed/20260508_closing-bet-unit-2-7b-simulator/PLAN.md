# PLAN — 단위 2-7b · Phase 2.5 백테스트 시뮬레이터

## 목표
단위 2-7a `phase25_data_loader` 출력 DataFrame을 입력으로, **PRD 12-1 라벨 4종 + 12-2 EV 계산식**에 따라 후보별 가상 PnL 및 전체 EV를 계산하는 시뮬레이터 모듈을 신설한다. 단위 2-7c walk-forward 분석에 EV 측정값 제공이 본 단위의 출력.

## 배경
- 단위 2-7a 데이터 로더 완료 (5/8, 커밋 b38ee88)
- candidate_labels 누적 37건 → 5/11 라벨링 후 56건 도달 예정
- PRD 12-1 라벨 정의 + 12-2 EV 계산식 직접 구현 단계
- **자동매매 코드 0줄** (Phase 1 알림형 안전 유지)

## 핵심 설계 결정

### 1. 시나리오 매핑 (라벨 → 매도가)
PRD 12-1 라벨 4종이 모두 데이터에 존재하므로 시뮬레이션 시 매도 시나리오를 라벨 기반으로 결정:

| 우선순위 | 조건 | 매도가 가정 | 사유 |
|---|---|---|---|
| 1 | `label_stop_risk=True` | 진입가 × (1 - 1.0%) (PRD 12-1 임계) | 손절이 먼저 발동되는 보수적 가정 (loss aversion) |
| 2 | `label_morning_exit=True` (and not stop_risk) | 진입가 × (1 + 1.2%) (PRD 12-1 임계) | 익절선 도달 — 임계값에서 청산 |
| 3 | 둘 다 False | 진입가 × (1 + `next_open_pct`) | 시가 청산 (둘다 미충족 = 갭 부족) |
| 4 | 라벨 없음 (NULL) | 시뮬레이션 제외 | 라벨링 누락 종목 |

**우선순위 2번 옵션 검토** (별도 결정):
- **옵션 A (보수)**: stop_risk 우선 (현 PLAN)
- **옵션 B (낙관)**: morning_exit 우선 (체결 가정 가능)
- **옵션 C (시간 기반)**: 둘 다 True 시 50/50 분할

→ **본 단위는 A 채택** (PRD 12-2 "EV > 0 진입 허용" 보수적 의도 정합). 옵션 B/C는 단위 2-7c에서 비교 분석.

### 2. 진입가 정의
- **PRD 의도**: 14:30~15:30 종가베팅 → 종가 진입
- candidate.entry_price (실제 매수가)는 Phase 1에서 NULL → 사용 불가
- **본 단위에서**: 라벨링 시점 가정 — `next_open_pct`/`next_morning_high_pct` 기준값(전일 종가)을 진입가 100으로 정규화하고 백분율 사용
- 절대값 PnL 계산 시 가상 buy_price=10000원 가정 (1주 단위 검증용)

### 3. EV 계산 (PRD 12-2 정합)
```
EV = P(Morning Exit) × 평균 익절 수익률
   - P(Stop Risk) × 평균 손실률
   - 거래비용 (cost_engine.round_trip_cost)
   - 슬리피지 (편도 × 2)
```
cost_slippage_engine 직접 활용 (이미 구현됨).

## 모듈 신설
- `closing_bet_system/backtest/phase25_simulator.py` (신규, ~280줄 예상)
  - Public:
    - `simulate_candidate(row: dict, *, scenario_policy="conservative", cost_engine=None) -> SimulationResult`
    - `simulate_dataset(df: pd.DataFrame, *, scenario_policy="conservative", cost_engine=None) -> pd.DataFrame`
    - `compute_ev(simulated_df: pd.DataFrame) -> EVReport`
  - Dataclass:
    - `SimulationResult` (per-candidate: scenario, exit_price, net_pnl_pct, cost_breakdown)
    - `EVReport` (P_exit, P_stop, mean_profit, mean_loss, raw_ev, net_ev, n_simulated)

## 변경 파일
| 파일 | 변경 유형 | 비고 |
|---|---|---|
| `closing_bet_system/backtest/phase25_simulator.py` | 신규 | ~280줄 |
| `scripts/test_phase25_simulator.py` | 신규 | ~350줄, 단위 테스트 18건+ |

## 구현 단계

### Step 1. Dataclass 정의
- `SimulationResult` — per-candidate 시뮬 결과
- `EVReport` — 전체 데이터셋 EV 통계

### Step 2. `simulate_candidate(row)` 단일 행 시뮬레이터
- 라벨 NULL 체크 → 제외 마킹
- 시나리오 매핑 (우선순위 1→2→3)
- `cost_engine.compute_pnl(buy_price, exit_price, shares=1)` 호출
- SimulationResult 반환

### Step 3. `simulate_dataset(df)` 전체 데이터셋 시뮬레이터
- `df.apply(simulate_candidate)` 순회
- 결과 컬럼 추가 (`scenario`, `simulated_exit_price`, `simulated_net_pnl_pct`, `simulated_excluded`)
- 원본 df 보존 (copy)

### Step 4. `compute_ev(simulated_df)` EV 계산
- `simulated_excluded=False` 행만 사용
- P(Morning Exit), P(Stop Risk) 계산
- 평균 익절/손실 수익률 계산 (PRD 12-2 정합)
- raw_ev = P_exit × profit - P_stop × loss
- net_ev = raw_ev - cost_engine.round_trip_cost(include_slippage=True)
- EVReport 반환

### Step 5. 단위 테스트 18건+
- SC-1~6: simulate_candidate 시나리오별
- DS-1~5: simulate_dataset 통합
- EV-1~5: compute_ev 정합성
- EDGE-1~3: NULL 라벨/0건/이상값

### Step 6. 통합 검증
- 실제 DB 5/4~5/7 라벨링 33건 시뮬 → EV 출력
- 5/4 19건(84% EV+) vs 5/7 17건(24% EV+) 시장 차이 정량 확인

## 완료 기준
1. py_compile 통과 (신규 모듈)
2. 단위 테스트 18건+ PASS
3. code-tester 심각 이슈 0건
4. 통합 검증: 실제 DB 5/4~5/7 EV 정량 출력 + 시장 차이 확인
5. 단위 2-7c walk-forward 분석이 호출할 수 있는 인터페이스 확정

## 롤백
- 신규 모듈 단독 (시스템 영향 0)
- `git revert <commit>` 또는 신규 파일 삭제로 즉시 롤백

## 위험
- **매우 낮음** — 읽기 전용 시뮬레이터, 자동매매 X
- 데이터 출처 단위 2-7a (이미 검증)
- cost_engine은 기존 검증된 모듈 (Phase 1 1-1 단위)

## 다음 단위 (별도)
- **단위 2-7c** walk-forward 리포트: 시간 분할 EV 안정성 / 점수 구간별 EV 분포 / Sharpe 비율
- **단위 2-8** 100건 자동화 게이트 (별도 100건 누적 후)

## 비범위 (본 단위 X)
- walk-forward 시간 분할 → 단위 2-7c
- 점수 구간별 EV 분석 → 단위 2-7c
- 자동매매 진입 결정 → 단위 2-4/2-5 (100건 게이트 후)
- ML 학습용 train/test split → Phase 3+
