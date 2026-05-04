"""scripts/test_closing_bet_telegram_review.py

Phase 1-7: ``TelegramReviewBot`` 단위 테스트.

- Mock TelegramNotifier 로 메시지 포맷/발송 검증
- 실거래 sendMessage 1건 발송 (--live, .env 의 신규 봇 토큰 사용)

실행:
    venv/bin/python scripts/test_closing_bet_telegram_review.py          # Mock only
    venv/bin/python scripts/test_closing_bet_telegram_review.py --live   # + 실제 1건 발송
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.notification.telegram_review_bot import (
    TelegramReviewBot,
    _escape_markdown,
    _fmt_int,
    _fmt_num,
    _gak,
    _to_date_label,
)


# ===== Fixtures =====


def _make_mock_notifier(enabled: bool = True) -> MagicMock:
    """TelegramNotifier 의 _enabled / send_message 만 모킹."""
    m = MagicMock()
    m._enabled = enabled
    m.send_message = MagicMock(return_value=True)
    return m


def _full_score_breakdown() -> object:
    """1-4 ScoreBreakdown 만점 (Phase 1 weighted=7)."""
    from closing_bet_system.engines.signal_score_engine import SignalScoreEngine
    eng = SignalScoreEngine()
    return eng.score(
        "005930",
        layer1={"inst_net_buy_estimated": 1e9, "foreign_net_buy_3d": 5e9,
                "program_net_buy_change": 0.05, "closing_flow_concentration": 0.6},
        layer2={"close_strength": 0.95, "volume_surprise": 3.0, "above_ma20": True,
                "closing_buy_sell_ratio": 1.5, "atr_overheat": 1.2},
        layer3={"near_52w_high": True, "theme_leadership_rank": 1,
                "has_positive_disclosure": True},
    )


def _dart_with_positive() -> dict:
    return {
        "has_positive_disclosure": True,
        "exclude_by_disclosure": False,
        "positive_matches": (("단일판매공급계약체결", "단일판매공급계약"),),
        "exclusion_matches": (),
    }


def _dart_with_exclusion() -> dict:
    return {
        "has_positive_disclosure": False,
        "exclude_by_disclosure": True,
        "positive_matches": (),
        "exclusion_matches": (("유상증자 결정", "유상증자"),),
    }


def _risk_assess_normal() -> dict:
    return {
        "can_enter": True, "final_size_factor": 1.0,
        "decision_reason": "진입 가능 (정상 시장)",
    }


def _risk_assess_reduced() -> dict:
    return {
        "can_enter": True, "final_size_factor": 0.5,
        "decision_reason": "진입 가능 (비중 축소 50%): KOSPI -2.5%",
    }


def _risk_assess_skip() -> dict:
    return {
        "can_enter": False, "final_size_factor": 0.0,
        "decision_reason": "매매 중단: 미국 선물 -2.0% (PRD 4-2)",
    }


# ===== 테스트 =====


def test_is_enabled_true():
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    assert bot.is_enabled is True
    print(f"  [PASS] _enabled=True → is_enabled=True")


def test_is_enabled_false():
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=False))
    assert bot.is_enabled is False
    print(f"  [PASS] _enabled=False → is_enabled=False")


def test_send_test_message_enabled():
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    assert bot.send_test_message() is True
    n.send_message.assert_called_once()
    args, kwargs = n.send_message.call_args
    text = args[0]
    assert "종가베팅" in text
    assert "테스트" in text
    print(f"  [PASS] send_test_message → notifier 1회 호출, '종가베팅' 포함")


def test_send_test_message_disabled():
    n = _make_mock_notifier(enabled=False)
    bot = TelegramReviewBot(notifier=n)
    assert bot.send_test_message() is False
    n.send_message.assert_not_called()
    print(f"  [PASS] disabled → send_test_message False, 호출 0회")


def test_send_alert_full():
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    ok = bot.send_alert(
        ticker="005930", name="삼성전자",
        score_breakdown=_full_score_breakdown(),
        dart_snapshot=_dart_with_positive(),
        risk_assessment=_risk_assess_normal(),
    )
    assert ok is True
    n.send_message.assert_called_once()
    text = n.send_message.call_args[0][0]
    assert "005930" in text
    assert "삼성전자" in text
    assert "ALERT" in text
    assert "L1" in text and "L2" in text and "L3" in text
    assert "Phase 1" in text
    assert "수동 매수" in text
    print(f"  [PASS] send_alert (full) → 메시지 본문 검증")


def test_send_alert_disabled():
    n = _make_mock_notifier(enabled=False)
    bot = TelegramReviewBot(notifier=n)
    ok = bot.send_alert("005930", "삼성전자", score_breakdown=_full_score_breakdown())
    assert ok is False
    n.send_message.assert_not_called()
    print(f"  [PASS] disabled → send_alert False, 호출 0회")


def test_send_alert_invalid_ticker():
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    for bad in ["12345", "ABCDEF", 12345, ""]:
        ok = bot.send_alert(bad, "이름")
        assert ok is False
    n.send_message.assert_not_called()
    print(f"  [PASS] 잘못된 ticker 4종 → 발송 0회")


def test_format_with_dart_exclusion():
    """DART 즉시제외 메시지 포맷."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message(
        "005930", "삼성전자",
        score_breakdown=_full_score_breakdown(),
        dart_snapshot=_dart_with_exclusion(),
        risk_assessment=_risk_assess_normal(),
    )
    assert "DART:" in text
    assert "즉시제외" in text or "🚫" in text
    assert "유상증자" in text
    print(f"  [PASS] DART 즉시제외 포맷 (유상증자)")


