# CONTEXT — 단위 2-7b · Phase 2.5 백테스트 시뮬레이터

## 변경 이유
단위 2-7a `phase25_data_loader` 완료 (5/8 b38ee88). 데이터 접근 계층 위에 PRD 12-1 라벨 → 가상 PnL → 12-2 EV 계산 시뮬레이터 신설. 단위 2-7c walk-forward 분석의 핵심 입력.

## PRD 12-1 라벨 정의 (인용)

| 라벨 | 계산 | 성공 기준 |
|---|---|---|
| Gap-up | 익일 시가 / 진입가 - 1 | ≥ +0.6% |
| Morning Exit | 익일 09:00~09:30 고가 / 진입가 - 1 | ≥ +1.2% |
| Stop Risk | 익일 09:00~09:30 저가 / 진입가 - 1 | ≤ -1.0% |

**구현 참고**: `closing_bet_system/collectors/label_provider.py`
- `LABEL_GAP_UP_THRESHOLD_PCT = 0.005` (0.5%, PRD 0.6%보다 보수적)
- `LABEL_STOP_RISK_THRESHOLD_PCT = -0.015` (-1.5%, PRD -1.0%보다 보수적)
- `label_morning_exit` / `label_net_ev_positive`: `cost_engine.minimum_target_return()` 사용 (≈ 0.91%)

→ 본 단위 시뮬레이터는 **DB의 라벨 boolean을 신뢰**하고, 매도가 가정을 위해 PRD 12-1 임계값을 사용.

## PRD 12-2 EV 계산식 (인용)

```
EV = P(Morning Exit 도달) × 평균 익절 수익률
   - P(Stop Risk 도달) × 평균 손실률
   - 거래비용 (왕복 약 0.5%)
   - 슬리피지 (왕복 약 0.2%)

진입 허용: EV > 0
강한 진입: EV > 0.5%
```

## 시나리오 매핑 (본 단위 핵심)

```python
def _map_scenario(label_stop_risk, label_morning_exit, next_open_pct):
    # 우선순위 1: stop_risk (보수적, loss aversion)
    if label_stop_risk:
        return ("stop_risk", -0.010)  # -1.0% 가정 매도가
    # 우선순위 2: morning_exit
    if label_morning_exit:
        return ("morning_exit", +0.012)  # +1.2% 가정 매도가
    # 우선순위 3: 시가 청산
    if next_open_pct is not None:
        return ("market_open", next_open_pct)
    # 라벨 일부 NULL → 시뮬 제외
    return ("excluded", None)
```

## 데이터 현황 (5/8 KST 18:30)

| 항목 | 값 |
|---|---|
| candidate_labels 누적 | 37건 |
| recommended/entered 누적 | 49건 |
| 5/4 라벨링 19건 | gap_up 17/19 (89%) / morning_exit 16/19 (84%) / stop_risk 3/19 (16%) / **net_ev+ 16/19 (84%)** |
| 5/7 라벨링 18건 | gap_up 3/17 (18%) / morning_exit 4/17 (24%) / stop_risk 13/17 (76%) / **net_ev+ 4/17 (24%)** + 셀트리온 백필 1건 |
| 5/8 후보 16건 | 5/11 라벨링 예정 |

**시장 상황 차이**:
- 5/4: 강한 시장 (상승장에서 갭상승 다수)
- 5/7: 약세 시장 (손절 위험 다수) — 시뮬 결과 EV 음수 예상
- → **EV 변동성 측정의 좋은 데이터셋**

## cost_slippage_engine API (재사용)

```python
from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine, get_engine

engine = get_engine()  # 싱글톤
breakdown = engine.compute_pnl(buy_price=10000, sell_price=10120, shares=1)
# breakdown.net_pnl_pct → 슬리피지 + 비용 차감 후 순수익률

engine.round_trip_cost(include_slippage=True)  # ≈ 0.0041 (0.41%)
engine.minimum_target_return()                  # ≈ 0.0091 (0.91%)
```

**중요 결정**:
- 시뮬 매도가 계산 시 PRD 12-1 임계값(±1.0%/+1.2%) 사용 — 절대값 적용
- net_pnl_pct 계산 시 `compute_pnl(buy_price=10000, sell_price=10000 × (1+pct))` 가상 호출
- 이는 슬리피지/세금/수수료 모두 자동 차감됨

## 데이터 로더 인터페이스 (단위 2-7a)

```python
from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset

df = load_phase25_dataset("2026-05-04", "2026-05-08", only_labeled=True)
# 컬럼: candidate_id, trade_date, ticker, ...
#   label_gap_up, label_morning_exit, label_stop_risk, label_net_ev_positive (BooleanDtype)
#   next_open_pct, next_morning_high_pct, next_morning_low_pct (float64)
#   is_labeled, is_featured (bool)
```

## 기존 인프라 의존
- `closing_bet_system/backtest/phase25_data_loader.py` (단위 2-7a, 5/8 완료)
- `closing_bet_system/engines/cost_slippage_engine.py` (Phase 1 1-1 단위, 검증됨)
- `pandas` 1.x+ / `dataclasses` (Python 표준)
- 외부 API 호출 0 (오프라인 분석)

## 영향 범위
- **신규 모듈 단독** — 기존 시스템 영향 없음
- main_orchestrator / collectors 변경 없음
- Phase 1 알림형 회로 무관 (자동매매 0줄)
- DB 변경 없음 (읽기 전용)

## 비범위 명시 (혼동 방지)
- **본 단위는 단일 기간 EV 측정만**: walk-forward 시간 분할은 단위 2-7c
- 점수 구간별 분포 분석 → 단위 2-7c
- 자동매매 진입 결정 → 단위 2-4/2-5 (100건 게이트 후)
- ML 학습 데이터셋 → Phase 3+

## 검증 데이터 (단위 테스트용)
- 5/4 19건 (강한 시장, 16/19 = 84% EV+) — 시뮬 EV 양수 예상
- 5/7 18건 (약세, 4/17 = 24% EV+, 셀트리온 백필 포함) — 시뮬 EV 음수 예상
- 통합 33건 평균 — 중간 영역

## 시나리오 매핑 검증 시 주의
- `label_stop_risk=True AND label_morning_exit=True` 케이스 존재 가능 (장중 변동성)
  - 본 단위: stop_risk 우선 (보수적)
  - 옵션 B/C 비교는 단위 2-7c
- `next_open_pct=NULL`일 수 있음 (라벨링 누락 부분만)
  - is_labeled=True인 행은 모든 라벨 존재 추정 — DB 스키마 검증 필요
