"""kis_orderbook_collector.poll_asking_price async generator 단위 테스트.

POLL-1~4: max_iterations / interval / 첫 yield 즉시 / cancel 전파.

실행:
    venv/bin/python scripts/test_kis_orderbook_polling.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors.kis_orderbook_collector import (
    KisOrderbookCollector,
    OrderbookSnapshot,
)
from config import now_kst


def _make_collector():
    """mock order_api 주입한 KisOrderbookCollector."""
    mock_api = MagicMock()
    # inquire_asking_price 응답
    mock_api.inquire_asking_price.return_value = {
        "success": True,
        "ask1": 75100,
        "bid1": 75000,
        "ask_volume1": 1000,
        "bid_volume1": 2000,
        "current_price": 75050,
    }
    return KisOrderbookCollector(mock_api)


def _run(coro):
    # 신규 이벤트 루프 생성 + 종료 시 close 보장 (async generator pending task 잔재 차단)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_POLL_1_max_iterations_3():
    """POLL-1: max_iterations=3 → 정확히 3건 yield."""
    collector = _make_collector()

    async def consume():
        results = []
        async for snap in collector.poll_asking_price(
            "005930", interval_sec=0.01, max_iterations=3
        ):
            results.append(snap)
        return results

    results = _run(consume())
    assert len(results) == 3, f"len={len(results)}, 3 기대"
    assert all(isinstance(s, OrderbookSnapshot) for s in results)
    print(f"✅ POLL-1 max_iterations=3 → {len(results)}건 yield")


def test_POLL_2_interval_timing():
    """POLL-2: interval_sec=0.1 → 3건 폴링 시 ~0.2초 (3-1 인터벌)."""
    collector = _make_collector()

    async def consume():
        start = time.perf_counter()
        count = 0
        async for snap in collector.poll_asking_price(
            "005930", interval_sec=0.1, max_iterations=3
        ):
            count += 1
        return time.perf_counter() - start

    elapsed = _run(consume())
    # 3건 yield 사이 2번 sleep (0.1 + 0.1 = 0.2). 첫 yield는 즉시
    # 다만 max_iterations 도달 후에도 마지막 sleep 발생 — 0.3까지 가능
    assert 0.15 < elapsed < 0.5, f"elapsed={elapsed:.3f}초, 0.15~0.5 기대"
    print(f"✅ POLL-2 interval 정확: {elapsed:.3f}초 (0.15~0.5)")


def test_POLL_3_first_yield_immediate():
    """POLL-3: 첫 yield는 sleep 없이 즉시."""
    collector = _make_collector()

    async def consume():
        start = time.perf_counter()
        async for snap in collector.poll_asking_price(
            "005930", interval_sec=1.0, max_iterations=1
        ):
            first_yield = time.perf_counter() - start
            return first_yield

    first_yield = _run(consume())
    # 첫 yield 시점은 KIS mock 호출 후 즉시 (< 0.1초)
    assert first_yield < 0.5, f"first_yield={first_yield:.3f}, < 0.5 기대"
    print(f"✅ POLL-3 첫 yield 즉시: {first_yield:.4f}초")


def test_POLL_4_cancel_propagation():
    """POLL-4: 외부 task.cancel() → CancelledError 정상 전파."""
    collector = _make_collector()

    async def long_poll():
        async for snap in collector.poll_asking_price(
            "005930", interval_sec=10.0, max_iterations=100
        ):
            pass   # 길게 폴링

    async def runner():
        task = asyncio.create_task(long_poll())
        await asyncio.sleep(0.05)  # 첫 yield 후 sleep 진입 대기
        task.cancel()
        try:
            await task
            return "no_cancel"
        except asyncio.CancelledError:
            return "cancelled"

    result = _run(runner())
    assert result == "cancelled", f"result={result}, 'cancelled' 기대"
    print("✅ POLL-4 cancel 전파 정확")


def main():
    tests = [
        test_POLL_1_max_iterations_3,
        test_POLL_2_interval_timing,
        test_POLL_3_first_yield_immediate,
        test_POLL_4_cancel_propagation,
    ]
    print(f"\n=== kis_orderbook_collector polling 단위 테스트 — {len(tests)}건 실행 ===\n")
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
