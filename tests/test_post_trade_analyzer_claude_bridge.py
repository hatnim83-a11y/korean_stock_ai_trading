"""test_post_trade_analyzer_claude_bridge.py — post_trade_analyzer bridge 통합 테스트.

검증 항목 (실제 CLI/API 호출 없음 — mock):
1. flag OFF: bridge 미호출, 기존 Anthropic API 경로 사용
2. flag ON + bridge 성공: bridge 결과 사용, API 미호출
3. flag ON + bridge 실패(None): 기존 API 로 폴백
4. _analyze_single: bridge 경로에서도 timing_score clamp(0~10) 유지

실행: pytest tests/test_post_trade_analyzer_claude_bridge.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
import modules.post_trade_analyzer.analyzer as analyzer_mod
from modules.post_trade_analyzer.analyzer import PostTradeAnalyzer


@pytest.fixture
def analyzer():
    return PostTradeAnalyzer(db=MagicMock())


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    # 각 테스트 격리: 기본 OFF 로 리셋
    monkeypatch.setattr(settings, "CLAUDE_CODE_BRIDGE_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "CLAUDE_CODE_CLI_PATH", "claude", raising=False)
    monkeypatch.setattr(settings, "CLAUDE_CODE_TIMEOUT_SEC", 30, raising=False)
    yield


def _fake_api_client(payload_text):
    """messages.create 가 payload_text 를 반환하는 fake Anthropic client."""
    client = MagicMock()
    msg = MagicMock()
    content_block = MagicMock()
    content_block.text = payload_text
    msg.content = [content_block]
    client.messages.create.return_value = msg
    return client


# ===== 1. flag OFF → API 사용, bridge 미호출 =====

def test_flag_off_uses_api_not_bridge(analyzer, monkeypatch):
    bridge_spy = MagicMock(return_value={"timing_score": 9})
    monkeypatch.setattr(analyzer_mod, "call_claude_code_json", bridge_spy)

    client = _fake_api_client('{"timing_score": 4, "lesson": "api"}')
    monkeypatch.setattr(analyzer, "_get_client", lambda: client)

    out = analyzer._call_llm_json("prompt", 1500)

    assert out == {"timing_score": 4, "lesson": "api"}
    bridge_spy.assert_not_called()
    client.messages.create.assert_called_once()


# ===== 2. flag ON + bridge 성공 → bridge 사용, API 미호출 =====

def test_flag_on_bridge_success_used(analyzer, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_BRIDGE_ENABLED", True, raising=False)
    bridge_spy = MagicMock(return_value={"timing_score": 8, "lesson": "bridge"})
    monkeypatch.setattr(analyzer_mod, "call_claude_code_json", bridge_spy)

    client = _fake_api_client('{"timing_score": 1}')
    monkeypatch.setattr(analyzer, "_get_client", lambda: client)

    out = analyzer._call_llm_json("prompt", 1500)

    assert out == {"timing_score": 8, "lesson": "bridge"}
    bridge_spy.assert_called_once()
    client.messages.create.assert_not_called()


# ===== 3. flag ON + bridge 실패 → API 폴백 =====

def test_flag_on_bridge_fail_falls_back_to_api(analyzer, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_BRIDGE_ENABLED", True, raising=False)
    bridge_spy = MagicMock(return_value=None)  # bridge 실패
    monkeypatch.setattr(analyzer_mod, "call_claude_code_json", bridge_spy)

    client = _fake_api_client('{"timing_score": 6, "lesson": "fallback"}')
    monkeypatch.setattr(analyzer, "_get_client", lambda: client)

    out = analyzer._call_llm_json("prompt", 1500)

    assert out == {"timing_score": 6, "lesson": "fallback"}
    bridge_spy.assert_called_once()
    client.messages.create.assert_called_once()


# ===== 4. _analyze_single: bridge 경로 clamp 유지 =====

def test_analyze_single_bridge_clamps_score(analyzer, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_BRIDGE_ENABLED", True, raising=False)
    # 범위 초과 점수 → clamp 10
    bridge_spy = MagicMock(return_value={"timing_score": 99, "lesson": "x"})
    monkeypatch.setattr(analyzer_mod, "call_claude_code_json", bridge_spy)

    review = {"stock_name": "테스트", "stock_code": "000000"}
    prices = [{"days_after_sell": 1, "check_date": "2026-07-01",
               "close_price": 100, "high_price": 110, "low_price": 90,
               "change_from_sell": 1.0}]
    result = analyzer._analyze_single(review, prices)

    assert result is not None
    assert result["timing_score"] == 10


def test_analyze_single_bridge_none_returns_none(analyzer, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_BRIDGE_ENABLED", True, raising=False)
    monkeypatch.setattr(analyzer_mod, "call_claude_code_json", MagicMock(return_value=None))
    # API 도 없음 → None
    monkeypatch.setattr(analyzer, "_get_client", lambda: None)

    review = {"stock_name": "테스트", "stock_code": "000000"}
    prices = []
    assert analyzer._analyze_single(review, prices) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
