"""price_utils.py KRX 호가 단위 정렬 단위 테스트.

TICK-1~6: get_tick_size 정확성 + align_to_tick floor/ceil 정합.

실행:
    venv/bin/python scripts/test_price_utils.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.execution.price_utils import align_to_tick, get_tick_size


def test_TICK_1_get_tick_size_table():
    """TICK-1: 7개 구간별 호가 단위 정확."""
    assert get_tick_size(1_999) == 1, f"1,999원 → 1원 기대 vs {get_tick_size(1_999)}"
    assert get_tick_size(2_000) == 5
    assert get_tick_size(4_999) == 5
    assert get_tick_size(5_000) == 10
    assert get_tick_size(19_999) == 10
    assert get_tick_size(20_000) == 50
    assert get_tick_size(49_999) == 50
    assert get_tick_size(50_000) == 100
    assert get_tick_size(199_999) == 100
    assert get_tick_size(200_000) == 500
    assert get_tick_size(499_999) == 500
    assert get_tick_size(500_000) == 1_000
    assert get_tick_size(10_000_000) == 1_000
    print("✅ TICK-1 호가 단위 테이블 7구간 정확")


def test_TICK_2_align_buy_floor():
    """TICK-2: 매수 floor — 가격 상한 이하 가장 큰 호가."""
    # 5,000원대(10원 단위)
    assert align_to_tick(7_523, "buy") == 7_520, f"7,523 buy → 7,520 기대 vs {align_to_tick(7_523, 'buy')}"
    assert align_to_tick(7_520, "buy") == 7_520
    assert align_to_tick(7_529, "buy") == 7_520
    # 50,000원대(100원 단위)
    assert align_to_tick(75_345, "buy") == 75_300
    # 200,000원대(500원 단위)
    assert align_to_tick(213_456, "buy") == 213_000
    # 500,000원 이상(1,000원 단위)
    assert align_to_tick(523_456, "buy") == 523_000
    print("✅ TICK-2 매수 floor 정확")


def test_TICK_3_align_sell_ceil():
    """TICK-3: 매도 ceil — 가격 하한 이상 가장 작은 호가."""
    # 5,000원대(10원 단위)
    assert align_to_tick(7_521, "sell") == 7_530, f"7,521 sell → 7,530 기대 vs {align_to_tick(7_521, 'sell')}"
    assert align_to_tick(7_520, "sell") == 7_520
    assert align_to_tick(7_529, "sell") == 7_530
    # 50,000원대
    assert align_to_tick(75_345, "sell") == 75_400
    # 200,000원대
    assert align_to_tick(213_456, "sell") == 213_500
    print("✅ TICK-3 매도 ceil 정확")


def test_TICK_4_boundary_2000():
    """TICK-4: 2,000원 경계 — 1,999는 1원 단위, 2,000은 5원 단위."""
    assert align_to_tick(1_999, "buy") == 1_999
    assert align_to_tick(2_001, "buy") == 2_000   # 5원 단위 floor
    assert align_to_tick(2_004, "buy") == 2_000
    assert align_to_tick(2_005, "buy") == 2_005
    assert align_to_tick(2_001, "sell") == 2_005  # 5원 단위 ceil
    print("✅ TICK-4 2,000원 경계 정확")


def test_TICK_5_zero_price():
    """TICK-5: price=0 → 0 반환."""
    assert align_to_tick(0, "buy") == 0
    assert align_to_tick(0.0, "sell") == 0
    print("✅ TICK-5 price=0 → 0")


def test_TICK_6_invalid_input():
    """TICK-6: price<0 또는 side 미지원 → ValueError."""
    try:
        align_to_tick(-100, "buy")
        raise AssertionError("price<0 ValueError 기대")
    except ValueError:
        pass
    try:
        align_to_tick(1000, "invalid")  # type: ignore[arg-type]
        raise AssertionError("side 미지원 ValueError 기대")
    except ValueError:
        pass
    print("✅ TICK-6 잘못된 입력 ValueError")


def main():
    tests = [
        test_TICK_1_get_tick_size_table,
        test_TICK_2_align_buy_floor,
        test_TICK_3_align_sell_ceil,
        test_TICK_4_boundary_2000,
        test_TICK_5_zero_price,
        test_TICK_6_invalid_input,
    ]
    print(f"\n=== price_utils 단위 테스트 — {len(tests)}건 실행 ===\n")
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
