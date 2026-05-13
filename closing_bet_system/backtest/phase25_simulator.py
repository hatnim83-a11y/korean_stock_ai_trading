"""closing_bet_system.backtest.phase25_simulator

Phase 2.5 백테스트 시뮬레이터.

단위 2-7a ``phase25_data_loader`` 출력 DataFrame을 입력으로, **PRD 12-1 라벨 4종 +
12-2 EV 계산식**에 따라 후보별 가상 PnL 및 전체 EV를 계산한다. 단위 2-7c
walk-forward 분석의 핵심 입력.

본 모듈은 **단일 기간 EV 측정만** 책임진다:
- walk-forward 시간 분할 → 단위 2-7c
- 점수 구간별 EV 분포 → 단위 2-7c
- 자동매매 진입 결정 → 단위 2-4/2-5 (별도 100건 게이트 후)

PRD 12-1 라벨 정의:
- ``label_morning_exit``: 익일 09:00~09:30 고가 ≥ +1.2%
- ``label_stop_risk``: 익일 09:00~09:30 저가 ≤ -1.0%
- ``label_gap_up``: 익일 시가 ≥ +0.6%
- ``label_net_ev_positive``: 비용 차감 후 EV > 0

PRD 12-2 EV 계산식::

    EV = P(Morning Exit) × 평균 익절 net_pnl_pct
       + P(Stop Risk)    × 평균 손실 net_pnl_pct
       + P(Market Open)  × 평균 시가 net_pnl_pct

    (cost_slippage_engine.compute_pnl이 net_pnl_pct에 이미 비용 차감 → 이중 차감 X)

시나리오 매핑 우선순위:

옵션 A "conservative" (손실 회피):
1. ``label_stop_risk=True`` → ``stop_risk`` 시나리오 (-1.0% 매도)
2. ``label_morning_exit=True`` → ``morning_exit`` 시나리오 (+1.2% 매도)
3. 둘 다 False → ``market_open`` 시나리오 (next_open_pct 매도)
4. 라벨 **둘 다 NULL** → ``excluded``.

옵션 B "aggressive" (낙관, 익절 우선):
1. ``label_morning_exit=True`` → ``morning_exit`` 시나리오 (+1.2% 매도)
2. ``label_stop_risk=True`` → ``stop_risk`` 시나리오 (-1.0% 매도)
3. 둘 다 False → ``market_open`` / 둘 다 NULL → ``excluded``.

옵션 C "split" (50/50 평균):
1. 양립 라벨(stop=morning=True) → ``split`` 시나리오 (morning_exit/stop_risk 평균 net_pnl)
2. 단일 라벨 → ``stop_risk`` 또는 ``morning_exit`` (conservative와 동일)
3. 둘 다 False → ``market_open`` / 둘 다 NULL → ``excluded``.

옵션 A/B/C 공통:
- 한쪽만 NULL이면 나머지 라벨로 판단 (예: stop=None, morning=False → market_open).

실행 예시:
    >>> from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset
    >>> from closing_bet_system.backtest.phase25_simulator import simulate_dataset, compute_ev
    >>> df = load_phase25_dataset("2026-05-04", "2026-05-07", only_labeled=True)
    >>> sim_df = simulate_dataset(df)
    >>> report = compute_ev(sim_df)
    >>> report.net_ev
    -0.001234   # 시장 약세 영향 음수
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.engines.cost_slippage_engine import (
    CostSlippageEngine,
    get_engine,
)
from logger import logger


# ===== 모듈 상수 =====

# PRD 12-1 임계값 (시뮬 매도가 가정)
_MORNING_EXIT_TARGET_PCT = 0.012   # +1.2%
_STOP_RISK_TARGET_PCT = -0.010     # -1.0%

# 가상 거래 단위 (PnL 정규화)
_DEFAULT_BUY_PRICE = 10000.0
_DEFAULT_SHARES = 1

# 시나리오 정책 (단위 2-7c 옵션 B/C 추가, 2026-05-08)
# PRD 10-1 분할매도 정책 2종 추가 (2026-05-13)
_DEFAULT_SCENARIO_POLICY = "conservative"
_VALID_SCENARIO_POLICIES = (
    "conservative",
    "aggressive",
    "split",
    "prd_split_optimistic",   # PRD 10-1: 갭업 시 잔여 50% × morning_high (낙관)
    "prd_split_realistic",    # PRD 10-1: 갭업 시 잔여 50% × (open+morning_high)/2 (현실)
)
_PRD_SPLIT_POLICIES = ("prd_split_optimistic", "prd_split_realistic")

# 시나리오 식별자
SCENARIO_STOP_RISK = "stop_risk"
SCENARIO_MORNING_EXIT = "morning_exit"
SCENARIO_MARKET_OPEN = "market_open"
SCENARIO_SPLIT = "split"          # 옵션 C: 양립 라벨 평균 (단위 2-7c)
SCENARIO_EXCLUDED = "excluded"

# PRD 10-1 시가 액션 매트릭스 신규 시나리오 (2026-05-13)
SCENARIO_PRD_SPLIT_GAPUP = "prd_split_gapup"      # open ≥ +0.5% → 50/50 분할매도
SCENARIO_PRD_SPLIT_FLAT = "prd_split_flat"        # -1% < open < +0.5% → 100% 시초가
SCENARIO_PRD_SPLIT_GAPDOWN = "prd_split_gapdown"  # open ≤ -1% → 100% 즉시 손절

# PRD 10-1 임계값 + 분할 비율
_PRD_GAPUP_THRESHOLD = 0.005       # +0.5% (분할매도 진입 경계)
_PRD_GAPDOWN_THRESHOLD = -0.010    # -1.0% (즉시 손절 경계)
_PRD_FIRST_SELL_RATIO = 0.5        # 1차 매도 비율 (PRD 50/50)

# 결과 컬럼명 (simulate_dataset 가 추가)
_RESULT_COLUMNS = (
    "scenario",
    "simulated_exit_pct",
    "simulated_net_pnl_pct",
    "simulated_excluded_reason",
)


# ===== Dataclass =====


@dataclass(frozen=True)
class SimulationResult:
    """단일 후보 시뮬레이션 결과."""

    candidate_id: int
    ticker: str
    scenario: str  # SCENARIO_* 중 하나
    simulated_exit_pct: Optional[float]      # 가상 매도가 등락률 (소수)
    simulated_net_pnl_pct: Optional[float]   # cost_engine 차감 후 순수익률 (소수)
    excluded_reason: Optional[str] = None    # excluded 시나리오에서만 채워짐


@dataclass(frozen=True)
class EVReport:
    """전체 데이터셋 EV 통계 (PRD 12-2 정합).

    Phase 2.5 (단위 2-7b): ``raw_ev == net_ev`` (cost_engine.compute_pnl이 이미 비용
    차감, 이중 차감 방지).

    옵션 C "split" 도입 (단위 2-7c) 시 split 시나리오 카운트는 별도 항으로 분리:
    raw_ev = p_morning * mean_profit + p_stop * mean_loss + p_open * mean_open
           + p_split * mean_split. 옵션 A/B에서는 n_split=0 / p_split=0 / mean_split=0.
    """

    n_total: int
    n_simulated: int            # excluded 제외
    n_morning_exit: int
    n_stop_risk: int
    n_market_open: int

    p_morning_exit: float       # n_morning_exit / n_simulated
    p_stop_risk: float
    p_market_open: float

    mean_profit_pct: float      # morning_exit 평균 net_pnl_pct (양수 부근)
    mean_loss_pct: float        # stop_risk 평균 net_pnl_pct (음수)
    mean_market_open_pct: float

    raw_ev: float               # 시나리오 가중 합
    net_ev: float               # raw_ev (단위 2-7b는 이미 비용 차감, 이중 차감 X)
    cost_basis: float           # 참고: cost_engine.round_trip_cost (include_slippage=True)

    # 옵션 C "split" 전용 (옵션 A/B에서는 0). default 추가로 회귀 영향 방지.
    n_split: int = 0
    p_split: float = 0.0
    mean_split_pct: float = 0.0

    # PRD 10-1 분할매도 정책 전용 (2026-05-13). 옵션 A/B/C에서는 0.
    # default 처리로 기존 호출부 회귀 영향 차단.
    n_prd_gapup: int = 0
    n_prd_flat: int = 0
    n_prd_gapdown: int = 0
    p_prd_gapup: float = 0.0
    p_prd_flat: float = 0.0
    p_prd_gapdown: float = 0.0
    mean_prd_gapup_pct: float = 0.0
    mean_prd_flat_pct: float = 0.0
    mean_prd_gapdown_pct: float = 0.0


# ===== Public API =====


def simulate_candidate(
    row: Union[dict, pd.Series],
    *,
    policy: str = _DEFAULT_SCENARIO_POLICY,
    cost_engine: Optional[CostSlippageEngine] = None,
) -> SimulationResult:
    """단일 후보 행 시뮬레이션.

    Args:
        row: candidate row (dict 또는 pd.Series).
            필수 키: candidate_id, ticker, label_stop_risk, label_morning_exit, next_open_pct.
        policy: 시나리오 우선순위 정책.
            ``"conservative"`` (옵션 A) / ``"aggressive"`` (옵션 B) / ``"split"`` (옵션 C).
        cost_engine: 의존성 주입 (테스트용). None 시 ``get_engine()`` 싱글톤.

    Returns:
        SimulationResult.
    """
    if policy not in _VALID_SCENARIO_POLICIES:
        raise ValueError(f"unsupported scenario_policy: {policy!r}")

    engine = cost_engine if cost_engine is not None else get_engine()

    cid = int(row.get("candidate_id", 0))
    ticker = str(row.get("ticker", ""))

    scenario, exit_pct, excluded_reason = _map_scenario(row, policy)

    if scenario == SCENARIO_EXCLUDED:
        return SimulationResult(
            candidate_id=cid,
            ticker=ticker,
            scenario=scenario,
            simulated_exit_pct=None,
            simulated_net_pnl_pct=None,
            excluded_reason=excluded_reason,
        )

    # 옵션 C "split": morning_exit + stop_risk 결과 평균
    if scenario == SCENARIO_SPLIT:
        morning_pnl = _compute_pnl_pct(engine, _MORNING_EXIT_TARGET_PCT)
        stop_pnl = _compute_pnl_pct(engine, _STOP_RISK_TARGET_PCT)
        avg_pnl = (morning_pnl + stop_pnl) / 2.0
        avg_exit_pct = (_MORNING_EXIT_TARGET_PCT + _STOP_RISK_TARGET_PCT) / 2.0
        return SimulationResult(
            candidate_id=cid,
            ticker=ticker,
            scenario=scenario,
            simulated_exit_pct=float(avg_exit_pct),
            simulated_net_pnl_pct=float(avg_pnl),
            excluded_reason=None,
        )

    # PRD 10-1 GAPUP 분할매도: 1차 50% × open + 2차 50% × (변형별 청산가)
    if scenario == SCENARIO_PRD_SPLIT_GAPUP:
        open_pct = _safe_float(row.get("next_open_pct"))
        morning_high = _safe_float(row.get("next_morning_high_pct"))
        exit_pct = _compute_prd_split_exit(open_pct, morning_high, policy)
        sell_price = _DEFAULT_BUY_PRICE * (1.0 + exit_pct)
        breakdown = engine.compute_pnl(
            buy_price=_DEFAULT_BUY_PRICE,
            sell_price=sell_price,
            shares=_DEFAULT_SHARES,
        )
        return SimulationResult(
            candidate_id=cid,
            ticker=ticker,
            scenario=scenario,
            simulated_exit_pct=float(exit_pct),
            simulated_net_pnl_pct=float(breakdown.net_pnl_pct),
            excluded_reason=None,
        )

    # 단일 시나리오: 가상 매수/매도 PnL (buy_price 10000원 정규화 + 등락률)
    sell_price = _DEFAULT_BUY_PRICE * (1.0 + exit_pct)
    breakdown = engine.compute_pnl(
        buy_price=_DEFAULT_BUY_PRICE,
        sell_price=sell_price,
        shares=_DEFAULT_SHARES,
    )

    return SimulationResult(
        candidate_id=cid,
        ticker=ticker,
        scenario=scenario,
        simulated_exit_pct=float(exit_pct),
        simulated_net_pnl_pct=float(breakdown.net_pnl_pct),
        excluded_reason=None,
    )


def simulate_dataset(
    df: pd.DataFrame,
    *,
    policy: str = _DEFAULT_SCENARIO_POLICY,
    cost_engine: Optional[CostSlippageEngine] = None,
) -> pd.DataFrame:
    """전체 데이터셋 시뮬레이션 — 결과 4컬럼 추가.

    Args:
        df: phase25_data_loader 출력 DataFrame (또는 호환 스키마).
        policy: 시나리오 정책.
        cost_engine: 의존성 주입.

    Returns:
        df.copy() + scenario / simulated_exit_pct / simulated_net_pnl_pct /
        simulated_excluded_reason 컬럼 추가.
    """
    if policy not in _VALID_SCENARIO_POLICIES:
        raise ValueError(f"unsupported scenario_policy: {policy!r}")

    out = df.copy()

    # 빈 df 입력 → 결과 컬럼만 추가하고 반환 (downstream 일관성)
    if out.empty:
        for col in _RESULT_COLUMNS:
            if col not in out.columns:
                if col == "scenario" or col == "simulated_excluded_reason":
                    out[col] = pd.Series([], dtype="object")
                else:
                    out[col] = pd.Series([], dtype="float64")
        return out

    engine = cost_engine if cost_engine is not None else get_engine()

    scenarios: list = []
    exit_pcts: list = []
    net_pnls: list = []
    excluded_reasons: list = []

    for _, row in out.iterrows():
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        scenarios.append(result.scenario)
        exit_pcts.append(result.simulated_exit_pct)
        net_pnls.append(result.simulated_net_pnl_pct)
        excluded_reasons.append(result.excluded_reason)

    out["scenario"] = scenarios
    out["simulated_exit_pct"] = pd.array(exit_pcts, dtype="Float64")  # nullable float
    out["simulated_net_pnl_pct"] = pd.array(net_pnls, dtype="Float64")
    out["simulated_excluded_reason"] = excluded_reasons

    return out


def compute_ev(
    simulated_df: pd.DataFrame,
    *,
    cost_engine: Optional[CostSlippageEngine] = None,
) -> EVReport:
    """시뮬레이션 결과 → EV 통계 (PRD 12-2 정합).

    excluded 행은 분모/분자에서 제외. raw_ev = net_ev (이미 cost_engine.compute_pnl이
    비용 차감했으므로 이중 차감 방지).

    Args:
        simulated_df: simulate_dataset 출력 (또는 호환 스키마).
        cost_engine: cost_basis 메타 계산용 (의존성 주입). 측정값에는 영향 없음.

    Returns:
        EVReport.
    """
    engine = cost_engine if cost_engine is not None else get_engine()
    cost_basis = float(engine.round_trip_cost(include_slippage=True))

    n_total = len(simulated_df)

    # 빈 df 또는 스키마 미충족 → 0 채움
    if n_total == 0 or "scenario" not in simulated_df.columns:
        return _empty_ev_report(n_total, cost_basis)

    valid = simulated_df[simulated_df["scenario"] != SCENARIO_EXCLUDED]
    n_simulated = len(valid)

    if n_simulated == 0:
        return _empty_ev_report(n_total, cost_basis)

    # 시나리오별 분리
    morning_mask = valid["scenario"] == SCENARIO_MORNING_EXIT
    stop_mask = valid["scenario"] == SCENARIO_STOP_RISK
    open_mask = valid["scenario"] == SCENARIO_MARKET_OPEN
    split_mask = valid["scenario"] == SCENARIO_SPLIT
    prd_gapup_mask = valid["scenario"] == SCENARIO_PRD_SPLIT_GAPUP
    prd_flat_mask = valid["scenario"] == SCENARIO_PRD_SPLIT_FLAT
    prd_gapdown_mask = valid["scenario"] == SCENARIO_PRD_SPLIT_GAPDOWN

    n_morning = int(morning_mask.sum())
    n_stop = int(stop_mask.sum())
    n_open = int(open_mask.sum())
    n_split = int(split_mask.sum())
    n_prd_gapup = int(prd_gapup_mask.sum())
    n_prd_flat = int(prd_flat_mask.sum())
    n_prd_gapdown = int(prd_gapdown_mask.sum())

    p_morning = n_morning / n_simulated
    p_stop = n_stop / n_simulated
    p_open = n_open / n_simulated
    p_split = n_split / n_simulated
    p_prd_gapup = n_prd_gapup / n_simulated
    p_prd_flat = n_prd_flat / n_simulated
    p_prd_gapdown = n_prd_gapdown / n_simulated

    mean_profit = _safe_mean(valid.loc[morning_mask, "simulated_net_pnl_pct"])
    mean_loss = _safe_mean(valid.loc[stop_mask, "simulated_net_pnl_pct"])
    mean_market_open = _safe_mean(valid.loc[open_mask, "simulated_net_pnl_pct"])
    mean_split = _safe_mean(valid.loc[split_mask, "simulated_net_pnl_pct"])
    mean_prd_gapup = _safe_mean(valid.loc[prd_gapup_mask, "simulated_net_pnl_pct"])
    mean_prd_flat = _safe_mean(valid.loc[prd_flat_mask, "simulated_net_pnl_pct"])
    mean_prd_gapdown = _safe_mean(valid.loc[prd_gapdown_mask, "simulated_net_pnl_pct"])

    raw_ev = (
        p_morning * mean_profit
        + p_stop * mean_loss
        + p_open * mean_market_open
        + p_split * mean_split
        + p_prd_gapup * mean_prd_gapup
        + p_prd_flat * mean_prd_flat
        + p_prd_gapdown * mean_prd_gapdown
    )
    # 이중 차감 방지: simulated_net_pnl_pct 가 이미 비용/슬리피지 차감 후 순수익률.
    # cost_basis 는 참고 메타일 뿐, raw_ev 에서 추가로 빼면 안 됨.
    net_ev = raw_ev

    return EVReport(
        n_total=n_total,
        n_simulated=n_simulated,
        n_morning_exit=n_morning,
        n_stop_risk=n_stop,
        n_market_open=n_open,
        p_morning_exit=p_morning,
        p_stop_risk=p_stop,
        p_market_open=p_open,
        mean_profit_pct=mean_profit,
        mean_loss_pct=mean_loss,
        mean_market_open_pct=mean_market_open,
        raw_ev=raw_ev,
        net_ev=net_ev,
        cost_basis=cost_basis,
        n_split=n_split,
        p_split=p_split,
        mean_split_pct=mean_split,
        n_prd_gapup=n_prd_gapup,
        n_prd_flat=n_prd_flat,
        n_prd_gapdown=n_prd_gapdown,
        p_prd_gapup=p_prd_gapup,
        p_prd_flat=p_prd_flat,
        p_prd_gapdown=p_prd_gapdown,
        mean_prd_gapup_pct=mean_prd_gapup,
        mean_prd_flat_pct=mean_prd_flat,
        mean_prd_gapdown_pct=mean_prd_gapdown,
    )


# ===== 내부 헬퍼 =====


def _map_scenario(
    row: Union[dict, pd.Series],
    policy: str,
) -> tuple[str, Optional[float], Optional[str]]:
    """시나리오 매핑 → (scenario, exit_pct, excluded_reason).

    정책별 우선순위:
    - "conservative" (옵션 A): stop_risk → morning_exit → market_open → excluded
    - "aggressive"   (옵션 B): morning_exit → stop_risk → market_open → excluded
    - "split"        (옵션 C): 양립 시 SCENARIO_SPLIT, 단일 라벨은 conservative 동일
    - "prd_split_*"  (PRD 10-1): next_open_pct 구간별 분할매도 — 별도 분기 처리
    """
    open_pct_raw = row.get("next_open_pct")

    # PRD 10-1 정책: 시가 % 구간별 매핑 (라벨 stop/morning과 무관, 시가만 사용)
    # GAPUP의 exit_pct는 simulate_candidate에서 morning_high와 합성하므로 None 반환.
    if policy in _PRD_SPLIT_POLICIES:
        open_pct = _safe_float(open_pct_raw)
        if open_pct is None:
            return SCENARIO_EXCLUDED, None, "missing_open_pct"
        if open_pct >= _PRD_GAPUP_THRESHOLD:
            # 분할매도 케이스 — exit_pct는 별도 합성 (None placeholder)
            return SCENARIO_PRD_SPLIT_GAPUP, None, None
        if open_pct <= _PRD_GAPDOWN_THRESHOLD:
            return SCENARIO_PRD_SPLIT_GAPDOWN, float(open_pct), None
        return SCENARIO_PRD_SPLIT_FLAT, float(open_pct), None

    stop = _safe_bool(row.get("label_stop_risk"))
    morning = _safe_bool(row.get("label_morning_exit"))

    # 라벨 둘 다 None (NULL) → excluded
    if stop is None and morning is None:
        return SCENARIO_EXCLUDED, None, "missing_labels"

    # 옵션 C "split": 양립 라벨 시 SCENARIO_SPLIT (exit_pct는 평균 계산을 simulate_candidate에서)
    if policy == "split" and stop is True and morning is True:
        return SCENARIO_SPLIT, None, None

    # 옵션 B "aggressive": morning 우선
    if policy == "aggressive":
        if morning is True:
            return SCENARIO_MORNING_EXIT, _MORNING_EXIT_TARGET_PCT, None
        if stop is True:
            return SCENARIO_STOP_RISK, _STOP_RISK_TARGET_PCT, None
    else:
        # 옵션 A "conservative" 또는 옵션 C "split"의 단일 라벨 분기
        if stop is True:
            return SCENARIO_STOP_RISK, _STOP_RISK_TARGET_PCT, None
        if morning is True:
            return SCENARIO_MORNING_EXIT, _MORNING_EXIT_TARGET_PCT, None

    # 둘 다 False (또는 한쪽 None + 다른쪽 False)
    open_pct = _safe_float(open_pct_raw)
    if open_pct is None:
        return SCENARIO_EXCLUDED, None, "missing_open_pct"

    return SCENARIO_MARKET_OPEN, open_pct, None


def _compute_pnl_pct(engine: CostSlippageEngine, exit_pct: float) -> float:
    """단일 등락률에 대한 net_pnl_pct 계산 (옵션 C split의 평균 계산용)."""
    sell_price = _DEFAULT_BUY_PRICE * (1.0 + exit_pct)
    breakdown = engine.compute_pnl(
        buy_price=_DEFAULT_BUY_PRICE,
        sell_price=sell_price,
        shares=_DEFAULT_SHARES,
    )
    return float(breakdown.net_pnl_pct)


def _compute_prd_split_exit(
    open_pct: Optional[float],
    morning_high: Optional[float],
    policy: str,
) -> float:
    """PRD 10-1 GAPUP 분할매도 합성 exit_pct 계산.

    - optimistic: 1차 50%×open + 2차 50%×morning_high (잔여를 09:30 고가에 정확히 청산)
    - realistic: 1차 50%×open + 2차 50%×(open+morning_high)/2
        = 0.75×open + 0.25×morning_high (시초가~고가 중간값에서 청산)

    morning_high가 NULL인 경우 graceful 폴백: 1차 매도 비율만큼 시초가 매도 + 잔여도
    동일 시초가 매도로 가정 → exit_pct = open_pct.
    """
    if open_pct is None:
        # 안전망 — _map_scenario에서 already 가드되어 도달하지 않을 것이나 방어적.
        return 0.0
    if morning_high is None:
        # morning_high NULL → 잔여 50%도 시초가에 매도한다고 가정 (보수적 폴백)
        return float(open_pct)
    if policy == "prd_split_optimistic":
        return float(_PRD_FIRST_SELL_RATIO * open_pct
                     + (1.0 - _PRD_FIRST_SELL_RATIO) * morning_high)
    # prd_split_realistic: 잔여 50%를 시초가~고가 중간값에서 청산 가정
    mid = (open_pct + morning_high) / 2.0
    return float(_PRD_FIRST_SELL_RATIO * open_pct
                 + (1.0 - _PRD_FIRST_SELL_RATIO) * mid)


def _safe_bool(value) -> Optional[bool]:
    """pd.NA / None / NaN → None. 그 외 → bool."""
    if value is None:
        return None
    # pd.NA 처리 — pd.isna 가 BooleanDtype/object 모두 처리
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def _safe_float(value) -> Optional[float]:
    """None / NaN / 변환 실패 → None. 그 외 → float."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_mean(series: pd.Series) -> float:
    """빈 Series 또는 모두 NaN → 0.0. 그 외 → 평균."""
    if series.empty:
        return 0.0
    val = series.mean(skipna=True)
    if pd.isna(val):
        return 0.0
    return float(val)


