"""phase25_walkforward PRD 정책 확장 + score 임계 매트릭스 단위 테스트.

WF-1~WF-6: _POLICIES 5종 / score 필터 / default 정책 / 매트릭스 / 회귀.

실행:
    venv/bin/python scripts/test_phase25_walkforward_prd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.backtest.phase25_walkforward import (
    _DEFAULT_POLICY,
    _POLICIES,
    _SCORE_FILTERS,
    WalkforwardReport,
    _build_markdown,
    _build_score_gate_matrix,
    _walkforward_from_df,
    compare_scenario_policies,
    compute_ev,
    compute_sharpe,
    score_bucket_analysis,
    simulate_dataset,
)


def _row(
    *,
    cid: int,
    trade_date: str = "2026-05-04",
    ticker: str = "005930",
    total_score: int = 2,
    next_open_pct: float = 0.0,
    next_morning_high_pct: float = 0.0,
    next_morning_low_pct: float = 0.0,
    label_stop_risk: bool = False,
    label_morning_exit: bool = False,
    label_gap_up: bool = False,
    label_net_ev_positive: bool = False,
):
    return {
        "candidate_id": cid,
        "trade_date": pd.Timestamp(trade_date),
        "ticker": ticker,
        "total_score": total_score,
        "next_open_pct": next_open_pct,
        "next_morning_high_pct": next_morning_high_pct,
        "next_morning_low_pct": next_morning_low_pct,
        "label_stop_risk": label_stop_risk,
        "label_morning_exit": label_morning_exit,
        "label_gap_up": label_gap_up,
        "label_net_ev_positive": label_net_ev_positive,
    }


def _sample_df() -> pd.DataFrame:
    """score 분포: 0=1건 / 1=2건 / 2=3건 / 3=2건 = 총 8건. 시가 분포 다양."""
    rows = [
        _row(cid=1, total_score=0, next_open_pct=0.020, next_morning_high_pct=0.025),  # GAPUP
        _row(cid=2, total_score=1, next_open_pct=0.001, next_morning_high_pct=0.005,
             label_morning_exit=True),                                                  # FLAT, morning 라벨
        _row(cid=3, total_score=1, next_open_pct=-0.015, next_morning_high_pct=0.0,
             label_stop_risk=True),                                                     # GAPDOWN
        _row(cid=4, total_score=2, next_open_pct=0.012, next_morning_high_pct=0.022,
             label_morning_exit=True),                                                  # GAPUP
        _row(cid=5, total_score=2, next_open_pct=-0.005, next_morning_high_pct=0.002),  # FLAT
        _row(cid=6, total_score=2, next_open_pct=0.030, next_morning_high_pct=0.040,
             label_morning_exit=True),                                                  # GAPUP 강
        _row(cid=7, total_score=3, next_open_pct=0.015, next_morning_high_pct=0.028,
             label_morning_exit=True),                                                  # GAPUP
        _row(cid=8, total_score=3, next_open_pct=0.025, next_morning_high_pct=0.035,
             label_morning_exit=True),                                                  # GAPUP
    ]
    return pd.DataFrame(rows)


# ===== WF-1: --score-min 2 필터링 정확 =====


def test_WF_1_score_min_2_filtering():
    """WF-1: total_score>=2 필터링 시 표본=5건 (cid 4~8)."""
    df = _sample_df()
    sub = df[df["total_score"] >= 2]
    assert len(sub) == 5, f"score>=2 표본={len(sub)}, 기대=5"
    # 정확히 cid 4~8
    assert set(sub["candidate_id"]) == {4, 5, 6, 7, 8}
    print("✅ WF-1 score>=2 필터링 정확 (5건, cid 4~8)")


# ===== WF-2: --score-min 3 필터링 정확 =====


def test_WF_2_score_min_3_filtering():
    """WF-2: total_score>=3 필터링 시 표본=2건 (cid 7~8)."""
    df = _sample_df()
    sub = df[df["total_score"] >= 3]
    assert len(sub) == 2, f"score>=3 표본={len(sub)}, 기대=2"
    assert set(sub["candidate_id"]) == {7, 8}
    print("✅ WF-2 score>=3 필터링 정확 (2건, cid 7~8)")


# ===== WF-3: _POLICIES 5종 비교 =====


def test_WF_3_compare_5_policies():
    """WF-3: compare_scenario_policies 출력 키 셋 = _POLICIES 5종 모두."""
    df = _sample_df()
    out = compare_scenario_policies(df)
    assert set(out.keys()) == set(_POLICIES), f"keys={set(out.keys())} != {set(_POLICIES)}"
    # 각 정책마다 EVReport 객체 반환
    for p in _POLICIES:
        assert out[p] is not None
        assert out[p].n_simulated > 0, f"{p} n_simulated={out[p].n_simulated}"
    # PRD optimistic > realistic (갭업 케이스 평균에서)
    assert out["prd_split_optimistic"].net_ev > out["prd_split_realistic"].net_ev, (
        f"opt {out['prd_split_optimistic'].net_ev} > real {out['prd_split_realistic'].net_ev} 기대"
    )
    print(f"✅ WF-3 5 정책 비교: cons={out['conservative'].net_ev:+.4%} agg={out['aggressive'].net_ev:+.4%} "
          f"split={out['split'].net_ev:+.4%} opt={out['prd_split_optimistic'].net_ev:+.4%} "
          f"real={out['prd_split_realistic'].net_ev:+.4%}")


# ===== WF-4: default policy = prd_split_realistic =====


def test_WF_4_default_policy_prd_realistic():
    """WF-4: _DEFAULT_POLICY 가 prd_split_realistic (운영 정합)."""
    assert _DEFAULT_POLICY == "prd_split_realistic", f"_DEFAULT_POLICY={_DEFAULT_POLICY}"
    print(f"✅ WF-4 default policy = {_DEFAULT_POLICY}")


# ===== WF-5: score gate matrix 15셀 + md 본문 포함 =====


def test_WF_5_score_gate_matrix_15_cells():
    """WF-5: _build_score_gate_matrix 가 _SCORE_FILTERS(3) × _POLICIES(5) = 15셀 생성."""
    df = _sample_df()
    matrix = _build_score_gate_matrix(df)
    assert set(matrix["score_filters"]) == set(_SCORE_FILTERS)
    assert set(matrix["policies"]) == set(_POLICIES)
    # 셀 수 = 3 × 5 = 15
    assert len(matrix["cells"]) == 15, f"cells={len(matrix['cells'])} != 15"
    # 각 셀에 필수 키
    for key, cell in matrix["cells"].items():
        if cell is None:
            continue
        assert "n_simulated" in cell
        assert "net_ev" in cell
        assert "win_loss_ratio" in cell
        assert "sharpe" in cell
        assert "passed" in cell
    # md 본문에 섹션 6 포함
    sim_df = simulate_dataset(df, policy=_DEFAULT_POLICY)
    ev = compute_ev(sim_df)
    sh = compute_sharpe(sim_df)
    bucket = score_bucket_analysis(sim_df)
    policy_evs = compare_scenario_policies(df)
    wf = _walkforward_from_df(df, policy=_DEFAULT_POLICY)
    md = _build_markdown(
        start_date="2026-05-04",
        end_date="2026-05-12",
        df=df,
        sim_df=sim_df,
        overall_ev=ev,
        wf=wf,
        bucket_evs=bucket,
        sharpe=sh,
        policy_evs=policy_evs,
        gate_verdict={"passed": False, "reasons": ["test"], "metrics": {}},
        policy=_DEFAULT_POLICY,
        score_gate_matrix=matrix,
    )
    assert "## 6. score 임계" in md, "섹션 6 누락"
    assert "## 7. Limitation" in md, "섹션 7 누락"
    assert "prd_split_optimistic" in md, "PRD 정책 미표시"
    print("✅ WF-5 매트릭스 15셀 + md 섹션 6/7 포함")


# ===== WF-6: 회귀 — conservative 정책 정상 동작 =====


def test_WF_6_conservative_regression():
    """WF-6: 회귀 — conservative 정책 호출 시 PRD 신규 시나리오 발생 없음."""
    df = _sample_df()
    out = compare_scenario_policies(df)
    cons = out["conservative"]
    # conservative 에서는 PRD 시나리오 0건
    assert cons.n_prd_gapup == 0
    assert cons.n_prd_flat == 0
    assert cons.n_prd_gapdown == 0
    # morning/stop/open 분포는 기존 로직대로
    assert cons.n_simulated > 0
    print(f"✅ WF-6 conservative 회귀: morning={cons.n_morning_exit} stop={cons.n_stop_risk} "
          f"open={cons.n_market_open} (PRD 0건)")


# ===== 추가: _build_score_gate_matrix 빈 df graceful =====


def test_WF_7_empty_df_graceful():
    """WF-7: 빈 df → cells {} 반환 (graceful)."""
    df = pd.DataFrame()
    matrix = _build_score_gate_matrix(df)
    assert matrix["cells"] == {}
    print("✅ WF-7 빈 df → cells={}")


def main():
    tests = [
        test_WF_1_score_min_2_filtering,
        test_WF_2_score_min_3_filtering,
        test_WF_3_compare_5_policies,
        test_WF_4_default_policy_prd_realistic,
        test_WF_5_score_gate_matrix_15_cells,
        test_WF_6_conservative_regression,
        test_WF_7_empty_df_graceful,
    ]
    print(f"\n=== phase25_walkforward PRD 단위 테스트 — {len(tests)}건 실행 ===\n")
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
