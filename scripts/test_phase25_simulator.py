"""phase25_simulator 단위 테스트.

PRD 12-1 라벨 매핑 + 12-2 EV 계산식 정합 검증.
mock cost_engine으로 비용 차감 격리 + 실제 cost_engine 통합 검증 양쪽.

실행:
    venv/bin/python scripts/test_phase25_simulator.py
"""

from __future__ import annotations

import sys
from dataclasses import asdict
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
    SimulationResult,
    _MORNING_EXIT_TARGET_PCT,
    _STOP_RISK_TARGET_PCT,
    compute_ev,
    simulate_candidate,
    simulate_dataset,
)
from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine


# ===== 픽스처 =====


def _make_engine() -> CostSlippageEngine:
    """예측 가능한 cost_engine (settings.yaml 기본값 사용)."""
    return CostSlippageEngine()


def _row(
    *,
    cid=1,
    ticker="005930",
    label_stop_risk=False,
    label_morning_exit=False,
    next_open_pct=0.0,
    label_gap_up=False,
    label_net_ev_positive=False,
):
    """테스트용 row dict 생성."""
    return {
        "candidate_id": cid,
        "ticker": ticker,
        "label_stop_risk": label_stop_risk,
        "label_morning_exit": label_morning_exit,
        "label_gap_up": label_gap_up,
        "label_net_ev_positive": label_net_ev_positive,
        "next_open_pct": next_open_pct,
    }


# ===== Step 2.1 — simulate_candidate 시나리오 =====


def test_SC_1_stop_risk_only():
    """SC-1: label_stop_risk=True only → stop_risk 시나리오, exit_pct=-1.0%."""
    engine = _make_engine()
    row = _row(label_stop_risk=True, label_morning_exit=False, next_open_pct=-0.005)
    result = simulate_candidate(row, cost_engine=engine)
    assert result.scenario == SCENARIO_STOP_RISK, f"scenario={result.scenario}"
    assert result.simulated_exit_pct == _STOP_RISK_TARGET_PCT
    # 비용 차감 후 -1.0%보다 더 큰 손실
    assert result.simulated_net_pnl_pct < _STOP_RISK_TARGET_PCT
    assert result.simulated_net_pnl_pct > -0.02  # 합리적 범위
    print("✅ SC-1 stop_risk → -1.0% 매도 + 비용 차감")


def test_SC_2_morning_exit_only():
    """SC-2: label_morning_exit=True only → morning_exit, exit_pct=+1.2%."""
    engine = _make_engine()
    row = _row(label_stop_risk=False, label_morning_exit=True, next_open_pct=0.008)
    result = simulate_candidate(row, cost_engine=engine)
    assert result.scenario == SCENARIO_MORNING_EXIT
    assert result.simulated_exit_pct == _MORNING_EXIT_TARGET_PCT
    # 비용 차감 후 +1.2% 보다 작은 익절
    assert result.simulated_net_pnl_pct < _MORNING_EXIT_TARGET_PCT
    assert result.simulated_net_pnl_pct > 0  # 양수 유지
    print("✅ SC-2 morning_exit → +1.2% 매도 + 비용 차감")


def test_SC_3_market_open():
    """SC-3: 둘 다 False, next_open_pct=+0.5% → market_open."""
    engine = _make_engine()
    row = _row(label_stop_risk=False, label_morning_exit=False, next_open_pct=0.005)
    result = simulate_candidate(row, cost_engine=engine)
    assert result.scenario == SCENARIO_MARKET_OPEN
    assert result.simulated_exit_pct == 0.005
    assert result.simulated_net_pnl_pct < 0.005  # 비용 차감
    print("✅ SC-3 market_open → next_open_pct 매도")


def test_SC_4_both_labels_conservative():
    """SC-4: stop_risk + morning_exit 둘 다 True → conservative 정책으로 stop_risk 우선."""
    engine = _make_engine()
    row = _row(label_stop_risk=True, label_morning_exit=True, next_open_pct=0.0)
    result = simulate_candidate(row, policy="conservative", cost_engine=engine)
    assert result.scenario == SCENARIO_STOP_RISK, "conservative=loss aversion"
    assert result.simulated_exit_pct == _STOP_RISK_TARGET_PCT
    print("✅ SC-4 양립 라벨 → stop_risk 우선 (보수)")


