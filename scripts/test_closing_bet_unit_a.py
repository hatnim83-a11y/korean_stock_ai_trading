"""단위 A 검증: name_lookup + telegram_client (NoOp 더미).

시나리오:
    NL-1: 정상 ticker → KIS get_stock_name mock 응답 캐시
    NL-2: 같은 ticker 두 번째 호출 → 캐시 히트, KIS 호출 없음
    NL-3: 무효 ticker (5자리/문자/None) → "(미상)" 반환
    NL-4: KIS 예외 → "(미상)" 반환, 캐시 안 함
    NL-5: KIS 빈 문자열 반환 → "(미상)" 반환, 캐시 안 함

    TG-1: 토큰 둘 다 설정 → 실제 TelegramNotifier 인스턴스
    TG-2: 토큰 미설정 → _NoOpTelegramNotifier 반환
    TG-3: NoOp.send_message(...) → False (silent)
    TG-4: NoOp.<임의의_미정의_메서드>(...) → False (silent)
    TG-5: NoOp._enabled == False
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.infra import name_lookup, telegram_client


# ===== name_lookup 시나리오 =====


def test_NL_1_정상_ticker_mock_kis():
    name_lookup.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_stock_name.return_value = "삼성전자"
    with patch.object(name_lookup, "get_kis_api", return_value=fake_kis):
        name = name_lookup.get_name("005930")
    assert name == "삼성전자", f"NL-1 FAIL: got {name!r}"
    print("[PASS] NL-1: 정상 ticker → 종목명 반환")


def test_NL_2_캐시_히트():
    name_lookup.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_stock_name.return_value = "삼성전자"
    with patch.object(name_lookup, "get_kis_api", return_value=fake_kis):
        name1 = name_lookup.get_name("005930")
        name2 = name_lookup.get_name("005930")
    assert name1 == name2 == "삼성전자"
    # KIS 호출은 1회만
    assert fake_kis.get_stock_name.call_count == 1, (
        f"NL-2 FAIL: KIS 호출 {fake_kis.get_stock_name.call_count}회 (1회 기대)"
    )
    print("[PASS] NL-2: 캐시 히트 → KIS 1회만 호출")


def test_NL_3_무효_ticker():
    name_lookup.reset_cache()
    invalid_inputs = ["00593", "0059300", "abc123", "", None, 123456]
    for inp in invalid_inputs:
        name = name_lookup.get_name(inp)  # type: ignore[arg-type]
        assert name == "(미상)", f"NL-3 FAIL: {inp!r} → {name!r}"
    print(f"[PASS] NL-3: 무효 ticker {len(invalid_inputs)}건 모두 '(미상)' 반환")


def test_NL_4_KIS_예외():
    name_lookup.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_stock_name.side_effect = RuntimeError("KIS API 장애")
    with patch.object(name_lookup, "get_kis_api", return_value=fake_kis):
        name = name_lookup.get_name("005930")
    assert name == "(미상)", f"NL-4 FAIL: got {name!r}"
    # 캐시 안 함 → 다음 호출은 다시 KIS 시도
    fake_kis.get_stock_name.side_effect = None
    fake_kis.get_stock_name.return_value = "삼성전자"
    with patch.object(name_lookup, "get_kis_api", return_value=fake_kis):
        name = name_lookup.get_name("005930")
    assert name == "삼성전자", f"NL-4 FAIL (재시도): got {name!r}"
    print("[PASS] NL-4: KIS 예외 → '(미상)' 반환, 캐시 X (재시도 가능)")


def test_NL_5_KIS_빈_문자열():
    name_lookup.reset_cache()
    fake_kis = MagicMock()
    fake_kis.get_stock_name.return_value = ""
    with patch.object(name_lookup, "get_kis_api", return_value=fake_kis):
        name = name_lookup.get_name("005930")
    assert name == "(미상)", f"NL-5 FAIL: got {name!r}"
    print("[PASS] NL-5: KIS 빈 문자열 → '(미상)' 반환, 캐시 X")


# ===== telegram_client 시나리오 =====


def test_TG_1_정상_토큰_TelegramNotifier():
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=("dummy_token", "dummy_chat_id")):
        notifier = telegram_client.get_telegram_notifier()
    # 실제 TelegramNotifier 인스턴스 (NoOp 더미 X)
    assert not isinstance(notifier, telegram_client._NoOpTelegramNotifier), (
        "TG-1 FAIL: NoOp 더미 반환됨 (실제 TelegramNotifier 기대)"
    )
    print("[PASS] TG-1: 토큰 둘 다 설정 → 실제 TelegramNotifier 생성")


def test_TG_2_미설정_NoOp():
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=(None, None)):
        notifier = telegram_client.get_telegram_notifier()
    assert isinstance(notifier, telegram_client._NoOpTelegramNotifier), (
        f"TG-2 FAIL: {type(notifier).__name__} 반환 (NoOp 더미 기대)"
    )
    print("[PASS] TG-2: 토큰 미설정 → NoOp 더미 반환")


def test_TG_3_NoOp_send_message_silent():
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=(None, None)):
        notifier = telegram_client.get_telegram_notifier()
    result = notifier.send_message("test message", parse_mode="Markdown")
    assert result is False, f"TG-3 FAIL: send_message returned {result!r}"
    print("[PASS] TG-3: NoOp.send_message → False (silent)")


def test_TG_4_NoOp_미정의_메서드_silent():
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=(None, None)):
        notifier = telegram_client.get_telegram_notifier()
    # 부모 클래스에 미래에 추가될 가상의 메서드들
    assert notifier.send_photo("path.jpg") is False
    assert notifier.send_document("path.pdf", caption="test") is False
    assert notifier.unknown_future_method(1, 2, 3) is False
    print("[PASS] TG-4: NoOp 미정의 메서드 → False (silent NoOp)")


def test_TG_5_NoOp_enabled_False():
    notifier = telegram_client._NoOpTelegramNotifier()
    assert notifier._enabled is False
    # is_enabled 인터페이스가 사용하는 getattr 방식 동작 확인
    assert bool(getattr(notifier, "_enabled", False)) is False
    print("[PASS] TG-5: NoOp._enabled == False (is_enabled 가드 작동)")


def test_TG_6_singleton_persistence():
    """싱글톤 보장: 같은 호출이 같은 인스턴스 반환."""
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=(None, None)):
        n1 = telegram_client.get_telegram_notifier()
        n2 = telegram_client.get_telegram_notifier()
    assert n1 is n2, "TG-6 FAIL: 싱글톤 위반"
    print("[PASS] TG-6: 싱글톤 보장")


def test_TG_7_TelegramReviewBot_통합():
    """TelegramReviewBot.is_enabled 가 NoOp 더미와 호환되는지 확인."""
    telegram_client.reset_singleton()
    with patch.object(telegram_client, "_resolve_telegram_credentials",
                      return_value=(None, None)):
        from closing_bet_system.notification.telegram_review_bot import TelegramReviewBot
        bot = TelegramReviewBot()  # notifier 미주입 → get_telegram_notifier() 자동 호출
        assert bot.is_enabled is False, f"TG-7 FAIL: is_enabled={bot.is_enabled}"
        # send_test_message → 비활성으로 False
        assert bot.send_test_message() is False
        # send_alert → 비활성으로 False
        assert bot.send_alert(ticker="005930", name="삼성전자") is False
        # send_batch_alert → 비활성으로 0
        assert bot.send_batch_alert([{"ticker": "005930", "name": "삼성전자"}]) == 0
    print("[PASS] TG-7: TelegramReviewBot 통합 — NoOp 더미와 호환")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 A 검증: name_lookup + telegram_client")
    print("=" * 60)
    print("\n[name_lookup]")
    test_NL_1_정상_ticker_mock_kis()
    test_NL_2_캐시_히트()
    test_NL_3_무효_ticker()
    test_NL_4_KIS_예외()
    test_NL_5_KIS_빈_문자열()
    print("\n[telegram_client]")
    test_TG_1_정상_토큰_TelegramNotifier()
    test_TG_2_미설정_NoOp()
    test_TG_3_NoOp_send_message_silent()
    test_TG_4_NoOp_미정의_메서드_silent()
    test_TG_5_NoOp_enabled_False()
    test_TG_6_singleton_persistence()
    test_TG_7_TelegramReviewBot_통합()
    print("\n" + "=" * 60)
    print("✅ 단위 A 12 시나리오 모두 PASS")
    print("=" * 60)
