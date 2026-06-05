"""Off-VM healthcheck ping 테스트 (2026-06-05 incident P0-A).

검증:
1. send_healthcheck_ping: 성공/실패/타임아웃/빈URL 모두 raise 없이 bool 반환
2. setup_schedules: ENABLED=False → 잡 미등록 / ENABLED=True+URL="" → 미등록+warning / ENABLED=True+URL → 등록
3. _run_healthcheck_ping 에 @_skip_on_holiday 미적용 (휴장일에도 ping)
"""

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.system_guard.healthcheck import send_healthcheck_ping


# ---------- 1. send_healthcheck_ping ----------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_ping_empty_url_returns_false():
    assert _run(send_healthcheck_ping("")) is False
    assert _run(send_healthcheck_ping("   ")) is False
    assert _run(send_healthcheck_ping(None)) is False  # type: ignore[arg-type]


def test_ping_success_2xx():
    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    with patch("modules.system_guard.healthcheck.httpx.AsyncClient", _Client):
        assert _run(send_healthcheck_ping("https://hc-ping.com/x")) is True


def test_ping_non_2xx_returns_false():
    class _Resp:
        status_code = 404

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    with patch("modules.system_guard.healthcheck.httpx.AsyncClient", _Client):
        assert _run(send_healthcheck_ping("https://hc-ping.com/x")) is False


def test_ping_exception_never_raises():
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            raise RuntimeError("network unreachable")

    with patch("modules.system_guard.healthcheck.httpx.AsyncClient", _Client):
        # 예외 전파 없이 False
        assert _run(send_healthcheck_ping("https://hc-ping.com/x")) is False


# ---------- 2. scheduler 잡 등록 ----------

def _make_scheduler():
    from scheduler import TradingScheduler
    return TradingScheduler()


def _has_job(sched, job_id):
    return sched.scheduler.get_job(job_id) is not None


def test_job_not_registered_when_disabled():
    with patch("scheduler.settings") as ms:
        ms.HEALTHCHECK_ENABLED = False
        sched = _make_scheduler()
        sched._setup_healthcheck_job()
        assert not _has_job(sched, "healthcheck_ping")


def test_job_not_registered_when_url_blank():
    with patch("scheduler.settings") as ms:
        ms.HEALTHCHECK_ENABLED = True
        ms.HEALTHCHECK_PING_URL = "   "
        ms.HEALTHCHECK_PING_INTERVAL_MIN = 5
        sched = _make_scheduler()
        sched._setup_healthcheck_job()
        assert not _has_job(sched, "healthcheck_ping")


def test_job_registered_when_enabled_with_url():
    with patch("scheduler.settings") as ms:
        ms.HEALTHCHECK_ENABLED = True
        ms.HEALTHCHECK_PING_URL = "https://hc-ping.com/abc"
        ms.HEALTHCHECK_PING_INTERVAL_MIN = 5
        sched = _make_scheduler()
        sched._setup_healthcheck_job()
        assert _has_job(sched, "healthcheck_ping")
        job = sched.scheduler.get_job("healthcheck_ping")
        assert job is not None


# ---------- 3. 휴장일 데코레이터 미적용 ----------

def test_run_healthcheck_ping_no_holiday_skip():
    """_run_healthcheck_ping 소스에 _skip_on_holiday 데코레이터가 없어야 한다."""
    from scheduler import TradingScheduler
    src = inspect.getsource(TradingScheduler._run_healthcheck_ping)
    assert "_skip_on_holiday" not in src


def test_run_healthcheck_ping_invokes_callback():
    """콜백이 등록돼 있으면 await 호출된다."""
    sched = _make_scheduler()
    cb = AsyncMock()
    sched.on_healthcheck_ping = cb
    _run(sched._run_healthcheck_ping())
    cb.assert_awaited_once()


def test_run_healthcheck_ping_callback_exception_isolated():
    """콜백이 예외를 던져도 _run_healthcheck_ping은 raise하지 않는다."""
    sched = _make_scheduler()
    sched.on_healthcheck_ping = AsyncMock(side_effect=RuntimeError("boom"))
    # 예외 전파 없어야 함
    _run(sched._run_healthcheck_ping())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
