"""슬롯당 배분 자본 계산 유틸 (자동 매수 / 수동 매수 공용).

main.py `execute_buy_orders()`의 인라인 슬롯 사이징 로직(swing_capital_pool 기반)을
추출해 단일 소스로 만든다. 자동 매수와 텔레그램 수동 매수(/buy)가 동일한 계산을 공유하므로
SWING_CAPITAL_RATIO / TRANCHE_FIRST_RATIO / MAX_POSITIONS 파라미터 변경 시 한 곳만 고치면 된다.

순수 함수로 유지(설정/잔고 의존성 주입)하여 순환 import와 테스트 부담을 회피한다.
"""

from __future__ import annotations


def compute_per_slot_capital(
    total_capital: float,
    available_cash: float,
    available_slots: int,
    *,
    max_positions: int,
    swing_capital_ratio: float,
    tranche_enabled: bool,
    tranche_first_ratio: float,
) -> dict:
    """슬롯당 배분 자본을 계산한다.

    계산식 (main.py:1382~1402 원본과 동일):
        swing_capital_pool   = int(total_capital × swing_capital_ratio)
        max_per_stock        = swing_capital_pool // max_positions
        per_slot_capital_full= min(available_cash // available_slots, max_per_stock)
        per_slot_capital     = per_slot_capital_full × tranche_first_ratio  (분할 진입 활성 시)

    Args:
        total_capital: 총자산 (KIS 총평가액 또는 폴백).
        available_cash: 가용 현금 (Market Guard 축소 반영된 값).
        available_slots: 매수 가능 슬롯 수 (MAX_POSITIONS - 보유 종목 수). > 0 이어야 함.
        max_positions: 최대 보유 종목 수 (settings.MAX_POSITIONS).
        swing_capital_ratio: 스윙 자본 비율 (env SWING_CAPITAL_RATIO, 기본 0.9).
        tranche_enabled: 분할 진입 활성 여부 (settings.TRANCHE_ENTRY_ENABLED).
        tranche_first_ratio: 1차 진입 비율 (settings.TRANCHE_FIRST_RATIO, 기본 0.5).

    Returns:
        dict: {
            swing_capital_pool, max_per_stock, per_slot_capital_full,
            per_slot_capital (1차 진입 실사용액), tranche_applied
        }

    Raises:
        ValueError: available_slots <= 0 (호출 측에서 슬롯 만석 사전 차단 권장).
    """
    if available_slots <= 0:
        raise ValueError(f"available_slots must be > 0 (got {available_slots})")

    swing_capital_pool = int(total_capital * swing_capital_ratio)
    max_per_stock = swing_capital_pool // max_positions
    per_slot_capital_full = int(min(available_cash // available_slots, max_per_stock))

    per_slot_capital = per_slot_capital_full
    tranche_applied = False
    if tranche_enabled:
        per_slot_capital = int(per_slot_capital_full * tranche_first_ratio)
        tranche_applied = True

    return {
        "swing_capital_pool": swing_capital_pool,
        "max_per_stock": int(max_per_stock),
        "per_slot_capital_full": per_slot_capital_full,
        "per_slot_capital": per_slot_capital,
        "tranche_applied": tranche_applied,
    }
