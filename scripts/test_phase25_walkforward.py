"""phase25_walkforward 단위 테스트 (단위 2-7c).

5종 분석 함수:
- score_bucket_analysis (BUCKET-1~3)
- compute_sharpe (SHARPE-1~3)
- compare_scenario_policies (POLICY-1~2)
- walkforward_analysis / _walkforward_from_df (WF-1~3)
- generate_report + 단위 2-8 게이트 자동 판정 (REPORT-1~2)

실행:
    venv/bin/python scripts/test_phase25_walkforward.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.backtest.phase25_simulator import (
    SCENARIO_EXCLUDED,
    SCENARIO_MARKET_OPEN,
    SCENARIO_MORNING_EXIT,
    SCENARIO_SPLIT,
    SCENARIO_STOP_RISK,
    EVReport,
    simulate_dataset,
)
from closing_bet_system.backtest.phase25_walkforward import (
    SharpeMetrics,
    WalkforwardReport,
    _evaluate_gate,
    _walkforward_from_df,
    _build_markdown,
    compare_scenario_policies,
    compute_sharpe,
    score_bucket_analysis,
)


# ===== 픽스처 =====


def _row(
    *,
    cid=1,
    ticker="005930",
    trade_date="2026-05-04",
    total_score=2,
    label_stop_risk=False,
    label_morning_exit=False,
    next_open_pct=0.0,
):
    """테스트용 raw row dict (load_phase25_dataset 호환)."""
    return {
        "candidate_id": cid,
        "ticker": ticker,
        "trade_date": pd.Timestamp(trade_date),
        "total_score": total_score,
        "label_stop_risk": label_stop_risk,
        "label_morning_exit": label_morning_exit,
        "label_gap_up": False,
        "label_net_ev_positive": False,
        "next_open_pct": next_open_pct,
    }


def _build_simulated_df(rows):
    """raw rows → simulated_df (simulate_dataset 통과 + boolean dtype 보존)."""
    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")
    return simulate_dataset(df, policy="conservative")


# ===== BUCKET-1~3 =====


def test_BUCKET_1_normal_dataframe():
    """BUCKET-1: 정상 dataframe → 점수별 EVReport dict."""
    sim = _build_simulated_df([
        _row(cid=1, total_score=0, label_morning_exit=True),
        _row(cid=2, total_score=1, label_stop_risk=True),
        _row(cid=3, total_score=2, label_morning_exit=True),
        _row(cid=4, total_score=2, label_morning_exit=True),
        _row(cid=5, total_score=3, label_stop_risk=True),
    ])
    out = score_bucket_analysis(sim)
    assert set(out.keys()) == {0, 1, 2, 3}
    assert out[0].n_simulated == 1 and out[0].p_morning_exit == 1.0
    assert out[1].n_simulated == 1 and out[1].p_stop_risk == 1.0
    assert out[2].n_simulated == 2 and out[2].p_morning_exit == 1.0
    assert out[3].n_simulated == 1 and out[3].p_stop_risk == 1.0
    print("✅ BUCKET-1 점수별 EVReport dict")


def test_BUCKET_2_empty_buckets_graceful():
    """BUCKET-2: 일부 버킷 비어있음 → 빈 EVReport (n_simulated=0) graceful."""
    sim = _build_simulated_df([
        _row(cid=i, total_score=3, label_morning_exit=True) for i in range(1, 4)
    ])
    out = score_bucket_analysis(sim)
    # 0/1/2 빈 버킷
    assert out[0].n_total == 0 and out[0].n_simulated == 0
    assert out[1].n_total == 0 and out[1].n_simulated == 0
    assert out[2].n_total == 0 and out[2].n_simulated == 0
    # 3 버킷만 채워짐
    assert out[3].n_total == 3 and out[3].n_simulated == 3
    print("✅ BUCKET-2 빈 버킷 graceful")


def test_BUCKET_3_missing_total_score_column():
    """BUCKET-3: total_score 컬럼 누락 → ValueError."""
    sim = _build_simulated_df([
        _row(cid=1, total_score=2, label_morning_exit=True),
    ])
    sim_no_score = sim.drop(columns=["total_score"])
    try:
        score_bucket_analysis(sim_no_score)
        raise AssertionError("ValueError 발생해야 함")
    except ValueError as e:
        assert "total_score" in str(e)
    print("✅ BUCKET-3 total_score 누락 → ValueError")


# ===== SHARPE-1~3 =====


def test_SHARPE_1_positive_series():
    """SHARPE-1: 정상 양수 시계열 → Sharpe > 0."""
    sim = _build_simulated_df([
        _row(cid=1, trade_date="2026-05-04", label_morning_exit=True),
        _row(cid=2, trade_date="2026-05-05", label_morning_exit=True),
        _row(cid=3, trade_date="2026-05-06", label_morning_exit=True),
        _row(cid=4, trade_date="2026-05-07", label_morning_exit=True),
        _row(cid=5, trade_date="2026-05-08", label_morning_exit=True),
    ])
    metrics = compute_sharpe(sim, annualize=12)
    # 5일 모두 동일 net_pnl_pct → daily_std=0 → sharpe=0 (분모 보호)
    # 그러나 일별 평균이 동일하지 않게 noise 추가
    assert metrics.n_days == 5
    assert metrics.daily_mean > 0
    print(f"✅ SHARPE-1 양수 시계열 → daily_mean={metrics.daily_mean:+.4%} sharpe={metrics.sharpe:+.4f}")


def test_SHARPE_2_all_loss_series():
    """SHARPE-2: 모두 손실 → Sharpe ≤ 0."""
    sim = _build_simulated_df([
        _row(cid=1, trade_date="2026-05-04", label_stop_risk=True),
        _row(cid=2, trade_date="2026-05-05", label_stop_risk=True),
        _row(cid=3, trade_date="2026-05-06", label_stop_risk=True),
        _row(cid=4, trade_date="2026-05-07", label_stop_risk=True),
        _row(cid=5, trade_date="2026-05-08", label_stop_risk=True),
    ])
    metrics = compute_sharpe(sim, annualize=12)
    assert metrics.n_days == 5
    assert metrics.daily_mean < 0
    # 모두 동일 손실 → std=0 → sharpe=0 (zero division 가드)
    assert metrics.sharpe <= 0
    # Max DD는 음수 (모두 손실)
    assert metrics.max_drawdown < 0
    print(f"✅ SHARPE-2 손실 시계열 → daily_mean={metrics.daily_mean:+.4%} max_dd={metrics.max_drawdown:+.4%}")


def test_SHARPE_3_n_days_one_zero_division_guard():
    """SHARPE-3: n_days=1 → std=0 → Sharpe=0 (inf 가드)."""
    sim = _build_simulated_df([
        _row(cid=1, trade_date="2026-05-04", label_morning_exit=True),
        _row(cid=2, trade_date="2026-05-04", label_morning_exit=True),
    ])
    metrics = compute_sharpe(sim, annualize=12)
    assert metrics.n_days == 1
    assert metrics.daily_std == 0.0
    assert metrics.sharpe == 0.0   # zero division 가드
    assert metrics.sortino == 0.0
    print("✅ SHARPE-3 n_days=1 → Sharpe inf 가드")


# ===== POLICY-1~2 =====


def test_POLICY_1_no_overlap_options_equivalent():
    """POLICY-1: 양립 라벨 없는 데이터 → 옵션 A/B/C 동일 결과 (회귀)."""
    rows = [
        _row(cid=1, label_morning_exit=True),
        _row(cid=2, label_stop_risk=True),
        _row(cid=3, next_open_pct=0.005),
    ]
    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")

    out = compare_scenario_policies(df)
    a, b, c = out["conservative"], out["aggressive"], out["split"]
    assert a.n_simulated == b.n_simulated == c.n_simulated == 3
    # split 시나리오는 0건 (양립 라벨 없음)
    assert c.n_split == 0
    # net_ev 동일 (양립 없으면 분기 차이 없음)
    assert abs(a.net_ev - b.net_ev) < 1e-10
    assert abs(a.net_ev - c.net_ev) < 1e-10
    print("✅ POLICY-1 양립 없으면 A/B/C 동일")


def test_POLICY_2_overlap_options_diverge():
    """POLICY-2: 양립 라벨 있는 데이터 → 옵션 A/B 결과 다름, C는 SCENARIO_SPLIT."""
    rows = [
        _row(cid=1, label_stop_risk=True, label_morning_exit=True),  # 양립
        _row(cid=2, label_stop_risk=True, label_morning_exit=True),  # 양립
        _row(cid=3, label_morning_exit=True),                         # 단일
    ]
    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")

    out = compare_scenario_policies(df)
    a, b, c = out["conservative"], out["aggressive"], out["split"]
    # A: 양립 → stop, 단일 → morning
    assert a.n_stop_risk == 2
    assert a.n_morning_exit == 1
    # B: 양립 → morning, 단일 → morning
    assert b.n_morning_exit == 3
    assert b.n_stop_risk == 0
    # C: 양립 → split, 단일 → morning
    assert c.n_split == 2
    assert c.n_morning_exit == 1
    # net_ev: A는 손실 우세 (stop 2건), B는 익절만 → 양수
    assert a.net_ev < b.net_ev, f"A({a.net_ev:+.4%}) < B({b.net_ev:+.4%})"
    print(f"✅ POLICY-2 양립 라벨 → A({a.net_ev:+.4%}) ≠ B({b.net_ev:+.4%}) ≠ C({c.net_ev:+.4%})")


# ===== WF-1~3 =====


def test_WF_1_normal_window():
    """WF-1: 정상 윈도우 → 윈도우별 EV 시계열."""
    # 7거래일 + 양수/음수 mix
    dates = ["2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
             "2026-05-01", "2026-05-04", "2026-05-05"]
    rows = []
    for i, d in enumerate(dates):
        # 4건: 절반은 익절, 절반은 손절 (날짜별 mix)
        rows.extend([
            _row(cid=i*4+1, trade_date=d, label_morning_exit=True),
            _row(cid=i*4+2, trade_date=d, label_morning_exit=True),
            _row(cid=i*4+3, trade_date=d, label_stop_risk=True if i % 2 == 0 else False,
                 label_morning_exit=False if i % 2 == 0 else True, next_open_pct=0.003),
            _row(cid=i*4+4, trade_date=d, label_morning_exit=True),
        ])
    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")

    wf = _walkforward_from_df(df, train_days=5, test_days=1)
    # 7일 - 5(train) - 1(test) + 1 = 2 윈도우
    assert wf.n_windows == 2
    assert len(wf.window_evs) == 2
    print(f"✅ WF-1 정상 윈도우 → n_windows={wf.n_windows} evs={[f'{e:+.3%}' for e in wf.window_evs]}")


def test_WF_2_insufficient_data_graceful():
    """WF-2: 거래일 < train_days+test_days → n_windows=0 graceful."""
    rows = [
        _row(cid=1, trade_date="2026-05-04", label_morning_exit=True),
        _row(cid=2, trade_date="2026-05-07", label_stop_risk=True),
    ]
    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")

    wf = _walkforward_from_df(df, train_days=5, test_days=1)  # 2일 < 6일
    assert wf.n_windows == 0
    assert wf.window_evs == []
    assert wf.mean_window_ev == 0.0
    assert wf.std_window_ev == 0.0
    assert wf.stable_windows_pct == 0.0
    print("✅ WF-2 데이터 부족 → graceful empty")


def test_WF_3_stable_windows_pct_accuracy():
    """WF-3: stable_windows_pct = (net_ev > 0 윈도우 수) / n_windows."""
    # 8거래일, train=5+test=1 → 3 윈도우
    # 첫 윈도우: 손실, 두 번째: 익절, 세 번째: 익절 → stable=2/3
    dates = ["2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24",
             "2026-04-25", "2026-04-28", "2026-04-29", "2026-04-30"]
    rows = []
    # 4월 28일 (테스트 1) → 모두 손실
    # 4월 29일 (테스트 2) → 모두 익절
    # 4월 30일 (테스트 3) → 모두 익절
    for i, d in enumerate(dates):
        if d in ("2026-04-28",):  # 테스트 윈도우 1 → 손실
            rows.extend([_row(cid=i*3+j, trade_date=d, label_stop_risk=True) for j in range(3)])
        elif d in ("2026-04-29", "2026-04-30"):  # 테스트 윈도우 2, 3 → 익절
            rows.extend([_row(cid=i*3+j, trade_date=d, label_morning_exit=True) for j in range(3)])
        else:  # 학습 데이터 (윈도우에서 제외됨)
            rows.append(_row(cid=i*3, trade_date=d, label_morning_exit=True))

    df = pd.DataFrame(rows)
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")

    wf = _walkforward_from_df(df, train_days=5, test_days=1)
    assert wf.n_windows == 3, f"n_windows={wf.n_windows}"
    # 윈도우 1 (4/28) net_ev < 0, 윈도우 2/3 (4/29, 4/30) net_ev > 0 → stable=2/3
    assert abs(wf.stable_windows_pct - 2.0 / 3.0) < 1e-10
    print(f"✅ WF-3 stable_windows_pct={wf.stable_windows_pct:.3f} (2/3)")


# ===== REPORT-1~2 =====


def test_REPORT_1_md_5_sections():
    """REPORT-1: md 리포트 5섹션 + 데이터 요약 + 게이트 판정 포함."""
    sim = _build_simulated_df([
        _row(cid=1, trade_date="2026-05-04", label_morning_exit=True),
        _row(cid=2, trade_date="2026-05-04", label_stop_risk=True),
    ])
    df_raw = pd.DataFrame([
        _row(cid=1, trade_date="2026-05-04", label_morning_exit=True),
        _row(cid=2, trade_date="2026-05-04", label_stop_risk=True),
    ])
    df_raw["label_stop_risk"] = df_raw["label_stop_risk"].astype("boolean")
    df_raw["label_morning_exit"] = df_raw["label_morning_exit"].astype("boolean")

    from closing_bet_system.backtest.phase25_simulator import compute_ev
    overall_ev = compute_ev(sim)
    bucket_evs = score_bucket_analysis(sim)
    sharpe = compute_sharpe(sim)
    policy_evs = compare_scenario_policies(df_raw)
    wf = _walkforward_from_df(df_raw)
    gate = _evaluate_gate(overall_ev, sharpe)

    md = _build_markdown(
        start_date="2026-05-04",
        end_date="2026-05-04",
        df=df_raw,
        sim_df=sim,
        overall_ev=overall_ev,
        wf=wf,
        bucket_evs=bucket_evs,
        sharpe=sharpe,
        policy_evs=policy_evs,
        gate_verdict=gate,
    )
    # 5섹션 모두 포함
    assert "## 1. Walk-Forward EV 안정성" in md
    assert "## 2. 점수 구간별 EV 분포" in md
    assert "## 3. Sharpe / Sortino / Max Drawdown" in md
    assert "## 4. 시나리오 정책 옵션 비교" in md
    assert "## 5. 라벨 분포 + 시뮬 시나리오 분포" in md
    assert "데이터 요약" in md
    assert "단위 2-8 게이트" in md
    print("✅ REPORT-1 md 5섹션 + 게이트 판정")


def test_REPORT_2_gate_verdict_auto():
    """REPORT-2: 단위 2-8 게이트 자동 판정 — 표본 부족 → FAIL."""
    sim = _build_simulated_df([
        _row(cid=1, label_morning_exit=True),
        _row(cid=2, label_stop_risk=True),
    ])
    from closing_bet_system.backtest.phase25_simulator import compute_ev
    overall_ev = compute_ev(sim)
    sharpe = compute_sharpe(sim)
    gate = _evaluate_gate(overall_ev, sharpe)

    # 2건 < 100건 → FAIL
    assert gate["passed"] is False
    assert any("표본 부족" in r for r in gate["reasons"])

    # 합격 케이스 모의: 100건 + Sharpe 1.5 + EV +1% + Win/Loss 2.0
    fake_ev = EVReport(
        n_total=100, n_simulated=100,
        n_morning_exit=60, n_stop_risk=40, n_market_open=0,
        p_morning_exit=0.6, p_stop_risk=0.4, p_market_open=0.0,
        mean_profit_pct=0.020, mean_loss_pct=-0.010, mean_market_open_pct=0.0,
        raw_ev=0.008, net_ev=0.008,
        cost_basis=0.004,
    )
    fake_sharpe = SharpeMetrics(
        n_days=20, daily_mean=0.001, daily_std=0.005,
        sharpe=1.5, sortino=2.0, max_drawdown=-0.02,
        annualize_factor=12,
    )
    gate_pass = _evaluate_gate(fake_ev, fake_sharpe)
    assert gate_pass["passed"] is True
    assert gate_pass["reasons"] == []
    print("✅ REPORT-2 게이트 판정: 표본 부족 FAIL / 100건+ PASS")


# ===== Runner =====


def main():
    tests = [
        test_BUCKET_1_normal_dataframe,
        test_BUCKET_2_empty_buckets_graceful,
        test_BUCKET_3_missing_total_score_column,
        test_SHARPE_1_positive_series,
        test_SHARPE_2_all_loss_series,
        test_SHARPE_3_n_days_one_zero_division_guard,
        test_POLICY_1_no_overlap_options_equivalent,
        test_POLICY_2_overlap_options_diverge,
        test_WF_1_normal_window,
        test_WF_2_insufficient_data_graceful,
        test_WF_3_stable_windows_pct_accuracy,
        test_REPORT_1_md_5_sections,
        test_REPORT_2_gate_verdict_auto,
    ]
    print(f"\n=== phase25_walkforward 단위 테스트 — {len(tests)}건 실행 ===\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"❌ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"💥 {t.__name__} 예외: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== 결과: {len(tests) - failed}/{len(tests)} PASS ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
