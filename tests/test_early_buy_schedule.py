"""test_early_buy_schedule.py - A3안 스케줄 시간 검증 (legacy / EARLY 양쪽)

파일 직접 실행: python tests/test_early_buy_schedule.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_minute_for_job(scheduler, job_id):
    """잡 ID로 등록된 cron minute 값 추출."""
    for job in scheduler.get_jobs():
        if job.id == job_id:
            # CronTrigger.fields[6]은 minute (apscheduler 4.x 기준 위치 다를 수 있음)
            for f in job.trigger.fields:
                if f.name == 'minute':
                    return int(str(f).strip("'"))
    return None


def test_legacy_schedule():
    """EARLY_BUY_ENABLED=False (default) → 09:00/05/10/15/25/26 legacy."""
    from config import settings
    # monkeypatch 대신 직접 확인 (default=False)
    assert settings.EARLY_BUY_ENABLED is False, "default가 False여야 안전"

    from scheduler import TradingScheduler
    s = TradingScheduler()
    s.setup_schedules()

    expected = {
        'stock_screening': 5,
        'execute_buy': 25,
        'monitoring_start': 26,
        'midweek_sell_loss': 10,
        'hold_period_sell': 15,
    }
    for jid, exp_min in expected.items():
        actual = _get_minute_for_job(s.scheduler, jid)
        assert actual == exp_min, f"legacy {jid}: 기대 {exp_min}, 실제 {actual}"
    print("  [PASS] legacy_schedule — 5개 잡 시간 정확")


def test_early_schedule(monkeypatch):
    """EARLY_BUY_ENABLED=True → 09:00/01/02/02/05/06."""
    from config import settings
    monkeypatch.setattr(settings, 'EARLY_BUY_ENABLED', True)
    assert settings.EARLY_BUY_ENABLED is True

    from scheduler import TradingScheduler
    s = TradingScheduler()
    s.setup_schedules()

    expected = {
        'stock_screening': 2,
        'execute_buy': 5,
        'monitoring_start': 6,
        'midweek_sell_loss': 1,
        'hold_period_sell': 2,
    }
    for jid, exp_min in expected.items():
        actual = _get_minute_for_job(s.scheduler, jid)
        assert actual == exp_min, f"EARLY {jid}: 기대 {exp_min}, 실제 {actual}"
    print("  [PASS] early_schedule — 5개 잡 시간 09:02/05/06/01/02")


def test_unchanged_jobs():
    """09:00 monitoring_start_early / midweek_sell_profit 은 양쪽 모드 동일."""
    from scheduler import TradingScheduler
    s = TradingScheduler()
    s.setup_schedules()

    for jid in ['midweek_sell_profit', 'monitoring_start_early', 'monitoring_stop']:
        for job in s.scheduler.get_jobs():
            if job.id == jid:
                break
        else:
            if jid == 'monitoring_start_early':
                # PARTIAL_PROFIT_EARLY_MONITORING_ENABLED=False면 등록 안 됨
                continue
            assert False, f"{jid} 잡 미등록"
    print("  [PASS] unchanged_jobs — 09:00 잡들 정상 등록")


def _run_all():
    """pytest 없이 직접 실행할 때 사용."""
    import pytest
    print("== test_early_buy_schedule.py ==")
    test_legacy_schedule()
    # monkeypatch 없는 환경에서는 test_early_schedule를 직접 실행 어려움 → pytest 권장
    test_unchanged_jobs()
    print("  (test_early_schedule은 pytest 필요)")
    print("OK\n")


if __name__ == '__main__':
    _run_all()