def test_SC_5_excluded_null_labels():
    """SC-5: 라벨 둘 다 None (pd.NA) → excluded."""
    engine = _make_engine()
    row = _row(label_stop_risk=None, label_morning_exit=None, next_open_pct=None)
    result = simulate_candidate(row, cost_engine=engine)
    assert result.scenario == SCENARIO_EXCLUDED
    assert result.excluded_reason == "missing_labels"
    assert result.simulated_exit_pct is None
    assert result.simulated_net_pnl_pct is None
    print("✅ SC-5 NULL 라벨 → excluded")


def test_SC_6_cost_deduction_verified():
    """SC-6: 비용 차감 정량 검증 — morning_exit +1.2% → net_pnl_pct ≈ +0.79%."""
    engine = _make_engine()
    row = _row(label_morning_exit=True)
    result = simulate_candidate(row, cost_engine=engine)
    # cost_engine round_trip ≈ 0.41% (수수료 0.03% × 2 + 거래세 0.18% + 슬리피지 0.2%)
    expected_min = 0.012 - 0.005  # 0.7%
    expected_max = 0.012 - 0.003  # 0.9%
    assert expected_min < result.simulated_net_pnl_pct < expected_max, (
        f"net_pnl_pct={result.simulated_net_pnl_pct} 범위 0.7~0.9% 외"
    )
    print(f"✅ SC-6 비용 차감: morning_exit +1.2% → net {result.simulated_net_pnl_pct:.4%}")


def test_SC_9_aggressive_both_labels():
    """SC-9: policy='aggressive' + 양립 라벨 → morning_exit 우선 (옵션 B)."""
    engine = _make_engine()
    row = _row(label_stop_risk=True, label_morning_exit=True, next_open_pct=0.0)
    result = simulate_candidate(row, policy="aggressive", cost_engine=engine)
    assert result.scenario == SCENARIO_MORNING_EXIT, f"aggressive=익절 우선 (실제: {result.scenario})"
    assert result.simulated_exit_pct == _MORNING_EXIT_TARGET_PCT
    assert result.simulated_net_pnl_pct > 0  # 익절 net 양수
    print("✅ SC-9 aggressive 양립 라벨 → morning_exit 우선")


def test_SC_10_split_both_labels_average():
    """SC-10: policy='split' + 양립 라벨 → SCENARIO_SPLIT, net_pnl=morning/stop 평균."""
    engine = _make_engine()
    row = _row(label_stop_risk=True, label_morning_exit=True, next_open_pct=0.0)
    result = simulate_candidate(row, policy="split", cost_engine=engine)
    assert result.scenario == SCENARIO_SPLIT
    # exit_pct 평균: (+0.012 + -0.010) / 2 = +0.001
    expected_exit = (_MORNING_EXIT_TARGET_PCT + _STOP_RISK_TARGET_PCT) / 2.0
    assert abs(result.simulated_exit_pct - expected_exit) < 1e-10

    # net_pnl 평균 = (morning_net + stop_net) / 2 — 양수와 음수 평균이라 음수 경향
    # morning_net ≈ +0.0079, stop_net ≈ -0.0141 → 평균 ≈ -0.0031
    morning_only = _row(label_morning_exit=True)
    stop_only = _row(label_stop_risk=True)
    rm = simulate_candidate(morning_only, cost_engine=engine)
    rs = simulate_candidate(stop_only, cost_engine=engine)
    expected_pnl = (rm.simulated_net_pnl_pct + rs.simulated_net_pnl_pct) / 2.0
    assert abs(result.simulated_net_pnl_pct - expected_pnl) < 1e-10
    print(f"✅ SC-10 split 양립 라벨 → 평균 net_pnl={result.simulated_net_pnl_pct:+.5f}")


def test_SC_11_options_equivalent_for_single_label():
    """SC-11: 단일 라벨 행은 옵션 A/B/C 모두 동일 결과 (회귀)."""
    engine = _make_engine()
    # 단일 morning 라벨
    row_m = _row(label_morning_exit=True)
    r_a = simulate_candidate(row_m, policy="conservative", cost_engine=engine)
    r_b = simulate_candidate(row_m, policy="aggressive", cost_engine=engine)
    r_c = simulate_candidate(row_m, policy="split", cost_engine=engine)
    assert r_a.scenario == r_b.scenario == r_c.scenario == SCENARIO_MORNING_EXIT
    assert r_a.simulated_net_pnl_pct == r_b.simulated_net_pnl_pct == r_c.simulated_net_pnl_pct

    # 단일 stop 라벨
    row_s = _row(label_stop_risk=True)
    r_a2 = simulate_candidate(row_s, policy="conservative", cost_engine=engine)
    r_b2 = simulate_candidate(row_s, policy="aggressive", cost_engine=engine)
    r_c2 = simulate_candidate(row_s, policy="split", cost_engine=engine)
    assert r_a2.scenario == r_b2.scenario == r_c2.scenario == SCENARIO_STOP_RISK
    assert r_a2.simulated_net_pnl_pct == r_b2.simulated_net_pnl_pct == r_c2.simulated_net_pnl_pct
    print("✅ SC-11 단일 라벨 → 옵션 A/B/C 동일 (회귀)")


