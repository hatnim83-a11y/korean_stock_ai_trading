"""test_theme_analyzer_shadow.py — theme_analyzer Claude Code batch shadow 테스트.

검증 항목 (실제 CLI/API 호출 없음 — fake runner / mock):
1. analyze_themes_batch_bridge: fake runner 로 JSON 배열 파싱 → 기존 result shape 반환
2. batch shape 검증/기본값 보정 (누락 필드 채움, score/confidence clamp)
3. bridge 실패(None/invalid) → 빈 리스트
4. shadow flag OFF → analyze_themes_sync 가 bridge 미호출, 운영 결과 그대로
5. shadow flag ON → bridge 호출되지만 운영 반환값은 기존 API 결과 그대로(무영향)

실행: pytest tests/test_theme_analyzer_shadow.py -v
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
import modules.theme_analyzer.ai_analyzer as ai_mod
from modules.theme_analyzer.ai_analyzer import (
    analyze_themes_batch_bridge,
    analyze_themes_sync,
)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner_returning(stdout, returncode=0):
    def _runner(cmd, env, timeout, input_text):
        return _FakeCompleted(returncode=returncode, stdout=stdout)
    return _runner


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_THEME_SHADOW", False, raising=False)
    monkeypatch.setattr(settings, "CLAUDE_CODE_CLI_PATH", "claude", raising=False)
    monkeypatch.setattr(settings, "CLAUDE_CODE_TIMEOUT_SEC", 30, raising=False)
    yield


THEMES = [
    {"name": "2차전지", "news": "정부 R&D 예산 증액과 글로벌 전기차 수요 회복 뉴스 " * 5},
    {"name": "AI반도체", "news": "HBM 수요 급증, 엔비디아 공급 확대 소식 " * 5},
]


# ===== 1. batch bridge 정상 파싱 =====

def test_batch_bridge_parses_array():
    payload = '''```json
    [
      {"theme_name": "2차전지", "score": 7.5, "reason": "수요 회복", "risk": "중국 경쟁", "outlook": "상승", "confidence": 0.8},
      {"theme_name": "AI반도체", "score": 8.0, "reason": "HBM 수요", "risk": "밸류에이션", "outlook": "상승", "confidence": 0.9}
    ]
    ```'''
    out = analyze_themes_batch_bridge(THEMES, runner=_runner_returning(payload))
    assert len(out) == 2
    names = {r["theme_name"] for r in out}
    assert names == {"2차전지", "AI반도체"}
    for r in out:
        assert set(["theme_name", "score", "reason", "risk", "outlook", "confidence"]).issubset(r.keys())


# ===== 2. shape 검증/기본값 보정 =====

def test_batch_bridge_fills_defaults_and_clamps():
    # score 범위 초과, confidence 누락, reason/risk/outlook 누락
    payload = '[{"theme_name": "2차전지", "score": 99}]'
    out = analyze_themes_batch_bridge(THEMES[:1], runner=_runner_returning(payload))
    assert len(out) == 1
    r = out[0]
    assert r["score"] == 10.0          # clamp 0~10
    assert r["outlook"] == "중립"       # default
    assert r["reason"] == ""            # default
    assert r["risk"] == ""              # default
    assert r["confidence"] == 0.7       # default


def test_batch_bridge_clamps_confidence():
    payload = '[{"theme_name": "AI반도체", "score": 5, "confidence": 5.0}]'
    out = analyze_themes_batch_bridge(THEMES[:1], runner=_runner_returning(payload))
    assert out[0]["confidence"] == 1.0  # clamp 0~1


# ===== 3. 실패 → 빈 리스트 =====

def test_batch_bridge_invalid_returns_empty():
    out = analyze_themes_batch_bridge(THEMES, runner=_runner_returning("완전 JSON 아님"))
    assert out == []


def test_batch_bridge_runner_fail_returns_empty():
    out = analyze_themes_batch_bridge(THEMES, runner=_runner_returning("", returncode=1))
    assert out == []


def test_batch_bridge_empty_input_returns_empty():
    assert analyze_themes_batch_bridge([], runner=_runner_returning("[]")) == []


# ===== 4/5. shadow 훅: 운영 결과 불변 =====

def _patch_operational(monkeypatch, api_results):
    """analyze_themes_batch(async) 를 스텁으로 대체해 API 호출 회피."""
    async def _fake_batch(themes_with_news, concurrent_limit=5, api_key=None):
        return api_results
    monkeypatch.setattr(ai_mod, "analyze_themes_batch", _fake_batch)


def test_shadow_off_no_bridge_call(monkeypatch):
    api_results = [{"theme_name": "2차전지", "score": 6.0, "outlook": "중립"}]
    _patch_operational(monkeypatch, api_results)
    shadow_spy = MagicMock(return_value=[{"theme_name": "2차전지", "score": 9.0}])
    monkeypatch.setattr(ai_mod, "analyze_themes_batch_bridge", shadow_spy)

    out = analyze_themes_sync(THEMES)

    assert out == api_results           # 운영 결과 그대로
    shadow_spy.assert_not_called()      # shadow OFF → bridge 미호출


def test_shadow_on_calls_bridge_but_result_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_THEME_SHADOW", True, raising=False)
    api_results = [{"theme_name": "2차전지", "score": 6.0, "outlook": "중립"}]
    _patch_operational(monkeypatch, api_results)
    shadow_spy = MagicMock(return_value=[{"theme_name": "2차전지", "score": 9.0}])
    monkeypatch.setattr(ai_mod, "analyze_themes_batch_bridge", shadow_spy)

    out = analyze_themes_sync(THEMES)

    # 운영 반환값은 기존 API 결과 그대로 (shadow 무영향)
    assert out == api_results
    # 하지만 shadow bridge 는 호출됨
    shadow_spy.assert_called_once()


def test_shadow_on_bridge_exception_does_not_break(monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_THEME_SHADOW", True, raising=False)
    api_results = [{"theme_name": "2차전지", "score": 6.0}]
    _patch_operational(monkeypatch, api_results)

    def _boom(*a, **k):
        raise RuntimeError("shadow 폭발")
    monkeypatch.setattr(ai_mod, "analyze_themes_batch_bridge", _boom)

    # shadow 예외가 운영 흐름을 깨면 안 됨
    out = analyze_themes_sync(THEMES)
    assert out == api_results


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
