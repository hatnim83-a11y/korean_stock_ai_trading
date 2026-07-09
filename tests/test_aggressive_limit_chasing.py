import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from modules.trading_engine.trading_engine import TradingEngine
from modules.trading_engine.kis_order_api import MockOrderApi


def _engine_with_quote(ask1=133_000, bid1=132_900, current=133_000):
    engine = TradingEngine(is_mock=True)
    api = MockOrderApi()

    def quote(_code):
        return {
            "success": True,
            "ask1": ask1,
            "bid1": bid1,
            "ask_volume1": 1000,
            "bid_volume1": 1000,
            "current_price": current,
            "message": "",
        }

    api.inquire_asking_price = quote
    engine.order_api = api
    return engine


def _set_b_defaults():
    """B안 동적 추격 기본 파라미터를 명시적으로 세팅(테스트 격리)."""
    settings.LIMIT_AGGRESSIVE_MAX_CHASE_PCT = 0.005
    settings.LIMIT_AGGRESSIVE_DYNAMIC_CHASE_PCT = 0.012
    settings.LIMIT_AGGRESSIVE_DYNAMIC_TRIGGER_PCT = 0.012
    settings.LIMIT_AGGRESSIVE_OVERHEAT_PCT = 0.018


def test_aggressive_limit_adds_ticks_above_ask1_within_cap():
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    engine = _engine_with_quote(ask1=133_000, bid1=132_900, current=133_600)

    price, source = engine._compute_aggressive_limit_price("069620", fallback_price=133_000)

    # raw = 133,000 + 2tick(100) = 133,200 ≤ base_cap(133,600) → 정상(기존 동작)
    assert price == 133_200
    assert source == "ask1+2tick"


def test_aggressive_limit_base_cap_holds_when_gap_exceeds_trigger():
    """ask1이 base cap보다 trigger(+1.2%)를 넘으면 기존 base cap 고정(:capped) 유지."""
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 3
    _set_b_defaults()
    # ask1 135,300 vs base cap 133,600 → cap_gap 1.27% > 1.2%(트리거),
    # fallback 대비 gap 1.73% < 1.8%(과열)라 base cap 고정이어야 한다.
    engine = _engine_with_quote(ask1=135_300, bid1=135_200, current=135_300)

    price, source = engine._compute_aggressive_limit_price("069620", fallback_price=133_000)

    # 133,000 × 1.005 = 133,665 → 100원 틱 절사 = 133,600 (base cap 고정)
    assert price == 133_600
    assert source == "ask1+3tick:capped"


