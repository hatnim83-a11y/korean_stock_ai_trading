"""closing_bet_system.infra.name_lookup

종가베팅용 ticker → 종목명 조회.

기존 ``KISApi.get_stock_name()`` (네이버 금융 + 내부 인스턴스 캐시) 를 래핑하면서
종가베팅 측에서 별도 thread-safe 캐시를 추가로 둔다. universe 산출/알림 발송 시
같은 종목이 반복 조회되더라도 KIS rate_limit/네이버 차단 부담을 최소화한다.

조회 실패 폴백: ``"(미상)"``.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger

from closing_bet_system.infra.kis_client import get_kis_api


_TICKER_PATTERN = re.compile(r"^\d{6}$")
_NAME_FALLBACK = "(미상)"

_name_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


def get_name(ticker: str) -> str:
    """종가베팅용 종목명 조회. 실패 시 ``"(미상)"``.

    캐시 히트 시 즉시 반환. 미스 시 KIS API 호출 후 캐시 저장.
    실패한 조회 결과는 캐시 안 함 (다음 호출에서 재시도 가능).

    Args:
        ticker: 6자리 종목코드.

    Returns:
        종목명 (캐시 또는 KIS 조회). 무효 ticker / 조회 실패 시 ``"(미상)"``.
    """
    if not isinstance(ticker, str) or not _TICKER_PATTERN.match(ticker):
        return _NAME_FALLBACK

    cached = _name_cache.get(ticker)
    if cached:
        return cached

    try:
        kis = get_kis_api()
        name = kis.get_stock_name(ticker)
    except Exception as e:
        logger.debug(f"[name_lookup] {ticker} KIS 조회 예외: {e}")
        return _NAME_FALLBACK

    if not isinstance(name, str) or not name.strip():
        return _NAME_FALLBACK

    name = name.strip()
    with _cache_lock:
        _name_cache[ticker] = name
    return name


def reset_cache() -> None:
    """테스트용: 인메모리 캐시 초기화."""
    with _cache_lock:
        _name_cache.clear()
