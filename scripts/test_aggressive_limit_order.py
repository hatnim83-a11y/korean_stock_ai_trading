"""
공격적 지정가 재시도 루프 단위 테스트 (MockOrderApi 기반).

6개 시나리오로 `_place_aggressive_limit_with_retry()` 동작을 검증한다.
실제 KIS API 호출 없이 MockOrderApi의 시나리오 변수로 상황을 재현.

실행: source venv/bin/activate && python scripts/test_aggressive_limit_order.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.trading_engine.trading_engine import (
    TradingEngine,
    _classify_order_error,
    _compute_weighted_avg_price,
    OrderErrorCategory,
)
from modules.trading_engine.kis_order_api import MockOrderApi


# ===== 공통 유틸 =====

def make_engine() -> TradingEngine:
    """MockOrderApi를 주입한 TradingEngine 생성"""
    engine = TradingEngine(is_mock=True)
    engine.order_api = MockOrderApi()
    # 테스트 속도를 위해 타임아웃/폴링 단축 (settings override)
    from config import settings as _settings
    _settings.LIMIT_AGGRESSIVE_RETRY_TIMEOUT = 2
    _settings.LIMIT_AGGRESSIVE_POLL_INTERVAL = 1
    _settings.LIMIT_AGGRESSIVE_MAX_RETRIES = 4
    _settings.LIMIT_AGGRESSIVE_TOTAL_TIMEOUT = 30
    _settings.LIMIT_AGGRESSIVE_CANCEL_DELAY = 0.1
    return engine


def assert_equal(actual, expected, label: str) -> bool:
    ok = actual == expected
    tag = "✅" if ok else "❌"
    print(f"  {tag} {label}: actual={actual}, expected={expected}")
    return ok


def assert_ge(actual, threshold, label: str) -> bool:
    ok = actual >= threshold
    tag = "✅" if ok else "❌"
    print(f"  {tag} {label}: actual={actual} >= {threshold}")
    return ok


# ===== 단위 테스트: 헬퍼 함수 =====

def test_error_classifier():
    print("\n[Unit] _classify_order_error")
    cases = [
        ("주문가능금액이 부족합니다", OrderErrorCategory.SIZE_ERROR),
        ("증거금이 부족합니다", OrderErrorCategory.SIZE_ERROR),
        ("호가 단위가 맞지 않습니다", OrderErrorCategory.PRICE_ERROR),
        ("거래정지된 종목입니다", OrderErrorCategory.FATAL),
        ("상장폐지 예정 종목", OrderErrorCategory.FATAL),
        ("일시적 시스템 장애", OrderErrorCategory.RECOVERABLE),
        ("", OrderErrorCategory.RECOVERABLE),
    ]
    all_pass = True
    for msg, expected in cases:
        actual = _classify_order_error(msg)
        ok = actual == expected
        all_pass = all_pass and ok
        tag = "✅" if ok else "❌"
        print(f"  {tag} '{msg}' → {actual.value} (expected {expected.value})")
    return all_pass


def test_weighted_avg_price():
    print("\n[Unit] _compute_weighted_avg_price")
    all_pass = True
    all_pass &= assert_equal(_compute_weighted_avg_price([]), 0, "빈 리스트")
    all_pass &= assert_equal(_compute_weighted_avg_price([(10, 10000)]), 10000, "단일 체결")
    # 30 @ 10000 + 20 @ 10100 = 300000 + 202000 = 502000 / 50 = 10040
    all_pass &= assert_equal(
        _compute_weighted_avg_price([(30, 10000), (20, 10100)]), 10040, "2회 부분체결"
    )
    all_pass &= assert_equal(
        _compute_weighted_avg_price([(0, 10000), (10, 0)]), 0, "무효 데이터 필터"
    )
    return all_pass


# ===== 시나리오 1: 100% 즉시 체결 =====

def test_scenario_1_full_fill():
    print("\n[Scenario 1] 100% 즉시 체결")
    engine = make_engine()
    engine.order_api.scenario_fill_ratio = 1.0

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], True, "success")
    ok &= assert_equal(result["quantity"], 10, "체결 수량")
    ok &= assert_equal(result["remaining_shares"], 0, "미체결 수량")
    ok &= assert_equal(result["retry_count"], 1, "재시도 횟수")
    ok &= assert_ge(result["filled_price"], 1, "체결가 > 0")
    return ok


# ===== 시나리오 2: 1차 70% + 2차 30% = 전량 =====

def test_scenario_2_partial_then_full():
    print("\n[Scenario 2] 1차 70% + 2차 100% (재시도 1회)")
    engine = make_engine()

    # 1차: 70% 체결 설정
    original_buy = engine.order_api.buy_limit_order
    call_count = {"n": 0}

    def patched_buy(stock_code, quantity, price):
        call_count["n"] += 1
        if call_count["n"] == 1:
            engine.order_api.scenario_fill_ratio = 0.7
        else:
            engine.order_api.scenario_fill_ratio = 1.0  # 2차는 100%
        return original_buy(stock_code, quantity, price)

    engine.order_api.buy_limit_order = patched_buy

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], True, "success")
    ok &= assert_equal(result["quantity"], 10, "총 체결 수량")
    ok &= assert_equal(result["remaining_shares"], 0, "미체결 수량")
    ok &= assert_ge(result["retry_count"], 2, "재시도 2회 이상")
    return ok


# ===== 시나리오 3: 50% + 0% + 50% (재시도 2회) =====

def test_scenario_3_mixed_fill():
    print("\n[Scenario 3] 1차 50% + 2차 0% + 3차 50% (재시도 3회)")
    engine = make_engine()
    original_buy = engine.order_api.buy_limit_order
    ratios = [0.5, 0.0, 1.0]  # 남은 수량에 대한 비율
    call_count = {"n": 0}

    def patched_buy(stock_code, quantity, price):
        i = call_count["n"]
        engine.order_api.scenario_fill_ratio = ratios[i] if i < len(ratios) else 1.0
        call_count["n"] += 1
        return original_buy(stock_code, quantity, price)

    engine.order_api.buy_limit_order = patched_buy

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], True, "success")
    ok &= assert_equal(result["quantity"], 10, "총 체결 수량")
    ok &= assert_equal(result["remaining_shares"], 0, "미체결 수량")
    ok &= assert_ge(result["retry_count"], 3, "재시도 3회 이상")
    return ok


# ===== 시나리오 4: 모두 0% — 최대 재시도 후 포기 =====

def test_scenario_4_no_fill_give_up():
    print("\n[Scenario 4] 전회 0% 체결 → 최대 재시도 후 포기")
    engine = make_engine()
    engine.order_api.scenario_fill_ratio = 0.0

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], False, "success=False")
    ok &= assert_equal(result["quantity"], 0, "체결 수량 0")
    ok &= assert_equal(result["remaining_shares"], 10, "미체결 수량 10")
    # max_retries(4) 에 도달해야 함
    ok &= assert_ge(result["retry_count"], 4, "최대 재시도 도달")
    return ok


# ===== 시나리오 5: 증거금 초과 → 수량 감축 =====

def test_scenario_5_size_error_reduction():
    print("\n[Scenario 5] 증거금 초과 에러 → 수량 감축 후 성공")
    engine = make_engine()
    original_buy = engine.order_api.buy_limit_order
    call_count = {"n": 0}

    def patched_buy(stock_code, quantity, price):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 첫 호출은 증거금 초과 에러
            engine.order_api.scenario_next_error = "주문가능금액이 부족합니다"
        else:
            engine.order_api.scenario_fill_ratio = 1.0
        return original_buy(stock_code, quantity, price)

    engine.order_api.buy_limit_order = patched_buy

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], True, "success (수량 감축 후 체결)")
    # 10 × 0.9 = 9주로 감축됐어야 함
    ok &= assert_equal(result["quantity"], 9, "수량 감축된 체결량")
    ok &= assert_equal(result["requested_quantity"], 10, "요청량은 원본 유지")
    return ok


# ===== 시나리오 6: 취소 직전 추가 체결 (race condition) =====

def test_scenario_6_cancel_race():
    print("\n[Scenario 6] 취소 직전 추가 체결 엣지케이스")
    engine = make_engine()
    engine.order_api.scenario_fill_ratio = 0.5  # 1차 50% 체결
    engine.order_api.scenario_cancel_race_qty = 2  # 취소 직전 +2주 추가 체결
    # 1차에서 10주 중 5주 체결 + 취소 직전 2주 추가 = 총 7주 체결 (남은 3주는 재시도로)

    original_buy = engine.order_api.buy_limit_order
    call_count = {"n": 0}

    def patched_buy(stock_code, quantity, price):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            # 2차부터는 100% 체결
            engine.order_api.scenario_fill_ratio = 1.0
        return original_buy(stock_code, quantity, price)

    engine.order_api.buy_limit_order = patched_buy

    result = engine._place_aggressive_limit_with_retry(
        stock_code="005930",
        stock_name="삼성전자",
        requested_qty=10,
        expected_price=70000,
    )

    ok = True
    ok &= assert_equal(result["success"], True, "success")
    # 1차: 5주 + race 2주 = 7주 / 2차: 3주 = 총 10주
    ok &= assert_equal(result["quantity"], 10, "최종 총 체결량")
    ok &= assert_equal(result["remaining_shares"], 0, "미체결 0")
    return ok


# ===== 메인 =====

def main() -> None:
    print("=" * 70)
    print("공격적 지정가 재시도 루프 단위 테스트")
    print("=" * 70)

    results = {
        "Error classifier": test_error_classifier(),
        "Weighted avg price": test_weighted_avg_price(),
        "Scenario 1 (full fill)": test_scenario_1_full_fill(),
        "Scenario 2 (70+30)": test_scenario_2_partial_then_full(),
        "Scenario 3 (50+0+50)": test_scenario_3_mixed_fill(),
        "Scenario 4 (no fill)": test_scenario_4_no_fill_give_up(),
        "Scenario 5 (size error)": test_scenario_5_size_error_reduction(),
        "Scenario 6 (cancel race)": test_scenario_6_cancel_race(),
    }

    print("\n" + "=" * 70)
    print("결과 요약")
    print("=" * 70)
    for name, ok in results.items():
        tag = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {tag}  {name}")

    all_pass = all(results.values())
    print("\n" + ("🎉 전체 PASS" if all_pass else "⚠️  일부 FAIL"))
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
