import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import TradingSystem, _format_buy_failure_reason


class DummyNotifier:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return True


class DummyDB:
    def __init__(self):
        self.updated = []

    def update_portfolio_buy_message(self, code, msg):
        self.updated.append((code, msg))
        return 1


def _system():
    sys = object.__new__(TradingSystem)
    sys.notifier = DummyNotifier()
    sys.db = DummyDB()
    sys.today_candidates = [
        {"stock_code": "006360", "stock_name": "GS건설"},
        {"stock_code": "069620", "stock_name": "대웅제약"},
    ]
    sys.today_ai_analysis = [
        {"code": "006360", "theme": "건설", "ai_sentiment": 7.0, "ai_confidence": 0.75, "ai_reason": "수급 개선"},
        {"code": "069620", "theme": "바이오", "ai_sentiment": 7.5, "ai_confidence": 0.75, "ai_reason": "기관 순매수"},
    ]
    sys._held_excluded = []
    sys._today_sold_excluded = []
    sys._morning_excluded = []
    sys._diversity_excluded = []
    sys._slot_excluded = []
    sys._buy_skip_reason = ""
    sys._theme_relaxation_applied = False
    sys._theme_relaxation_count = 0
    return sys


def test_format_buy_failure_reason_includes_fill_retry_and_message():
    reason = _format_buy_failure_reason({
        "requested_quantity": 5,
        "quantity": 0,
        "remaining_shares": 5,
        "retry_count": 5,
        "message": "미체결",
    })
    assert "체결 0/5주" in reason
    assert "미체결 5주" in reason
    assert "재시도 5회" in reason
    assert "사유: 미체결" in reason


def test_buy_summary_shows_only_actual_fills_as_new_buys_and_failed_attempts():
    sys = _system()
    filled = [{
        "stock_code": "006360", "stock_name": "GS건설", "amount": 729300,
        "price": 28050, "filled_price": 28050, "quantity": 26,
        "stop_loss": 26404, "take_profit": 30996,
    }]
    failed = [{
        "success": False, "stock_code": "069620", "stock_name": "대웅제약",
        "requested_quantity": 5, "quantity": 0, "remaining_shares": 5,
        "retry_count": 5, "message": "미체결",
    }]

    sys._send_buy_summary([], filled, 1, failed_buy_orders=failed)

    msg = sys.notifier.messages[-1]
    assert "📥 신규 매수: 1종목" in msg
    assert "1. GS건설 (006360)" in msg
    assert "2. 대웅제약 (069620)" not in msg
    assert "⚠️ 미매수/미체결: 1종목" in msg
    assert "대웅제약 (069620) — 체결 0/5주 / 미체결 5주 / 재시도 5회 / 사유: 미체결" in msg
    assert [code for code, _ in sys.db.updated] == ["006360"]
