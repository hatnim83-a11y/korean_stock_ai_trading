"""scripts/test_closing_bet_cost_engine.py

Phase 1-1: ``CostSlippageEngine`` 단위 테스트.

- PRD 7-2 공식 직접 검증 (수기 계산값과 일치)
- 비용 분해 정확성
- 정상/손실/엣지 케이스
- 슬리피지 모드 (None/0/명시값)

실행:
    venv/bin/python scripts/test_closing_bet_cost_engine.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.engines.cost_slippage_engine import (
    CostBreakdown,
    CostSlippageEngine,
)


# settings.yaml 기본값
_TEST_SETTINGS = {
    "cost": {
        "buy_commission": 0.00015,
        "sell_commission": 0.00015,
        "transaction_tax": 0.0018,
        "estimated_slippage": 0.001,
        "safety_margin": 0.005,
    }
}


def _approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


# ===== 테스트 케이스 =====


def test_round_trip_cost():
    """왕복 비용 = buy_comm + sell_comm + tax + slippage * 2."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    expected = 0.00015 + 0.00015 + 0.0018 + 0.001 * 2  # 0.0041
    assert _approx(eng.round_trip_cost(include_slippage=True), expected)
    print(f"  [PASS] round_trip_cost(slippage=True) = {eng.round_trip_cost():.4f}")

    expected_no_slip = 0.00015 + 0.00015 + 0.0018  # 0.0021
    assert _approx(eng.round_trip_cost(include_slippage=False), expected_no_slip)
    print(f"  [PASS] round_trip_cost(slippage=False) = {eng.round_trip_cost(False):.4f}")


def test_minimum_target_return():
    """목표 최소 = 왕복 비용 + safety_margin."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    expected = 0.0041 + 0.005  # 0.0091 = 0.91%
    assert _approx(eng.minimum_target_return(), expected)
    print(f"  [PASS] minimum_target_return = {eng.minimum_target_return():.4f} (≈ {eng.minimum_target_return()*100:.2f}%)")


def test_prd_formula_verification():
    """PRD 7-2 공식 직접 수기 검증.

    예시: buy=10,000원, sell=10,300원
    - 매수 비용 = 10,000 × (1 + 0.00015) = 10,001.5
    - 매도 회수 = 10,300 × (1 - 0.00015 - 0.0018) = 10,300 × 0.99805 = 10,279.915
    - 순수익 = 10,279.915 - 10,001.5 = 278.415
    - net_pnl_pct = 278.415 / 10,001.5 = 0.027837...
    - 슬리피지 (왕복 0.2%): -(10,000+10,300) × 0.001 = -20.3
    - 최종 net_pnl(1주) = 278.415 - 20.3 = 258.115
    """
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    bd = eng.compute_pnl(buy_price=10_000, sell_price=10_300, shares=1)

    # PRD 공식 직접 계산
    buy_total = 10_000 * (1 + 0.00015)
    sell_net = 10_300 * (1 - 0.00015 - 0.0018)
    expected_pct_no_slip = (sell_net - buy_total) / buy_total

    # 슬리피지 차감 후 실제 net_pnl
    slip_cost = (10_000 + 10_300) * 0.001
    expected_net_pnl = (sell_net - buy_total) - slip_cost
    expected_net_pnl_pct = expected_net_pnl / buy_total

    assert _approx(bd.net_pnl, expected_net_pnl, 1e-6), \
        f"net_pnl 불일치: {bd.net_pnl} vs {expected_net_pnl}"
    assert _approx(bd.net_pnl_pct, expected_net_pnl_pct, 1e-9), \
        f"net_pnl_pct 불일치: {bd.net_pnl_pct} vs {expected_net_pnl_pct}"
    print(f"  [PASS] PRD 7-2 공식 검증 — net_pnl={bd.net_pnl:.4f}, net_pnl_pct={bd.net_pnl_pct*100:.4f}%")
    print(f"         (슬리피지 미반영 PRD: {expected_pct_no_slip*100:.4f}%)")


def test_breakdown_components():
    """CostBreakdown 의 모든 분해 컬럼 정확성 검증."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    bd = eng.compute_pnl(buy_price=10_000, sell_price=10_300, shares=10)

    assert bd.gross_buy_amount == 100_000
    assert bd.gross_sell_amount == 103_000
    assert _approx(bd.buy_commission, 100_000 * 0.00015)        # 15
    assert _approx(bd.sell_commission, 103_000 * 0.00015)       # 15.45
    assert _approx(bd.transaction_tax, 103_000 * 0.0018)        # 185.4
    assert _approx(bd.estimated_slippage, (100_000 + 103_000) * 0.001)  # 203
    assert bd.pretax_pnl == 3_000
    assert _approx(bd.pretax_pnl_pct, 0.03)
    print(f"  [PASS] 비용 분해 — buy_comm={bd.buy_commission:.2f}, "
          f"sell_comm={bd.sell_commission:.2f}, tax={bd.transaction_tax:.2f}, "
          f"slippage={bd.estimated_slippage:.2f}")


