"""
volume_filter.py - 거래량 이상 감지 필터

장 초반 거래량이 비정상적으로 낮거나 높은 종목을 필터링합니다.

로직:
- 거래량 < 평소의 50%: 제외 (유동성 부족)
- 거래량 > 평소의 500%: 경고 (과열 가능)
- 정상 범위: 통과

사용법:
    from modules.morning_filter.volume_filter import VolumeFilter
    
    volume_filter = VolumeFilter()
    result = volume_filter.check(stock_code, current_volume=1000000, avg_volume=800000)
"""

import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


@dataclass
class VolumeCheckResult:
    """거래량 필터 결과"""
    passed: bool                # 통과 여부
    stock_code: str             # 종목 코드
    current_volume: int         # 현재 거래량
    avg_volume: int             # 20일 평균 거래량
    volume_ratio: float         # 거래량 비율
    warning: bool               # 경고 여부 (과열)
    reason: str                 # 통과/제외 사유


class VolumeFilter:
    """
    거래량 이상 감지 필터
    
    장 초반 거래량을 분석하여 유동성이 부족하거나
    과열된 종목을 필터링합니다.
    """
    
    def __init__(
        self,
        min_volume_ratio: float = None,
        max_volume_ratio: float = 5.0,
        time_weight: float = 1.0
    ):
        """
        Args:
            min_volume_ratio: 최소 거래량 비율 (20일 평균 대비)
            max_volume_ratio: 최대 거래량 비율 (경고 기준)
            time_weight: 시간 가중치 (장 초반은 거래량이 적으므로 조정)
        """
        self.min_volume_ratio = min_volume_ratio or settings.MIN_VOLUME_RATIO
        self.max_volume_ratio = max_volume_ratio
        self.time_weight = time_weight
        
        logger.info(
            f"📊 거래량 필터 초기화: 최소 {self.min_volume_ratio*100:.0f}%, "
            f"경고 {self.max_volume_ratio*100:.0f}%"
        )
    
    def check(
        self,
        stock_code: str,
        current_volume: int,
        avg_volume: int,
        stock_name: str = "",
        trading_minutes: int = 20
    ) -> VolumeCheckResult:
        """
        거래량 체크
        
        Args:
            stock_code: 종목 코드
            current_volume: 현재 거래량 (당일 누적)
            avg_volume: 20일 평균 거래량 (일일 전체)
            stock_name: 종목명 (로깅용)
            trading_minutes: 장 시작 후 경과 시간 (분)
            
        Returns:
            VolumeCheckResult: 필터 결과
        """
        name_str = f"[{stock_name}]" if stock_name else f"[{stock_code}]"
        
        if avg_volume <= 0:
            return VolumeCheckResult(
                passed=True,  # 데이터 없으면 일단 통과
                stock_code=stock_code,
                current_volume=current_volume,
                avg_volume=avg_volume,
                volume_ratio=0.0,
                warning=False,
                reason="평균 거래량 데이터 없음"
            )
        
        # 시간 가중치 적용
        # 장 초반 20분이면 전체 거래 시간(390분)의 약 5%
        # 따라서 현재 거래량은 평균의 5%가 정상
        # 예: 390분 기준, 20분 경과 → 예상 거래량 = 평균 * (20/390)
        TOTAL_TRADING_MINUTES = 390  # 09:00 ~ 15:30
        expected_volume = avg_volume * (trading_minutes / TOTAL_TRADING_MINUTES)
        
        if expected_volume <= 0:
            expected_volume = avg_volume * 0.05  # 최소 5%
        
        # 현재 거래량 비율 (예상 대비)
        volume_ratio = current_volume / expected_volume if expected_volume > 0 else 0
        
        warning = False
        
        # 최소 거래량 체크
        if volume_ratio < self.min_volume_ratio:
            logger.info(
                f"   ❌ {name_str} 거래량 부족 ({volume_ratio:.1%} < {self.min_volume_ratio:.0%}) - 제외"
            )
            return VolumeCheckResult(
                passed=False,
                stock_code=stock_code,
                current_volume=current_volume,
                avg_volume=avg_volume,
                volume_ratio=volume_ratio,
                warning=False,
                reason=f"거래량 부족 ({volume_ratio:.0%})"
            )
        
        # 과열 경고 체크
        if volume_ratio > self.max_volume_ratio:
            logger.warning(
                f"{name_str} 거래량 과열 ({volume_ratio:.1%} > {self.max_volume_ratio:.0%}) - 경고"
            )
            warning = True
        
        # 통과
        logger.info(f"   ✅ {name_str} 거래량 정상 ({volume_ratio:.0%}) - 통과")
        return VolumeCheckResult(
            passed=True,
            stock_code=stock_code,
            current_volume=current_volume,
            avg_volume=avg_volume,
            volume_ratio=volume_ratio,
            warning=warning,
            reason=f"거래량 정상 ({volume_ratio:.0%})" + (" ⚠️과열" if warning else "")
        )
    
    def check_multiple(
        self,
        stocks: list[dict],
        trading_minutes: int = 20
    ) -> tuple[list[dict], list[dict]]:
        """
        여러 종목 일괄 체크
        
        Args:
            stocks: 종목 리스트
                [{'code': '005930', 'name': '삼성전자', 
                  'current_volume': 1000000, 'avg_volume': 800000}, ...]
            trading_minutes: 장 시작 후 경과 시간 (분)
        
        Returns:
            (통과 종목 리스트, 제외 종목 리스트)
        """
        passed = []
        excluded = []
        
        for stock in stocks:
            result = self.check(
                stock_code=stock.get("code", ""),
                current_volume=stock.get("current_volume", stock.get("volume", 0)),
                avg_volume=stock.get("avg_volume", stock.get("volume_20_avg", 0)),
                stock_name=stock.get("name", ""),
                trading_minutes=trading_minutes
            )
            
            # 결과 정보 추가
            stock["volume_result"] = result
            stock["volume_ratio"] = result.volume_ratio
            stock["volume_warning"] = result.warning
            
            if result.passed:
                passed.append(stock)
            else:
                excluded.append(stock)
        
        logger.info(
            f"📊 거래량 필터 결과: {len(passed)}개 통과, {len(excluded)}개 제외"
        )
        
        return passed, excluded


