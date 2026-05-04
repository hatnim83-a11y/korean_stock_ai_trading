"""closing_bet_system.engines.cost_slippage_engine

PRD 7 — 거래비용 및 슬리피지 엔진.

세 가지 책임:

1. **순수익률 계산** (PRD 7-2 공식)
   ::

       net_pnl_pct = (sell_price × (1 - sell_comm - tax) - buy_price × (1 + buy_comm))
                    / (buy_price × (1 + buy_comm))

2. **최소 목표 수익률** (PRD 7-2)
   ::

       minimum_target = round_trip_cost + safety_margin
                      ≈ 0.41% + 0.5% = 0.91% (settings.yaml 기본값)

3. **비용 분해** (`CostBreakdown` dataclass)
   - 실거래/백테스트 양쪽에서 동일 인터페이스
   - DB(`candidates.{buy_commission, sell_commission, transaction_tax,
     estimated_slippage, net_pnl_pct}`)에 그대로 매핑

설계 결정:
- 슬리피지 처리는 호출 측이 결정 — ``slippage_rate=None`` (기본, 추정값 사용) 또는
  ``slippage_rate=0.0`` (실거래 가격이 이미 슬리피지 반영된 경우).
- 코스피/코스닥 비용 차이는 settings.yaml 단일값으로 통합 (Phase 1 단순화). Phase 3
  에서 시장별/종목별 동적 비용 도입 예정.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.storage.db import _load_settings


@dataclass(frozen=True)
class CostBreakdown:
    """1회 매수/매도 거래의 비용 분해 + 손익 계산.

    DB ``candidates`` 테이블의 비용 컬럼과 1:1 매핑.
    모든 금액은 원(KRW), 비율은 소수 (0.01 = 1%).
    """

    buy_price: float           # 매수 단가
    sell_price: float          # 매도 단가
    shares: int                # 주식 수
    slippage_rate: float       # 적용된 슬리피지율 (편도)

    # 분해 (금액)
    gross_buy_amount: float    # buy_price × shares
    gross_sell_amount: float   # sell_price × shares
    buy_commission: float      # 매수 수수료 (원)
    sell_commission: float     # 매도 수수료 (원)
    transaction_tax: float     # 매도 거래세 (원)
    estimated_slippage: float  # 추정 슬리피지 비용 (원, 왕복)

    # 손익
    pretax_pnl: float          # 세전 손익 (원, 슬리피지 미반영)
    pretax_pnl_pct: float      # 세전 손익률 (소수)
    net_pnl: float             # 비용·세금·슬리피지 차감 손익 (원)
    net_pnl_pct: float         # PRD 7-2 순수익률 (소수)


class CostSlippageEngine:
    """거래비용 + 슬리피지 + 순수익률 계산기.

    settings.yaml ``cost.*`` 섹션을 로드한다. 의존성 주입 가능 (테스트 용).
    """

    def __init__(self, settings: Optional[dict] = None):
        cost = (settings or _load_settings()).get("cost", {})
        self.buy_commission_rate = float(cost.get("buy_commission", 0.00015))
        self.sell_commission_rate = float(cost.get("sell_commission", 0.00015))
        self.transaction_tax_rate = float(cost.get("transaction_tax", 0.0018))
        self.estimated_slippage_rate = float(cost.get("estimated_slippage", 0.001))
        self.safety_margin = float(cost.get("safety_margin", 0.005))

    # ===== 비용 합계 =====

    def round_trip_cost(self, include_slippage: bool = True) -> float:
        """왕복 거래비용 (수수료 + 거래세 + slippage 편도×2).

        Args:
            include_slippage: True 면 estimated_slippage × 2 포함 (백테스트/추정).
                False 면 수수료 + 거래세만 (실거래 후 사후 분석 시).

        Returns:
            왕복 비용율 (소수). settings 기본값 기준 약 0.0041 (0.41%).
        """
        rate = self.buy_commission_rate + self.sell_commission_rate + self.transaction_tax_rate
        if include_slippage:
            rate += self.estimated_slippage_rate * 2
        return rate

    def minimum_target_return(self) -> float:
        """진입 시 목표해야 할 최소 세전 수익률 (PRD 7-2).

        ``round_trip_cost(include_slippage=True) + safety_margin``.
        settings 기본값 기준 약 0.0091 (0.91%) — PRD 의 1.2% (코스피) 대비 보수적.
        """
        return self.round_trip_cost(include_slippage=True) + self.safety_margin

    # ===== PRD 7-2 순수익률 =====

    def compute_pnl(
        self,
        buy_price: float,
        sell_price: float,
        shares: int = 1,
        slippage_rate: Optional[float] = None,
    ) -> CostBreakdown:
        """1회 매수/매도 손익 분해.

        PRD 7-2 공식:
        ::

            net_pnl_pct = (sell × (1 - sell_comm - tax) - buy × (1 + buy_comm))
                         / (buy × (1 + buy_comm))

        ``slippage_rate`` 처리:
        - ``None`` (기본): ``self.estimated_slippage_rate`` 사용. 백테스트/추정 시.
        - ``0.0``: 슬리피지 적용 X. 실거래 ``filled_price`` 가 이미 슬리피지 반영된 경우.
        - ``float``: 명시적 슬리피지 (편도) — 사후 측정값 등.

        ``slippage_rate`` 는 ``net_pnl`` 에만 영향 (양 방향 편도 × 2). PRD 공식의
        ``net_pnl_pct`` 는 슬리피지 미포함이므로 ``net_pnl`` 별도 차감.

        Args:
            buy_price: 매수 단가 (원)
            sell_price: 매도 단가 (원)
            shares: 주식 수 (양수)
            slippage_rate: 편도 슬리피지율, ``None`` 시 추정값 사용

        Returns:
            ``CostBreakdown``

        Raises:
            ValueError: buy_price/sell_price ≤ 0 또는 shares ≤ 0
        """
        if buy_price <= 0 or sell_price <= 0:
            raise ValueError(f"가격은 양수여야 합니다 (buy={buy_price}, sell={sell_price})")
        # bool 은 int subclass 라 isinstance(True, int) == True. 명시 거부.
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            raise ValueError(f"shares 는 양의 정수여야 합니다 (got {shares!r})")

        slip = self.estimated_slippage_rate if slippage_rate is None else float(slippage_rate)
        if slip < 0:
            raise ValueError(f"slippage_rate 는 비음수여야 합니다 (got {slippage_rate})")

        gross_buy = buy_price * shares
        gross_sell = sell_price * shares

        buy_comm = gross_buy * self.buy_commission_rate
        sell_comm = gross_sell * self.sell_commission_rate
        tax = gross_sell * self.transaction_tax_rate

        # 슬리피지: 매수 시 진입가 + slip%, 매도 시 청산가 - slip%. 비용 합계 ≈ (buy + sell) × slip
        slippage_cost = (gross_buy + gross_sell) * slip

        pretax_pnl = gross_sell - gross_buy
        pretax_pnl_pct = pretax_pnl / gross_buy

        # PRD 7-2 공식 — 슬리피지는 별도 처리
        prd_numerator = (
            sell_price * (1 - self.sell_commission_rate - self.transaction_tax_rate)
            - buy_price * (1 + self.buy_commission_rate)
        )
        prd_denominator = buy_price * (1 + self.buy_commission_rate)
        prd_net_pnl_pct = prd_numerator / prd_denominator

        # net_pnl(원) = PRD 비율 × gross_buy_with_commission - slippage 비용
        gross_buy_with_buy_comm = gross_buy + buy_comm
        net_pnl_no_slippage = prd_net_pnl_pct * gross_buy_with_buy_comm
        net_pnl = net_pnl_no_slippage - slippage_cost

        # 슬리피지 반영 후 최종 net_pnl_pct (분모는 동일하게 매수 비용 포함)
        net_pnl_pct = net_pnl / gross_buy_with_buy_comm

        return CostBreakdown(
            buy_price=float(buy_price),
            sell_price=float(sell_price),
            shares=int(shares),
            slippage_rate=slip,
            gross_buy_amount=float(gross_buy),
            gross_sell_amount=float(gross_sell),
            buy_commission=float(buy_comm),
            sell_commission=float(sell_comm),
            transaction_tax=float(tax),
            estimated_slippage=float(slippage_cost),
            pretax_pnl=float(pretax_pnl),
            pretax_pnl_pct=float(pretax_pnl_pct),
            net_pnl=float(net_pnl),
            net_pnl_pct=float(net_pnl_pct),
        )

    # ===== 헬퍼 =====

    def to_db_payload(self, breakdown: CostBreakdown) -> dict:
        """``CostBreakdown`` → ``candidates`` 테이블 비용 컬럼 dict.

        DB save 시 사용. 키 이름은 schema_v1 컬럼명과 1:1 매칭.
        """
        return {
            "buy_commission": breakdown.buy_commission,
            "sell_commission": breakdown.sell_commission,
            "transaction_tax": breakdown.transaction_tax,
            "estimated_slippage": breakdown.estimated_slippage,
            "net_pnl_pct": breakdown.net_pnl_pct,
        }


# 모듈 레벨 싱글톤 (kis_client.py 패턴 동일 — double-checked locking)
_engine_instance: Optional[CostSlippageEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> CostSlippageEngine:
    """기본 ``CostSlippageEngine`` 싱글톤 (settings.yaml 기반).

    Thread-safe — 동시 호출 시 단일 인스턴스 보장.
    """
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = CostSlippageEngine()
    return _engine_instance


def reset_singleton() -> None:
    """테스트용 — 싱글톤 초기화."""
    global _engine_instance
    with _engine_lock:
        _engine_instance = None
