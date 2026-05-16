"""closing_bet_system.collectors.morning_price_collector

단위 2-5b — 09:00~10:30 시가/고가/현재가 동시 조회 collector.

PRD 10-1 시가 액션 매트릭스의 매도 분기는 **시가 갭률** 기준이다:
    gap_rate = (open_price - entry_price) / entry_price

본 collector 는 `KISApi.get_current_price` (modules/stock_screener/kis_api.py:422)
응답에서 open/high/low/price 4 필드를 추출해 ExitExecutor 에 전달한다.

기존 인프라 재사용 — 신규 KIS HTTP 호출 없음 (단위 2-5a STEP0 검증 1 결론).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from modules.stock_screener.kis_api import KISApi
from config import now_kst
from logger import logger


# 종목코드 정규식 (CLAUDE.md modules 규칙)
_TICKER_PATTERN = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class MorningPriceSnapshot:
    """09:00~10:30 시점 시가/고가/현재가 스냅샷."""

    ticker: str
    open_price: int          # 시가 (stck_oprc)
    high: int                 # 당일 고가 (stck_hgpr)
    low: int                  # 당일 저가 (stck_lwpr)
    current_price: int        # 현재가 (stck_prpr)
    change_rate: float        # 등락률 % (prdy_ctrt)
    captured_at: str          # KST ISO 8601


class MorningPriceCollector:
    """KIS 현재가 조회 비동기 collector (시가 갭률 산출용)."""

    def __init__(self, kis_api: KISApi):
        self.kis_api = kis_api

    async def get_snapshot(self, ticker: str) -> Optional[MorningPriceSnapshot]:
        """시가/고가/현재가 동시 조회.

        Args:
            ticker: 6자리 종목코드.

        Returns:
            ``MorningPriceSnapshot`` 또는 None (KIS 응답 실패 또는 시가 0).
        """
        if not isinstance(ticker, str) or not _TICKER_PATTERN.match(ticker):
            logger.warning(f"[morning_price_collector] 잘못된 ticker: {ticker!r}")
            return None

        try:
            payload = await asyncio.to_thread(self.kis_api.get_current_price, ticker)
        except Exception as exc:
            logger.warning(
                f"[morning_price_collector] {ticker} get_current_price 예외: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        if not payload or not isinstance(payload, dict):
            logger.warning(f"[morning_price_collector] {ticker} 응답 None")
            return None

        open_price = int(payload.get("open") or 0)
        high = int(payload.get("high") or 0)
        low = int(payload.get("low") or 0)
        current_price = int(payload.get("price") or 0)
        change_rate = float(payload.get("change_rate") or 0.0)

        # 시가 0 → 매도 갭률 계산 불가 → graceful None
        if open_price <= 0:
            logger.warning(
                f"[morning_price_collector] {ticker} 시가 0 — 매도 갭률 계산 불가"
            )
            return None

        return MorningPriceSnapshot(
            ticker=ticker,
            open_price=open_price,
            high=high,
            low=low,
            current_price=current_price,
            change_rate=change_rate,
            captured_at=now_kst().isoformat(),
        )
