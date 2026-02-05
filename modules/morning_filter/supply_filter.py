"""
supply_filter.py - 당일 실시간 수급 필터

장 초반 외국인/기관의 매수/매도 동향을 확인하여 필터링합니다.

로직:
- 외국인 + 기관 합산 순매수 > 0: 통과
- 외국인 + 기관 합산 순매도: 제외
- 선택적: 외국인 필수, 기관 필수

사용법:
    from modules.morning_filter.supply_filter import SupplyFilter
    
    supply_filter = SupplyFilter()
    result = supply_filter.check(stock_code)
"""

import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
import time

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


@dataclass
class SupplyCheckResult:
    """수급 필터 결과"""
    passed: bool                    # 통과 여부
    stock_code: str                 # 종목 코드
    foreign_net_buy: int            # 외국인 순매수 (원)
    institution_net_buy: int        # 기관 순매수 (원)
    total_net_buy: int              # 합산 순매수 (원)
    reason: str                     # 통과/제외 사유


class SupplyFilter:
    """
    당일 실시간 수급 필터
    
    장 초반 외국인/기관의 순매수 동향을 확인합니다.
    """
    
    def __init__(
        self,
        min_net_buy: int = None,
        require_foreign: bool = None,
        require_institution: bool = None
    ):
        """
        Args:
            min_net_buy: 최소 합산 순매수 금액 (원)
            require_foreign: 외국인 순매수 필수 여부
            require_institution: 기관 순매수 필수 여부
        """
        self.min_net_buy = min_net_buy if min_net_buy is not None else settings.MIN_MORNING_NET_BUY
        self.require_foreign = require_foreign if require_foreign is not None else settings.REQUIRE_FOREIGN_BUY
        self.require_institution = require_institution if require_institution is not None else settings.REQUIRE_INSTITUTION_BUY
        
        # KIS API 클라이언트 (지연 로딩)
        self._kis_api = None
        
        logger.info(
            f"📊 수급 필터 초기화: 최소 순매수 {self.min_net_buy:,}원, "
            f"외국인필수={self.require_foreign}, 기관필수={self.require_institution}"
        )
    
    @property
    def kis_api(self):
        """KIS API 지연 로딩"""
        if self._kis_api is None:
            from modules.stock_screener.kis_api import KISApi
            self._kis_api = KISApi()
        return self._kis_api
    
    def check(
        self,
        stock_code: str,
        stock_name: str = "",
        foreign_net_buy: int = None,
        institution_net_buy: int = None
    ) -> SupplyCheckResult:
        """
        당일 수급 체크
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목명 (로깅용)
            foreign_net_buy: 외국인 순매수 (직접 제공 시)
            institution_net_buy: 기관 순매수 (직접 제공 시)
            
        Returns:
            SupplyCheckResult: 필터 결과
        """
        name_str = f"[{stock_name}]" if stock_name else f"[{stock_code}]"
        
        # 수급 데이터 가져오기 (제공되지 않은 경우 API 호출)
        if foreign_net_buy is None or institution_net_buy is None:
            try:
                supply_data = self.kis_api.get_investor_trading(stock_code)
                if supply_data:
                    foreign_net_buy = supply_data.get("foreign_net_buy", 0)
                    institution_net_buy = supply_data.get("institution_net_buy", 0)
                else:
                    foreign_net_buy = 0
                    institution_net_buy = 0
            except Exception as e:
                logger.warning(f"{name_str} 수급 조회 실패: {e}")
                foreign_net_buy = 0
                institution_net_buy = 0
        
        # 합산 순매수
        total_net_buy = foreign_net_buy + institution_net_buy
        
        # 외국인 필수 체크
        if self.require_foreign and foreign_net_buy <= 0:
            logger.debug(f"{name_str} 외국인 순매수 없음 - 제외")
            return SupplyCheckResult(
                passed=False,
                stock_code=stock_code,
                foreign_net_buy=foreign_net_buy,
                institution_net_buy=institution_net_buy,
                total_net_buy=total_net_buy,
                reason=f"외국인 순매도 ({foreign_net_buy:,}원)"
            )
        
        # 기관 필수 체크
        if self.require_institution and institution_net_buy <= 0:
            logger.debug(f"{name_str} 기관 순매수 없음 - 제외")
            return SupplyCheckResult(
                passed=False,
                stock_code=stock_code,
                foreign_net_buy=foreign_net_buy,
                institution_net_buy=institution_net_buy,
                total_net_buy=total_net_buy,
                reason=f"기관 순매도 ({institution_net_buy:,}원)"
            )
        
        # 합산 순매수 체크
        if total_net_buy < self.min_net_buy:
            logger.debug(f"{name_str} 합산 순매도 - 제외")
            return SupplyCheckResult(
                passed=False,
                stock_code=stock_code,
                foreign_net_buy=foreign_net_buy,
                institution_net_buy=institution_net_buy,
                total_net_buy=total_net_buy,
                reason=f"순매도 ({total_net_buy:,}원)"
            )
        
        # 통과
        logger.debug(
            f"{name_str} 수급 양호 (외국인 {foreign_net_buy/100_000_000:.1f}억, "
            f"기관 {institution_net_buy/100_000_000:.1f}억) - 통과"
        )
        return SupplyCheckResult(
            passed=True,
            stock_code=stock_code,
            foreign_net_buy=foreign_net_buy,
            institution_net_buy=institution_net_buy,
            total_net_buy=total_net_buy,
            reason=f"수급 양호 (합산 {total_net_buy/100_000_000:.1f}억원)"
        )
    
    def check_multiple(
        self,
        stocks: list[dict],
        delay: float = 0.11
    ) -> tuple[list[dict], list[dict]]:
        """
        여러 종목 일괄 체크 (API 호출 포함)
        
        Args:
            stocks: 종목 리스트
            delay: API 호출 간 대기 시간 (초)
        
        Returns:
            (통과 종목 리스트, 제외 종목 리스트)
        """
        passed = []
        excluded = []
        
        for stock in stocks:
            result = self.check(
                stock_code=stock.get("code", ""),
                stock_name=stock.get("name", ""),
                foreign_net_buy=stock.get("foreign_net_buy"),
                institution_net_buy=stock.get("institution_net_buy")
            )
            
            # 결과 정보 추가
            stock["supply_result"] = result
            stock["morning_foreign_net"] = result.foreign_net_buy
            stock["morning_institution_net"] = result.institution_net_buy
            stock["morning_total_net"] = result.total_net_buy
            
            if result.passed:
                passed.append(stock)
            else:
                excluded.append(stock)
            
            # API 호출 제한
            if delay > 0:
                time.sleep(delay)
        
        logger.info(
            f"📊 수급 필터 결과: {len(passed)}개 통과, {len(excluded)}개 제외"
        )
        
        return passed, excluded


