from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot, _escape_markdown


class DummyNotifier:
    _enabled = True

    def __init__(self):
        self.calls = []

    def send_message(self, text, parse_mode="Markdown", disable_notification=False):
        self.calls.append((text, parse_mode, disable_notification))
        return True


def test_daily_summary_shows_phase1_fills_when_entered_zero():
    notifier = DummyNotifier()
    bot = TelegramReviewBot(notifier=notifier)

    ok = bot.send_daily_summary(
        "2026-06-22",
        {"recommended": 11, "entered": 0, "rejected_filter": 0, "rejected_manual": 0},
        recommended_count=277,
        closed_positions=[],
        phase1_fills=[
            {
                "ticker": "080220",
                "name": "제주반도체",
                "candidate_status": "recommended",
                "entry_phase1_order_id": "0027515100",
                "total_shares": 11,
                "avg_entry_price": 134300,
                "total_amount": 1477300,
                "phase1_only": True,
            }
        ],
    )

    assert ok is True
    text, parse_mode, _ = notifier.calls[-1]
    assert parse_mode == "Markdown"
    assert "Entered: 0건" in text
    assert "Phase1 체결" in text
    assert "제주반도체" in text
    assert "11주 @ 134,300원" in text
    assert "0027515100" in text


def test_escape_markdown_escapes_closing_bracket_too():
    assert _escape_markdown("A_B*C[`]") == r"A\_B\*C\[\`\]"