def test_format_with_risk_skip():
    """시장 매매중단 메시지 포맷."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message(
        "005930", "삼성전자",
        score_breakdown=_full_score_breakdown(),
        risk_assessment=_risk_assess_skip(),
    )
    assert "외부리스크" in text
    assert "진입 불가" in text or "🚫" in text
    print(f"  [PASS] 시장 매매중단 포맷")


def test_format_with_risk_reduced():
    """비중 축소 메시지 포맷."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message(
        "005930", "삼성전자",
        score_breakdown=_full_score_breakdown(),
        risk_assessment=_risk_assess_reduced(),
    )
    assert "비중 축소" in text
    assert "50%" in text
    print(f"  [PASS] 비중 축소 포맷 (50%)")


def test_format_with_rank():
    """배치 순위 표시."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message(
        "005930", "삼성전자",
        score_breakdown=_full_score_breakdown(),
        rank=2, total=5,
    )
    assert "[2/5]" in text
    print(f"  [PASS] 배치 순위 [2/5] 포맷")


def test_format_no_score_breakdown():
    """score_breakdown=None 안전 처리."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message("005930", "삼성전자")
    assert "005930" in text
    assert "—" in text   # score 표시 없음
    assert "Phase 1" in text
    print(f"  [PASS] score_breakdown=None → '—' 안전 표기")


