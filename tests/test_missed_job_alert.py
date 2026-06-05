"""P0-B 누락 핵심 잡 탐지 + 장중 비정상 재시작 경보 테스트 (2026-06-05 incident).

검증:
- B1: _on_job_missed — core잡→콜백 스케줄 / 비core 무시 / 휴장일 무시 / dedup / 예외격리 / 리스너 등록 토글 / core_job_ids 동적 교집합
- alert: alert_job_missed 전송 실패 예외 소거
"""

import asyncio
import sys
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


def _make_scheduler():
    from scheduler import TradingScheduler
    return TradingScheduler()


def _missed_event(job_id, sched_time=None):
    """EVENT_JOB_MISSED 이벤트 모사 (JobExecutionEvent: job_id, scheduled_run_time)."""
    ev = types.SimpleNamespace()
    ev.job_id = job_id
    ev.scheduled_run_time = sched_time or datetime(2026, 6, 5, 9, 5, tzinfo=KST)
    return ev


# ---------- B1: _on_job_missed ----------

def test_missed_core_job_schedules_callback():
    sched = _make_scheduler()
    sched.core_job_ids = {"stock_screening", "execute_buy"}
    calls = []

    async def cb(job_id, ts):
        calls.append(job_id)

    sched.on_job_missed_alert = cb

    async def drive():
        with patch("scheduler.is_trading_day", return_value=True):
            sched._on_job_missed(_missed_event("stock_screening"))
        await asyncio.sleep(0)  # create_task 실행 기회

    asyncio.get_event_loop().run_until_complete(drive())
    assert "stock_screening" in calls


def test_missed_non_core_job_ignored():
    sched = _make_scheduler()
    sched.core_job_ids = {"stock_screening"}
    calls = []

    async def cb(job_id, ts):
        calls.append(job_id)

    sched.on_job_missed_alert = cb

    async def drive():
        with patch("scheduler.is_trading_day", return_value=True):
            sched._on_job_missed(_missed_event("daily_report"))  # 비core
        await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(drive())
    assert calls == []


def test_missed_on_holiday_ignored():
    sched = _make_scheduler()
    sched.core_job_ids = {"stock_screening"}
    calls = []

    async def cb(job_id, ts):
        calls.append(job_id)

    sched.on_job_missed_alert = cb

    async def drive():
        with patch("scheduler.is_trading_day", return_value=False):  # 휴장일
            sched._on_job_missed(_missed_event("stock_screening"))
        await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(drive())
    assert calls == []


def test_missed_dedup_blocks_duplicate():
    sched = _make_scheduler()
    sched.core_job_ids = {"stock_screening"}
    calls = []

    async def cb(job_id, ts):
        calls.append(job_id)

    sched.on_job_missed_alert = cb
    ev = _missed_event("stock_screening")

    async def drive():
        with patch("scheduler.is_trading_day", return_value=True):
            sched._on_job_missed(ev)
            sched._on_job_missed(ev)  # 동일 (job_id, scheduled_run_time)
        await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(drive())
    assert len(calls) == 1


def test_missed_listener_exception_isolated():
    """콜백/내부 예외가 _on_job_missed 밖으로 전파되지 않는다."""
    sched = _make_scheduler()
    sched.core_job_ids = {"stock_screening"}
    sched.on_job_missed_alert = "not-callable"  # create_task 시 TypeError 유발

    with patch("scheduler.is_trading_day", return_value=True):
        # 예외 전파 없어야 함
        sched._on_job_missed(_missed_event("stock_screening"))


def test_listener_registered_when_enabled():
    from apscheduler.events import EVENT_JOB_MISSED
    with patch("scheduler.settings") as ms:
        ms.JOB_RECOVERY_ALERT_ENABLED = True
        sched = _make_scheduler()
        # 리스너 목록에 _on_job_missed 등록 확인
        found = any(
            getattr(cb, "__name__", "") == "_on_job_missed"
            for (cb, mask) in sched.scheduler._listeners
        )
        assert found


def test_listener_not_registered_when_disabled():
    with patch("scheduler.settings") as ms:
        ms.JOB_RECOVERY_ALERT_ENABLED = False
        sched = _make_scheduler()
        found = any(
            getattr(cb, "__name__", "") == "_on_job_missed"
            for (cb, mask) in sched.scheduler._listeners
        )
        assert not found