def test_SC_8_one_side_null_to_market_open():
    """SC-8: 한쪽 라벨 NULL → 나머지 라벨로 판단 (excluded 아님).

    code-tester 주의 1번 반영: docstring과 동작 일치 명시 검증.
    """
    engine = _make_engine()
    # stop=None, morning=False, open_pct=+0.5% → market_open (excluded 아님)
    row1 = _row(label_stop_risk=None, label_morning_exit=False, next_open_pct=0.005)
    r1 = simulate_candidate(row1, cost_engine=engine)
    assert r1.scenario == SCENARIO_MARKET_OPEN, f"한쪽 NULL → market_open (실제: {r1.scenario})"

    # stop=False, morning=None, open_pct=+0.5% → market_open
    row2 = _row(label_stop_risk=False, label_morning_exit=None, next_open_pct=0.005)
    r2 = simulate_candidate(row2, cost_engine=engine)
    assert r2.scenario == SCENARIO_MARKET_OPEN

    # stop=None, morning=True → morning_exit (한쪽 NULL이라도 True가 우선)
    row3 = _row(label_stop_risk=None, label_morning_exit=True)
    r3 = simulate_candidate(row3, cost_engine=engine)
    assert r3.scenario == SCENARIO_MORNING_EXIT
    print("✅ SC-8 한쪽 NULL → 나머지 라벨로 판단")


def test_SC_7_invalid_policy():
    """SC-7: 잘못된 policy → ValueError."""
    engine = _make_engine()
    row = _row(label_morning_exit=True)
    try:
        simulate_candidate(row, policy="not_a_policy", cost_engine=engine)
        raise AssertionError("ValueError 발생해야 함")
    except ValueError as e:
        assert "scenario_policy" in str(e)
    print("✅ SC-7 잘못된 policy")


# ===== Step 2.2 — simulate_dataset 통합 =====


def test_DS_1_normal_dataframe():
    """DS-1: 정상 dataframe → scenario 컬럼 분포 정상."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=1, label_stop_risk=True),
        _row(cid=2, label_morning_exit=True),
        _row(cid=3, next_open_pct=0.003),
        _row(cid=4, label_stop_risk=True, label_morning_exit=True),
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    assert len(sim) == 4
    scenarios = sim["scenario"].tolist()
    assert scenarios.count(SCENARIO_STOP_RISK) == 2  # cid 1, 4
    assert scenarios.count(SCENARIO_MORNING_EXIT) == 1  # cid 2
    assert scenarios.count(SCENARIO_MARKET_OPEN) == 1  # cid 3
    print("✅ DS-1 시나리오 분포")


def test_DS_2_empty_df():
    """DS-2: 빈 df → 빈 df 반환 (결과 컬럼 추가)."""
    engine = _make_engine()
    df = pd.DataFrame(columns=["candidate_id", "ticker", "label_stop_risk",
                                "label_morning_exit", "next_open_pct"])
    sim = simulate_dataset(df, cost_engine=engine)
    assert len(sim) == 0
    for col in ("scenario", "simulated_exit_pct", "simulated_net_pnl_pct",
                "simulated_excluded_reason"):
        assert col in sim.columns, f"{col} 컬럼 누락"
    print("✅ DS-2 빈 df → 컬럼 추가")


def test_DS_3_all_null_labels():
    """DS-3: 모든 행 라벨 NULL → 모두 excluded."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=1, label_stop_risk=None, label_morning_exit=None),
        _row(cid=2, label_stop_risk=None, label_morning_exit=None),
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    assert (sim["scenario"] == SCENARIO_EXCLUDED).all()
    assert (sim["simulated_excluded_reason"] == "missing_labels").all()
    print("✅ DS-3 모두 excluded")


def test_DS_4_original_df_preserved():
    """DS-4: 원본 df 변경 없음 (copy 검증)."""
    engine = _make_engine()
    df = pd.DataFrame([_row(label_stop_risk=True)])
    original_cols = set(df.columns)
    _ = simulate_dataset(df, cost_engine=engine)
    assert set(df.columns) == original_cols, "원본 컬럼 변경됨"
    assert "scenario" not in df.columns, "원본에 결과 컬럼 추가됨"
    print("✅ DS-4 원본 df 보존")


