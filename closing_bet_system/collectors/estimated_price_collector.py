"""closing_bet_system.collectors.estimated_price_collector

PRD 9-3 동시호가 예상체결가 모니터링 collector.

기존 인프라 ``KISOrderApi.inquire_asking_price`` (modules/trading_engine/kis_order_api.py:819,
TR ``FHKST01010200``) 응답에서 ``antc_cnpr``(예상체결가) / ``antc_vol``(예상거래량) /
호가 1단계 ``askp1``/``bidp1`` 잔량 추출.

PRD 9-3:
- 15:20부터 폴링 (5초 default, 단위 2-4a 결정)
- 예상체결가 +0.5% 상승 → 2차 진입 보류
- 매수/매도 잔량 비율 < 0.8 → 2차 진입 취소

15:20 이전 호출 시 KIS 응답에 antc_cnpr 가 비어있거나 0 → ``EstimatedPriceSnapshot``
``estimated_price=None`` graceful 반환.

5/15 dry_run에서 antc_cnpr 필드명/값 최종 검증 (단위 2-4a STEP0 명시).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import time as time_cls
from typing import Optional

from modules.trading_engine.kis_order_api import KISOrderApi
from config import now_kst
from logger import logger


# 모듈 상수 (PRD 9-3 박제)
_ESTIMATED_PRICE_WINDOW_START = time_cls(15, 20)   # 15:20 이전 None 반환


@dataclass(frozen=True)
class EstimatedPriceSnapshot:
    """예상체결가 + 호가 잔량 스냅샷."""

    ticker: str
    estimated_price: Optional[int]      # 예상체결가 (원, 15:20 이전 None)
    estimated_volume: Optional[int]     # 예상거래량
    ask_total: int                       # 매도 1단계 잔량 합계 (askp_rsqn1)
    bid_total: int                       # 매수 1단계 잔량 합계 (bidp_rsqn1)
    captured_at: str                     # KST ISO 8601


class EstimatedPriceCollector:
    """KIS 동시호가 예상체결가 비동기 collector."""

    def __init__(self, order_api: KISOrderApi):
        self.order_api = order_api

    async def get_estimated_price(
        self,
        ticker: str,
    ) -> Optional[EstimatedPriceSnapshot]:
        """예상체결가 + 호가 1단계 잔량 조회.

        Args:
            ticker: 종목코드 6자리.

        Returns:
            EstimatedPriceSnapshot 또는 None (KIS 응답 실패).
            ``estimated_price=None`` 은 정상 응답이지만 동시호가 시간대 외(15:20 이전 등).
        """
        try:
            payload = await asyncio.to_thread(
                self.order_api.inquire_asking_price, ticker
            )
        except Exception as exc:
            logger.warning(
                f"[estimated_price_collector] {ticker} inquire_asking_price 예외: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        if not payload or not isinstance(payload, dict):
            return None

        # KIS 응답에서 antc_cnpr / antc_vol / askp_rsqn1 / bidp_rsqn1 추출
        # 응답 형식: output1 또는 직접 dict
        output = payload.get("output1") or payload.get("output") or payload

        # 예상체결가 (15:20 이전이거나 동시호가 아닌 시간대는 0 또는 빈 값)
        antc_cnpr_raw = output.get("antc_cnpr", "") if isinstance(output, dict) else ""
        antc_vol_raw = output.get("antc_vol", "") if isinstance(output, dict) else ""
        ask_total_raw = output.get("askp_rsqn1", "") if isinstance(output, dict) else ""
        bid_total_raw = output.get("bidp_rsqn1", "") if isinstance(output, dict) else ""

        # 시간대 가드 — 15:20 이전이면 estimated_price=None
        now = now_kst().time()
        if now < _ESTIMATED_PRICE_WINDOW_START:
            estimated_price: Optional[int] = None
            estimated_volume: Optional[int] = None
        else:
            est_int = _safe_int(antc_cnpr_raw)
            estimated_price = est_int if est_int > 0 else None
            est_vol = _safe_int(antc_vol_raw)
            estimated_volume = est_vol if est_vol > 0 else None

        return EstimatedPriceSnapshot(
            ticker=ticker,
            estimated_price=estimated_price,
            estimated_volume=estimated_volume,
            ask_total=_safe_int(ask_total_raw),
            bid_total=_safe_int(bid_total_raw),
            captured_at=now_kst().isoformat(),
        )


def _safe_int(value) -> int:
    """KIS 응답 빈 문자열/None → 0. 그 외 → int (실패 시 0)."""
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