def test_aggressive_limit_kt_dynamic_chase_lifts_above_base_cap():
    """2026-07-09 KT 사례: base cap에 걸리던 주문가가 dynamic cap으로 상승."""
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    # 실제 로그상 기존 주문가는 54,500원(base cap), ask1은 최대 55,100원까지 관측.
    # fallback 54,250이면 base cap이 54,500원으로 재현된다.
    engine = _engine_with_quote(ask1=55_100, bid1=54_900, current=55_100)

    price, source = engine._compute_aggressive_limit_price("030200", fallback_price=54_250)

    base_cap = (int(54_250 * 1.005) // 100) * 100  # 54,500
    dyn_cap = (int(base_cap * 1.012) // 100) * 100  # 55,100
    # raw = 55,100 + 2tick(100) = 55,300 > dyn_cap(55,100) → dyn_capped
    assert price == dyn_cap
    assert price > base_cap  # 기존 cap(54,700)보다 높아 체결 가능
    assert price >= 55_100
    assert source == "ask1+2tick:dyn_capped"

    quote = engine._last_aggressive_quote
    assert quote["base_cap_price"] == base_cap
    assert quote["dynamic_cap_price"] == dyn_cap
    assert quote["overheat"] is False
    assert quote["gap_pct"] > 0


def test_aggressive_limit_skt_small_overshoot_allowed():
    """2026-07-09 SKT 사례: 작은 cap 초과는 dynamic으로 raw 그대로 허용."""
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    # 실제 로그상 기존 주문가는 86,600원(base cap), ask1은 최대 87,000원까지 관측.
    # fallback 86,200이면 base cap이 86,600원으로 재현된다.
    engine = _engine_with_quote(ask1=87_000, bid1=86_900, current=87_000)

    price, source = engine._compute_aggressive_limit_price("017670", fallback_price=86_200)

    base_cap = (int(86_200 * 1.005) // 100) * 100  # 86,600
    dyn_cap = (int(base_cap * 1.012) // 100) * 100  # 87,600
    # raw = 87,000 + 2tick(100) = 87,200 ≤ dyn_cap(87,600) → dynamic(raw 그대로)
    assert price == 87_200
    assert price > base_cap
    assert source == "ask1+2tick:dynamic"

    quote = engine._last_aggressive_quote
    assert quote["dynamic_cap_price"] == dyn_cap


def test_aggressive_limit_overheat_gives_up_immediately():
    """과열(ask1이 기준 대비 +1.8% 이상)이면 주문가 0 + overheat source 반환."""
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    # 기준 100,000 / ask1 102,000 → gap 2.0% ≥ 1.8% → 과열 포기
    engine = _engine_with_quote(ask1=102_000, bid1=101_900, current=102_000)

    price, source = engine._compute_aggressive_limit_price("000000", fallback_price=100_000)

    assert price == 0
    assert source.endswith(":overheat")
    quote = engine._last_aggressive_quote
    assert quote["overheat"] is True
    assert quote["note"]  # 진단 메시지 존재


def test_aggressive_limit_overheat_fast_break_no_orders():
    """과열이면 retry 루프가 주문 없이 즉시 중단(재시도 0, 반복 발주 방지)."""
    settings.LIMIT_AGGRESSIVE_RETRY_TIMEOUT = 1
    settings.LIMIT_AGGRESSIVE_POLL_INTERVAL = 1
    settings.LIMIT_AGGRESSIVE_MAX_RETRIES = 5
    settings.LIMIT_AGGRESSIVE_TOTAL_TIMEOUT = 5
    settings.LIMIT_AGGRESSIVE_CANCEL_DELAY = 0
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    engine = _engine_with_quote(ask1=102_000, bid1=101_900, current=102_000)

    result = engine._place_aggressive_limit_with_retry(
        stock_code="000000",
        stock_name="과열종목",
        requested_qty=10,
        expected_price=100_000,
    )

    assert result["success"] is False
    assert result["quantity"] == 0
    assert result["retry_count"] == 0  # 주문 미발주
    assert "과열" in result["message"]
    assert result["quote_samples"][0]["overheat"] is True


def test_aggressive_limit_result_contains_diagnostics_for_unfilled_orders():
    settings.LIMIT_AGGRESSIVE_RETRY_TIMEOUT = 1
    settings.LIMIT_AGGRESSIVE_POLL_INTERVAL = 1
    settings.LIMIT_AGGRESSIVE_MAX_RETRIES = 2
    settings.LIMIT_AGGRESSIVE_TOTAL_TIMEOUT = 5
    settings.LIMIT_AGGRESSIVE_CANCEL_DELAY = 0
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    _set_b_defaults()
    engine = _engine_with_quote(ask1=133_000, bid1=132_900, current=133_600)
    engine.order_api.scenario_fill_ratio = 0.0

    result = engine._place_aggressive_limit_with_retry(
        stock_code="069620",
        stock_name="대웅제약",
        requested_qty=5,
        expected_price=133_000,
    )

    assert result["success"] is False
    assert result["quantity"] == 0
    assert result["remaining_shares"] == 5
    assert result["limit_price_min"] == 133_200
    assert result["limit_price_max"] == 133_200
    assert result["quote_samples"][0]["ask1"] == 133_000
    assert result["quote_samples"][0]["order_price"] == 133_200
    assert "추정" in result["message"]
