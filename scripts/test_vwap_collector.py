"""vwap_collector.py 단위 테스트.

VWAP-1~6: 정상 계산 / 빈 응답 / 거래량 0 / 시간대 외 / 예외 / 시간 가드.

실행:
    venv/bin/python scripts/test_vwap_collector.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time as time_cls, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors.vwap_collector import (
    InsufficientDataError,
    VWAPCollector,
)


class _MockKISApi:
    """get_minute_price mock."""

    def __init__(self, bars=None, exception=None):
        self._bars = bars
        self._exception = exception
        self.last_call = None

    def get_minute_price(self, stock_code, time_to="153000", count=30):
        self.last_call = {"ticker": stock_code, "time_to": time_to, "count": count}
        if self._exception:
            raise self._exception
        return self._bars


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# 14:50 이후 KST 고정
_FIXED_NOW_AFTER_1450 = datetime(2026, 5, 15, 15, 20, 0, tzinfo=timezone(timedelta(hours=9)))
_FIXED_NOW_BEFORE_1450 = datetime(2026, 5, 15, 14, 30, 0, tzinfo=timezone(timedelta(hours=9)))


def test_VWAP_1_normal_calculation():
    """VWAP-1: 14:50~15:18 정상 분봉으로 VWAP 계산.

    bars: close=75000@vol=1000, close=75500@vol=2000, close=76000@vol=1500
    VWAP = (75000*1000 + 75500*2000 + 76000*1500) / 4500
         = (75000000 + 151000000 + 114000000) / 4500
         = 340000000 / 4500 = 75555.555...
    """
    bars = [
        {"time": "151800", "close": 76_000, "volume": 1_500},
        {"time": "151700", "close": 75_500, "volume": 2_000},
        {"time": "151500", "close": 75_000, "volume": 1_000},
    ]
    mock_api = _MockKISApi(bars=bars)
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_AFTER_1450):
        vwap = _run(vc.get_vwap("005930"))
    expected = (76_000 * 1_500 + 75_500 * 2_000 + 75_000 * 1_000) / 4_500
    assert abs(vwap - expected) < 0.01, f"vwap={vwap}, 기대={expected}"
    print(f"✅ VWAP-1 정상 계산: {vwap:.2f} (기대 {expected:.2f})")


def test_VWAP_2_filter_outside_window():
    """VWAP-2: 14:50 이전 분봉은 필터링 (1449는 제외, 1450~1518만 사용)."""
    bars = [
        {"time": "144900", "close": 70_000, "volume": 5_000},   # 14:49 → 제외
        {"time": "145000", "close": 75_000, "volume": 1_000},   # 14:50 → 포함
        {"time": "151800", "close": 76_000, "volume": 1_000},   # 15:18 → 포함
        {"time": "151900", "close": 80_000, "volume": 1_000},   # 15:19 → 제외
    ]
    mock_api = _MockKISApi(bars=bars)
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_AFTER_1450):
        vwap = _run(vc.get_vwap("005930"))
    expected = (75_000 + 76_000) / 2.0   # 동일 volume이라 평균
    assert abs(vwap - expected) < 0.01, f"vwap={vwap}, 기대={expected}"
    print(f"✅ VWAP-2 시간대 필터 정확: {vwap:.2f}")


def test_VWAP_3_empty_response():
    """VWAP-3: 빈 응답 → None."""
    mock_api = _MockKISApi(bars=[])
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_AFTER_1450):
        vwap = _run(vc.get_vwap("005930"))
    assert vwap is None
    print("✅ VWAP-3 빈 응답 → None")


def test_VWAP_4_zero_volume():
    """VWAP-4: 거래량 모두 0 → None."""
    bars = [
        {"time": "151000", "close": 75_000, "volume": 0},
        {"time": "151500", "close": 76_000, "volume": 0},
    ]
    mock_api = _MockKISApi(bars=bars)
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_AFTER_1450):
        vwap = _run(vc.get_vwap("005930"))
    assert vwap is None
    print("✅ VWAP-4 거래량 0 → None")


def test_VWAP_5_before_1450():
    """VWAP-5: 14:50 이전 호출 → InsufficientDataError."""
    mock_api = _MockKISApi(bars=[])
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_BEFORE_1450):
        try:
            _run(vc.get_vwap("005930"))
            raise AssertionError("InsufficientDataError 기대")
        except InsufficientDataError:
            pass
    print("✅ VWAP-5 14:50 이전 호출 → InsufficientDataError")


def test_VWAP_6_kis_exception():
    """VWAP-6: KIS 예외 → None graceful."""
    mock_api = _MockKISApi(exception=RuntimeError("KIS 500"))
    vc = VWAPCollector(mock_api)
    with patch("closing_bet_system.collectors.vwap_collector.now_kst",
               return_value=_FIXED_NOW_AFTER_1450):
        vwap = _run(vc.get_vwap("005930"))
    assert vwap is None
    print("✅ VWAP-6 KIS 예외 → None graceful")


def main():
    tests = [
        test_VWAP_1_normal_calculation,
        test_VWAP_2_filter_outside_window,
        test_VWAP_3_empty_response,
        test_VWAP_4_zero_volume,
        test_VWAP_5_before_1450,
        test_VWAP_6_kis_exception,
    ]
    print(f"\n=== vwap_collector 단위 테스트 — {len(tests)}건 실행 ===\n")
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
