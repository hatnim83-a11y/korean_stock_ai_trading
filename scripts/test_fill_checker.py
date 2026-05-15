"""fill_checker.py 단위 테스트.

FILL-1~8: get_fill_status 정상/부분체결/재시도/예외/미매칭 등.

실행:
    venv/bin/python scripts/test_fill_checker.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.execution.fill_checker import FillChecker, FillStatus


class _MockKISOrderApi:
    """get_order_status mock — call 기반 응답 시퀀스."""

    def __init__(self, responses=None, exceptions=None):
        self._responses = responses or []
        self._exceptions = exceptions or []
        self.call_count = 0

    def get_order_status(self, order_id=None, order_date=None):
        idx = self.call_count
        self.call_count += 1
        if idx < len(self._exceptions) and self._exceptions[idx] is not None:
            raise self._exceptions[idx]
        if idx < len(self._responses):
            return self._responses[idx]
        return []


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_FILL_1_full_fill():
    """FILL-1: 전량 체결 정상."""
    mock_api = _MockKISOrderApi(responses=[[{
        "order_id": "ODNO123",
        "order_qty": 100,
        "filled_qty": 100,
        "filled_price": 75000,
        "status": "체결",
    }]])
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO123", backoff_base_sec=0.01))
    assert result is not None
    assert result.is_filled is True
    assert result.is_partial is False
    assert result.executed_shares == 100
    assert result.executed_price == 75000
    assert result.remaining_shares == 0
    print(f"✅ FILL-1 전량 체결: {result.executed_shares}주 @ {result.executed_price}원")


def test_FILL_2_partial_fill():
    """FILL-2: 부분 체결."""
    mock_api = _MockKISOrderApi(responses=[[{
        "order_id": "ODNO456",
        "order_qty": 100,
        "filled_qty": 60,
        "filled_price": 74500,
        "status": "미체결",
    }]])
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO456", backoff_base_sec=0.01))
    assert result.is_partial is True
    assert result.is_filled is False
    assert result.executed_shares == 60
    assert result.remaining_shares == 40
    print(f"✅ FILL-2 부분 체결: 60/100주 @ {result.executed_price}원, 잔량 40")


def test_FILL_3_unfilled():
    """FILL-3: 전량 미체결."""
    mock_api = _MockKISOrderApi(responses=[[{
        "order_id": "ODNO789",
        "order_qty": 50,
        "filled_qty": 0,
        "filled_price": 0,
        "status": "미체결",
    }]])
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO789", backoff_base_sec=0.01))
    assert result.is_filled is False
    assert result.is_partial is False
    assert result.executed_shares == 0
    assert result.remaining_shares == 50
    print("✅ FILL-3 전량 미체결")


def test_FILL_4_retry_500_then_success():
    """FILL-4: 첫 호출 500 → 2차 성공 (5/13 사건 시뮬레이션)."""
    mock_api = _MockKISOrderApi(
        responses=[None, [{
            "order_id": "ODNO_RETRY",
            "order_qty": 30,
            "filled_qty": 30,
            "filled_price": 50000,
            "status": "체결",
        }]],
        exceptions=[RuntimeError("KIS 500"), None],
    )
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO_RETRY", backoff_base_sec=0.01))
    assert mock_api.call_count == 2, f"call_count={mock_api.call_count}, 2 기대"
    assert result is not None
    assert result.is_filled is True
    print(f"✅ FILL-4 500 1회 → 재시도 후 성공 (호출 {mock_api.call_count}회)")


def test_FILL_5_retry_all_fail():
    """FILL-5: 3회 모두 실패 → None 반환."""
    mock_api = _MockKISOrderApi(
        responses=[None, None, None],
        exceptions=[RuntimeError("500")] * 3,
    )
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO_FAIL", backoff_base_sec=0.01))
    assert mock_api.call_count == 3
    assert result is None
    print("✅ FILL-5 3회 모두 실패 → None")


def test_FILL_6_not_found_in_response():
    """FILL-6: 응답에 order_id 미매칭 → not_found."""
    mock_api = _MockKISOrderApi(responses=[[{
        "order_id": "OTHER_ODNO",
        "order_qty": 10,
        "filled_qty": 10,
        "filled_price": 10000,
        "status": "체결",
    }]])
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("MISSING_ODNO", backoff_base_sec=0.01))
    assert result is not None
    assert result.raw_status == "not_found"
    assert result.executed_shares == 0
    print("✅ FILL-6 응답 미매칭 → not_found")


def test_FILL_7_empty_response():
    """FILL-7: 빈 응답 ([]) — 재시도 안 함, 즉시 not_found 반환."""
    mock_api = _MockKISOrderApi(responses=[[]])
    fc = FillChecker(mock_api)
    result = _run(fc.get_fill_status("ODNO_EMPTY", backoff_base_sec=0.01))
    assert mock_api.call_count == 1, f"빈 응답은 재시도 X, call={mock_api.call_count}"
    assert result is not None
    assert result.raw_status == "not_found"
    print("✅ FILL-7 빈 응답 → 재시도 X")


def test_FILL_8_backoff_progression():
    """FILL-8: 재시도 backoff 진행 확인 (mock asyncio.sleep)."""
    sleep_calls = []

    orig_sleep = asyncio.sleep

    async def mock_sleep(delay):
        sleep_calls.append(delay)
        await orig_sleep(0.001)  # 실제로는 작은 시간만

    asyncio.sleep = mock_sleep  # type: ignore
    try:
        mock_api = _MockKISOrderApi(
            responses=[None, None, [{
                "order_id": "ODNO_BO",
                "order_qty": 10,
                "filled_qty": 10,
                "filled_price": 1000,
                "status": "체결",
            }]],
            exceptions=[RuntimeError("err1"), RuntimeError("err2"), None],
        )
        fc = FillChecker(mock_api)
        result = _run(fc.get_fill_status("ODNO_BO", backoff_base_sec=2.0))
        # backoff: 1차 실패 후 sleep(2*1=2초) / 2차 실패 후 sleep(2*2=4초)
        assert sleep_calls == [2.0, 4.0], f"backoff={sleep_calls}, [2.0, 4.0] 기대"
        assert result is not None and result.is_filled
        print(f"✅ FILL-8 backoff 진행 정확: {sleep_calls}")
    finally:
        asyncio.sleep = orig_sleep  # type: ignore


def main():
    tests = [
        test_FILL_1_full_fill,
        test_FILL_2_partial_fill,
        test_FILL_3_unfilled,
        test_FILL_4_retry_500_then_success,
        test_FILL_5_retry_all_fail,
        test_FILL_6_not_found_in_response,
        test_FILL_7_empty_response,
        test_FILL_8_backoff_progression,
    ]
    print(f"\n=== fill_checker 단위 테스트 — {len(tests)}건 실행 ===\n")
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
