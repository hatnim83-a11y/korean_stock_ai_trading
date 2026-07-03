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


def test_aggressive_limit_adds_ticks_above_ask1_within_cap():
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    settings.LIMIT_AGGRESSIVE_MAX_CHASE_PCT = 0.005
    engine = _engine_with_quote(ask1=133_000, bid1=132_900, current=133_600)

    price, source = engine._compute_aggressive_limit_price("069620", fallback_price=133_000)

    assert price == 133_200
    assert source == "ask1+2tick"


def test_aggressive_limit_chase_is_capped_by_expected_price():
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 3
    settings.LIMIT_AGGRESSIVE_MAX_CHASE_PCT = 0.005
    engine = _engine_with_quote(ask1=133_900, bid1=133_800, current=134_000)

    price, source = engine._compute_aggressive_limit_price("069620", fallback_price=133_000)

    # 133,000 × 1.005 = 133,665, rounded down to the 100원 tick = 133,600
    assert price == 133_600
    assert source == "ask1+3tick:capped"


def test_aggressive_limit_result_contains_diagnostics_for_unfilled_orders():
    settings.LIMIT_AGGRESSIVE_RETRY_TIMEOUT = 1
    settings.LIMIT_AGGRESSIVE_POLL_INTERVAL = 1
    settings.LIMIT_AGGRESSIVE_MAX_RETRIES = 2
    settings.LIMIT_AGGRESSIVE_TOTAL_TIMEOUT = 5
    settings.LIMIT_AGGRESSIVE_CANCEL_DELAY = 0
    settings.LIMIT_AGGRESSIVE_CHASE_TICKS = 2
    settings.LIMIT_AGGRESSIVE_MAX_CHASE_PCT = 0.005
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