def test_loss_case():
    """손실 거래: buy 10,000 → sell 9,800."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    bd = eng.compute_pnl(buy_price=10_000, sell_price=9_800, shares=1)
    assert bd.pretax_pnl < 0
    assert bd.net_pnl < bd.pretax_pnl  # 비용 차감으로 더 큰 손실
    print(f"  [PASS] 손실 케이스 — pretax={bd.pretax_pnl:.2f}, net={bd.net_pnl:.2f}")


def test_slippage_modes():
    """slippage_rate 모드: None / 0 / 명시값."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)

    # 1) None — 추정값(0.001) 사용
    bd_none = eng.compute_pnl(10_000, 10_300, 1, slippage_rate=None)
    # 2) 0 — 슬리피지 미적용 (실거래)
    bd_zero = eng.compute_pnl(10_000, 10_300, 1, slippage_rate=0.0)
    # 3) 명시 — 사후 측정값 0.0015
    bd_explicit = eng.compute_pnl(10_000, 10_300, 1, slippage_rate=0.0015)

    assert bd_zero.estimated_slippage == 0
    assert bd_zero.net_pnl > bd_none.net_pnl  # 슬리피지 없으니 net_pnl 더 좋음
    assert bd_explicit.estimated_slippage > bd_none.estimated_slippage
    assert bd_explicit.net_pnl < bd_none.net_pnl  # 슬리피지 더 크니 net_pnl 더 나쁨

    print(f"  [PASS] slippage 모드 — None={bd_none.net_pnl:.2f}, "
          f"zero={bd_zero.net_pnl:.2f}, explicit_0.0015={bd_explicit.net_pnl:.2f}")


def test_invalid_inputs():
    """입력 검증."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    bad_cases = [
        (-100, 10_300, 1, "음수 매수가"),
        (10_000, 0, 1, "0 매도가"),
        (10_000, 10_300, 0, "0 shares"),
        (10_000, 10_300, -5, "음수 shares"),
        (10_000, 10_300, 1.5, "float shares"),
        (10_000, 10_300, True, "bool shares (int subclass 회피)"),
    ]
    for buy, sell, shares, label in bad_cases:
        try:
            eng.compute_pnl(buy, sell, shares)
            print(f"  [FAIL] {label} 차단 안됨")
            sys.exit(1)
        except ValueError:
            pass

    # 음수 slippage_rate
    try:
        eng.compute_pnl(10_000, 10_300, 1, slippage_rate=-0.001)
        print("  [FAIL] 음수 slippage 차단 안됨")
        sys.exit(1)
    except ValueError:
        pass

    print(f"  [PASS] 잘못된 입력 7케이스 모두 차단 (bool shares 포함)")


def test_db_payload_keys():
    """to_db_payload() 가 candidates 테이블 컬럼명과 일치하는지."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    bd = eng.compute_pnl(10_000, 10_300, 1)
    payload = eng.to_db_payload(bd)

    expected_keys = {
        "buy_commission", "sell_commission", "transaction_tax",
        "estimated_slippage", "net_pnl_pct"
    }
    assert set(payload.keys()) == expected_keys, f"키 불일치: {payload.keys()}"
    for k, v in payload.items():
        assert isinstance(v, float), f"{k} type 불일치: {type(v)}"
    print(f"  [PASS] DB payload 컬럼 5개 일치, 모두 float")