def check_volume_conditions(
    stock_code: str,
    current_volume: int,
    avg_volume: int,
    trading_minutes: int = 20
) -> tuple[bool, float, str]:
    """
    간편 거래량 체크 함수
    
    Args:
        stock_code: 종목 코드
        current_volume: 현재 거래량
        avg_volume: 20일 평균 거래량
        trading_minutes: 장 시작 후 경과 시간 (분)
        
    Returns:
        (통과 여부, 거래량 비율, 사유)
        
    Example:
        >>> passed, ratio, reason = check_volume_conditions("005930", 100000, 2000000, 20)
        >>> print(f"통과: {passed}, 비율: {ratio:.0%}")
    """
    volume_filter = VolumeFilter()
    result = volume_filter.check(
        stock_code, current_volume, avg_volume, 
        trading_minutes=trading_minutes
    )
    return result.passed, result.volume_ratio, result.reason


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 거래량 필터 테스트")
    print("=" * 60)
    
    # 테스트 케이스
    # 20일 평균 거래량 기준, 장 시작 20분 후
    # 예상 거래량 = 평균 * (20/390) ≈ 5.1%
    test_cases = [
        {"code": "005930", "name": "삼성전자", 
         "current_volume": 1_000_000, "avg_volume": 10_000_000},  # 10% → 약 2배 (정상)
        {"code": "000660", "name": "SK하이닉스", 
         "current_volume": 50_000, "avg_volume": 5_000_000},  # 1% → 약 0.2배 (부족)
        {"code": "051910", "name": "LG에너지", 
         "current_volume": 500_000, "avg_volume": 1_000_000},  # 50% → 약 10배 (과열)
        {"code": "006400", "name": "삼성SDI", 
         "current_volume": 100_000, "avg_volume": 2_000_000},  # 5% → 약 1배 (정상)
    ]
    
    volume_filter = VolumeFilter()
    passed, excluded = volume_filter.check_multiple(test_cases, trading_minutes=20)
    
    print("\n✅ 통과 종목:")
    for stock in passed:
        warning = " ⚠️과열주의" if stock['volume_warning'] else ""
        print(f"   - {stock['name']}: {stock['volume_ratio']:.0%}{warning}")
    
    print("\n❌ 제외 종목:")
    for stock in excluded:
        print(f"   - {stock['name']}: {stock['volume_result'].reason}")
    
    print("\n" + "=" * 60)
