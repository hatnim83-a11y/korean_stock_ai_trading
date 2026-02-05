"""
gap_filter.py - 시초가 갭 필터

장 시작 시 시초가가 전일 종가 대비 과도하게 높거나 낮은 종목을 제외합니다.

로직:
- 갭상승 +3% 초과: 제외 (추격매수 위험)
- 갭하락 -3% 초과: 제외 (약세 신호)
- 적정 범위 내: 통과

사용법:
    from modules.morning_filter.gap_filter import GapFilter
    
    gap_filter = GapFilter()
    result = gap_filter.check(stock_code, prev_close=75000, open_price=77000)
"""

import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


@dataclass
class GapCheckResult:
    """갭 필터 결과"""
    passed: bool                # 통과 여부
    stock_code: str             # 종목 코드
    prev_close: float           # 전일 종가
    open_price: float           # 시초가
    gap_percent: float          # 갭 비율 (%)
    reason: str                 # 통과/제외 사유


class GapFilter:
    """
    시초가 갭 필터
    
    장 시작 시 시초가와 전일 종가를 비교하여
    과도한 갭이 있는 종목을 필터링합니다.
    """
    
    def __init__(
        self,
        max_gap_up: float = None,
        max_gap_down: float = None
    ):
        """
        Args:
            max_gap_up: 허용 최대 갭상승률 (%, 기본값: config 설정)
            max_gap_down: 허용 최대 갭하락률 (%, 기본값: config 설정)
        """
        self.max_gap_up = max_gap_up or settings.MAX_GAP_UP_PERCENT
        self.max_gap_down = max_gap_down or settings.MAX_GAP_DOWN_PERCENT
        
        logger.info(
            f"📊 갭 필터 초기화: 상승 ±{self.max_gap_up}%, 하락 ±{self.max_gap_down}%"
        )
    
    def check(
        self,
        stock_code: str,
        prev_close: float,
        open_price: float,
        stock_name: str = ""
    ) -> GapCheckResult:
        """
        시초가 갭 체크
        
        Args:
            stock_code: 종목 코드
            prev_close: 전일 종가
            open_price: 시초가 (또는 현재가)
            stock_name: 종목명 (로깅용)
            
        Returns:
            GapCheckResult: 필터 결과
        """
        if prev_close <= 0:
            return GapCheckResult(
                passed=False,
                stock_code=stock_code,
                prev_close=prev_close,
                open_price=open_price,
                gap_percent=0.0,
                reason="전일 종가 데이터 없음"
            )
        
        # 갭 비율 계산
        gap_percent = ((open_price - prev_close) / prev_close) * 100
        
        name_str = f"[{stock_name}]" if stock_name else f"[{stock_code}]"
        
        # 갭상승 체크
        if gap_percent > self.max_gap_up:
            logger.debug(
                f"{name_str} 갭상승 초과 ({gap_percent:+.2f}% > +{self.max_gap_up}%) - 제외"
            )
            return GapCheckResult(
                passed=False,
                stock_code=stock_code,
                prev_close=prev_close,
                open_price=open_price,
                gap_percent=gap_percent,
                reason=f"갭상승 초과 ({gap_percent:+.2f}%)"
            )
        
        # 갭하락 체크
        if gap_percent < -self.max_gap_down:
            logger.debug(
                f"{name_str} 갭하락 초과 ({gap_percent:+.2f}% < -{self.max_gap_down}%) - 제외"
            )
            return GapCheckResult(
                passed=False,
                stock_code=stock_code,
                prev_close=prev_close,
                open_price=open_price,
                gap_percent=gap_percent,
                reason=f"갭하락 초과 ({gap_percent:+.2f}%)"
            )
        
        # 통과
        logger.debug(f"{name_str} 갭 범위 내 ({gap_percent:+.2f}%) - 통과")
        return GapCheckResult(
            passed=True,
            stock_code=stock_code,
            prev_close=prev_close,
            open_price=open_price,
            gap_percent=gap_percent,
            reason=f"정상 범위 ({gap_percent:+.2f}%)"
        )
    
    def check_multiple(
        self,
        stocks: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        여러 종목 일괄 체크
        
        Args:
            stocks: 종목 리스트
                [{'code': '005930', 'name': '삼성전자', 
                  'prev_close': 75000, 'open_price': 77000}, ...]
        
        Returns:
            (통과 종목 리스트, 제외 종목 리스트)
        """
        passed = []
        excluded = []
        
        for stock in stocks:
            result = self.check(
                stock_code=stock.get("code", ""),
                prev_close=stock.get("prev_close", 0),
                open_price=stock.get("open_price", stock.get("current_price", 0)),
                stock_name=stock.get("name", "")
            )
            
            # 결과 정보 추가
            stock["gap_result"] = result
            stock["gap_percent"] = result.gap_percent
            
            if result.passed:
                passed.append(stock)
            else:
                excluded.append(stock)
        
        logger.info(
            f"📊 갭 필터 결과: {len(passed)}개 통과, {len(excluded)}개 제외"
        )
        
        return passed, excluded


def check_gap_conditions(
    stock_code: str,
    prev_close: float,
    open_price: float,
    max_gap_up: float = None,
    max_gap_down: float = None
) -> tuple[bool, float, str]:
    """
    간편 갭 체크 함수
    
    Args:
        stock_code: 종목 코드
        prev_close: 전일 종가
        open_price: 시초가
        max_gap_up: 허용 최대 갭상승률 (%)
        max_gap_down: 허용 최대 갭하락률 (%)
        
    Returns:
        (통과 여부, 갭 비율, 사유)
        
    Example:
        >>> passed, gap, reason = check_gap_conditions("005930", 75000, 77000)
        >>> print(f"통과: {passed}, 갭: {gap:+.2f}%")
        통과: True, 갭: +2.67%
    """
    gap_filter = GapFilter(max_gap_up, max_gap_down)
    result = gap_filter.check(stock_code, prev_close, open_price)
    return result.passed, result.gap_percent, result.reason


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 시초가 갭 필터 테스트")
    print("=" * 60)
    
    # 테스트 케이스
    test_cases = [
        {"code": "005930", "name": "삼성전자", "prev_close": 75000, "open_price": 76500},  # +2%
        {"code": "000660", "name": "SK하이닉스", "prev_close": 180000, "open_price": 188000},  # +4.4%
        {"code": "051910", "name": "LG에너지", "prev_close": 420000, "open_price": 410000},  # -2.4%
        {"code": "006400", "name": "삼성SDI", "prev_close": 320000, "open_price": 305000},  # -4.7%
        {"code": "035420", "name": "NAVER", "prev_close": 220000, "open_price": 221000},  # +0.45%
    ]
    
    gap_filter = GapFilter()
    passed, excluded = gap_filter.check_multiple(test_cases)
    
    print("\n✅ 통과 종목:")
    for stock in passed:
        print(f"   - {stock['name']}: {stock['gap_percent']:+.2f}%")
    
    print("\n❌ 제외 종목:")
    for stock in excluded:
        print(f"   - {stock['name']}: {stock['gap_percent']:+.2f}% ({stock['gap_result'].reason})")
    
    print("\n" + "=" * 60)
