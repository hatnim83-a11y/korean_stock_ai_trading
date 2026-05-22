"""단위 테스트 — 종가베팅 텔레그램 메시지 형식 통일 (5/22 작업).

PLAN.md / CHECKLIST.md 의 EN-1~5, EX-1~5, RB-1~3 시나리오 검증.

실행:
    venv/bin/python scripts/test_closing_bet_notifier_format.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


# ===== Mock TelegramReviewBot =====


class _MockNotifier:
    """notifier.send_message 호출 시 본문 캡처."""

    def __init__(self):
        self.messages: list[str] = []
        self._enabled = True

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        self.messages.append(text)
        return True


def _make_mock_bot():
    """is_enabled=True인 mock TelegramReviewBot."""
    from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot

    mock_notifier = _MockNotifier()
    bot = TelegramReviewBot(notifier=mock_notifier)
    return bot, mock_notifier


# ===== Fixtures =====


def _make_fill_status(executed_price: float, executed_shares: int):
    fs = MagicMock()
    fs.executed_price = executed_price
    fs.executed_shares = executed_shares
    return fs


def _make_candidate_order(
    candidate_id: int = 1,
    ticker: str = "005930",
    name: str = "삼성전자",
    target_price: int = 75000,
    quantity: int = 10,
    submitted: bool = True,
    order_id: str = "ORD123",
    fill_status=None,
    rejection_reason=None,
):
    from closing_bet_system.execution.entry_executor import CandidateOrder

    return CandidateOrder(
        candidate_id=candidate_id,
        ticker=ticker,
        name=name,
        target_price=target_price,
        quantity=quantity,
        submitted=submitted,
        order_id=order_id,
        fill_status=fill_status,
        rejection_reason=rejection_reason,
    )


def _make_phase1_result(orders: list, dry_run: bool = True):
    from closing_bet_system.execution.entry_executor import Phase1Result

    return Phase1Result(
        trade_date="2026-05-22",
        total_candidates=len(orders) + 3,
        submitted=sum(1 for o in orders if o.submitted),
        filled=sum(
            1
            for o in orders
            if o.fill_status and getattr(o.fill_status, "executed_shares", 0) > 0
        ),
        skipped_price_cap=1,
        fund_guard_rejected=0,
        market_guard_status="CAUTION",
        orders=orders,
        errors=[],
    )


# ===== EN-1: phase1 dry_run + 2종목 → 종목 메시지 2건 + 요약 1건 =====


def test_EN_1_phase1_dry_run_two_tickers():
    from closing_bet_system.notification.entry_notifier import EntryNotifier

    bot, mock = _make_mock_bot()
    notifier = EntryNotifier(telegram_bot=bot)

    orders = [
        _make_candidate_order(candidate_id=1, ticker="005930", name="삼성전자"),
        _make_candidate_order(
            candidate_id=2, ticker="000660", name="SK하이닉스", target_price=180000
        ),
    ]
    result = _make_phase1_result(orders, dry_run=True)
    sent = notifier.send_phase1_result(result, dry_run=True)

    assert sent is True, "send 성공해야 함"
    assert len(mock.messages) == 3, f"종목 2 + 요약 1 = 3건이어야 함: got {len(mock.messages)}"
    # 종목별 메시지에 ticker/name 포함
    assert "005930" in mock.messages[0]
    assert "삼성전자" in mock.messages[0]
    assert "DRY-RUN" in mock.messages[0]
    assert "000660" in mock.messages[1]
    # 요약 메시지에 통계 포함
    assert "1차 진입 종료" in mock.messages[2]
    assert "MarketGuard" in mock.messages[2]


# ===== EN-2: phase1 실발주 + 1종목 체결 =====


def test_EN_2_phase1_real_fill():
    from closing_bet_system.notification.entry_notifier import EntryNotifier

    bot, mock = _make_mock_bot()
    notifier = EntryNotifier(telegram_bot=bot)

    fs = _make_fill_status(executed_price=74950, executed_shares=10)
    orders = [_make_candidate_order(fill_status=fs)]
    result = _make_phase1_result(orders, dry_run=False)
    notifier.send_phase1_result(result, dry_run=False)

    assert len(mock.messages) == 2
    msg = mock.messages[0]
    assert "실발주" in msg
    assert "체결 완료" in msg
    assert "74,950원" in msg  # executed_price 표시
    assert "ORD123" in msg


# ===== EN-3: phase1 빈 orders → 요약만 발송 =====


def test_EN_3_phase1_empty_orders():
    from closing_bet_system.notification.entry_notifier import EntryNotifier

    bot, mock = _make_mock_bot()
    notifier = EntryNotifier(telegram_bot=bot)

    result = _make_phase1_result([], dry_run=True)
    notifier.send_phase1_result(result, dry_run=True)

    assert len(mock.messages) == 1, "요약 1건만"
    assert "1차 진입 종료" in mock.messages[0]


# ===== EN-4: phase1 에러 표시 =====


def test_EN_4_phase1_with_errors():
    from closing_bet_system.execution.entry_executor import Phase1Result
    from closing_bet_system.notification.entry_notifier import EntryNotifier

    bot, mock = _make_mock_bot()
    notifier = EntryNotifier(telegram_bot=bot)

    result = Phase1Result(
        trade_date="2026-05-22",
        orders=[],
        errors=["KIS API 500 — ticker 005930", "submit_fail 000660"],
    )
    notifier.send_phase1_result(result, dry_run=False)

    assert any("에러 2건" in m for m in mock.messages)


# ===== EN-5: phase2 보류/취소 분류 =====


def test_EN_5_phase2_skip_and_cancel():
    from closing_bet_system.execution.entry_executor import Phase2Result
    from closing_bet_system.notification.entry_notifier import EntryNotifier

    bot, mock = _make_mock_bot()
    notifier = EntryNotifier(telegram_bot=bot)

    result = Phase2Result(
        trade_date="2026-05-22",
        eligible=3,
        submitted=2,
        filled=1,
        skipped_estimated_price=1,
        cancelled_ask_bid=1,
        orders=[],
    )
    notifier.send_phase2_result(result, dry_run=True)

    assert any("보류" in m and "취소" in m for m in mock.messages)


# ===== EX-1: emergency_stop 손실 표시 =====


def _make_candidate_logger_mock(buy_price: float = 75000):
    """get_candidate가 buy_price를 반환하도록 mock."""
    cl = MagicMock()
    cl.get_candidate.return_value = {
        "candidate_id": 1,
        "entry_phase1_executed_price": buy_price,
        "entry_phase1_executed_shares": 10,
        "entry_phase2_executed_price": None,
        "entry_phase2_executed_shares": None,
        "entry_price": buy_price,
    }
    return cl


def _make_candidate_exit(
    candidate_id: int = 1,
    ticker: str = "005930",
    name: str = "삼성전자",
    action_value: str = "EMERGENCY_STOP",
    target_shares: int = 10,
    submitted: bool = True,
    order_id: str = "SELL123",
    executed_price: float = 73000,
    executed_shares: int = 10,
    rejection_reason=None,
    gap_rate: float = -0.015,
):
    from closing_bet_system.execution.exit_executor import CandidateExit

    action = MagicMock()
    action.value = action_value
    fs = (
        _make_fill_status(executed_price, executed_shares)
        if executed_price and executed_shares
        else None
    )
    return CandidateExit(
        candidate_id=candidate_id,
        ticker=ticker,
        name=name,
        action=action,
        target_shares=target_shares,
        submitted=submitted,
        order_id=order_id,
        fill_status=fs,
        rejection_reason=rejection_reason,
        gap_rate=gap_rate,
    )


def _make_exit_result(orders: list, cycle: str = "emergency_stop"):
    from closing_bet_system.execution.exit_executor import ExitResult

    return ExitResult(
        trade_date="2026-05-25",
        cycle=cycle,
        total_targets=len(orders),
        filled=sum(
            1
            for o in orders
            if o.fill_status and getattr(o.fill_status, "executed_shares", 0) > 0
        ),
        unfilled=0,
        cancelled=0,
        orders=orders,
        action_counts={"EMERGENCY_STOP": 1},
    )


def test_EX_1_emergency_stop_loss():
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    cl = _make_candidate_logger_mock(buy_price=75000)
    notifier = ExitNotifier(telegram_bot=bot, candidate_logger=cl)

    orders = [_make_candidate_exit(executed_price=73000)]
    result = _make_exit_result(orders, cycle="emergency_stop")
    notifier.send_emergency_stop_result(result, dry_run=False)

    assert len(mock.messages) == 2
    msg = mock.messages[0]
    assert "🔴" in msg, "손실 시 🔴 컬러"
    assert "75,000원" in msg
    assert "73,000원" in msg
    assert "-20,000원" in msg  # (73000-75000)*10
    assert "-2.67%" in msg or "-2.66%" in msg  # 손실률
    summary = mock.messages[1]
    assert "EMERGENCY STOP" in summary
    assert "누적 손익" in summary


# ===== EX-2: morning_exit 수익 표시 =====


def test_EX_2_morning_exit_profit():
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    cl = _make_candidate_logger_mock(buy_price=75000)
    notifier = ExitNotifier(telegram_bot=bot, candidate_logger=cl)

    orders = [
        _make_candidate_exit(
            executed_price=78000, action_value="GAP_UP_HIGH", gap_rate=0.04
        )
    ]
    result = _make_exit_result(orders, cycle="morning_exit")
    notifier.send_morning_exit_result(result, dry_run=False)

    msg = mock.messages[0]
    assert "🟢" in msg
    assert "+30,000원" in msg
    assert "+4.00%" in msg


# ===== EX-3: force_close 미체결 취소 =====


def test_EX_3_force_close_cancelled():
    from closing_bet_system.execution.exit_executor import ExitResult
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    notifier = ExitNotifier(telegram_bot=bot)

    result = ExitResult(
        trade_date="2026-05-25",
        cycle="force_close",
        total_targets=2,
        filled=1,
        unfilled=1,
        cancelled=1,
        orders=[],
    )
    notifier.send_force_close_result(result, dry_run=False)

    summary = mock.messages[-1]
    assert "FORCE CLOSE" in summary
    assert "취소 처리: 1건" in summary


# ===== EX-4: 원가 조회 실패 폴백 =====


def test_EX_4_buy_price_lookup_fail():
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    cl = MagicMock()
    cl.get_candidate.return_value = None  # 조회 실패
    notifier = ExitNotifier(telegram_bot=bot, candidate_logger=cl)

    orders = [_make_candidate_exit(executed_price=73000)]
    result = _make_exit_result(orders)
    notifier.send_emergency_stop_result(result, dry_run=False)

    msg = mock.messages[0]
    assert "🟡" in msg, "조회 실패 시 🟡"
    assert "원가 조회 실패" in msg


# ===== EX-5: rejection_reason 표시 =====


def test_EX_5_rejection_reason():
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    cl = _make_candidate_logger_mock()
    notifier = ExitNotifier(telegram_bot=bot, candidate_logger=cl)

    orders = [
        _make_candidate_exit(
            executed_price=None,
            executed_shares=None,
            submitted=False,
            rejection_reason="sell_lock_busy",
        )
    ]
    result = _make_exit_result(orders)
    notifier.send_emergency_stop_result(result, dry_run=False)

    msg = mock.messages[0]
    assert "매도 거부" in msg
    # markdown escape: sell_lock_busy → sell\_lock\_busy
    assert "sell" in msg and "lock" in msg and "busy" in msg


# ===== EX-6: DRY-RUN 매도 → 손익 미산정 (실 발주 없음) =====


def test_EX_6_dry_run_no_pnl():
    from closing_bet_system.execution.exit_executor import CandidateExit
    from closing_bet_system.notification.exit_notifier import ExitNotifier

    bot, mock = _make_mock_bot()
    cl = _make_candidate_logger_mock(buy_price=75000)
    notifier = ExitNotifier(telegram_bot=bot, candidate_logger=cl)

    # dry_run에선 fill_status=None이 정상 (ExitExecutor dry_run 분기)
    action = MagicMock()
    action.value = "EMERGENCY_STOP"
    order = CandidateExit(
        candidate_id=1,
        ticker="005930",
        name="삼성전자",
        action=action,
        target_shares=10,
        submitted=True,
        order_id="DRY-RUN",
        fill_status=None,  # dry_run 분기에서 박제 없음
        gap_rate=-0.015,
    )
    result = _make_exit_result([order], cycle="emergency_stop")
    notifier.send_emergency_stop_result(result, dry_run=True)

    msg = mock.messages[0]
    assert "🧪" in msg, "DRY-RUN 컬러 🧪"
    assert "DRY-RUN" in msg
    assert "손익 미산정" in msg
    assert "실 발주 없음" in msg
    # PnL 계산 라인이 없어야 함
    assert "수익금:" not in msg


# ===== RB-1: daily_summary 기본 (closed_positions=None) =====


def test_RB_1_daily_summary_no_closed():
    bot, mock = _make_mock_bot()

    bot.send_daily_summary(
        trade_date="2026-05-22",
        status_counts={
            "recommended": 17,
            "entered": 0,
            "rejected_filter": 2,
            "rejected_manual": 0,
        },
        recommended_count=198,
    )

    assert len(mock.messages) == 1
    msg = mock.messages[0]
    assert "Recommended: 17건" in msg
    assert "Entered: 0건" in msg
    assert "198건" in msg
    assert "오늘 청산 종목 PnL" not in msg


# ===== RB-2: daily_summary + 청산 포지션 PnL =====


def test_RB_2_daily_summary_with_closed():
    bot, mock = _make_mock_bot()

    closed = [
        {
            "ticker": "010170",
            "name": "대한광통신",
            "profit_abs": 1500.0,
            "profit_rate": 6.4,
        },
        {
            "ticker": "027360",
            "name": "아주IB투자",
            "profit_abs": -800.0,
            "profit_rate": -2.3,
        },
    ]
    bot.send_daily_summary(
        trade_date="2026-05-25",
        status_counts={"recommended": 20, "entered": 2, "rejected_filter": 1, "rejected_manual": 0},
        recommended_count=200,
        closed_positions=closed,
    )

    msg = mock.messages[0]
    assert "오늘 청산 종목 PnL" in msg
    assert "+1,500원" in msg
    assert "-800원" in msg
    assert "+6.40%" in msg
    assert "-2.30%" in msg
    assert "오늘 총 손익" in msg
    assert "+700원" in msg


# ===== RB-3: system_start / stop =====


def test_RB_3_system_start_stop():
    bot, mock = _make_mock_bot()

    bot.send_system_start(
        info={
            "entry_dry_run": False,
            "exit_dry_run": False,
            "capital_ratio": 0.10,
        }
    )
    bot.send_system_stop(reason="manual restart")

    assert len(mock.messages) == 2
    assert "시스템 시작" in mock.messages[0]
    assert "실발주" in mock.messages[0]
    assert "10.0%" in mock.messages[0]
    assert "시스템 중지" in mock.messages[1]
    assert "manual restart" in mock.messages[1]


# ===== 실행 =====


if __name__ == "__main__":
    tests = [
        ("EN-1", test_EN_1_phase1_dry_run_two_tickers),
        ("EN-2", test_EN_2_phase1_real_fill),
        ("EN-3", test_EN_3_phase1_empty_orders),
        ("EN-4", test_EN_4_phase1_with_errors),
        ("EN-5", test_EN_5_phase2_skip_and_cancel),
        ("EX-1", test_EX_1_emergency_stop_loss),
        ("EX-2", test_EX_2_morning_exit_profit),
        ("EX-3", test_EX_3_force_close_cancelled),
        ("EX-4", test_EX_4_buy_price_lookup_fail),
        ("EX-5", test_EX_5_rejection_reason),
        ("EX-6", test_EX_6_dry_run_no_pnl),
        ("RB-1", test_RB_1_daily_summary_no_closed),
        ("RB-2", test_RB_2_daily_summary_with_closed),
        ("RB-3", test_RB_3_system_start_stop),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name} PASS")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} FAIL: {e}")
            failed.append((name, str(e)))
        except Exception as e:
            print(f"  ❌ {name} ERROR: {type(e).__name__}: {e}")
            failed.append((name, f"{type(e).__name__}: {e}"))
    print(f"\n=== 결과: {passed}/{len(tests)} PASS ===")
    if failed:
        sys.exit(1)
