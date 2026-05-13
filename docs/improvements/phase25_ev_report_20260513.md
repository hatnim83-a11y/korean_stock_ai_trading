# Phase 2.5 EV 리포트 — 2026-05-04 ~ 2026-05-12

_생성: 2026-05-13 18:19:45 KST_
_평가 정책: **prd_split_realistic**_

## 🔴 단위 2-8 게이트: FAIL (정책=prd_split_realistic)

**미달 사유**:
- 표본 부족 88<100

## 데이터 요약

- 라벨링 후보 수: **88건**
- 시뮬레이션 가능: **88건** (excluded 0건 제외)
- 거래일 수: **5일**

## 1. Walk-Forward EV 안정성

- 학습 윈도우: 5일 / 테스트 윈도우: 1일
- 윈도우 수: **0**
- ⚠️ 데이터 부족 (거래일 < train_days+test_days). 100건+ 누적 후 재실행

## 2. 점수 구간별 EV 분포

| total_score | n_total | n_simulated | net_ev | mean_profit | mean_loss |
|---|---|---|---|---|---|
| 0 | 7 | 7 | +0.7961% | +0.0000% | +0.0000% |
| 1 | 28 | 28 | -0.2069% | +0.0000% | +0.0000% |
| 2 | 38 | 38 | +1.1700% | +0.0000% | +0.0000% |
| 3 | 13 | 13 | +3.1661% | +0.0000% | +0.0000% |

## 3. Sharpe / Sortino / Max Drawdown

- 거래일 수 (n_days): **5**
- 일별 평균 수익률: +1.2343%
- 일별 표준편차: 3.1694%
- 연환산 계수: 12 (월간=12)
- **Sharpe**: +1.3491
- **Sortino**: +18.6184
- **Max Drawdown**: -2.1761%

## 4. 시나리오 정책 옵션 비교 (5종)

| 정책 | net_ev | n_sim | morning | stop | open | split | prd_gapup | prd_flat | prd_gapdown |
|---|---|---|---|---|---|---|---|---|---|
| **conservative** | -0.4770% | 88 | 36 | 50 | 2 | 0 | 0 | 0 | 0 |
| **aggressive** | -0.1280% | 88 | 50 | 36 | 2 | 0 | 0 | 0 | 0 |
| **split** | -0.3025% | 88 | 36 | 36 | 2 | 14 | 0 | 0 | 0 |
| **prd_split_optimistic** | +1.5564% | 88 | 0 | 0 | 0 | 0 | 48 | 14 | 26 |
| **prd_split_realistic** | +0.9541% | 88 | 0 | 0 | 0 | 0 | 48 | 14 | 26 |

- 옵션 B - 옵션 A delta: **+0.3489%** (낙관 우세)
- PRD optimistic - realistic delta: **+0.6023%** (잔여 50% 청산가 가정 영향)
- **운영 EV 기대치 구간: +0.9541% ~ +1.5564%** (realistic ~ optimistic)

## 5. 라벨 분포 + 시뮬 시나리오 분포

| 시나리오 | 건수 |
|---|---|
| morning_exit | 0 |
| stop_risk | 0 |
| market_open | 0 |
| split | 0 |
| prd_split_gapup | 48 |
| prd_split_flat | 14 |
| prd_split_gapdown | 26 |
| excluded | 0 |

## 6. score 임계 × 정책 게이트 매트릭스 (단위 2-4 진입 결정 근거)

score 임계별 필터링 후 5개 정책 각각의 게이트 PASS/FAIL 매트릭스 (총 15셀).

| score 필터 | 정책 | n_sim | net_ev | W/L | Sharpe | 결과 |
|---|---|---|---|---|---|---|
| 전체 | conservative | 88 | -0.4770% | 0.56 | -2.46 | 🔴 FAIL |
| 전체 | aggressive | 88 | -0.1280% | 0.56 | -0.56 | 🔴 FAIL |
| 전체 | split | 88 | -0.3025% | 0.56 | -1.54 | 🔴 FAIL |
| 전체 | prd_split_optimistic | 88 | +1.5564% | ∞ | +1.75 | 🔴 FAIL |
| 전체 | prd_split_realistic | 88 | +0.9541% | ∞ | +1.35 | 🔴 FAIL |
| ≥2 | conservative | 53 | -0.2896% | 0.56 | -0.84 | 🔴 FAIL |
| ≥2 | aggressive | 53 | +0.0829% | 0.56 | +0.89 | 🔴 FAIL |
| ≥2 | split | 53 | -0.1033% | 0.56 | -0.06 | 🔴 FAIL |
| ≥2 | prd_split_optimistic | 53 | +2.3984% | ∞ | +2.31 | 🔴 FAIL |
| ≥2 | prd_split_realistic | 53 | +1.5884% | ∞ | +1.92 | 🔴 FAIL |
| ≥3 | conservative | 15 | +0.0553% | 0.56 | +0.58 | 🔴 FAIL |
| ≥3 | aggressive | 15 | +0.3477% | 0.56 | +2.22 | 🔴 FAIL |
| ≥3 | split | 15 | +0.2015% | 0.56 | +1.27 | 🔴 FAIL |
| ≥3 | prd_split_optimistic | 15 | +3.5223% | ∞ | +2.82 | 🔴 FAIL |
| ≥3 | prd_split_realistic | 15 | +2.6482% | ∞ | +2.63 | 🔴 FAIL |

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
