"""closing_bet_system.execution.fill_checker

KIS 주문 체결 조회 thin wrapper + 3회 재시도 (5/13 KIS 500 사건 패턴).

기존 인프라 ``KISOrderApi.get_order_status(order_id, order_date)`` (kis_order_api.py:569,
TR_ID ``TTTC8001R``) 를 비동기 래핑한다. 메인 봇 ``label_provider._fetch_daily_price_with_retry``
(closing_bet_system/collectors/label_provider.py) 의 재시도 패턴을 따름.

엔드포인트 응답 필드 (KIS):
- ``odno``: 주문번호
- ``tot_ccld_qty``: 총 체결수량
- ``avg_prvs``: 평균 체결가
- ``ord_qty``: 주문수량
- ``ord_unpr``: 주문단가

산출 ``FillStatus``:
- ``executed_shares``: 체결 수량 (int, 0=미체결)
- ``executed_price``: 평균 체결가 (int, 0=미체결)
- ``remaining_shares``: 미체결 잔량 (int)
- ``is_filled``: 전량 체결 여부 (bool)
- ``is_partial``: 부분 체결 여부 (bool, 일부만 체결되고 잔량 > 0)

5/13 사건: KIS 500 1회 → 라벨 누락 → fill_checker는 3회 재시도(5초/10초/15초 지수 백오프).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from modules.trading_engine.kis_order_api import KISOrderApi
from logger import logger


# 모듈 상수 — 5/13 사건 회피용 (label_provider._fetch_daily_price_with_retry 동일 패턴)
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SEC = 5.0


@dataclass(frozen=True)
class FillStatus:
    """주문 체결 상태."""

    order_id: str
    order_qty: int
    executed_shares: int       # 총 체결수량 (avg_prvs > 0 시 양수)
    executed_price: int        # 평균 체결가 (원, 미체결 시 0)
    remaining_shares: int      # 미체결 잔량 (order_qty - executed_shares)
    is_filled: bool            # 전량 체결 여부
    is_partial: bool           # 부분 체결 여부 (executed > 0 AND remaining > 0)
    raw_status: str            # KIS 응답 status (\"체결\"/\"미체결\")


class FillChecker:
    """KIS 주문 체결 조회 비동기 래퍼."""

    def __init__(self, kis_order_api: KISOrderApi):
        self.kis_order_api = kis_order_api

    async def get_fill_status(
        self,
        order_id: str,
        *,
        order_date: Optional[str] = None,
        max_attempts: int = _MAX_ATTEMPTS,
        backoff_base_sec: float = _BACKOFF_BASE_SEC,
    ) -> Optional[FillStatus]:
        """주문 ODNO 의 체결 상태 조회 (3회 재시도).

        Args:
            order_id: KIS 주문번호 (ODNO, 매수 시 발급).
            order_date: 주문일자 YYYYMMDD (None=오늘).
            max_attempts: 최대 시도 횟수 (default 3).
            backoff_base_sec: 지수 백오프 기본값 (default 5초, 5/10/15초).

        Returns:
            FillStatus 또는 None (max_attempts 모두 실패).

        Notes:
            빈 응답 ([])은 정상 응답 (KIS 가 ODNO 못 찾음, 미체결도 아닌 상태) →
            재시도 안 함, FillStatus(executed=0, remaining=order_qty=0) 반환.
            예외 발생 시만 재시도.
        """
        last_exc: Optional[BaseException] = None

        for attempt in range(1, max_attempts + 1):
            try:
                orders = await asyncio.to_thread(
                    self.kis_order_api.get_order_status,
                    order_id=order_id,
                    order_date=order_date,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = backoff_base_sec * attempt
                    logger.warning(
                        f"[fill_checker] order_id={order_id} attempt={attempt}/{max_attempts} "
                        f"실패: {type(exc).__name__}: {exc} → {delay:.1f}초 후 재시도"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    f"[fill_checker] order_id={order_id} 전체 실패: "
                    f"{type(exc).__name__}: {exc}"
                )
                return None
            # 정상 응답 → 매칭 주문 검색
            return self._extract_fill(order_id, orders)

        # 도달 불가 (방어적)
        if last_exc:
            logger.error(f"[fill_checker] 알 수 없는 실패: {last_exc!r}")
        return None

    @staticmethod
    def _extract_fill(order_id: str, orders: list[dict]) -> FillStatus:
        """get_order_status 응답 리스트에서 order_id 매칭 + FillStatus 변환.

        ODNO 미매칭 시 모든 필드 0 (KIS 응답에 해당 주문이 없는 경우).
        """
        target = None
        for o in orders or []:
            if o.get("order_id") == order_id:
                target = o
                break
        if target is None:
            return FillStatus(
                order_id=order_id,
                order_qty=0,
                executed_shares=0,
                executed_price=0,
                remaining_shares=0,
                is_filled=False,
                is_partial=False,
                raw_status="not_found",
            )

        order_qty = int(target.get("order_qty", 0) or 0)
        executed_shares = int(target.get("filled_qty", 0) or 0)
        executed_price = int(target.get("filled_price", 0) or 0)
        remaining = max(order_qty - executed_shares, 0)
        is_filled = executed_shares > 0 and remaining == 0
        is_partial = executed_shares > 0 and remaining > 0
        raw_status = str(target.get("status", ""))

        return FillStatus(
            order_id=order_id,
            order_qty=order_qty,
            executed_shares=executed_shares,
            executed_price=executed_price,
            remaining_shares=remaining,
            is_filled=is_filled,
            is_partial=is_partial,
            raw_status=raw_status,
        )