def test_DS_5_boolean_dtype_compat():
    """DS-5: 라벨 BooleanDtype + pd.NA 정상 처리 (단위 2-7a 데이터 호환)."""
    engine = _make_engine()
    df = pd.DataFrame([
        {"candidate_id": 1, "ticker": "A", "label_stop_risk": True,
         "label_morning_exit": False, "next_open_pct": 0.005},
        {"candidate_id": 2, "ticker": "B", "label_stop_risk": pd.NA,
         "label_morning_exit": pd.NA, "next_open_pct": pd.NA},
    ])
    df["label_stop_risk"] = df["label_stop_risk"].astype("boolean")
    df["label_morning_exit"] = df["label_morning_exit"].astype("boolean")
    sim = simulate_dataset(df, cost_engine=engine)
    assert sim.iloc[0]["scenario"] == SCENARIO_STOP_RISK
    assert sim.iloc[1]["scenario"] == SCENARIO_EXCLUDED
    print("✅ DS-5 BooleanDtype + pd.NA")


# ===== Step 2.3 — compute_ev 정합 =====


def test_EV_1_all_morning_exit():
    """EV-1: 100% morning_exit → P_exit=1.0, raw_ev=평균 익절."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=i, label_morning_exit=True) for i in range(1, 6)
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    report = compute_ev(sim, cost_engine=engine)
    assert report.n_simulated == 5
    assert report.p_morning_exit == 1.0
    assert report.p_stop_risk == 0.0
    assert report.raw_ev > 0, "100% 익절 → 양수"
    assert report.raw_ev == report.mean_profit_pct
    print(f"✅ EV-1 100% morning_exit → raw_ev={report.raw_ev:+.4%}")


def test_EV_2_all_stop_risk():
    """EV-2: 100% stop_risk → P_stop=1.0, raw_ev=평균 손실 (음수)."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=i, label_stop_risk=True) for i in range(1, 6)
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    report = compute_ev(sim, cost_engine=engine)
    assert report.p_stop_risk == 1.0
    assert report.raw_ev < 0, "100% 손절 → 음수"
    assert report.raw_ev == report.mean_loss_pct
    print(f"✅ EV-2 100% stop_risk → raw_ev={report.raw_ev:+.4%}")


def test_EV_3_mixed_distribution():
    """EV-3: 50/50 분포 → 가중 평균."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=1, label_morning_exit=True),
        _row(cid=2, label_morning_exit=True),
        _row(cid=3, label_stop_risk=True),
        _row(cid=4, label_stop_risk=True),
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    report = compute_ev(sim, cost_engine=engine)
    assert report.p_morning_exit == 0.5
    assert report.p_stop_risk == 0.5
    expected_ev = 0.5 * report.mean_profit_pct + 0.5 * report.mean_loss_pct
    assert abs(report.raw_ev - expected_ev) < 1e-10, "가중 합 정확도"
    # 익절 + 손실 비대칭 (1.2% vs 1.0%) + 비용 → raw_ev 음수
    assert report.raw_ev < 0
    print(f"✅ EV-3 50/50 분포 → raw_ev={report.raw_ev:+.4%}")


def test_EV_4_empty_df():
    """EV-4: 빈 df → EVReport 0 채움 (zero division 회귀)."""
    engine = _make_engine()
    sim = pd.DataFrame(columns=["scenario", "simulated_net_pnl_pct"])
    report = compute_ev(sim, cost_engine=engine)
    assert report.n_total == 0
    assert report.n_simulated == 0
    assert report.raw_ev == 0.0
    assert report.net_ev == 0.0
    assert report.cost_basis > 0  # 메타는 정상
    print("✅ EV-4 빈 df → 0 채움")


def test_EV_5_no_double_cost_deduction():
    """EV-5: net_ev == raw_ev (이중 차감 방지 핵심 검증)."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=1, label_morning_exit=True),
        _row(cid=2, label_stop_risk=True),
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    report = compute_ev(sim, cost_engine=engine)
    assert report.net_ev == report.raw_ev, (
        f"net_ev({report.net_ev}) != raw_ev({report.raw_ev}) — 이중 차감 의심"
    )
    # cost_basis는 메타로만 존재 (계산에 미반영)
    assert report.cost_basis > 0
    assert abs(report.raw_ev - (report.cost_basis * -1)) > 1e-6, (
        "raw_ev와 cost_basis는 다른 값이어야 함"
    )
    print(f"✅ EV-5 이중 차감 방지: raw_ev={report.raw_ev:+.4%} == net_ev={report.net_ev:+.4%}")


