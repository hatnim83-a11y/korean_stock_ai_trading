"""closing_bet_system.infra.telegram_client

종가베팅 전용 텔레그램 봇.

기존 ``modules/reporter/telegram_notifier.py`` 의 ``TelegramNotifier`` 가 이미
``__init__(bot_token=None, chat_id=None)`` 인자 주입을 지원한다 (라인 53~57).
별도 리팩터 없이 신규 봇 토큰을 인자로 넘기면 즉시 분리 운영이 가능하다.

env 변수 (사용자가 ``.env`` 에 추가해야 함):
- ``CLOSING_BET_TELEGRAM_BOT_TOKEN``
- ``CLOSING_BET_TELEGRAM_CHAT_ID``

settings.yaml 의 ``telegram.bot_token_env`` / ``telegram.chat_id_env`` 에서
실제 env 키 이름을 조회하므로, 봇 분리/병합 시 settings.yaml 만 수정하면 된다.

채널 격리: 토큰/chat_id 미설정 시 ``TelegramNotifier`` 를 생성하지 않고
NoOp 더미 (``_NoOpTelegramNotifier``) 를 반환한다. 부모 클래스 내부 속성
(``_enabled``, ``base_url``) 을 직접 변경하던 기존 패턴을 제거하여 부모
클래스 리팩터 시 silent break 위험을 차단한다.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# .env 자동 로드 (기존 시스템과 동일)
try:
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from logger import logger
from modules.reporter.telegram_notifier import TelegramNotifier

from closing_bet_system.storage.db import _load_settings


_notifier_instance: Optional[Any] = None
_notifier_lock = threading.Lock()


class _NoOpTelegramNotifier:
    """텔레그램 비활성 시 silent NoOp 더미.

    채널 격리(스윙 봇 토큰 폴백 차단)를 위해 ``TelegramNotifier`` 자체를
    생성하지 않는다. ``TelegramReviewBot`` 이 ``getattr(notifier, "_enabled", False)``
    로 활성 여부를 판단하므로 ``_enabled = False`` 만 노출하면 모든 발송이 자연스럽게 스킵된다.

    부모 클래스 인터페이스가 변해도 ``__getattr__`` 가 모든 메서드를 silent NoOp 으로
    처리하므로 호출 측 변경 없이 안전하다 (``send_message``, ``send_photo`` 등 미래 API 포함).
    """

    _enabled: bool = False
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None

    def __getattr__(self, name: str):
        # send_message 가 bool 반환하는 인터페이스에 일관되게 False 반환
        def _noop(*args, **kwargs) -> bool:
            return False

        return _noop


def _resolve_telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """settings.yaml 에서 env 키 이름을 가져와 실제 값을 반환."""
    settings = _load_settings()
    tg_cfg = settings.get("telegram", {})
    bot_token_env = tg_cfg.get("bot_token_env", "CLOSING_BET_TELEGRAM_BOT_TOKEN")
    chat_id_env = tg_cfg.get("chat_id_env", "CLOSING_BET_TELEGRAM_CHAT_ID")
    return os.getenv(bot_token_env), os.getenv(chat_id_env)


def get_telegram_notifier() -> Any:
    """종가베팅 전용 TelegramNotifier 싱글톤.

    토큰/chat_id 가 둘 다 설정된 경우에만 실제 ``TelegramNotifier`` 를 생성한다.
    하나라도 없으면 ``_NoOpTelegramNotifier`` 를 반환하여 스윙 봇 토큰 폴백을 차단한다
    (채널 격리).

    Returns:
        ``TelegramNotifier`` (활성) 또는 ``_NoOpTelegramNotifier`` (비활성).
    """
    global _notifier_instance
    if _notifier_instance is None:
        with _notifier_lock:
            if _notifier_instance is None:
                bot_token, chat_id = _resolve_telegram_credentials()
                if bot_token and chat_id:
                    _notifier_instance = TelegramNotifier(
                        bot_token=bot_token,
                        chat_id=chat_id,
                    )
                    logger.info("[종가베팅] 텔레그램 봇 인스턴스 생성 (활성)")
                else:
                    _notifier_instance = _NoOpTelegramNotifier()
                    logger.warning(
                        "[종가베팅] 텔레그램 토큰/chat_id 미설정 — NoOp 더미 사용 "
                        "(.env 의 CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID 등록 필요). "
                        "스윙 봇 폴백은 차단됨 (채널 격리)."
                    )
    return _notifier_instance


def reset_singleton() -> None:
    """테스트용: 싱글톤 초기화."""
    global _notifier_instance
    with _notifier_lock:
        _notifier_instance = None
