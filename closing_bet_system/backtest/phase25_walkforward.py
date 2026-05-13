"""closing_bet_system.backtest.phase25_walkforward

Phase 2.5 walk-forward 분석 + 점수 구간/Sharpe/정책 비교 통합 리포트 생성기 (단위 2-7c).

단위 2-7a 데이터 로더 + 단위 2-7b 시뮬레이터 출력을 입력으로, **단위 2-8 100건
자동화 게이트 합격선 판정 근거**를 5종 분석으로 산출:

1. **walk-forward EV 안정성** — 시간 분할 윈도우별 EV 시계열 (인프라 구축, 100건+ 시 본격)
2. **점수 구간별 EV 분포** — total_score 0/1/2/3 구간별 EV 차이 (점수 모델 유효성)
3. **Sharpe / Sortino / Max Drawdown** — risk-adjusted return 측정
4. **시나리오 정책 옵션 비교** — A "conservative" / B "aggressive" / C "split" EV 비교
5. **라벨 분포 + 시뮬 시나리오 분포** — 데이터 누적 추세 모니터링

PRD 12-2 / 단위 2-8 게이트 기준:
- 평균 EV ≥ +0.5%
- Win/Loss ratio ≥ 1.3 (mean_profit_pct / abs(mean_loss_pct))
- 월간 Sharpe ≥ 1.0
- 표본 수 ≥ 100건

자동매매 진입은 본 단위에서 결정하지 않는다 (단위 2-8 사용자 승인 후).

실행 예시:
    >>> from closing_bet_system.backtest.phase25_walkforward import generate_report
    >>> path = generate_report("2026-05-04", "2026-05-07")
    >>> # docs/improvements/phase25_ev_report_20260508.md 생성
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset
from closing_bet_system.backtest.phase25_simulator import (
    SCENARIO_EXCLUDED,
    SCENARIO_MARKET_OPEN,
    SCENARIO_MORNING_EXIT,
    SCENARIO_PRD_SPLIT_FLAT,
    SCENARIO_PRD_SPLIT_GAPDOWN,
    SCENARIO_PRD_SPLIT_GAPUP,
    SCENARIO_SPLIT,
    SCENARIO_STOP_RISK,
    EVReport,
    compute_ev,
    simulate_dataset,
)
from config import now_kst
from logger import logger


# ===== 모듈 상수 =====

# Walk-forward 윈도우 기본값
_DEFAULT_TRAIN_DAYS = 5
_DEFAULT_TEST_DAYS = 1

# Sharpe 연환산 계수
_DEFAULT_ANNUALIZE_FACTOR = 12   # 월간 환산 (Phase 2.5 권장)

# 단위 2-8 자동화 게이트 기준 (PRD 12-2 / project_closing_bet_followups.md)
_GATE_EV_THRESHOLD = 0.005        # +0.5%
_GATE_WIN_LOSS_RATIO = 1.3
_GATE_SHARPE = 1.0
_GATE_MIN_SAMPLES = 100

# 점수 구간 (total_score)
_SCORE_BUCKETS = (0, 1, 2, 3)

# 정책 옵션 (단위 2-7b 확장)
# 2026-05-13: PRD 10-1 분할매도 2종 추가 — 운영 정합 평가용 default
_POLICIES = (
    "conservative",
    "aggressive",
    "split",
    "prd_split_optimistic",
    "prd_split_realistic",
)
_DEFAULT_POLICY = "prd_split_realistic"   # 운영 정책 정합 default (2026-05-13)

# score 임계 필터 (None=전체 / 2=score≥2 / 3=score≥3, 단위 2-4 진입 결정 근거)
_SCORE_FILTERS: tuple[Optional[int], ...] = (None, 2, 3)

# md 리포트 기본 출력 디렉토리
_DEFAULT_REPORT_DIR = "docs/improvements"


# ===== Dataclass =====


@dataclass(frozen=True)
class WalkforwardReport:
    """Walk-forward 윈도우 슬라이딩 분석 결과."""

    n_windows: int
    window_evs: list[float] = field(default_factory=list)
    mean_window_ev: float = 0.0
    std_window_ev: float = 0.0
    stable_windows_pct: float = 0.0   # net_ev > 0 윈도우 비율
    train_days: int = _DEFAULT_TRAIN_DAYS
    test_days: int = _DEFAULT_TEST_DAYS


@dataclass(frozen=True)
class SharpeMetrics:
    """Sharpe / Sortino / Max Drawdown 통계 (일별 시계열 기반)."""

    n_days: int
    daily_mean: float
    daily_std: float
    sharpe: float                 # daily_mean / daily_std × √annualize
    sortino: float                # daily_mean / downside_std × √annualize
    max_drawdown: float           # 누적 수익률 곡선의 최대 낙폭 (음수)
    annualize_factor: int = _DEFAULT_ANNUALIZE_FACTOR


# ===== Public API =====


def score_bucket_analysis(simulated_df: pd.DataFrame) -> dict[int, EVReport]:
    """total_score 구간별 EVReport (점수 모델 유효성 검증).

    Args:
        simulated_df: simulate_dataset 출력 (scenario / simulated_net_pnl_pct 컬럼 필수).
            ``total_score`` 컬럼 필수.

    Returns:
        {0: EVReport, 1: EVReport, 2: EVReport, 3: EVReport}.
        해당 버킷 데이터 없으면 빈 EVReport (n_total=0).

    Raises:
        ValueError: total_score 컬럼 누락 시.
    """
    if "total_score" not in simulated_df.columns:
        raise ValueError("simulated_df에 'total_score' 컬럼 필요")

    out: dict[int, EVReport] = {}
    for score in _SCORE_BUCKETS:
        bucket = simulated_df[simulated_df["total_score"] == score]
        out[score] = compute_ev(bucket)

    return out


def compute_sharpe(
    simulated_df: pd.DataFrame,
    *,
    annualize: int = _DEFAULT_ANNUALIZE_FACTOR,
) -> SharpeMetrics:
    """일별 시계열 → Sharpe / Sortino / Max Drawdown.

    Args:
        simulated_df: simulate_dataset 출력 (trade_date / simulated_net_pnl_pct 필수).
        annualize: 연환산 계수 (12=월간, 252=연간, 5=주간).

    Returns:
        SharpeMetrics. n_days < 1 시 0 채움 (graceful).

    Notes:
        무위험 수익률 0 가정 (Phase 2.5 단순화).
        n_days < 5 시 신뢰도 낮음 warning 로그.
        std=0 시 Sharpe inf 가드 → 0.0 반환.
    """
    if simulated_df.empty or "trade_date" not in simulated_df.columns:
        return _empty_sharpe_metrics(annualize)
    if "scenario" not in simulated_df.columns:
        return _empty_sharpe_metrics(annualize)

    valid = simulated_df[simulated_df["scenario"] != SCENARIO_EXCLUDED]
    if valid.empty or "simulated_net_pnl_pct" not in valid.columns:
        return _empty_sharpe_metrics(annualize)

    # 일별 평균 net_pnl_pct 시계열 — trade_date가 str/Timestamp 혼재해도 안전
    trade_date_dt = pd.to_datetime(valid["trade_date"], errors="coerce")
    daily = (
        valid.assign(_dt=trade_date_dt.dt.date)
        .groupby("_dt")["simulated_net_pnl_pct"]
        .mean()
        .dropna()
        .sort_index()
    )

    n_days = len(daily)
    if n_days == 0:
        return _empty_sharpe_metrics(annualize)

    if n_days < 5:
        logger.warning(
            f"[phase25_walkforward.compute_sharpe] n_days={n_days} < 5: "
            "Sharpe 신뢰도 낮음. 100건 누적 후 재실행 권장"
        )

    daily_arr = daily.astype(float).to_numpy()
    daily_mean = float(daily_arr.mean())
    daily_std = float(daily_arr.std(ddof=1)) if n_days >= 2 else 0.0

    sqrt_factor = math.sqrt(max(annualize, 1))

    # Sharpe — std=0 시 0 (분모 보호)
    sharpe = (daily_mean / daily_std * sqrt_factor) if daily_std > 0 else 0.0

    # Sortino — downside_std (음수 수익률만)
    downside = daily_arr[daily_arr < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) >= 2 else 0.0
    sortino = (daily_mean / downside_std * sqrt_factor) if downside_std > 0 else 0.0

    # Max Drawdown (일별 누적 수익률 곡선)
    cumulative = (1.0 + daily_arr).cumprod()
    running_max = pd.Series(cumulative).cummax().to_numpy()
    drawdowns = (cumulative - running_max) / running_max
    max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0.0

    return SharpeMetrics(
        n_days=n_days,
        daily_mean=daily_mean,
        daily_std=daily_std,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        annualize_factor=annualize,
    )


def compare_scenario_policies(df: pd.DataFrame) -> dict[str, EVReport]:
    """옵션 A/B/C 동일 데이터셋에 적용 → EVReport 비교.

    Args:
        df: load_phase25_dataset 출력 (시뮬 전 raw df).

    Returns:
        {"conservative": EVReport, "aggressive": EVReport, "split": EVReport}.
    """
    out: dict[str, EVReport] = {}
    for policy in _POLICIES:
        sim = simulate_dataset(df, policy=policy)
        out[policy] = compute_ev(sim)
    return out


def walkforward_analysis(
    start_date: str,
    end_date: str,
    *,
    train_days: int = _DEFAULT_TRAIN_DAYS,
    test_days: int = _DEFAULT_TEST_DAYS,
    policy: str = "conservative",
    db_path: Optional[str] = None,
    only_labeled: bool = True,
) -> WalkforwardReport:
    """학습 train_days → 테스트 test_days 슬라이딩 윈도우 EV 시계열.

    Phase 2.5 (소량 데이터) 단계에서는 인프라만 구축. 거래일 수 < train_days+test_days 시
    n_windows=0 graceful 반환. 100건+ 누적 후 본격 분석.

    Args:
        start_date / end_date: ISO 8601 문자열.
        train_days: 학습 윈도우 거래일 수.
        test_days: 테스트 윈도우 거래일 수.
        policy: 시나리오 정책.
        db_path: closing_bet DB 경로 (None=기본).
        only_labeled: True 시 라벨링된 후보만.

    Returns:
        WalkforwardReport.
    """
    df = load_phase25_dataset(
        start_date,
        end_date,
        db_path=db_path,
        only_labeled=only_labeled,
    )
    return _walkforward_from_df(df, train_days=train_days, test_days=test_days, policy=policy)


def generate_report(
    start_date: str,
    end_date: str,
    *,
    output_path: Optional[str] = None,
    db_path: Optional[str] = None,
    only_labeled: bool = True,
    policy: str = _DEFAULT_POLICY,
) -> str:
    """5종 분석 + score 임계 매트릭스 통합 → markdown 리포트 출력.

    Args:
        start_date / end_date: ISO 8601 문자열.
        output_path: 출력 경로. None 시 ``{_DEFAULT_REPORT_DIR}/phase25_ev_report_{YYYYMMDD}.md``.
        db_path: closing_bet DB 경로 (None=기본).
        only_labeled: True 시 라벨링된 후보만.
        policy: 게이트 판정용 시나리오 정책. default ``prd_split_realistic`` (운영 정합).

    Returns:
        실제 작성된 파일 경로 (절대 경로).
    """
    df = load_phase25_dataset(start_date, end_date, db_path=db_path, only_labeled=only_labeled)
    # 게이트 판정 기준 정책 (default prd_split_realistic, 2026-05-13~ 운영 정합)
    sim_df = simulate_dataset(df, policy=policy)

    # 5종 분석
    wf = _walkforward_from_df(df, policy=policy)
    bucket_evs = score_bucket_analysis(sim_df) if not sim_df.empty else {}
    sharpe = compute_sharpe(sim_df)
    policy_evs = compare_scenario_policies(df) if not df.empty else {}
    overall_ev = compute_ev(sim_df)

    # 단위 2-8 게이트 자동 판정 (default 정책 기준)
    gate_verdict = _evaluate_gate(overall_ev, sharpe)

    # 신규: score 임계 필터별 게이트 판정 매트릭스 (5 policy × 3 score = 15셀)
    score_gate_matrix = _build_score_gate_matrix(df)

    # 출력 경로 (KST 기준 — 서버 UTC 23:59 실행 시 파일명 KST 다음 날 방지)
    if output_path is None:
        today = now_kst().strftime("%Y%m%d")
        output_path = str(_PROJECT_ROOT / _DEFAULT_REPORT_DIR / f"phase25_ev_report_{today}.md")

    out_path = Path(output_path)
    if not out_path.is_absolute():
        out_path = _PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # md 본문 생성
    md = _build_markdown(
        start_date=start_date,
        end_date=end_date,
        df=df,
        sim_df=sim_df,
        overall_ev=overall_ev,
        wf=wf,
        bucket_evs=bucket_evs,
        sharpe=sharpe,
        policy_evs=policy_evs,
        gate_verdict=gate_verdict,
        policy=policy,
        score_gate_matrix=score_gate_matrix,
    )

    out_path.write_text(md, encoding="utf-8")
    logger.info(f"[phase25_walkforward] md 리포트 생성: {out_path} (policy={policy})")
    return str(out_path)


def _build_score_gate_matrix(df: pd.DataFrame) -> dict:
    """score 임계(None/2/3) × 정책 5종 = 15셀 매트릭스.

    각 셀에 (n_simulated, net_ev, win_loss_ratio, sharpe, passed) 정보.
    score_min=None은 전체, 2/3은 total_score >= n 필터링 후 시뮬.

    Returns:
        {"score_filters": [None, 2, 3], "policies": [...], "cells": {(score, policy): dict}}.
    """
    if df.empty or "total_score" not in df.columns:
        return {"score_filters": list(_SCORE_FILTERS), "policies": list(_POLICIES), "cells": {}}

    cells: dict = {}
    for score_min in _SCORE_FILTERS:
        if score_min is None:
            sub = df
        else:
            sub = df[df["total_score"] >= score_min]
        if sub.empty:
            for p in _POLICIES:
                cells[(score_min, p)] = None
            continue
        for p in _POLICIES:
            sim = simulate_dataset(sub, policy=p)
            ev = compute_ev(sim)
            sh = compute_sharpe(sim)
            verdict = _evaluate_gate(ev, sh)
            cells[(score_min, p)] = {
                "n_simulated": ev.n_simulated,
                "net_ev": ev.net_ev,
                "win_loss_ratio": _win_loss_ratio(ev),
                "sharpe": sh.sharpe,
                "passed": verdict["passed"],
                "reasons": verdict["reasons"],
            }
    return {"score_filters": list(_SCORE_FILTERS), "policies": list(_POLICIES), "cells": cells}


# ===== 내부 헬퍼 =====


def _walkforward_from_df(
    df: pd.DataFrame,
    *,
    train_days: int = _DEFAULT_TRAIN_DAYS,
    test_days: int = _DEFAULT_TEST_DAYS,
    policy: str = "conservative",
) -> WalkforwardReport:
    """raw df → WalkforwardReport (load 거치지 않는 in-memory 변형, 테스트 활용)."""
    if df.empty or "trade_date" not in df.columns:
        return WalkforwardReport(
            n_windows=0,
            window_evs=[],
            train_days=train_days,
            test_days=test_days,
        )

    unique_dates = sorted(pd.to_datetime(df["trade_date"]).dt.date.unique())
    n_dates = len(unique_dates)

    # 거래일 수 < train_days + test_days → graceful empty
    if n_dates < train_days + test_days:
        return WalkforwardReport(
            n_windows=0,
            window_evs=[],
            train_days=train_days,
            test_days=test_days,
        )

    # 슬라이딩: train_days 학습 + test_days 테스트, 1일씩 이동
    window_evs: list[float] = []
    for start_idx in range(0, n_dates - train_days - test_days + 1):
        test_start = start_idx + train_days
        test_end = test_start + test_days
        test_dates = set(unique_dates[test_start:test_end])

        test_df = df[df["trade_date"].dt.date.isin(test_dates)]
        if test_df.empty:
            continue

        sim_test = simulate_dataset(test_df, policy=policy)
        report = compute_ev(sim_test)

        if report.n_simulated > 0:
            window_evs.append(float(report.net_ev))

    n_windows = len(window_evs)
    if n_windows == 0:
        return WalkforwardReport(
            n_windows=0,
            window_evs=[],
            train_days=train_days,
            test_days=test_days,
        )

    arr = pd.Series(window_evs)
    mean_ev = float(arr.mean())
    std_ev = float(arr.std(ddof=1)) if n_windows >= 2 else 0.0
    stable_pct = float((arr > 0).sum() / n_windows)

    return WalkforwardReport(
        n_windows=n_windows,
        window_evs=window_evs,
        mean_window_ev=mean_ev,
        std_window_ev=std_ev,
        stable_windows_pct=stable_pct,
        train_days=train_days,
        test_days=test_days,
    )


def _empty_sharpe_metrics(annualize: int) -> SharpeMetrics:
    return SharpeMetrics(
        n_days=0,
        daily_mean=0.0,
        daily_std=0.0,
        sharpe=0.0,
        sortino=0.0,
        max_drawdown=0.0,
        annualize_factor=annualize,
    )


def _evaluate_gate(overall_ev: EVReport, sharpe: SharpeMetrics) -> dict:
    """단위 2-8 자동화 게이트 자동 판정.

    Returns:
        {
            "passed": bool,
            "reasons": list[str],   # FAIL 사유 (passed=True 시 빈 리스트)
            "metrics": dict,
        }
    """
    reasons: list[str] = []
    wl_ratio = _win_loss_ratio(overall_ev)
    metrics = {
        "n_simulated": overall_ev.n_simulated,
        "net_ev": overall_ev.net_ev,
        "win_loss_ratio": wl_ratio,
        "sharpe": sharpe.sharpe,
    }

    if overall_ev.n_simulated < _GATE_MIN_SAMPLES:
        reasons.append(f"표본 부족 {overall_ev.n_simulated}<{_GATE_MIN_SAMPLES}")
    if overall_ev.net_ev < _GATE_EV_THRESHOLD:
        reasons.append(f"EV {overall_ev.net_ev:+.4%} < {_GATE_EV_THRESHOLD:+.2%}")
    # Win/Loss ratio: inf (n_stop_risk=0) → 손절 없음, 게이트 PASS
    if not math.isinf(wl_ratio) and wl_ratio < _GATE_WIN_LOSS_RATIO:
        reasons.append(f"Win/Loss ratio {wl_ratio:.2f} < {_GATE_WIN_LOSS_RATIO}")
    if sharpe.sharpe < _GATE_SHARPE:
        reasons.append(f"Sharpe {sharpe.sharpe:.2f} < {_GATE_SHARPE}")

    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "metrics": metrics,
    }


def _win_loss_ratio(ev: EVReport) -> float:
    """Win/Loss ratio = mean_profit_pct / abs(mean_loss_pct).

    - n_stop_risk=0 (손절 없음): inf 반환 → 게이트는 손절 없음=PASS로 처리
    - mean_profit_pct<=0: 익절도 없거나 음수 → 0.0 (실패)
    """
    if ev.n_stop_risk == 0:
        return float("inf")
    if ev.mean_loss_pct >= 0 or ev.mean_profit_pct <= 0:
        return 0.0
    return float(ev.mean_profit_pct / abs(ev.mean_loss_pct))


def _build_markdown(
    *,
    start_date: str,
    end_date: str,
    df: pd.DataFrame,
    sim_df: pd.DataFrame,
    overall_ev: EVReport,
    wf: WalkforwardReport,
    bucket_evs: dict[int, EVReport],
    sharpe: SharpeMetrics,
    policy_evs: dict[str, EVReport],
    gate_verdict: dict,
    policy: str = _DEFAULT_POLICY,
    score_gate_matrix: Optional[dict] = None,
) -> str:
    """md 리포트 본문 생성. 5섹션 + 게이트 판정 + (6) score 임계 매트릭스 + (7) limitation."""
    lines: list[str] = []
    lines.append(f"# Phase 2.5 EV 리포트 — {start_date} ~ {end_date}")
    lines.append("")
    lines.append(f"_생성: {now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST_")
    lines.append(f"_평가 정책: **{policy}**_")
    lines.append("")

    # 단위 2-8 게이트 판정
    if gate_verdict["passed"]:
        lines.append(f"## 🟢 단위 2-8 게이트: PASS (정책={policy})")
    else:
        lines.append(f"## 🔴 단위 2-8 게이트: FAIL (정책={policy})")
        lines.append("")
        lines.append("**미달 사유**:")
        for r in gate_verdict["reasons"]:
            lines.append(f"- {r}")
    lines.append("")

    # 데이터 요약
    lines.append("## 데이터 요약")
    lines.append("")
    lines.append(f"- 라벨링 후보 수: **{len(df)}건**")
    lines.append(f"- 시뮬레이션 가능: **{overall_ev.n_simulated}건** (excluded {overall_ev.n_total - overall_ev.n_simulated}건 제외)")
    lines.append(f"- 거래일 수: **{sharpe.n_days}일**")
    lines.append("")

    # 1. Walk-forward
    lines.append("## 1. Walk-Forward EV 안정성")
    lines.append("")
    lines.append(f"- 학습 윈도우: {wf.train_days}일 / 테스트 윈도우: {wf.test_days}일")
    lines.append(f"- 윈도우 수: **{wf.n_windows}**")
    if wf.n_windows == 0:
        lines.append("- ⚠️ 데이터 부족 (거래일 < train_days+test_days). 100건+ 누적 후 재실행")
    else:
        lines.append(f"- 평균 윈도우 EV: **{wf.mean_window_ev:+.4%}**")
        lines.append(f"- 표준편차: {wf.std_window_ev:.4%}")
        lines.append(f"- 안정 윈도우 비율 (EV>0): **{wf.stable_windows_pct:.1%}**")
        if wf.window_evs:
            evs_str = " / ".join(f"{e:+.3%}" for e in wf.window_evs)
            lines.append(f"- 윈도우 EV 시계열: {evs_str}")
    lines.append("")

    # 2. 점수 구간별 EV
    lines.append("## 2. 점수 구간별 EV 분포")
    lines.append("")
    if not bucket_evs:
        lines.append("- ⚠️ 시뮬 데이터 없음")
    else:
        lines.append("| total_score | n_total | n_simulated | net_ev | mean_profit | mean_loss |")
        lines.append("|---|---|---|---|---|---|")
        for score in sorted(bucket_evs):
            ev = bucket_evs[score]
            lines.append(
                f"| {score} | {ev.n_total} | {ev.n_simulated} | "
                f"{ev.net_ev:+.4%} | {ev.mean_profit_pct:+.4%} | {ev.mean_loss_pct:+.4%} |"
            )
    lines.append("")

    # 3. Sharpe
    lines.append("## 3. Sharpe / Sortino / Max Drawdown")
    lines.append("")
    lines.append(f"- 거래일 수 (n_days): **{sharpe.n_days}**")
    if sharpe.n_days < 5:
        lines.append("  - ⚠️ n_days < 5: 신뢰도 낮음. 100건+ 누적 후 본 분석")
    lines.append(f"- 일별 평균 수익률: {sharpe.daily_mean:+.4%}")
    lines.append(f"- 일별 표준편차: {sharpe.daily_std:.4%}")
    lines.append(f"- 연환산 계수: {sharpe.annualize_factor} (월간={'12' if sharpe.annualize_factor==12 else '?'})")
    lines.append(f"- **Sharpe**: {sharpe.sharpe:+.4f}")
    lines.append(f"- **Sortino**: {sharpe.sortino:+.4f}")
    lines.append(f"- **Max Drawdown**: {sharpe.max_drawdown:+.4%}")
    lines.append("")

    # 4. 정책 옵션 비교 (5종 — conservative/aggressive/split + PRD optimistic/realistic)
    lines.append("## 4. 시나리오 정책 옵션 비교 (5종)")
    lines.append("")
    if not policy_evs:
        lines.append("- ⚠️ 데이터 없음")
    else:
        lines.append("| 정책 | net_ev | n_sim | morning | stop | open | split | prd_gapup | prd_flat | prd_gapdown |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for p in _POLICIES:
            ev = policy_evs.get(p)
            if ev is None:
                continue
            lines.append(
                f"| **{p}** | {ev.net_ev:+.4%} | {ev.n_simulated} | "
                f"{ev.n_morning_exit} | {ev.n_stop_risk} | {ev.n_market_open} | {ev.n_split} | "
                f"{ev.n_prd_gapup} | {ev.n_prd_flat} | {ev.n_prd_gapdown} |"
            )
        # 인사이트 1: A vs B delta
        ev_a = policy_evs.get("conservative")
        ev_b = policy_evs.get("aggressive")
        if ev_a and ev_b:
            delta = ev_b.net_ev - ev_a.net_ev
            lines.append("")
            lines.append(f"- 옵션 B - 옵션 A delta: **{delta:+.4%}** "
                         f"({'낙관 우세' if delta > 0 else '보수 우세'})")
        # 인사이트 2: PRD optimistic vs realistic delta (운영 기대치 구간)
        ev_opt = policy_evs.get("prd_split_optimistic")
        ev_real = policy_evs.get("prd_split_realistic")
        if ev_opt and ev_real:
            delta_prd = ev_opt.net_ev - ev_real.net_ev
            lines.append(f"- PRD optimistic - realistic delta: **{delta_prd:+.4%}** "
                         f"(잔여 50% 청산가 가정 영향)")
            lines.append(f"- **운영 EV 기대치 구간: {ev_real.net_ev:+.4%} ~ {ev_opt.net_ev:+.4%}** "
                         f"(realistic ~ optimistic)")
    lines.append("")

    # 5. 라벨 분포 + 시뮬 시나리오 분포
    lines.append("## 5. 라벨 분포 + 시뮬 시나리오 분포")
    lines.append("")
    if sim_df.empty:
        lines.append("- ⚠️ 시뮬 데이터 없음")
    else:
        sc_dist = sim_df["scenario"].value_counts().to_dict()
        lines.append("| 시나리오 | 건수 |")
        lines.append("|---|---|")
        for sc in (SCENARIO_MORNING_EXIT, SCENARIO_STOP_RISK, SCENARIO_MARKET_OPEN,
                   SCENARIO_SPLIT, SCENARIO_PRD_SPLIT_GAPUP, SCENARIO_PRD_SPLIT_FLAT,
                   SCENARIO_PRD_SPLIT_GAPDOWN, SCENARIO_EXCLUDED):
            lines.append(f"| {sc} | {sc_dist.get(sc, 0)} |")
    lines.append("")

    # 6. score 임계 필터 × 정책 매트릭스 (단위 2-4 entry_executor 진입 결정 근거)
    lines.append("## 6. score 임계 × 정책 게이트 매트릭스 (단위 2-4 진입 결정 근거)")
    lines.append("")
    if not score_gate_matrix or not score_gate_matrix.get("cells"):
        lines.append("- ⚠️ 데이터 없음")
    else:
        lines.append("score 임계별 필터링 후 5개 정책 각각의 게이트 PASS/FAIL 매트릭스 (총 15셀).")
        lines.append("")
        lines.append("| score 필터 | 정책 | n_sim | net_ev | W/L | Sharpe | 결과 |")
        lines.append("|---|---|---|---|---|---|---|")
        for score_min in score_gate_matrix["score_filters"]:
            score_label = "전체" if score_min is None else f"≥{score_min}"
            for p in score_gate_matrix["policies"]:
                cell = score_gate_matrix["cells"].get((score_min, p))
                if cell is None:
                    lines.append(f"| {score_label} | {p} | 0 | — | — | — | ⚠️ 데이터 없음 |")
                    continue
                wl = cell["win_loss_ratio"]
                wl_str = "∞" if math.isinf(wl) else f"{wl:.2f}"
                verdict = "🟢 PASS" if cell["passed"] else "🔴 FAIL"
                lines.append(
                    f"| {score_label} | {p} | {cell['n_simulated']} | "
                    f"{cell['net_ev']:+.4%} | {wl_str} | {cell['sharpe']:+.2f} | {verdict} |"
                )
        lines.append("")
        lines.append("**해석 가이드**:")
        lines.append("- 표본<100건은 자동 FAIL되지만 EV/W-L/Sharpe 개선 추세로 정성 판단 가능")
        lines.append("- score≥2 / score≥3 임계 진입 시 EV 양수 + Sharpe 개선이면 축소 포지션 진입 근거")
    lines.append("")

    # 7. Limitation (PRD 분할매도 시뮬 한계)
    lines.append("## 7. Limitation — PRD 분할매도 시뮬 가정")
    lines.append("")
    lines.append("PRD 10-1 시가 액션 매트릭스는 다음날 09:00~09:30 사이 분 단위 가격 추이에")
    lines.append("따라 분할매도가 발화한다. 현 라벨 데이터는 시점 3개(시초가/09:30 고가/09:30 저가)만")
    lines.append("저장되어 있어 다음 가정으로 시뮬:")
    lines.append("")
    lines.append("- **prd_split_optimistic**: 갭업(open ≥ +0.5%) 시 잔여 50% × `morning_high_pct` 청산")
    lines.append("  - 09:00~09:30 고점을 정확히 잡는다는 낙관적 가정 → EV **상한** 추정치")
    lines.append("- **prd_split_realistic**: 갭업 시 잔여 50% × `(open + morning_high) / 2` 청산")
    lines.append("  - 시초가와 고가의 중간값에서 청산되는 현실적 가정 → EV **하한** 추정치")
    lines.append("- 보합/약갭다운/갭다운(open < +0.5%)은 100% 시초가 매도 (현 라벨 데이터로 충분 정합)")
    lines.append("")
    lines.append("**운영 시 실제 EV는 realistic ~ optimistic 구간 사이에 위치**할 것으로 예상.")
    lines.append("분 단위 데이터 수집 인프라(단위 2-1 orderbook_snapshots 누적) 진척 시 정밀화 가능.")
    lines.append("")

    # 메타
    lines.append("---")
    lines.append("")
    lines.append("_본 리포트는 단위 2-7c walk-forward 분석 인프라 산출물이다. "
                 "단위 2-8 100건 자동화 게이트 합격 판정 근거이며, "
                 "자동매매 진입 결정은 본 단위에서 하지 않는다._")

    return "\n".join(lines) + "\n"


# ===== CLI 진입점 =====


def _main_cli() -> None:
    """단발 검증용 CLI: md 리포트 생성.

    --policy: 게이트 판정용 정책 (default prd_split_realistic, 운영 정합)
              {conservative, aggressive, split, prd_split_optimistic, prd_split_realistic}
    --score-min: score 필터 (없으면 매트릭스에서 전체/≥2/≥3 모두 자동 표시)
    """
    import argparse

    parser = argparse.ArgumentParser(description="Phase 2.5 walk-forward 분석 리포트")
    parser.add_argument("--start", default="2026-05-04")
    parser.add_argument("--end", default="2026-05-07")
    parser.add_argument("--output", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument(
        "--policy",
        default=_DEFAULT_POLICY,
        choices=list(_POLICIES),
        help=f"게이트 판정 정책 (default {_DEFAULT_POLICY})",
    )
    args = parser.parse_args()

    path = generate_report(
        start_date=args.start,
        end_date=args.end,
        output_path=args.output,
        db_path=args.db_path,
        policy=args.policy,
    )
    print(f"리포트 경로: {path}")


if __name__ == "__main__":
    _main_cli()