def test_EV_6_excluded_filter():
    """EV-6: excluded 행은 분모/분자에서 제외."""
    engine = _make_engine()
    df = pd.DataFrame([
        _row(cid=1, label_morning_exit=True),
        _row(cid=2, label_stop_risk=None, label_morning_exit=None),  # excluded
        _row(cid=3, label_stop_risk=True),
    ])
    sim = simulate_dataset(df, cost_engine=engine)
    report = compute_ev(sim, cost_engine=engine)
    assert report.n_total == 3
    assert report.n_simulated == 2  # excluded 1건 제외
    assert report.p_morning_exit == 0.5
    assert report.p_stop_risk == 0.5
    print("✅ EV-6 excluded 분모 제외")


# ===== Step 2.4 — Edge case =====


def test_EDGE_1_dependency_injection():
    """EDGE-1: cost_engine 의존성 주입 — None → 싱글톤 자동 생성."""
    row = _row(label_morning_exit=True)
    # cost_engine=None 명시 → get_engine() 호출됨
    result = simulate_candidate(row)  # cost_engine 인자 생략
    assert result.scenario == SCENARIO_MORNING_EXIT
    assert result.simulated_net_pnl_pct is not None
    print("✅ EDGE-1 cost_engine None → 싱글톤")


def test_EDGE_2_unsupported_policy_dataset():
    """EDGE-2: simulate_dataset도 잘못된 policy 시 ValueError."""
    engine = _make_engine()
    df = pd.DataFrame([_row(label_morning_exit=True)])
    try:
        simulate_dataset(df, policy="not_a_policy", cost_engine=engine)
        raise AssertionError("ValueError 발생해야 함")
    except ValueError:
        pass
    print("✅ EDGE-2 simulate_dataset policy 검증")


def test_EDGE_3_pd_series_input():
    """EDGE-3: simulate_candidate 가 pd.Series 입력도 지원 (df.iterrows() 호환)."""
    engine = _make_engine()
    series = pd.Series(_row(label_stop_risk=True))
    result = simulate_candidate(series, cost_engine=engine)
    assert result.scenario == SCENARIO_STOP_RISK
    print("✅ EDGE-3 pd.Series 입력 지원")


def test_EDGE_4_market_open_negative_pct():
    """EDGE-4: market_open 시나리오에서 next_open_pct가 음수 → 음수 net_pnl."""
    engine = _make_engine()
    row = _row(label_stop_risk=False, label_morning_exit=False, next_open_pct=-0.003)
    result = simulate_candidate(row, cost_engine=engine)
    assert result.scenario == SCENARIO_MARKET_OPEN
    assert result.simulated_exit_pct == -0.003
    assert result.simulated_net_pnl_pct < -0.003  # 비용 추가
    print("✅ EDGE-4 market_open 음수 등락률")


# ===== Runner =====


def main():
    tests = [
        # SC: simulate_candidate 시나리오
        test_SC_1_stop_risk_only,
        test_SC_2_morning_exit_only,
        test_SC_3_market_open,
        test_SC_4_both_labels_conservative,
        test_SC_5_excluded_null_labels,
        test_SC_6_cost_deduction_verified,
        test_SC_7_invalid_policy,
        test_SC_8_one_side_null_to_market_open,
        test_SC_9_aggressive_both_labels,
        test_SC_10_split_both_labels_average,
        test_SC_11_options_equivalent_for_single_label,
        # DS: simulate_dataset 통합
        test_DS_1_normal_dataframe,
        test_DS_2_empty_df,
        test_DS_3_all_null_labels,
        test_DS_4_original_df_preserved,
        test_DS_5_boolean_dtype_compat,
        # EV: compute_ev 정합
        test_EV_1_all_morning_exit,
        test_EV_2_all_stop_risk,
        test_EV_3_mixed_distribution,
        test_EV_4_empty_df,
        test_EV_5_no_double_cost_deduction,
        test_EV_6_excluded_filter,
        # EDGE
        test_EDGE_1_dependency_injection,
        test_EDGE_2_unsupported_policy_dataset,
        test_EDGE_3_pd_series_input,
        test_EDGE_4_market_open_negative_pct,
    ]
    print(f"\n=== phase25_simulator 단위 테스트 — {len(tests)}건 실행 ===\n")
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
