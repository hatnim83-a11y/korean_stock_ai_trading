"""
시장 위기 방어 모듈 (Market Crisis Guard)

코스피/코스닥 지수 등락률을 기반으로 시장 전체 상황을 판단하여
매수 진입을 스킵/지연/축소하는 가드 로직.

판단 기준:
  CRISIS  — 양쪽 모두 CRISIS_DROP 이하 → 매수 전면 스킵
  DANGER  — 양쪽 동시 DANGER_DROP 이하 또는 한쪽 CRISIS_DROP 이하 → 지연 재체크
  CAUTION — 한쪽만 DANGER_DROP 이하 → 투자금 축소 진입
  NORMAL  — 정상 매수
"""

import logging
from enum import Enum
from typing import Tuple

from config import settings
from modules.stock_screener.kis_api import KISApi

logger = logging.getLogger(__name__)


class MarketStatus(Enum):
    NORMAL = "normal"
    CAUTION = "caution"
    DANGER = "danger"
    CRISIS = "crisis"


class MarketGuard:
    """코스피/코스닥 지수 기반 시장 위기 판정기"""

    def __init__(self):
        self.kis = KISApi()

    def check(self) -> Tuple[MarketStatus, dict]:
        """코스피/코스닥 전일 대비 등락률 조회 → (상태, 상세정보) 반환

        Returns:
            (MarketStatus, {kospi_rate, kosdaq_rate, reason})
        """
        kospi = self.kis.get_index_price("0001")
        kosdaq = self.kis.get_index_price("1001")

        kospi_rate = kospi["change_rate"] if kospi else None
        kosdaq_rate = kosdaq["change_rate"] if kosdaq else None
        info = {"kospi_rate": kospi_rate, "kosdaq_rate": kosdaq_rate}

        # 양쪽 API 모두 실패 → 안전 모드로 정상 진행
        if kospi_rate is None and kosdaq_rate is None:
            logger.warning("[MarketGuard] 코스피/코스닥 지수 조회 모두 실패 — 안전 모드 NORMAL")
            info["reason"] = "API 실패 — 안전 모드로 정상 진행"
            return MarketStatus.NORMAL, info

        # 한쪽만 실패 → 실패한 쪽은 0%로 간주
        if kospi_rate is None:
            logger.warning("[MarketGuard] 코스피 지수 조회 실패 — 0%로 간주")
            kospi_rate = 0.0
            info["kospi_rate"] = kospi_rate
        if kosdaq_rate is None:
            logger.warning("[MarketGuard] 코스닥 지수 조회 실패 — 0%로 간주")
            kosdaq_rate = 0.0
            info["kosdaq_rate"] = kosdaq_rate

        crisis = settings.MARKET_GUARD_CRISIS_DROP     # -2.0
        danger = settings.MARKET_GUARD_DANGER_DROP     # -1.0
        caution = settings.MARKET_GUARD_CAUTION_DROP   # -1.0 (독립 조정 가능)

        # CRISIS: 양쪽 모두 -2% 이하
        if kospi_rate <= crisis and kosdaq_rate <= crisis:
            info["reason"] = "양시장 동반 폭락"
            logger.warning(f"[MarketGuard] CRISIS — KOSPI {kospi_rate:+.2f}% / KOSDAQ {kosdaq_rate:+.2f}%")
            return MarketStatus.CRISIS, info

        # DANGER: 양쪽 동시 -1% 이하 OR 한쪽만 -2% 이하
        if (kospi_rate <= danger and kosdaq_rate <= danger) or \
           kospi_rate <= crisis or kosdaq_rate <= crisis:
            info["reason"] = "시장 급락 — 지연 재체크 필요"
            logger.warning(f"[MarketGuard] DANGER — KOSPI {kospi_rate:+.2f}% / KOSDAQ {kosdaq_rate:+.2f}%")
            return MarketStatus.DANGER, info

        # CAUTION: 한쪽만 caution 기준 이하
        if kospi_rate <= caution or kosdaq_rate <= caution:
            info["reason"] = "한쪽 시장 약세"
            logger.info(f"[MarketGuard] CAUTION — KOSPI {kospi_rate:+.2f}% / KOSDAQ {kosdaq_rate:+.2f}%")
            return MarketStatus.CAUTION, info

        # NORMAL
        info["reason"] = "시장 정상"
        logger.info(f"[MarketGuard] NORMAL — KOSPI {kospi_rate:+.2f}% / KOSDAQ {kosdaq_rate:+.2f}%")
        return MarketStatus.NORMAL, info
