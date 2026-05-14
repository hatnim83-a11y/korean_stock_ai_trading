"""closing_bet_system.execution.price_utils

KRX 호가 단위(틱) 정렬 헬퍼.

PRD 9-2 가격 상한 `min(VWAP × 1.005, 당일 고가, 예상체결가 × 1.002)` 계산 결과를
실제 KIS 매수 주문에 보내려면 KRX 호가 단위로 정렬해야 한다. 단위 위반 시 KIS가
주문을 거부한다.

KRX 호가 단위 테이블 (코스피/코스닥 동일, 2023-01-25 개편 기준):
- 2,000원 미만: 1원
- 2,000원 ~ 5,000원 미만: 5원
- 5,000원 ~ 20,000원 미만: 10원
- 20,000원 ~ 50,000원 미만: 50원
- 50,000원 ~ 200,000원 미만: 100원
- 200,000원 ~ 500,000원 미만: 500원
- 500,000원 이상: 1,000원

매수(buy): floor (상한이므로 그 이하 가장 큰 호가)
매도(sell): ceil (하한이므로 그 이상 가장 작은 호가)
"""

from __future__ import annotations

from typing import Literal


# KRX 호가 단위 테이블 (price_min, tick_size) — 오름차순
# 임계값 미만일 때 해당 tick 사용. 마지막 항목은 그 이상 모든 가격에 적용.
_TICK_TABLE: tuple[tuple[int, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (10**12, 1_000),   # 500,000원 이상 일괄 1,000원 (1조원 캡)
)


def get_tick_size(price: float) -> int:
    """가격에 적용되는 KRX 호가 단위 반환.

    Args:
        price: 기준 가격 (float).

    Returns:
        호가 단위 (int, 원 단위).
    """
    p = float(price)
    for upper_bound, tick in _TICK_TABLE:
        if p < upper_bound:
            return tick
    return _TICK_TABLE[-1][1]   # 안전망


def align_to_tick(price: float, side: Literal["buy", "sell"]) -> int:
    """가격을 KRX 호가 단위로 정렬 (매수=floor / 매도=ceil).

    Args:
        price: 정렬 전 가격 (float, 0 이상).
        side: ``"buy"`` 매수(가격 상한이므로 floor) / ``"sell"`` 매도(가격 하한이므로 ceil).

    Returns:
        호가 단위 정렬된 가격 (int, 원 단위).

    Raises:
        ValueError: price < 0 또는 side 미지원.
    """
    if price < 0:
        raise ValueError(f"price must be >= 0, got {price!r}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    p = float(price)
    if p == 0.0:
        return 0

    tick = get_tick_size(p)

    if side == "buy":
        # 매수: floor — 가격 상한 이하 가장 큰 호가
        return int(int(p) // tick * tick)
    # 매도: ceil — 가격 하한 이상 가장 작은 호가
    return int((int(p) + tick - 1) // tick * tick)
