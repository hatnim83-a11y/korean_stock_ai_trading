# Phase 2.5 EV 리포트 — 2026-05-04 ~ 2026-05-13

_생성: 2026-05-14 22:27:36 KST_
_평가 정책: **prd_split_realistic**_

## 🟢 단위 2-8 게이트: PASS (정책=prd_split_realistic)

## 데이터 요약

- 라벨링 후보 수: **103건**
- 시뮬레이션 가능: **103건** (excluded 0건 제외)
- 거래일 수: **6일**

## 1. Walk-Forward EV 안정성

- 학습 윈도우: 5일 / 테스트 윈도우: 1일
- 윈도우 수: **1**
- 평균 윈도우 EV: **+1.5221%**
- 표준편차: 0.0000%
- 안정 윈도우 비율 (EV>0): **100.0%**
- 윈도우 EV 시계열: +1.522%

## 2. 점수 구간별 EV 분포

| total_score | n_total | n_simulated | net_ev | mean_profit | mean_loss |
|---|---|---|---|---|---|
| 0 | 7 | 7 | +0.7961% | +0.0000% | +0.0000% |
| 1 | 30 | 30 | -0.1378% | +0.0000% | +0.0000% |
| 2 | 42 | 42 | +1.1850% | +0.0000% | +0.0000% |
| 3 | 20 | 20 | +2.7754% | +0.0000% | +0.0000% |

## 3. Sharpe / Sortino / Max Drawdown

- 거래일 수 (n_days): **6**
- 일별 평균 수익률: +1.2823%
- 일별 표준편차: 2.8372%
- 연환산 계수: 12 (월간=12)
- **Sharpe**: +1.5656
- **Sortino**: +19.3421
- **Max Drawdown**: -2.1761%

## 4. 시나리오 정책 옵션 비교 (5종)

| 정책 | net_ev | n_sim | morning | stop | open | split | prd_gapup | prd_flat | prd_gapdown |
|---|---|---|---|---|---|---|---|---|---|
| **conservative** | -0.4846% | 103 | 42 | 59 | 2 | 0 | 0 | 0 | 0 |
| **aggressive** | -0.0588% | 103 | 62 | 39 | 2 | 0 | 0 | 0 | 0 |
| **split** | -0.2717% | 103 | 42 | 39 | 2 | 20 | 0 | 0 | 0 |
| **prd_split_optimistic** | +1.6438% | 103 | 0 | 0 | 0 | 0 | 57 | 19 | 27 |
| **prd_split_realistic** | +1.0368% | 103 | 0 | 0 | 0 | 0 | 57 | 19 | 27 |

- 옵션 B - 옵션 A delta: **+0.4259%** (낙관 우세)
- PRD optimistic - realistic delta: **+0.6069%** (잔여 50% 청산가 가정 영향)
- **운영 EV 기대치 구간: +1.0368% ~ +1.6438%** (realistic ~ optimistic)

## 5. 라벨 분포 + 시뮬 시나리오 분포

| 시나리오 | 건수 |
|---|---|
| morning_exit | 0 |
| stop_risk | 0 |
| market_open | 0 |
| split | 0 |
| prd_split_gapup | 57 |
| prd_split_flat | 19 |
| prd_split_gapdown | 27 |
| excluded | 0 |

## 6. score 임계 × 정책 게이트 매트릭스 (단위 2-4 진입 결정 근거)

score 임계별 필터링 후 5개 정책 각각의 게이트 PASS/FAIL 매트릭스 (총 15셀).

| score 필터 | 정책 | n_sim | net_ev | W/L | Sharpe | 결과 |
|---|---|---|---|---|---|---|
| 전체 | conservative | 103 | -0.4846% | 0.56 | -2.85 | 🔴 FAIL |
| 전체 | aggressive | 103 | -0.0588% | 0.56 | -0.13 | 🔴 FAIL |
| 전체 | split | 103 | -0.2717% | 0.56 | -1.53 | 🔴 FAIL |
| 전체 | prd_split_optimistic | 103 | +1.6438% | ∞ | +2.00 | 🟢 PASS |
| 전체 | prd_split_realistic | 103 | +1.0368% | ∞ | +1.57 | 🟢 PASS |
| ≥2 | conservative | 66 | -0.3102% | 0.56 | -1.10 | 🔴 FAIL |
| ≥2 | aggressive | 66 | +0.1217% | 0.56 | +1.10 | 🔴 FAIL |
| ≥2 | split | 66 | -0.0943% | 0.56 | -0.11 | 🔴 FAIL |
| ≥2 | prd_split_optimistic | 66 | +2.3883% | ∞ | +2.49 | 🔴 FAIL |
| ≥2 | prd_split_realistic | 66 | +1.5963% | ∞ | +2.08 | 🔴 FAIL |
| ≥3 | conservative | 24 | -0.0361% | 0.56 | +0.40 | 🔴 FAIL |
| ≥3 | aggressive | 24 | +0.4208% | 0.56 | +2.61 | 🔴 FAIL |
| ≥3 | split | 24 | +0.1924% | 0.56 | +1.33 | 🔴 FAIL |
| ≥3 | prd_split_optimistic | 24 | +3.1530% | ∞ | +2.99 | 🔴 FAIL |
| ≥3 | prd_split_realistic | 24 | +2.3160% | ∞ | +2.78 | 🔴 FAIL |

**해석 가이드**:
- 표본<100건은 자동 FAIL되지만 EV/W-L/Sharpe 개선 추세로 정성 판단 가능
- score≥2 / score≥3 임계 진입 시 EV 양수 + Sharpe 개선이면 축소 포지션 진입 근거

## 7. Limitation — PRD 분할매도 시뮬 가정

PRD 10-1 시가 액션 매트릭스는 다음날 09:00~09:30 사이 분 단위 가격 추이에
따라 분할매도가 발화한다. 현 라벨 데이터는 시점 3개(시초가/09:30 고가/09:30 저가)만
저장되어 있어 다음 가정으로 시뮬:

- **prd_split_optimistic**: 갭업(open ≥ +0.5%) 시 잔여 50% × `morning_high_pct` 청산
  - 09:00~09:30 고점을 정확히 잡는다는 낙관적 가정 → EV **상한** 추정치
- **prd_split_realistic**: 갭업 시 잔여 50% × `(open + morning_high) / 2` 청산
  - 시초가와 고가의 중간값에서 청산되는 현실적 가정 → EV **하한** 추정치
- 보합/약갭다운/갭다운(open < +0.5%)은 100% 시초가 매도 (현 라벨 데이터로 충분 정합)

**운영 시 실제 EV는 realistic ~ optimistic 구간 사이에 위치**할 것으로 예상.
분 단위 데이터 수집 인프라(단위 2-1 orderbook_snapshots 누적) 진척 시 정밀화 가능.

---

_본 리포트는 단위 2-7c walk-forward 분석 인프라 산출물이다. 단위 2-8 100건 자동화 게이트 합격 판정 근거이며, 자동매매 진입 결정은 본 단위에서 하지 않는다._
