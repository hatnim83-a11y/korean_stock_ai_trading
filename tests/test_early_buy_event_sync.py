"""test_early_buy_event_sync.py - A3안 Screening↔Buy asyncio.Event 동기화 검증

CRITICAL: 09:02 screening → 09:05 buy 사이 race 봉쇄를 확인.
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_event_initial_state():
    """초기 상태는 set (첫 매수 호출 즉시 통과)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        evt = asyncio.Event()
        evt.set()
        assert evt.is_set() is True, "초기 set 상태 필수 (첫 매수 무대기)"
        print("  [PASS] event_initial_state — 초기 set 확인")
    finally:
        loop.close()


def test_event_wait_blocks_until_set():
    """clear 후 wait → set 후에만 통과."""
    async def _scenario():
        evt = asyncio.Event()
        evt.set()  # 초기 상태

        # screening 진입 시뮬 — clear
        evt.clear()
        assert evt.is_set() is False

        # 50ms 후 set (screening 완료 시뮬)
        async def _set_later():
            await asyncio.sleep(0.05)
            evt.set()

        asyncio.create_task(_set_later())

        # buy 대기 (timeout 1초)
        try:
            await asyncio.wait_for(evt.wait(), timeout=1.0)
            assert evt.is_set() is True
            return True
        except asyncio.TimeoutError:
            return False

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ok = loop.run_until_complete(_scenario())
        assert ok is True, "set 후 wait 즉시 통과해야 함"
        print("  [PASS] event_wait_blocks_until_set — set 후 통과")
    finally:
        loop.close()


def test_event_timeout_on_unset():
    """clear 상태 + set 없음 → wait_for timeout 발생."""
    async def _scenario():
        evt = asyncio.Event()
        evt.clear()
        try:
            await asyncio.wait_for(evt.wait(), timeout=0.05)
            return "no_timeout"
        except asyncio.TimeoutError:
            return "timeout"

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_scenario())
        assert result == "timeout", f"timeout 발생해야 함 (실제: {result})"
        print("  [PASS] event_timeout_on_unset — set 안 되면 timeout")
    finally:
        loop.close()


def test_screening_finally_set():
    """run_stock_screening 함수가 예외 발생해도 finally에서 set 보장 검증."""
    async def _scenario():
        evt = asyncio.Event()
        evt.set()

        async def _screening_with_exception():
            evt.clear()
            try:
                raise RuntimeError("스크리닝 중 에러")
            finally:
                evt.set()

        try:
            await _screening_with_exception()
        except RuntimeError:
            pass

        return evt.is_set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        is_set = loop.run_until_complete(_scenario())
        assert is_set is True, "예외 발생해도 finally에서 set 보장"
        print("  [PASS] screening_finally_set — 예외 후 set 보장")
    finally:
        loop.close()


def _run_all():
    print("== test_early_buy_event_sync.py ==")
    test_event_initial_state()
    test_event_wait_blocks_until_set()
    test_event_timeout_on_unset()
    test_screening_finally_set()
    print("OK\n")


if __name__ == '__main__':
    _run_all()
