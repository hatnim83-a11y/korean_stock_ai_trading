# CONTEXT — 단위 2-7c · Phase 2.5 walk-forward 분석 리포트

## 변경 이유
단위 2-7b 시뮬레이터(5/8 커밋 5b79a8c) 통합 검증에서 시장 변동성에 EV 매우 민감 정량 확인 (5/4 +0.35% / 5/7 -1.08% / delta -1.43%p). 단일 기간 EV로는 자동매매 진입 결정 불가 → walk-forward 시간 분할 + 다각도 분석으로 안정성 측정 필요.

## 단위 2-7b 시뮬레이터 인터페이스 (재사용)

```python
from closing_bet_system.backtest.phase25_simulator import (
    SimulationResult, EVReport,
    simulate_candidate, simulate_dataset, compute_ev,
    SCENARIO_STOP_RISK, SCENARIO_MORNING_EXIT,
    SCENARIO_MARKET_OPEN, SCENARIO_EXCLUDED,
    _MORNING_EXIT_TARGET_PCT, _STOP_RISK_TARGET_PCT,
)

# 단일 기간
df = load_phase25_dataset("2026-05-04", "2026-05-07", only_labeled=True)
sim_df = simulate_dataset(df, policy="conservative")
report = compute_ev(sim_df)
# report.raw_ev / net_ev / p_morning_exit / p_stop_risk / mean_profit_pct / mean_loss_pct
```

### 정책 옵션 (단위 2-7c에서 확장)
- 현재: `"conservative"` (stop_risk 우선) — 옵션 A
- 추가 예정: `"aggressive"` (morning_exit 우선) — 옵션 B
- 추가 예정: `"split"` (50/50) — 옵션 C

## 단위 2-7a 데이터 로더 인터페이스 (재사용)

```python
from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset

df, meta = load_phase25_dataset(
    start_date="2026-05-04",
    end_date="2026-05-08",
    only_labeled=True,    # 라벨링된 행만
    return_meta=True,     # 메타 dict 반환
)
# df.shape (49, 50) — recommended/entered + features + labels
# meta {rows, labeled_rows, features_rows, date_range, statuses, db_path, generated_at}
```

## PRD 12-2 / 단위 2-8 게이트 기준 (재인용)

```
EV = P(Morning Exit) × 평균 익절 수익률
   - P(Stop Risk) × 평균 손실률
   - 거래비용 (왕복 약 0.5%)  ← cost_engine.compute_pnl이 자동 차감

진입 허용: EV > 0
강한 진입: EV > 0.5%
```

### 단위 2-8 합격 기준 (project_closing_bet_followups.md 인용)
- EV ≥ 0.5%
- Win/Loss ratio ≥ 1.3
- 월간 Sharpe ≥ 1.0
- 표본 100건+

## 데이터 현황 (5/8 KST 19:25 기준)

| 항목 | 값 |
|---|---|
| candidate_labels 누적 | 37건 |
| recommended/entered 누적 | 49건 |
| features 누적 | 56건 (rejected 포함) |
| 5/4 라벨링 | 19건 (강세 시장, EV+ 84%) |
| 5/7 라벨링 | 18건 (약세 시장, EV+ 24%, 셀트리온 백필 포함) |
| 5/8 후보 | 16건 (5/11 자동 라벨링 예정) |
| **5/11 후 예상 누적** | 56건 (단위 2-7c 진입 적정 시점) |
| **100건 도달 예상** | 5/28 (단위 2-8 게이트) |

## 단위 2-7b 시뮬 정량 결과 (참조)

```
5/4 (강세 15건): morning 80% / stop 20% / open 0% / net_ev = +0.3477%
5/7 (약세 18건): morning 11.1% / stop 83.3% / open 5.6% / net_ev = -1.0798%
통합 33건: net_ev = -0.4309% / cost_basis = 0.4100%
셀트리온(31): scenario=stop_risk, net_pnl=-1.4068%

비용 차감 정량:
- morning_exit +1.2% → net_pnl +0.7863% (round_trip 0.41% 정확 차감)
- stop_risk -1.0% → net_pnl -1.4068%
```

## walk-forward 윈도우 설계

### Phase 2.5 권장 (소량 데이터)
- 학습 5일 → 테스트 1일 슬라이딩
- 5/4 시작 → 5/4(학습) + 5/5~5/9(테스트) ... 슬라이딩
- 데이터 5/4 단일이라 윈도우 부족 → **본 단위는 인프라만 구축**

