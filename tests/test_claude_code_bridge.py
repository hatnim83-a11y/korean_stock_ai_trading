"""test_claude_code_bridge.py — Claude Code CLI(Max 세션) 공통 bridge 테스트.

검증 항목 (모두 fake runner / mock 으로 — 실제 CLI 실행 없음):
1. strip_anthropic_env: ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 제거, 나머지 보존
2. run_claude_code: fake runner 로 정상 stdout 반환 + subprocess env 에 두 키 미노출
3. run_claude_code: 비정상 종료(returncode!=0) / 공백 stdout → None
4. run_claude_code: TimeoutExpired → None
5. extract_json: ```json 펜스 / 순수 객체 / 배열 / 잡음 섞인 텍스트 파싱, invalid → None
6. call_claude_code_json: 정상 파싱 dict, JSON invalid → None
7. prompt 는 stdin(input) 으로 전달 (arg 로 노출되지 않음)

실행: pytest tests/test_claude_code_bridge.py -v
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.claude_code_bridge import (
    strip_anthropic_env,
    run_claude_code,
    extract_json,
    call_claude_code_json,
)


class _FakeCompleted:
    """subprocess.CompletedProcess 유사 객체."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_runner(captured, *, returncode=0, stdout="{}", stderr="", raise_timeout=False):
    """호출 인자를 captured dict 에 기록하는 fake runner 를 만든다."""

    def _runner(cmd, env, timeout, input_text):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["timeout"] = timeout
        captured["input_text"] = input_text
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        return _FakeCompleted(returncode=returncode, stdout=stdout, stderr=stderr)

    return _runner


# ===== 1. strip_anthropic_env =====

def test_strip_anthropic_env_removes_both_keys():
    src = {
        "ANTHROPIC_API_KEY": "secret1",
        "ANTHROPIC_AUTH_TOKEN": "secret2",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }
    out = strip_anthropic_env(src)
    assert "ANTHROPIC_API_KEY" not in out
    assert "ANTHROPIC_AUTH_TOKEN" not in out
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/x"
    # 원본 불변
    assert "ANTHROPIC_API_KEY" in src


def test_strip_anthropic_env_no_keys_ok():
    out = strip_anthropic_env({"PATH": "/usr/bin"})
    assert out == {"PATH": "/usr/bin"}


# ===== 2. run_claude_code 정상 + env 제거 =====

def test_run_claude_code_returns_stdout_and_strips_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak-2")
    captured = {}
    runner = _make_runner(captured, stdout='{"ok": true}')

    out = run_claude_code("hello prompt", timeout=30, runner=runner, cli_path="claude")

    assert out == '{"ok": true}'
    # subprocess env 에 두 키가 없어야 한다 (Max 세션 강제)
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
    # timeout 전달
    assert captured["timeout"] == 30


def test_run_claude_code_prompt_via_stdin_not_argv():
    captured = {}
    runner = _make_runner(captured, stdout="ok")
    run_claude_code("SECRET_PROMPT_TEXT", timeout=10, runner=runner)
    # prompt 는 stdin(input_text) 으로 전달, argv 에는 노출되지 않아야 함
    assert captured["input_text"] == "SECRET_PROMPT_TEXT"
    assert "SECRET_PROMPT_TEXT" not in captured["cmd"]


# ===== 3. 비정상 종료 / 공백 =====

def test_run_claude_code_nonzero_returncode_none():
    captured = {}
    runner = _make_runner(captured, returncode=1, stdout="", stderr="err")
    assert run_claude_code("p", timeout=10, runner=runner) is None


def test_run_claude_code_blank_stdout_none():
    captured = {}
    runner = _make_runner(captured, returncode=0, stdout="   \n  ")
    assert run_claude_code("p", timeout=10, runner=runner) is None


# ===== 4. timeout =====

def test_run_claude_code_timeout_none():
    captured = {}
    runner = _make_runner(captured, raise_timeout=True)
    assert run_claude_code("p", timeout=1, runner=runner) is None


# ===== 5. extract_json =====

def test_extract_json_fenced():
    text = 'blah\n```json\n{"score": 7.5, "outlook": "상승"}\n```\ntail'
    out = extract_json(text)
    assert out == {"score": 7.5, "outlook": "상승"}


def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_array():
    out = extract_json('[{"theme_name": "AI", "score": 8}]')
    assert isinstance(out, list)
    assert out[0]["theme_name"] == "AI"


def test_extract_json_with_noise():
    text = '여기 결과입니다:\n{"x": 1, "y": 2}\n감사합니다.'
    assert extract_json(text) == {"x": 1, "y": 2}


def test_extract_json_invalid_none():
    assert extract_json("완전히 JSON 이 아닌 텍스트") is None
    assert extract_json("") is None
    assert extract_json(None) is None


# ===== 6. call_claude_code_json =====

def test_call_claude_code_json_ok():
    captured = {}
    runner = _make_runner(captured, stdout='```json\n{"timing_score": 8}\n```')
    out = call_claude_code_json("prompt", timeout=10, runner=runner)
    assert out == {"timing_score": 8}


def test_call_claude_code_json_invalid_none():
    captured = {}
    runner = _make_runner(captured, stdout="not json at all")
    assert call_claude_code_json("prompt", timeout=10, runner=runner) is None


def test_call_claude_code_json_runner_fail_none():
    captured = {}
    runner = _make_runner(captured, returncode=2, stdout="")
    assert call_claude_code_json("prompt", timeout=10, runner=runner) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
