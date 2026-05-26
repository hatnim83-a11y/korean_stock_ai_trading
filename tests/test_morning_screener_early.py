"""test_morning_screener_early.py - A3안 MorningScreener 필터 토글 검증

EARLY_BUY_ENABLED=True 시:
- 거래량 필터 OFF (None) — EARLY_BUY_DISABLE_VOLUME_FILTER default=True
- 체결강도 필터 보존 — EARLY_BUY_DISABLE_STRENGTH_FILTER default=False
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_legacy_filters_on():
    """default(EARLY_BUY_ENABLED=False) → 거래량/체결강도 둘 다 ON."""
    from config import settings
    assert settings.EARLY_BUY_ENABLED is False

    from modules.morning_filter import MorningScreener
    s = MorningScreener()
    assert s.volume_filter is not None, "legacy: 거래량 필터 ON 필수"
    assert s.strength_filter is not None, "legacy: 체결강도 필터 ON 필수"
    assert s.enable_volume_filter is True
    assert s.enable_strength_filter is True
    print("  [PASS] legacy_filters_on — 둘 다 ON")


def test_early_volume_off_strength_on(monkeypatch):
    """EARLY_BUY_ENABLED=True → 거래량 OFF, 체결강도 보존."""
    from config import settings
    monkeypatch.setattr(settings, 'EARLY_BUY_ENABLED', True)
    monkeypatch.setattr(settings, 'EARLY_BUY_DISABLE_VOLUME_FILTER', True)
    monkeypatch.setattr(settings, 'EARLY_BUY_DISABLE_STRENGTH_FILTER', False)

    from modules.morning_filter import MorningScreener
    s = MorningScreener()
    assert s.volume_filter is None, "EARLY: 거래량 필터 OFF (None)"
    assert s.strength_filter is not None, "EARLY: 체결강도 보존"
    assert s.enable_volume_filter is False
    assert s.enable_strength_filter is True
    print("  [PASS] early_volume_off_strength_on")


def test_early_both_off(monkeypatch):
    """EARLY + DISABLE_STRENGTH_FILTER=True → 둘 다 OFF (수동 토글)."""
    from config import settings
    monkeypatch.setattr(settings, 'EARLY_BUY_ENABLED', True)
    monkeypatch.setattr(settings, 'EARLY_BUY_DISABLE_VOLUME_FILTER', True)
    monkeypatch.setattr(settings, 'EARLY_BUY_DISABLE_STRENGTH_FILTER', True)

    from modules.morning_filter import MorningScreener
    s = MorningScreener()
    assert s.volume_filter is None
    assert s.strength_filter is None
    print("  [PASS] early_both_off — 양쪽 OFF 수동 토글")


def test_explicit_args_override_toggle(monkeypatch):
    """명시 인자가 토글보다 우선 — 호환성 보장."""
    from config import settings
    monkeypatch.setattr(settings, 'EARLY_BUY_ENABLED', True)

    from modules.morning_filter import MorningScreener
    s = MorningScreener(enable_volume_filter=True, enable_strength_filter=False)
    assert s.volume_filter is not None, "명시 True가 EARLY 토글보다 우선"
    assert s.strength_filter is None, "명시 False가 우선"
    print("  [PASS] explicit_args_override_toggle")


def _run_all():
    print("== test_morning_screener_early.py ==")
    test_legacy_filters_on()
    print("  (monkeypatch 테스트는 pytest 필요)")
    print("OK\n")


if __name__ == '__main__':
    _run_all()