def test_settings_default_load():
    """settings 미주입 시 settings.yaml 자동 로드."""
    from closing_bet_system.engines.cost_slippage_engine import get_engine, reset_singleton

    reset_singleton()
    eng = get_engine()
    # 기본 settings.yaml 값과 일치 (값 자체는 _TEST_SETTINGS와 동일)
    assert _approx(eng.buy_commission_rate, 0.00015)
    assert _approx(eng.transaction_tax_rate, 0.0018)
    print(f"  [PASS] settings.yaml 자동 로드 + 싱글톤")


def test_breakeven_target():
    """minimum_target_return 만큼 상승하면 비용 차감 후 양의 EV."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    target = eng.minimum_target_return()  # 0.91%

    buy = 10_000
    sell = buy * (1 + target)
    bd = eng.compute_pnl(buy, sell, shares=1)
    # 비용 차감 후 0% 근처여야 함 (정확한 break-even 은 PRD 공식 기반이라 약간 다를 수 있음)
    # safety_margin 0.5% 가 들어가 있으므로 양수 보장
    assert bd.net_pnl_pct > 0, f"target={target*100:.2f}% 상승 시 net_pnl_pct={bd.net_pnl_pct*100:.4f}% (음수)"
    print(f"  [PASS] minimum_target_return({target*100:.2f}%) 상승 시 net_pnl_pct={bd.net_pnl_pct*100:.4f}%")


def test_breakeven_at_round_trip_cost():
    """round_trip_cost 만큼 상승 시 net_pnl_pct ≈ 0 — 공식 정확성 강한 검증."""
    eng = CostSlippageEngine(settings=_TEST_SETTINGS)
    target = eng.round_trip_cost(include_slippage=True)  # 0.41% (safety_margin 제외)

    buy = 10_000
    sell = buy * (1 + target)
    bd = eng.compute_pnl(buy, sell, shares=1)
    # break-even 부근 — buy_commission 으로 인해 약간 음수 (분모 확장 효과)
    # 절대값 < 0.05% 정도면 정확
    assert abs(bd.net_pnl_pct) < 0.0005, \
        f"round_trip_cost({target*100:.2f}%) 상승 시 net_pnl_pct={bd.net_pnl_pct*100:.4f}% (break-even 이탈)"
    print(f"  [PASS] round_trip_cost 상승 시 break-even — net_pnl_pct={bd.net_pnl_pct*100:.4f}%")


def test_thread_safe_singleton():
    """싱글톤 동시 호출 시 단일 인스턴스 보장."""
    import threading
    from closing_bet_system.engines.cost_slippage_engine import get_engine, reset_singleton

    reset_singleton()
    instances = []

    def acquire():
        instances.append(get_engine())

    threads = [threading.Thread(target=acquire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(instances) == 10
    assert all(inst is instances[0] for inst in instances), "싱글톤 인스턴스 불일치"
    print(f"  [PASS] 10 thread 동시 호출 → 단일 인스턴스 보장")


def main():
    print("=== Phase 1-1 cost_slippage_engine 단위 테스트 ===\n")
    tests = [
        test_round_trip_cost,
        test_minimum_target_return,
        test_prd_formula_verification,
        test_breakdown_components,
        test_loss_case,
        test_slippage_modes,
        test_invalid_inputs,
        test_db_payload_keys,
        test_settings_default_load,
        test_breakeven_target,
        test_breakeven_at_round_trip_cost,
        test_thread_safe_singleton,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()
    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
