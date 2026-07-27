"""claude_code_bridge.py — Claude Code CLI(Max 로그인 세션) 공통 브릿지.

Anthropic API 직접 호출 대신, 로컬에 로그인된 Claude Code CLI(`claude`)를 subprocess 로
호출해 프롬프트를 처리한다. subprocess 실행 env 에서 `ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` 을 제거하여 API 키가 아닌 **Max 구독 로그인 세션**을 사용하게 한다.

핵심 안전 설계
--------------
- 이 모듈은 스스로 활성화되지 않는다. 호출 측(post_trade_analyzer / theme_analyzer)이
  명시적 flag 로만 사용한다.
- 실패 / timeout / JSON invalid 시 항상 `None` 을 반환한다 → 호출 측이 기존 API 로 폴백.
- 실제 CLI 실행 없이 `runner` 콜러블을 주입해 단위 테스트가 가능하다.
- 프롬프트는 argv 가 아닌 **stdin** 으로 전달한다(길이/이스케이프/노출 회피).

사용법
------
    from modules.claude_code_bridge import call_claude_code_json

    result = call_claude_code_json(prompt, timeout=120)   # dict/list 또는 None
"""

import json
import os
import re
import subprocess
from typing import Any, Callable, Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import logger

# ===== 상수 =====
# subprocess env 에서 제거할 키 — 반드시 Max 로그인 세션을 쓰도록 강제
_STRIP_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

DEFAULT_CLI_PATH = "claude"
DEFAULT_TIMEOUT_SEC = 120

# runner 시그니처: (cmd: list[str], env: dict, timeout: float, input_text: str) -> CompletedProcess-유사
RunnerType = Callable[[list, dict, float, str], Any]


def strip_anthropic_env(env: dict) -> dict:
    """`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` 을 제거한 env 복사본을 반환.

    원본 dict 는 변경하지 않는다(불변). Max 세션 강제용.
    """
    cleaned = dict(env)
    for key in _STRIP_ENV_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _default_runner(cmd: list, env: dict, timeout: float, input_text: str):
    """실제 subprocess 실행 runner (테스트에서는 fake runner 로 대체)."""
    return subprocess.run(
        cmd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_claude_code(
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    runner: Optional[RunnerType] = None,
    cli_path: Optional[str] = None,
) -> Optional[str]:
    """Claude Code CLI 를 호출하고 stdout 텍스트를 반환한다.

    Args:
        prompt: CLI 에 stdin 으로 전달할 프롬프트
        timeout: subprocess timeout(초)
        runner: 주입형 실행 함수(테스트용). None 이면 실제 subprocess 실행
        cli_path: `claude` 바이너리 경로. None 이면 DEFAULT_CLI_PATH

    Returns:
        정상 시 stdout 문자열. 비정상 종료 / 공백 / timeout / 예외 시 None
    """
    run = runner or _default_runner
    binary = cli_path or DEFAULT_CLI_PATH
    # `-p` : print 모드(비대화형). 프롬프트는 stdin 으로 전달.
    cmd = [binary, "-p"]
    env = strip_anthropic_env(os.environ)

    try:
        completed = run(cmd, env, timeout, prompt)
    except subprocess.TimeoutExpired:
        logger.warning(f"[ClaudeCodeBridge] CLI timeout ({timeout}s)")
        return None
    except FileNotFoundError:
        logger.warning(f"[ClaudeCodeBridge] CLI 실행 파일 미발견: {binary}")
        return None
    except Exception as e:
        logger.warning(f"[ClaudeCodeBridge] CLI 실행 예외: {e}")
        return None

    returncode = getattr(completed, "returncode", 1)
    if returncode != 0:
        stderr = (getattr(completed, "stderr", "") or "")[:300]
        logger.warning(f"[ClaudeCodeBridge] CLI 비정상 종료(rc={returncode}): {stderr}")
        return None

    stdout = getattr(completed, "stdout", "") or ""
    if not stdout.strip():
        logger.warning("[ClaudeCodeBridge] CLI stdout 비어 있음")
        return None

    return stdout


def extract_json(text: Optional[str]) -> Optional[Any]:
    """텍스트에서 JSON(객체 또는 배열)을 추출/파싱한다.

    처리 순서:
    1. ```json ... ``` (또는 ``` ... ```) 펜스 내부
    2. 전체 문자열 그대로
    3. 첫 `{`/`[` ~ 대응되는 마지막 `}`/`]` 구간

    실패 시 None.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()

    # 1) 코드 펜스 추출
    fenced = _extract_fenced(raw)
    candidates = []
    if fenced is not None:
        candidates.append(fenced)
    candidates.append(raw)

    # 3) 첫 여는 괄호 ~ 마지막 닫는 괄호
    span = _brace_span(raw)
    if span is not None:
        candidates.append(span)

    for cand in candidates:
        try:
            return json.loads(cand.strip())
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _extract_fenced(text: str) -> Optional[str]:
    """```json ... ``` 또는 ``` ... ``` 펜스 내부 문자열 반환."""
    m = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return None


def _brace_span(text: str) -> Optional[str]:
    """첫 `{`/`[` 부터 대응되는 마지막 `}`/`]` 까지의 부분 문자열."""
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    end = text.rfind(close_ch)
    if end == -1 or end < start:
        return None
    return text[start:end + 1]


def call_claude_code_json(
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    runner: Optional[RunnerType] = None,
    cli_path: Optional[str] = None,
) -> Optional[Any]:
    """`run_claude_code` 실행 후 stdout 에서 JSON 을 추출해 반환한다.

    실행 실패 / 파싱 실패 시 None.
    """
    stdout = run_claude_code(prompt, timeout=timeout, runner=runner, cli_path=cli_path)
    if stdout is None:
        return None
    result = extract_json(stdout)
    if result is None:
        logger.warning("[ClaudeCodeBridge] JSON 추출 실패")
    return result