def _empty_ev_report(n_total: int, cost_basis: float) -> EVReport:
    """0 채움 EVReport (빈 df / 모두 excluded 케이스)."""
    return EVReport(
        n_total=n_total,
        n_simulated=0,
        n_morning_exit=0,
        n_stop_risk=0,
        n_market_open=0,
        p_morning_exit=0.0,
        p_stop_risk=0.0,
        p_market_open=0.0,
        mean_profit_pct=0.0,
        mean_loss_pct=0.0,
        mean_market_open_pct=0.0,
        raw_ev=0.0,
        net_ev=0.0,
        cost_basis=cost_basis,
    )


# ===== CLI 진입점 =====


def _main_cli() -> None:
    """단발 검증용 CLI: 5/4~5/7 라벨링 데이터 시뮬 + EV 출력."""
    import argparse

    from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset

    parser = argparse.ArgumentParser(description="Phase 2.5 시뮬레이터 단발 검증")
    parser.add_argument("--start", default="2026-05-04")
    parser.add_argument("--end", default="2026-05-07")
    parser.add_argument("--policy", default=_DEFAULT_SCENARIO_POLICY)
    args = parser.parse_args()

    df = load_phase25_dataset(args.start, args.end, only_labeled=True)
    sim_df = simulate_dataset(df, policy=args.policy)
    report = compute_ev(sim_df)

    logger.info(f"[phase25_simulator] {args.start}~{args.end} policy={args.policy}")
    logger.info(
        f"  n_total={report.n_total} n_simulated={report.n_simulated} "
        f"morning={report.n_morning_exit} stop={report.n_stop_risk} open={report.n_market_open}"
    )
    logger.info(
        f"  P: morning={report.p_morning_exit:.3f} stop={report.p_stop_risk:.3f} "
        f"open={report.p_market_open:.3f}"
    )
    logger.info(
        f"  mean: profit={report.mean_profit_pct:+.5f} loss={report.mean_loss_pct:+.5f} "
        f"market_open={report.mean_market_open_pct:+.5f}"
    )
    logger.info(
        f"  raw_ev={report.raw_ev:+.5f} net_ev={report.net_ev:+.5f} "
        f"cost_basis={report.cost_basis:.5f}"
    )


if __name__ == "__main__":
    _main_cli()
