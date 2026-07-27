"""test_pyramid_in_telegram_alert.py — v17 불타기(2차 진입) 텔레그램 알림 배선 회귀 테스트.

배경 (2026-07-13):
    ISC 종목이 v17 2차 진입(불타기) 체결됐으나 Telegram 알림이 미발송됨.
    근본 원인: PortfolioMonitorV2 인스턴스에 notifier가 주입되지 않아
    _check_and_execute_pyramid_in() step 16의 `hasattr(self, "notifier")` 가드가
    항상 False → 무음 skip.

검증 대상:
    1. notifier 주입 시 2차 진입 성공 → send_message 정확히 1회 호출 (불타기 포맷)
    2. notifier=None → DB/메모리 상태(tranche_count=2) 무손상, 예외 없음
    3. notifier.send_message가 예외를 던져도 상태 무손상, 예외 전파 안 됨

RED 근거: 미수정 코드에서 PortfolioMonitorV2(use_mock=True, notifier=...) 는
    `TypeError: unexpected keyword argument 'notifier'` 로 실패한다.

파일 직접 실행: python3 tests/test_pyramid_in_telegram_alert.py
"""

import sys
import asyncio
from pathlib import Path
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from config import settings
from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2
from modules.trading_engine.buy_lock import buy_lock
from modules.market_guard import MarketStatus


# ----- 테스트 더블 -----

class FakeNotifier:
    """send_message 호출을 기록하는 텔레그램 노티파이어 페이크."""

    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)


class RaisingNotifier:
    """send_message가 항상 예외를 던지는 페이크 (전송 실패 시나리오)."""

    def __init__(self):
        self.calls = 0

    def send_message(self, text):
        self.calls += 1
        raise RuntimeError("텔레그램 전송 실패(테스트)")


class FakeDB:
    """Database 스텁 — 실제 trading.db 접근 없이 2차 진입 UPDATE/save_trade 흡수."""

    update_calls = []
    saved_trades = []

    def __init__(self):
        pass

    def connect(self):
        pass

    def update_portfolio_second_tranche(self, **kwargs):
        FakeDB.update_calls.append(kwargs)
        return 1  # rowcount=1 (holding row 존재)

    def save_trade(self, data):
        FakeDB.saved_trades.append(data)
        return 1

    def close(self):
        pass


class FakeMarketGuard:
    """MarketGuard 스텁 — 항상 NORMAL (2차 진입 허용)."""

    def check(self):
        return (MarketStatus.NORMAL, {})


def _fake_execute_portfolio(orders, save_to_db, wait_for_fill):
    """trading_engine.execute_portfolio 스텁 — 요청 수량 전량 체결."""
    o = orders[0]
    return {
        "orders": [
            {
                "success": True,
                "stock_code": o["stock_code"],
                "quantity": o["quantity"],
                "filled_price": o["price"],
            }
        ],
        "failed_count": 0,
    }


# ----- 공용 셋업 -----

_CODE = "900001"


def _build_monitor_with_position(notifier):
    """+6% 도달 + 2차 대기 상태의 포지션을 가진 모니터 생성."""
    monitor = PortfolioMonitorV2(use_mock=True, notifier=notifier)
    monitor.add_position(
        stock_code=_CODE,
        stock_name="테스트불타기",
        shares=10,
        buy_price=10000,
        stop_loss_price=9300,
        theme="테스트",
        first_buy_price=10000,
        avg_buy_price=10000,
        tranche_count=1,
        second_tranche_executed=False,
        second_tranche_pending=True,  # 2차 대기
    )
    pos = monitor.positions[_CODE]
    pos.current_price = 10600  # +6% > PYRAMID_TRIGGER_PCT(5%)
    return monitor, pos


