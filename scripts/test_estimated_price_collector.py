"""estimated_price_collector.py 단위 테스트.

EP-1~5: 정상 / 15:20 이전 / 빈 응답 / 예외 / 빈 필드 graceful.

실행:
    venv/bin/python scripts/test_estimated_price_collector.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors.estimated_price_collector import (
    EstimatedPriceCollector,
    EstimatedPriceSnapshot,
)


class _MockOrderApi:
    """inquire_asking_price mock."""

    def __init__(self, payload=None, exception=None):
        self._payload = payload
        self._exception = exception

    def inquire_asking_price(self, stock_code):
        if self._exception:
            raise self._exception
        return self._payload


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


_AFTER_1520 = datetime(2026, 5, 15, 15, 25, 0, tzinfo=timezone(timedelta(hours=9)))
_BEFORE_1520 = datetime(2026, 5, 15, 15, 10, 0, tzinfo=timezone(timedelta(hours=9)))


def test_EP_1_normal_response():
    """EP-1: 정상 응답 (15:20 이후) — antc_cnpr 양수."""
    mock_api = _MockOrderApi(payload={
        "output1": {
            "antc_cnpr": "76500",
            "antc_vol": "1234567",
            "askp_rsqn1": "5000",
            "bidp_rsqn1": "3000",
        }
    })
    ec = EstimatedPriceCollector(mock_api)
    with patch("closing_bet_system.collectors.estimated_price_collector.now_kst",
               return_value=_AFTER_1520):
        snap = _run(ec.get_estimated_price("005930"))
    assert snap is not None
    assert snap.estimated_price == 76_500
    assert snap.estimated_volume == 1_234_567
    assert snap.ask_total == 5_000
    assert snap.bid_total == 3_000
    print(f"✅ EP-1 정상 응답: {snap.estimated_price}원 (ask 5000 / bid 3000)")


def test_EP_2_before_1520():
    """EP-2: 15:20 이전 호출 → estimated_price=None (graceful)."""
    mock_api = _MockOrderApi(payload={
        "output1": {
            "antc_cnpr": "75000",
            "askp_rsqn1": "1000",
            "bidp_rsqn1": "2000",
        }
    })
    ec = EstimatedPriceCollector(mock_api)
    with patch("closing_bet_system.collectors.estimated_price_collector.now_kst",
               return_value=_BEFORE_1520):
        snap = _run(ec.get_estimated_price("005930"))
    assert snap is not None
    assert snap.estimated_price is None  # 시간대 가드
    # 호가는 정상 (시간대 무관)
    assert snap.ask_total == 1_000
    assert snap.bid_total == 2_000
    print("✅ EP-2 15:20 이전 → estimated_price None / 호가는 정상")


def test_EP_3_empty_payload():
    """EP-3: KIS 빈 응답 → None."""
    mock_api = _MockOrderApi(payload=None)
    ec = EstimatedPriceCollector(mock_api)
    with patch("closing_bet_system.collectors.estimated_price_collector.now_kst",
               return_value=_AFTER_1520):
        snap = _run(ec.get_estimated_price("005930"))
    assert snap is None
    print("✅ EP-3 빈 응답 → None")


def test_EP_4_kis_exception():
    """EP-4: KIS 예외 → None graceful."""
    mock_api = _MockOrderApi(exception=RuntimeError("KIS 500"))
    ec = EstimatedPriceCollector(mock_api)
    with patch("closing_bet_system.collectors.estimated_price_collector.now_kst",
               return_value=_AFTER_1520):
        snap = _run(ec.get_estimated_price("005930"))
    assert snap is None
    print("✅ EP-4 KIS 예외 → None graceful")


def test_EP_5_blank_fields():
    """EP-5: 응답 필드 빈 문자열 → 0/None graceful."""
    mock_api = _MockOrderApi(payload={
        "output1": {
            "antc_cnpr": "",
            "antc_vol": "",
            "askp_rsqn1": "",
            "bidp_rsqn1": "",
        }
    })
    ec = EstimatedPriceCollector(mock_api)
    with patch("closing_bet_system.collectors.estimated_price_collector.now_kst",
               return_value=_AFTER_1520):
        snap = _run(ec.get_estimated_price("005930"))
    assert snap is not None
    assert snap.estimated_price is None
    assert snap.estimated_volume is None
    assert snap.ask_total == 0
    assert snap.bid_total == 0
    print("✅ EP-5 빈 필드 → None/0 graceful")


def main():
    tests = [
        test_EP_1_normal_response,
        test_EP_2_before_1520,
        test_EP_3_empty_payload,
        test_EP_4_kis_exception,
        test_EP_5_blank_fields,
    ]
    print(f"\n=== estimated_price_collector 단위 테스트 — {len(tests)}건 실행 ===\n")
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