def check_realtime_supply(
    stock_code: str,
    min_net_buy: int = 0
) -> tuple[bool, int, int, str]:
    """
    간편 수급 체크 함수
    
    Args:
        stock_code: 종목 코드
        min_net_buy: 최소 합산 순매수 금액 (원)
        
    Returns:
        (통과 여부, 외국인 순매수, 기관 순매수, 사유)
        
    Example:
        >>> passed, foreign, inst, reason = check_realtime_supply("005930")
        >>> print(f"통과: {passed}, 외국인: {foreign/1e8:.1f}억")
    """
    supply_filter = SupplyFilter(min_net_buy=min_net_buy)
    result = supply_filter.check(stock_code)
    return (
        result.passed,
        result.foreign_net_buy,
        result.institution_net_buy,
        result.reason
    )


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 당일 수급 필터 테스트")
    print("=" * 60)
    
    # 테스트 케이스 (모의 데이터)
    test_cases = [
        {"code": "005930", "name": "삼성전자", 
         "foreign_net_buy": 50_000_000_000, "institution_net_buy": 30_000_000_000},  # 외국인+기관 매수
        {"code": "000660", "name": "SK하이닉스", 
         "foreign_net_buy": -20_000_000_000, "institution_net_buy": 10_000_000_000},  # 외국인 매도, 기관 매수
        {"code": "051910", "name": "LG에너지", 
         "foreign_net_buy": -30_000_000_000, "institution_net_buy": -10_000_000_000},  # 둘 다 매도
        {"code": "006400", "name": "삼성SDI", 
         "foreign_net_buy": 5_000_000_000, "institution_net_buy": -3_000_000_000},  # 합산 매수
    ]
    
    supply_filter = SupplyFilter()
    passed, excluded = supply_filter.check_multiple(test_cases, delay=0)
    
    print("\n✅ 통과 종목:")
    for stock in passed:
        print(f"   - {stock['name']}: 외국인 {stock['morning_foreign_net']/1e8:.1f}억, "
              f"기관 {stock['morning_institution_net']/1e8:.1f}억")
    
    print("\n❌ 제외 종목:")
    for stock in excluded:
        print(f"   - {stock['name']}: {stock['supply_result'].reason}")
    
    print("\n" + "=" * 60)
