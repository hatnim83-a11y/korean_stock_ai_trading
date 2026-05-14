"""closing_bet_system.collectors.vwap_collector

PRD 9-2 가격 상한 룰의 VWAP 항(14:50~15:18) 계산 collector.

PRD 9-2: 매수 허용 상한가 = min(14:50~15:18 VWAP × 1.005, 당일 고가, 예상체결가 × 1.002)

KIS API ``get_minute_price(time_to="151800", count=30)`` → 14:50~15:18 28분 분봉 →
``VWAP = Σ(close × volume) / Σ(volume)``.

14:50 이전 호출 시 ``InsufficientDataError`` (분봉 데이터 부족 → VWAP 계산 의미 없음 →
호출자가 가격 상한 룰에서 VWAP 항 제외하고 진행할지 결정).

5/15 dry_run에서 KIS 응답 형식 최종 검증 (단위 2-4a STEP0 명시).
"""

from __future__ import annotations

import asyncio
from datetime import time as time_cls
from typing import Optional

from modules.stock_screener.kis_api import KISApi
from config import now_kst
from logger import logger


# 모듈 상수 (PRD 9-2 박제)
_VWAP_WINDOW_START_HHMM = "1450"   # 14:50 이전 호출 시 InsufficientDataError
_VWAP_WINDOW_END_HHMM = "1518"     # 분봉 조회 종료 시각 (HHMMSS 변환 시 "151800")
_VWAP_BAR_COUNT = 30               # 14:50~15:18 = 28분 + 여유 2분


class InsufficientDataError(RuntimeError):
    """VWAP 계산용 분봉 데이터 부족."""


class VWAPCollector:
    """14:50~15:18 VWAP 계산 비동기 collector."""

    def __init__(self, kis_api: KISApi):
        self.kis_api = kis_api

    async def get_vwap(
        self,
        ticker: str,
        *,
        time_to: str = f"{_VWAP_WINDOW_END_HHMM}00",
        bar_count: int = _VWAP_BAR_COUNT,
    ) -> Optional[float]:
        """14:50~15:18 시간대 분봉 VWAP 계산.

        Args:
            ticker: 종목코드 6자리.
            time_to: KIS 분봉 조회 종료 시각 (HHMMSS, default ``"151800"``).
            bar_count: 가져올 분봉 개수.

        Returns:
            VWAP 가격 (float) 또는 None (분봉 응답 없음 또는 거래량 합계 0).

        Raises:
            InsufficientDataError: 현재 시각이 14:50 이전인 경우.
        """
        # 14:50 이전 호출 시 InsufficientDataError (PRD 9-2 정합)
        now = now_kst().time()
        if now < time_cls(14, 50):
            raise InsufficientDataError(
                f"VWAP 계산은 14:50 이후만 가능 (현재 {now.strftime('%H:%M:%S')} KST)"
            )

        try:
            bars = await asyncio.to_thread(
                self.kis_api.get_minute_price,
                stock_code=ticker,
                time_to=time_to,
                count=bar_count,
            )
        except Exception as exc:
            logger.warning(
                f"[vwap_collector] {ticker} get_minute_price 예외: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        if not bars:
            logger.warning(f"[vwap_collector] {ticker} 분봉 응답 비어있음")
            return None

        # VWAP = Σ(close × volume) / Σ(volume) — 14:50 이후 분봉만 필터
        weighted_sum = 0.0
        volume_sum = 0
        for bar in bars:
            bar_time = bar.get("time", "")
            # 분봉 시각 HHMMSS 형식 (예: "151800")
            if not bar_time or len(bar_time) < 4:
                continue
            hhmm = bar_time[:4]
            if hhmm < _VWAP_WINDOW_START_HHMM:
                continue
            if hhmm > _VWAP_WINDOW_END_HHMM:
                continue
            close = bar.get("close", 0) or 0
            volume = bar.get("volume", 0) or 0
            if close > 0 and volume > 0:
                weighted_sum += float(close) * float(volume)
                volume_sum += int(volume)

        if volume_sum == 0:
            logger.warning(
                f"[vwap_collector] {ticker} 14:50~15:18 거래량 0 — VWAP 계산 불가"
            )
            return None

        vwap = weighted_sum / volume_sum
        logger.debug(
            f"[vwap_collector] {ticker} VWAP={vwap:.2f} (volume_sum={volume_sum})"
        )
        return float(vwap)
