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
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional

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


_notifier_instance: Optional[TelegramNotifier] = None
_notifier_lock = threading.Lock()


def _resolve_telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    """settings.yaml 에서 env 키 이름을 가져와 실제 값을 반환."""
    settings = _load_settings()
    tg_cfg = settings.get("telegram", {})
    bot_token_env = tg_cfg.get("bot_token_env", "CLOSING_BET_TELEGRAM_BOT_TOKEN")
    chat_id_env = tg_cfg.get("chat_id_env", "CLOSING_BET_TELEGRAM_CHAT_ID")
    return os.getenv(bot_token_env), os.getenv(chat_id_env)


def get_telegram_notifier() -> TelegramNotifier:
    """종가베팅 전용 TelegramNotifier 싱글톤.

    종가베팅 신규 봇 토큰/chat_id 미설정 시에는 ``_enabled=False`` 로 강제 비활성화한다.
    이는 ``TelegramNotifier.__init__`` 의 settings 폴백(스윙 봇 토큰)이 동작해
    종가베팅 알림이 스윙 채널로 흘러가는 것을 막기 위함이다 (채널 격리).
    """
    global _notifier_instance
    if _notifier_instance is None:
        with _notifier_lock:
            if _notifier_instance is None:
                bot_token, chat_id = _resolve_telegram_credentials()
                _notifier_instance = TelegramNotifier(
                    bot_token=bot_token,
                    chat_id=chat_id,
                )
                if bot_token and chat_id:
                    logger.info("[종가베팅] 텔레그램 봇 인스턴스 생성 (활성)")
                else:
                    # 강제 비활성화: 스윙 봇 토큰 폴백 차단 (채널 격리)
                    _notifier_instance.bot_token = None
                    _notifier_instance.chat_id = None
                    _notifier_instance._enabled = False
                    _notifier_instance.base_url = ""
                    logger.warning(
                        "[종가베팅] 텔레그램 토큰/chat_id 미설정 — 알림 비활성화 "
                        "(.env 의 CLOSING_BET_TELEGRAM_BOT_TOKEN/CHAT_ID 등록 필요). "
                        "스윙 봇 폴백은 차단됨 (채널 격리)."
                    )
    return _notifier_instance


def reset_singleton() -> None:
    """테스트용: 싱글톤 초기화."""
    global _notifier_instance
    with _notifier_lock:
        _notifier_instance = None