def test_core_job_ids_dynamic_intersection():
    """setup_schedules 후 core_job_ids가 등록 잡과 교집합."""
    import logging
    logging.disable(logging.CRITICAL)
    sched = _make_scheduler()
    sched.setup_schedules()
    from scheduler import CANDIDATE_CORE_JOB_IDS
    registered = {j.id for j in sched.scheduler.get_jobs()}
    assert sched.core_job_ids == set(CANDIDATE_CORE_JOB_IDS) & registered
    # 핵심 매매 잡은 실제 등록되어 포함되어야
    assert "stock_screening" in sched.core_job_ids
    assert "execute_buy" in sched.core_job_ids
    # 비core 잡은 미포함
    assert "daily_report" not in sched.core_job_ids


# ---------- alert_job_missed 예외 소거 (TradingSystem 메서드 단위) ----------

def test_alert_job_missed_swallows_send_failure():
    """notifier.send_message가 던져도 alert_job_missed는 raise 안 함."""
    from main import TradingSystem
    sys_obj = TradingSystem.__new__(TradingSystem)  # __init__ 우회
    sys_obj.notifier = MagicMock()
    sys_obj.notifier.send_message.side_effect = RuntimeError("telegram down")

    # 예외 전파 없어야 함
    asyncio.get_event_loop().run_until_complete(
        sys_obj.alert_job_missed("stock_screening", datetime(2026, 6, 5, 9, 5, tzinfo=KST))
    )
    sys_obj.notifier.send_message.assert_called_once()


# ---------- B2: _run_startup_recovery_check ----------

def _make_system_for_b2():
    from main import TradingSystem
    s = TradingSystem.__new__(TradingSystem)
    s.notifier = MagicMock()
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_b2_alerts_during_market_hours():
    s = _make_system_for_b2()
    fake_now = datetime(2026, 6, 5, 10, 0, tzinfo=KST)  # 장중
    with patch("main.is_trading_day", return_value=True), \
         patch("main.now_kst", return_value=fake_now), \
         patch("main.settings") as ms, \
         patch("os.path.exists", return_value=False):
        ms.JOB_RECOVERY_ALERT_ENABLED = True
        _run(s._run_startup_recovery_check())
    s.notifier.send_message.assert_called_once()


def test_b2_silent_after_market_close():
    s = _make_system_for_b2()
    fake_now = datetime(2026, 6, 5, 19, 0, tzinfo=KST)  # 장 마감 후 (incident 재부팅 시각대)
    with patch("main.is_trading_day", return_value=True), \
         patch("main.now_kst", return_value=fake_now), \
         patch("main.settings") as ms:
        ms.JOB_RECOVERY_ALERT_ENABLED = True
        _run(s._run_startup_recovery_check())
    s.notifier.send_message.assert_not_called()


def test_b2_silent_on_holiday():
    s = _make_system_for_b2()
    fake_now = datetime(2026, 6, 5, 10, 0, tzinfo=KST)
    with patch("main.is_trading_day", return_value=False), \
         patch("main.now_kst", return_value=fake_now), \
         patch("main.settings") as ms:
        ms.JOB_RECOVERY_ALERT_ENABLED = True
        _run(s._run_startup_recovery_check())
    s.notifier.send_message.assert_not_called()


def test_b2_disabled_toggle():
    s = _make_system_for_b2()
    fake_now = datetime(2026, 6, 5, 10, 0, tzinfo=KST)
    with patch("main.is_trading_day", return_value=True), \
         patch("main.now_kst", return_value=fake_now), \
         patch("main.settings") as ms:
        ms.JOB_RECOVERY_ALERT_ENABLED = False
        _run(s._run_startup_recovery_check())
    s.notifier.send_message.assert_not_called()


def test_b2_cooldown_blocks_repeat():
    s = _make_system_for_b2()
    fake_now = datetime(2026, 6, 5, 10, 0, tzinfo=KST)
    with patch("main.is_trading_day", return_value=True), \
         patch("main.now_kst", return_value=fake_now), \
         patch("main.settings") as ms, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getmtime", return_value=fake_now.timestamp() - 600):  # 10분 전 발송 → cooldown 내
        ms.JOB_RECOVERY_ALERT_ENABLED = True
        _run(s._run_startup_recovery_check())
    s.notifier.send_message.assert_not_called()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