def test_format_underscore_escape():
    """이름에 underscore 포함 시 escape (Markdown v1 충돌 방지)."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message("005930", "test_name_with_underscore")
    # _ → \_ 로 escape
    assert "test\\_name\\_with\\_underscore" in text
    print(f"  [PASS] underscore escape (Markdown 충돌 방지)")


def test_format_atr_overheat_str_safe():
    """회귀: dict 입력의 atr_overheat_value 가 str 이어도 ValueError 없이 안전 (P0 fix)."""
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    # str / None / NaN 모두 _fmt_num() 으로 안전 처리
    for bad in ["hot", float("nan"), float("inf"), True, "abc"]:
        text = bot.format_alert_message(
            "005930", "삼성전자",
            score_breakdown={"raw_total": 7, "weighted_total": 7.0, "decision": "ALERT",
                             "layer1_subscore": 4, "layer2_subscore": 3, "layer3_subscore": 0,
                             "atr_overheat_value": bad, "excluded_by_filter": False},
        )
        assert "atr" in text   # 라인 자체는 있음
        # str/NaN 들어와도 크래시 없이 "—" 또는 정상 포맷
    print(f"  [PASS] atr_overheat_value 비-float 입력 5종 안전 (P0 회귀)")


def test_send_batch_alert():
    """배치 알림 — 헤더 1회 + 종목별 N회."""
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    candidates = [
        {"ticker": "005930", "name": "삼성전자",
         "score_breakdown": _full_score_breakdown(),
         "dart_snapshot": _dart_with_positive(),
         "risk_assessment": _risk_assess_normal()},
        {"ticker": "000660", "name": "SK하이닉스",
         "score_breakdown": _full_score_breakdown(),
         "risk_assessment": _risk_assess_reduced()},
        {"ticker": "035420", "name": "NAVER",
         "score_breakdown": _full_score_breakdown()},
    ]
    success = bot.send_batch_alert(candidates)
    assert success == 3
    # 헤더 1회 + 종목 3회 = 4회 호출
    assert n.send_message.call_count == 4
    # 헤더에 "3건" 포함
    header_text = n.send_message.call_args_list[0][0][0]
    assert "3건" in header_text
    # 첫 종목 [1/3]
    first_text = n.send_message.call_args_list[1][0][0]
    assert "[1/3]" in first_text
    print(f"  [PASS] batch 3종목 → 4회 호출 (헤더+3) + 순위 표시")


def test_send_batch_alert_empty():
    """빈 배치 → 발송 0회."""
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    assert bot.send_batch_alert([]) == 0
    n.send_message.assert_not_called()
    print(f"  [PASS] 빈 배치 → 발송 0회")


def test_send_batch_alert_disabled():
    """봇 비활성 시 배치도 스킵."""
    n = _make_mock_notifier(enabled=False)
    bot = TelegramReviewBot(notifier=n)
    candidates = [{"ticker": "005930", "name": "삼성전자"}]
    assert bot.send_batch_alert(candidates) == 0
    n.send_message.assert_not_called()
    print(f"  [PASS] disabled → batch 0건")


def test_send_daily_summary():
    """일일 요약 메시지."""
    n = _make_mock_notifier(enabled=True)
    bot = TelegramReviewBot(notifier=n)
    counts = {"recommended": 5, "entered": 2, "rejected_filter": 8, "rejected_manual": 1}
    ok = bot.send_daily_summary(date(2026, 5, 4), counts, recommended_count=12)
    assert ok is True
    text = n.send_message.call_args[0][0]
    assert "2026-05-04" in text
    assert "5건" in text   # recommended
    assert "12건" in text  # 누적
    assert "30건" in text  # 게이트
    print(f"  [PASS] 일일 요약 (2026-05-04, recommended=5, 누적=12/30)")


def test_send_daily_summary_disabled():
    n = _make_mock_notifier(enabled=False)
    bot = TelegramReviewBot(notifier=n)
    assert bot.send_daily_summary(date(2026, 5, 4), {}, 0) is False
    print(f"  [PASS] disabled → daily_summary False")


def test_helpers_escape():
    """_escape_markdown — 특수문자 5종 escape."""
    # 단일 문자별 검증
    assert _escape_markdown("foo_bar") == "foo\\_bar"
    assert _escape_markdown("foo*bar") == "foo\\*bar"
    assert _escape_markdown("foo[bar") == "foo\\[bar"
    assert _escape_markdown("foo`bar") == "foo\\`bar"
    # backslash 자체도 escape (먼저 처리되므로 한 글자 → 두 글자)
    assert _escape_markdown("a\\b") == "a\\\\b"
    # None / int 안전
    assert _escape_markdown(None) == ""
    assert _escape_markdown(123) == "123"
    print(f"  [PASS] _escape_markdown 7케이스")


def test_helpers_format():
    """_fmt_num / _fmt_int / _gak / _to_date_label."""
    assert _fmt_num(7.0) == "7.0"
    assert _fmt_num(None) == "—"
    assert _fmt_num("invalid") == "—"
    assert _fmt_int(4) == "4"
    assert _fmt_int(None) == "—"
    assert _gak({"k": 1}, "k", 0) == 1
    assert _gak(None, "k", -1) == -1

    class O:
        a = "x"
    assert _gak(O(), "a", "default") == "x"
    assert _gak(O(), "missing", "default") == "default"

    assert _to_date_label(date(2026, 5, 4)) == "2026-05-04"
    assert _to_date_label("2026-05-04") == "2026-05-04"
    assert _to_date_label(None).startswith("20")
    print(f"  [PASS] 헬퍼 _fmt/_gak/_to_date_label 11케이스")


def test_dart_snapshot_dataclass_compat():
    """DartDisclosureSnapshot dataclass 객체도 처리."""
    from closing_bet_system.collectors.dart_disclosure_collector import (
        DartDisclosureSnapshot,
    )
    from config import now_kst
    snap = DartDisclosureSnapshot(
        ticker="005930",
        snapshot_time=now_kst(),
        is_valid=True,
        positive_matches=(("단일판매공급계약체결", "단일판매공급계약"),),
        has_positive_disclosure=True,
    )
    bot = TelegramReviewBot(notifier=_make_mock_notifier(enabled=True))
    text = bot.format_alert_message("005930", "삼성전자",
                                      score_breakdown=_full_score_breakdown(),
                                      dart_snapshot=snap)
    assert "DART" in text
    assert "단일판매공급계약" in text
    print(f"  [PASS] DartDisclosureSnapshot dataclass 객체 호환")


def test_live_send(live: bool = False):
    """실제 신규 봇으로 sendMessage 1건 (--live 옵션)."""
    if not live:
        print(f"  [SKIP] --live 플래그 시 활성화")
        return
    bot = TelegramReviewBot()   # 기본 싱글톤 (실제 토큰)
    if not bot.is_enabled:
        print(f"  [SKIP] 봇 비활성 (.env 미설정)")
        return
    ok = bot.send_test_message()
    print(f"  [LIVE] send_test_message → {ok}")
    assert ok is True


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실제 신규 봇으로 1건 발송")
    args = parser.parse_args()

    print("=== Phase 1-7 TelegramReviewBot 단위 테스트 ===\n")
    tests = [
        test_is_enabled_true,
        test_is_enabled_false,
        test_send_test_message_enabled,
        test_send_test_message_disabled,
        test_send_alert_full,
        test_send_alert_disabled,
        test_send_alert_invalid_ticker,
        test_format_with_dart_exclusion,
        test_format_with_risk_skip,
        test_format_with_risk_reduced,
        test_format_with_rank,
        test_format_no_score_breakdown,
        test_format_underscore_escape,
        test_format_atr_overheat_str_safe,
        test_send_batch_alert,
        test_send_batch_alert_empty,
        test_send_batch_alert_disabled,
        test_send_daily_summary,
        test_send_daily_summary_disabled,
        test_helpers_escape,
        test_helpers_format,
        test_dart_snapshot_dataclass_compat,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"▶ test_live_send (--live={'ON' if args.live else 'OFF'})")
    test_live_send(live=args.live)
    print()

    print(f"[ALL PASS] {len(tests)}개 mock 테스트 통과")


if __name__ == "__main__":
    main()
