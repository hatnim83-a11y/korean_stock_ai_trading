"""
test_makeup_reselection.py - is_makeup_reselection_day() 단위 테스트

config.is_makeup_reselection_day(today, target_weekday=1)이 다음을 정확히 판정하는지 검증:
- 화요일 휴장 이후 첫 영업일이면 True + missed_date 반환
- 그 외에는 False, None 반환

mock 전략: monkeypatch로 config.is_trading_day를 가짜 함수로 교체.
실제 holidays.KR에 의존하지 않고, 시나리오마다 비영업일 셋만 정의해서 결정적으로 검증.

파일 직접 실행: python tests/test_makeup_reselection.py
또는 pytest: pytest tests/test_makeup_reselection.py -v
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config


def _make_fake_is_trading_day(non_business_days: set[date]):
    """
    가짜 is_trading_day 생성기.

    non_business_days에 포함된 날짜 또는 주말이면 False, 그 외엔 True.
    """
    def fake(check_date=None):
        if check_date is None:
            check_date = date.today()
        if check_date.weekday() >= 5:
            return False
        return check_date not in non_business_days
    return fake


def test_makeup_day_after_childrens_day(monkeypatch):
    """시나리오 ① 어린이날(05/05 화) 휴장 다음날 05/06(수) → True"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day({date(2026, 5, 5)}))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 6))
    assert is_makeup is True
    assert missed == date(2026, 5, 5)
    print("  [PASS] makeup_day_after_childrens_day")


def test_regular_tuesday_returns_false(monkeypatch):
    """시나리오 ② 정규 화요일(05/12) → False (보정 불필요)"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day(set()))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 12))
    assert is_makeup is False
    assert missed is None
    print("  [PASS] regular_tuesday_returns_false")


def test_makeup_chance_missed(monkeypatch):
    """시나리오 ③ 화 휴장 후 수요일은 영업일(보정 가능했음). 목요일에 호출 → False

    의도된 동작: 보정 기회는 '직전 화요일 이후 첫 영업일' 1회만. 그 다음 영업일엔 발화 X.
    """
    # 05/05(화)만 휴장. 05/06(수)은 영업일.
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day({date(2026, 5, 5)}))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 7))  # 목
    assert is_makeup is False
    assert missed is None
    print("  [PASS] makeup_chance_missed")


def test_regular_wednesday_returns_false(monkeypatch):
    """시나리오 ④ 평범한 수요일(직전 화 영업일). 04/29(수) 호출 → False"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day(set()))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 4, 29))
    assert is_makeup is False
    assert missed is None
    print("  [PASS] regular_wednesday_returns_false")


def test_toggle_off_always_false(monkeypatch):
    """시나리오 ⑤ 토글 OFF 시 보정 케이스에도 False 반환"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day({date(2026, 5, 5)}))
    monkeypatch.setattr(config.settings, "THEME_MAKEUP_RESELECTION_ENABLED", False)
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 6))
    assert is_makeup is False
    assert missed is None
    print("  [PASS] toggle_off_always_false")


def test_consecutive_holidays_thursday_makeup(monkeypatch):
    """엣지 ⑥ 화+수 연속 휴장 → 목요일에 보정 발화 (직전 화요일 missed 반환)"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day({date(2026, 5, 5), date(2026, 5, 6)}))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 7))
    assert is_makeup is True
    assert missed == date(2026, 5, 5)
    print("  [PASS] consecutive_holidays_thursday_makeup")


def test_today_is_holiday_returns_false(monkeypatch):
    """엣지 ⑦ 오늘이 비영업일이면 (영업일 가드 진입 못 함) → False"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day({date(2026, 5, 5), date(2026, 5, 6)}))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 6))
    assert is_makeup is False
    assert missed is None
    print("  [PASS] today_is_holiday_returns_false")


def test_monday_with_no_holidays(monkeypatch):
    """엣지 ⑧ 월요일(직전 화요일 = 6일 전) — 정상 영업일이면 False"""
    monkeypatch.setattr(config, "is_trading_day",
                        _make_fake_is_trading_day(set()))
    is_makeup, missed = config.is_makeup_reselection_day(date(2026, 5, 11))  # 월
    assert is_makeup is False
    assert missed is None
    print("  [PASS] monday_with_no_holidays")


def main():
    """파일 직접 실행 시 — pytest 없이 monkeypatch 흉내."""
    import contextlib

    class FakeMonkeypatch:
        def __init__(self):
            self._stack = []

        def setattr(self, target, name, value):
            if isinstance(target, str):
                # config.is_trading_day 형식 미지원 — config 모듈로 대체
                raise NotImplementedError("문자열 path는 미지원, config 모듈 직접 전달")
            old = getattr(target, name)
            self._stack.append((target, name, old))
            setattr(target, name, value)

        def undo(self):
            while self._stack:
                target, name, old = self._stack.pop()
                setattr(target, name, old)

    print("🧪 is_makeup_reselection_day 단위 테스트 시작")
    print("=" * 60)
    tests = [
        test_makeup_day_after_childrens_day,
        test_regular_tuesday_returns_false,
        test_makeup_chance_missed,
        test_regular_wednesday_returns_false,
        test_toggle_off_always_false,
        test_consecutive_holidays_thursday_makeup,
        test_today_is_holiday_returns_false,
        test_monday_with_no_holidays,
    ]
    for fn in tests:
        mp = FakeMonkeypatch()
        try:
            fn(mp)
        finally:
            mp.undo()
    print("=" * 60)
    print(f"✅ 전체 테스트 통과 ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