### Phase 2.8+ (100건+ 누적 후)
- 학습 4주 → 테스트 1주
- 월간 Sharpe 산출 가능

## Sharpe 계산 (PRD 미명시 — 일반 정의)
```
Sharpe = (평균 일별 수익률 - 무위험 수익률) / 일별 수익률 표준편차 × √(연환산일)
       ≈ daily_mean / daily_std × √(252) — 연간
       ≈ daily_mean / daily_std × √12 — 월간
```

본 단위에서는 무위험 수익률 0 가정 (Phase 1 단순화). Phase 3에서 한국 국채 3년물 수익률 적용 검토.

## 옵션 정책 비교 (B/C 도입 필요성)

### 양립 라벨 사례 (`stop_risk=True AND morning_exit=True`)
- 09:00~09:30 30분 윈도우에서 둘 다 도달 = **장중 변동성 큰 종목**
- 옵션 A 보수: stop_risk 우선 → 손실 가정
- 옵션 B 낙관: morning_exit 우선 → 익절 가정
- 옵션 C 중립: 50/50 → 평균값
- **현실 추정**: 평균적으로 둘 다 발생 = 시간 순서 미상이라 정확한 답 없음
- **본 단위 분석**: 옵션 A/B 차이가 EV에 얼마나 영향 주는지 정량 측정

### 5/4~5/7 33건 양립 라벨 빈도 (예상)
- 단위 2-7b 검증 결과로 추정 시 stop=18건 + morning=14건 합 32건 (33건과 비슷)
- 양립 라벨은 매우 적을 가능성 높음 → 옵션 차이 작을 것 예상
- 실측 후 정량 확인 필요

## 영향 범위
- **신규 모듈 단독** + 단위 2-7b 시뮬레이터 정책 옵션 추가 (~30줄)
- 기존 시스템 영향 0 (오프라인 분석)
- Phase 1 알림형 회로 무관 (자동매매 0줄)

## 기존 인프라 의존
- `closing_bet_system/backtest/phase25_data_loader.py` (단위 2-7a)
- `closing_bet_system/backtest/phase25_simulator.py` (단위 2-7b)
- `closing_bet_system/engines/cost_slippage_engine.py` (Phase 1 1-1)
- `pandas` (groupby + rolling)
- `numpy` (std / sqrt)
- `matplotlib` (선택, 시계열 차트)

## 검증 데이터 (단위 테스트용)
- 5/4 19건 (강세) — score_bucket_analysis 입력
- 5/7 18건 (약세) — 시간 차이 윈도우
- 통합 33건 — Sharpe 계산용 (n=2 일자라 부족, mock 추가 필요)
- 셀트리온 백필 (candidate_id=31) — 옵션 A/B/C에서 모두 stop_risk (label_morning_exit=False)

## 비범위 명시 (혼동 방지)
- 자동매매 진입 결정 → 단위 2-8 (별도, 100건 게이트)
- ML 학습 데이터셋 → Phase 3+
- regime_detector → Phase 3
- 실시간 모니터링 → 단위 2-6 dashboard (이미 완료)

## 작업 중 발견 사항

### 2026-05-08 세션 (단위 2-7c 사전 작성)
- 단위 2-7b 통합 검증에서 시장 변동성에 EV 민감 정량 확인 → walk-forward 분석 필요성 입증
- 단위 2-7b 시뮬레이터 정책 옵션 B/C 인터페이스만 설계 (실제 구현은 본 단위)
- candidate_labels 5/11 라벨링 후 56건 도달 → 단위 2-7c 본격 분석 적정 시점
- **다음 세션에서 본 단위 진입 시 주의사항**:
  1. 단위 2-7b의 `_VALID_SCENARIO_POLICIES` 튜플 확장 (옵션 B/C 추가)
  2. 옵션 추가 시 `_map_scenario` 분기 확장 — 양립 라벨 처리 결정 (현재 stop_risk 우선)
  3. 5/11 라벨링 자동 발화 후 56건 데이터로 walkforward 첫 실측 가능
  4. label_provider 재시도 로직 자연 검증 (5/11 KIS 호출 정상 시 19/19 라벨링)
  5. Sharpe 일별 표본 수 부족 (5/8 시점 2일자) → 신뢰구간 표시 + 100건 누적 후 본 분석