def _run_pyramid(monitor, pos):
    """의존성을 전부 페이크로 대체한 상태에서 2차 진입 실행."""
    FakeDB.update_calls = []
    FakeDB.saved_trades = []
    # 이전 테스트 잔여 락 방어
    try:
        buy_lock.release(_CODE)
    except Exception:
        pass

    # 실발주/실API 차단: trading_engine 메서드 직접 대체
    monitor.trading_engine.execute_portfolio = _fake_execute_portfolio
    monitor.trading_engine.get_balance = lambda: {}  # 잔고 API 미호출

    # 15:15 종가베팅 시간 가드 회피 (10:00 고정)
    fixed_now = config.now_kst().replace(hour=10, minute=0, second=0, microsecond=0)

    with ExitStack() as stack:
        stack.enter_context(mock.patch(
            "modules.trading_engine.portfolio_monitor_v2.now_kst",
            return_value=fixed_now,
        ))
        stack.enter_context(mock.patch(
            "modules.trading_engine.portfolio_monitor_v2.Database", FakeDB,
        ))
        stack.enter_context(mock.patch(
            "modules.market_guard.MarketGuard", FakeMarketGuard,
        ))
        stack.enter_context(mock.patch.object(
            settings, "TRANCHE_ENTRY_ENABLED", True, create=True,
        ))
        stack.enter_context(mock.patch.object(
            settings, "DRY_RUN_PYRAMID", False, create=True,
        ))
        stack.enter_context(mock.patch.object(
            settings, "PYRAMID_TRIGGER_PCT", 0.05, create=True,
        ))
        return asyncio.run(monitor._check_and_execute_pyramid_in(pos))


# ----- 테스트 -----

def test_pyramid_in_sends_exactly_one_telegram_message():
    """notifier 주입 시 2차 진입 성공 → 불타기 메시지 정확히 1회."""
    notifier = FakeNotifier()
    monitor, pos = _build_monitor_with_position(notifier)

    result = _run_pyramid(monitor, pos)

    assert result is True, "2차 진입 발주 시도는 True 반환해야 함"
    assert len(notifier.messages) == 1, (
        f"불타기 알림은 정확히 1회여야 함: 실제 {len(notifier.messages)}회"
    )
    msg = notifier.messages[0]
    assert "불타기" in msg, f"불타기 포맷 아님: {msg!r}"
    assert _CODE in msg, f"종목코드 누락: {msg!r}"
    # 메모리 상태 갱신 확인
    # 2차 체결 수량 = int(target(100,000) // current_price(10,600)) = 9주 → 총 19주
    assert pos.tranche_count == 2
    assert pos.second_tranche_executed is True
    assert pos.shares == 19
    assert len(FakeDB.update_calls) == 1, "DB 2차 진입 UPDATE 1회여야 함"
    print("  [PASS] pyramid_in_sends_exactly_one_telegram_message")


def test_pyramid_in_no_notifier_state_intact():
    """notifier=None → 알림 없이도 DB/메모리 상태 정상, 예외 없음."""
    monitor, pos = _build_monitor_with_position(notifier=None)

    result = _run_pyramid(monitor, pos)

    assert result is True
    assert pos.tranche_count == 2, "notifier 없어도 2차 진입 상태 갱신돼야 함"
    assert pos.second_tranche_executed is True
    assert pos.shares == 19
    assert len(FakeDB.update_calls) == 1, "DB UPDATE는 notifier와 무관하게 1회"
    print("  [PASS] pyramid_in_no_notifier_state_intact")


def test_pyramid_in_notifier_raises_state_intact():
    """notifier.send_message 예외 → 예외 소거, 2차 진입 상태 무손상."""
    notifier = RaisingNotifier()
    monitor, pos = _build_monitor_with_position(notifier)

    result = _run_pyramid(monitor, pos)  # 예외가 전파되면 여기서 실패

    assert result is True
    assert notifier.calls == 1, "send_message는 1회 시도돼야 함"
    assert pos.tranche_count == 2, "전송 실패해도 2차 진입 상태 정상"
    assert pos.second_tranche_executed is True
    assert pos.shares == 19
    assert len(FakeDB.update_calls) == 1
    print("  [PASS] pyramid_in_notifier_raises_state_intact")


def test_default_notifier_is_none():
    """notifier 미지정 시 self.notifier는 None (기본값 안전)."""
    monitor = PortfolioMonitorV2(use_mock=True)
    assert monitor.notifier is None
    print("  [PASS] default_notifier_is_none")


def main():
    tests = [
        test_default_notifier_is_none,
        test_pyramid_in_sends_exactly_one_telegram_message,
        test_pyramid_in_no_notifier_state_intact,
        test_pyramid_in_notifier_raises_state_intact,
    ]
    print(f"\n{'='*60}\nv17 불타기 텔레그램 알림 배선 테스트\n{'='*60}")
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
    print(f"{'='*60}")
    print(f"결과: {len(tests) - failed}/{len(tests)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
