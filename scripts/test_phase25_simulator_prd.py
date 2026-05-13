"""phase25_simulator PRD 10-1 분할매도 시나리오 단위 테스트.

PRD 10-1 5구간 매트릭스 + optimistic/realistic 변형 검증.
EVReport 신규 9 필드 default 0 회귀 검증.

실행:
    venv/bin/python scripts/test_phase25_simulator_prd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.backtest.phase25_simulator import (
    SCENARIO_EXCLUDED,
    SCENARIO_PRD_SPLIT_FLAT,
    SCENARIO_PRD_SPLIT_GAPDOWN,
    SCENARIO_PRD_SPLIT_GAPUP,
    EVReport,
    _compute_prd_split_exit,
    _PRD_FIRST_SELL_RATIO,
    _PRD_GAPDOWN_THRESHOLD,
    _PRD_GAPUP_THRESHOLD,
    compute_ev,
    simulate_candidate,
    simulate_dataset,
)
from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine


def _make_engine() -> CostSlippageEngine:
    return CostSlippageEngine()


def _row(
    *,
    cid: int = 1,
    ticker: str = "005930",
    next_open_pct: float = 0.0,
    next_morning_high_pct: float = 0.0,
    next_morning_low_pct: float = 0.0,
    label_stop_risk: bool = False,
    label_morning_exit: bool = False,
    total_score: int = 2,
):
    return {
        "candidate_id": cid,
        "ticker": ticker,
        "next_open_pct": next_open_pct,
        "next_morning_high_pct": next_morning_high_pct,
        "next_morning_low_pct": next_morning_low_pct,
        "label_stop_risk": label_stop_risk,
        "label_morning_exit": label_morning_exit,
        "total_score": total_score,
    }


# ===== PRD-1 ~ PRD-5: 5구간 정확한 분기 검증 =====


def test_PRD_1_strong_gapup_optimistic_and_realistic():
    """PRD-1: open=+2.5% / morning_high=+3.0% — GAPUP 시나리오.

    optimistic exit = 0.5*0.025 + 0.5*0.030 = 0.0275 (+2.75%)
    realistic  exit = 0.75*0.025 + 0.25*0.030 = 0.02625 (+2.625%)
    """
    engine = _make_engine()
    row = _row(next_open_pct=0.025, next_morning_high_pct=0.030)

    opt = simulate_candidate(row, policy="prd_split_optimistic", cost_engine=engine)
    real = simulate_candidate(row, policy="prd_split_realistic", cost_engine=engine)

    assert opt.scenario == SCENARIO_PRD_SPLIT_GAPUP, f"optimistic scenario={opt.scenario}"
    assert real.scenario == SCENARIO_PRD_SPLIT_GAPUP, f"realistic scenario={real.scenario}"
    assert abs(opt.simulated_exit_pct - 0.0275) < 1e-9, f"opt exit={opt.simulated_exit_pct}"
    assert abs(real.simulated_exit_pct - 0.02625) < 1e-9, f"real exit={real.simulated_exit_pct}"
    # optimistic 이 realistic 보다 PnL이 큰 (또는 같은) 값
    assert opt.simulated_net_pnl_pct > real.simulated_net_pnl_pct, (
        f"opt {opt.simulated_net_pnl_pct} vs real {real.simulated_net_pnl_pct}"
    )
    print(f"✅ PRD-1 GAPUP opt={opt.simulated_exit_pct*100:+.3f}% real={real.simulated_exit_pct*100:+.3f}%")


def test_PRD_2_mild_gapup():
    """PRD-2: open=+1.0% / morning_high=+2.0% — GAPUP +0.5~+2% 구간.

    optimistic exit = 0.5*0.010 + 0.5*0.020 = 0.015 (+1.5%)
    realistic  exit = 0.75*0.010 + 0.25*0.020 = 0.0125 (+1.25%)
    """
    engine = _make_engine()
    row = _row(next_open_pct=0.010, next_morning_high_pct=0.020)

    opt = simulate_candidate(row, policy="prd_split_optimistic", cost_engine=engine)
    real = simulate_candidate(row, policy="prd_split_realistic", cost_engine=engine)

    assert opt.scenario == SCENARIO_PRD_SPLIT_GAPUP
    assert real.scenario == SCENARIO_PRD_SPLIT_GAPUP
    assert abs(opt.simulated_exit_pct - 0.015) < 1e-9, f"opt exit={opt.simulated_exit_pct}"
    assert abs(real.simulated_exit_pct - 0.0125) < 1e-9, f"real exit={real.simulated_exit_pct}"
    print(f"✅ PRD-2 GAPUP(+1~+2%) opt={opt.simulated_exit_pct*100:+.3f}% real={real.simulated_exit_pct*100:+.3f}%")


def test_PRD_3_flat_open():
    """PRD-3: open=+0.2% — FLAT (보합 구간) 100% 시초가 매도."""
    engine = _make_engine()
    row = _row(next_open_pct=0.002, next_morning_high_pct=0.010)

    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_PRD_SPLIT_FLAT, f"{policy} scenario={result.scenario}"
        assert abs(result.simulated_exit_pct - 0.002) < 1e-9
    print(f"✅ PRD-3 FLAT open=+0.2% → exit=+0.20% (양쪽 정책 동일)")


def test_PRD_4_mild_gapdown():
    """PRD-4: open=-0.7% — FLAT 구간 (보합~약갭다운) 100% 시초가 매도.

    PRD 10-1 "보합 -0.5~+0.5%" 및 "약갭다운 -0.5~-1%"는 모두 09:10 전량 매도라
    시뮬상 동일 처리 (-1% 초과 시 즉시 손절만 별도). 즉 -0.7%도 SCENARIO_PRD_SPLIT_FLAT.
    """
    engine = _make_engine()
    row = _row(next_open_pct=-0.007, next_morning_high_pct=0.001)

    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_PRD_SPLIT_FLAT
        assert abs(result.simulated_exit_pct - (-0.007)) < 1e-9
    print(f"✅ PRD-4 FLAT(-0.7%) → exit=-0.70%")


def test_PRD_5_strong_gapdown():
    """PRD-5: open=-1.5% — GAPDOWN 즉시 손절 (open ≤ -1%)."""
    engine = _make_engine()
    row = _row(next_open_pct=-0.015, next_morning_high_pct=0.0)

    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_PRD_SPLIT_GAPDOWN, f"{policy} scenario={result.scenario}"
        assert abs(result.simulated_exit_pct - (-0.015)) < 1e-9
    print(f"✅ PRD-5 GAPDOWN(-1.5%) → exit=-1.50%")


# ===== PRD-6 ~ PRD-8: NULL 처리 =====


def test_PRD_6_null_open_excluded():
    """PRD-6: next_open_pct NULL → excluded(missing_open_pct)."""
    engine = _make_engine()
    row = _row(next_open_pct=None, next_morning_high_pct=0.01)
    # dict.get은 None 그대로 반환. pd.NA로도 시도.
    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_EXCLUDED, f"{policy} scenario={result.scenario}"
        assert result.excluded_reason == "missing_open_pct"
    print("✅ PRD-6 open NULL → excluded(missing_open_pct)")


def test_PRD_7_null_morning_high_fallback():
    """PRD-7: GAPUP인데 morning_high NULL → 잔여 50% 시초가 폴백 → exit_pct=open_pct."""
    engine = _make_engine()
    row = _row(next_open_pct=0.015, next_morning_high_pct=None)
    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_PRD_SPLIT_GAPUP
        assert abs(result.simulated_exit_pct - 0.015) < 1e-9, (
            f"{policy} morning NULL → exit_pct {result.simulated_exit_pct}, 기대 0.015"
        )
    print("✅ PRD-7 morning_high NULL → open만 사용 (graceful)")


def test_PRD_8_all_null_excluded():
    """PRD-8: open / morning_high 모두 NULL → excluded(missing_open_pct).

    PRD 정책은 라벨이 아닌 open_pct에만 의존하므로 라벨 NULL은 무관.
    """
    engine = _make_engine()
    row = _row(next_open_pct=None, next_morning_high_pct=None,
               label_stop_risk=None, label_morning_exit=None)
    for policy in ("prd_split_optimistic", "prd_split_realistic"):
        result = simulate_candidate(row, policy=policy, cost_engine=engine)
        assert result.scenario == SCENARIO_EXCLUDED
        assert result.excluded_reason == "missing_open_pct"
    print("✅ PRD-8 전체 NULL → excluded")


# ===== PRD-9: optimistic vs realistic EV 차이 검증 =====


def test_PRD_9_optimistic_vs_realistic_ev_delta():
    """PRD-9: 동일 GAPUP 데이터셋에 두 정책 적용 시 optimistic > realistic.

    morning_high > open 구간에서 optimistic의 잔여 청산가가 higher → EV 더 큼.
    """
    engine = _make_engine()
    rows = [
        _row(cid=1, next_open_pct=0.010, next_morning_high_pct=0.030),
        _row(cid=2, next_open_pct=0.015, next_morning_high_pct=0.025),
        _row(cid=3, next_open_pct=0.020, next_morning_high_pct=0.035),
    ]
    df = pd.DataFrame(rows)

    sim_opt = simulate_dataset(df, policy="prd_split_optimistic", cost_engine=engine)
    sim_real = simulate_dataset(df, policy="prd_split_realistic", cost_engine=engine)
    ev_opt = compute_ev(sim_opt, cost_engine=engine)
    ev_real = compute_ev(sim_real, cost_engine=engine)

    assert ev_opt.net_ev > ev_real.net_ev, (
        f"opt {ev_opt.net_ev:.6f} 가 real {ev_real.net_ev:.6f} 보다 커야 함"
    )
    delta = ev_opt.net_ev - ev_real.net_ev
    print(f"✅ PRD-9 opt EV={ev_opt.net_ev:+.4%} > real EV={ev_real.net_ev:+.4%} (delta={delta:+.4%})")


# ===== PRD-10: EVReport 신규 필드 default 0 회귀 =====


def test_PRD_10_evreport_defaults_no_regression():
    """PRD-10: 옵션 A/B/C(prd 정책 미사용)에서 신규 PRD 필드 default 0 보장."""
    engine = _make_engine()
    rows = [
        _row(cid=1, label_stop_risk=False, label_morning_exit=True, next_open_pct=0.01),
        _row(cid=2, label_stop_risk=True, label_morning_exit=False, next_open_pct=-0.01),
    ]
    df = pd.DataFrame(rows)
    for policy in ("conservative", "aggressive", "split"):
        sim = simulate_dataset(df, policy=policy, cost_engine=engine)
        ev = compute_ev(sim, cost_engine=engine)
        # 신규 PRD 필드 모두 0
        assert ev.n_prd_gapup == 0
        assert ev.n_prd_flat == 0
        assert ev.n_prd_gapdown == 0
        assert ev.p_prd_gapup == 0.0
        assert ev.p_prd_flat == 0.0
        assert ev.p_prd_gapdown == 0.0
        assert ev.mean_prd_gapup_pct == 0.0
        assert ev.mean_prd_flat_pct == 0.0
        assert ev.mean_prd_gapdown_pct == 0.0
    print("✅ PRD-10 옵션 A/B/C 회귀 영향 없음 (PRD 필드 default 0)")


# ===== PRD-11: compute_ev 가중 합 수동 검증 =====


def test_PRD_11_compute_ev_weighted_sum_manual():
    """PRD-11: PRD 시나리오 가중 합 정확성 — 3건(GAPUP/FLAT/GAPDOWN) 각 1건 수동 계산.

    raw_ev = p_gapup * mean_gapup + p_flat * mean_flat + p_gapdown * mean_gapdown
    """
    engine = _make_engine()
    rows = [
        _row(cid=1, next_open_pct=0.015, next_morning_high_pct=0.025),   # GAPUP
        _row(cid=2, next_open_pct=0.002, next_morning_high_pct=0.005),   # FLAT
        _row(cid=3, next_open_pct=-0.015, next_morning_high_pct=0.0),    # GAPDOWN
    ]
    df = pd.DataFrame(rows)
    sim = simulate_dataset(df, policy="prd_split_realistic", cost_engine=engine)
    ev = compute_ev(sim, cost_engine=engine)

    assert ev.n_prd_gapup == 1
    assert ev.n_prd_flat == 1
    assert ev.n_prd_gapdown == 1
    assert ev.n_simulated == 3

    # 수동 계산: p=1/3 각각, raw_ev = (gapup+flat+gapdown)/3
    expected = (ev.mean_prd_gapup_pct + ev.mean_prd_flat_pct + ev.mean_prd_gapdown_pct) / 3.0
    assert abs(ev.raw_ev - expected) < 1e-9, (
        f"raw_ev={ev.raw_ev:.9f}, expected={expected:.9f}"
    )
    # net_ev == raw_ev (이중 차감 방지)
    assert abs(ev.net_ev - ev.raw_ev) < 1e-12
    print(f"✅ PRD-11 가중 합 정확: raw_ev={ev.raw_ev:+.4%} == manual={expected:+.4%}")


# ===== _compute_prd_split_exit 직접 검증 =====


def test_PRD_helper_optimistic():
    """헬퍼 직접 호출 — optimistic = 0.5*open + 0.5*morning_high."""
    assert abs(_compute_prd_split_exit(0.02, 0.04, "prd_split_optimistic") - 0.03) < 1e-9
    assert abs(_compute_prd_split_exit(0.01, 0.02, "prd_split_optimistic") - 0.015) < 1e-9
    print("✅ helper optimistic 정확")


def test_PRD_helper_realistic():
    """헬퍼 직접 호출 — realistic = 0.75*open + 0.25*morning_high."""
    assert abs(_compute_prd_split_exit(0.02, 0.04, "prd_split_realistic") - 0.025) < 1e-9
    assert abs(_compute_prd_split_exit(0.01, 0.02, "prd_split_realistic") - 0.0125) < 1e-9
    print("✅ helper realistic 정확")


def test_PRD_thresholds_constants():
    """모듈 상수 임계값 정합성."""
    assert _PRD_GAPUP_THRESHOLD == 0.005
    assert _PRD_GAPDOWN_THRESHOLD == -0.010
    assert _PRD_FIRST_SELL_RATIO == 0.5
    print("✅ 모듈 상수 정확 (+0.5% / -1.0% / 50%)")


def main():
    tests = [
        test_PRD_1_strong_gapup_optimistic_and_realistic,
        test_PRD_2_mild_gapup,
        test_PRD_3_flat_open,
        test_PRD_4_mild_gapdown,
        test_PRD_5_strong_gapdown,
        test_PRD_6_null_open_excluded,
        test_PRD_7_null_morning_high_fallback,
        test_PRD_8_all_null_excluded,
        test_PRD_9_optimistic_vs_realistic_ev_delta,
        test_PRD_10_evreport_defaults_no_regression,
        test_PRD_11_compute_ev_weighted_sum_manual,
        test_PRD_helper_optimistic,
        test_PRD_helper_realistic,
        test_PRD_thresholds_constants,
    ]
    print(f"\n=== phase25_simulator PRD 단위 테스트 — {len(tests)}건 실행 ===\n")
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
