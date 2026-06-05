"""
dynamic_gap.py - 동적 갭 기준 모듈

시장 상황(KOSPI/KOSDAQ 등락률)에 따라 갭 허용 기준을 자동 조정합니다.

로직:
- 시장 강세 (KOSPI +1% 이상): 갭 허용 완화 (±4%)
- 시장 중립 (-1% ~ +1%): 기본 설정 (±3%)
- 시장 약세 (KOSPI -1% 이하): 갭 허용 강화 (±2%)

사용법:
    from modules.morning_filter.dynamic_gap import DynamicGapCalculator
    
    calculator = DynamicGapCalculator()
    max_gap_up, max_gap_down = calculator.get_dynamic_gap()
"""

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


@dataclass
class MarketCondition:
    """시장 상태"""
    kospi_change: float = 0.0        # KOSPI 등락률 (%)
    kosdaq_change: float = 0.0       # KOSDAQ 등락률 (%)
    market_status: str = "neutral"   # bullish, neutral, bearish
    volatility: str = "normal"       # high, normal, low


@dataclass 
class DynamicGapConfig:
    """동적 갭 설정"""
    max_gap_up: float
    max_gap_down: float
    reason: str


class DynamicGapCalculator:
    """
    동적 갭 기준 계산기
    
    시장 상황에 따라 갭 허용 기준을 조정합니다.
    """
    
    def __init__(
        self,
        base_gap_up: float = None,
        base_gap_down: float = None,
        enable_dynamic: bool = True
    ):
        """
        Args:
            base_gap_up: 기본 갭상승 허용률 (%)
            base_gap_down: 기본 갭하락 허용률 (%)
            enable_dynamic: 동적 조정 활성화
        """
        self.base_gap_up = base_gap_up or settings.MAX_GAP_UP_PERCENT
        self.base_gap_down = base_gap_down or settings.MAX_GAP_DOWN_PERCENT
        self.enable_dynamic = enable_dynamic
        
        # 조정 규칙
        self.rules = {
            "bullish": {  # 강세장
                "gap_up_adjust": +1.0,    # 갭상승 허용 +1%
                "gap_down_adjust": +0.5,  # 갭하락 허용 +0.5%
            },
            "bearish": {  # 약세장
                "gap_up_adjust": -1.0,    # 갭상승 허용 -1% (더 엄격)
                "gap_down_adjust": -0.5,  # 갭하락 허용 -0.5%
            },
            "neutral": {  # 중립
                "gap_up_adjust": 0.0,
                "gap_down_adjust": 0.0,
            }
        }
        
        # KIS API (지연 로딩)
        self._kis_api = None
        self._cached_condition: Optional[MarketCondition] = None
        
        logger.info(f"📊 동적 갭 계산기 초기화 (기본: ±{self.base_gap_up}%)")
    
    @property
    def kis_api(self):
        """KIS API 지연 로딩"""
        if self._kis_api is None:
            try:
                from modules.stock_screener.kis_api import KISApi
                self._kis_api = KISApi()
            except Exception as e:
                logger.warning(f"KIS API 로딩 실패: {e}")
        return self._kis_api

    def _classify_status(self, change: float) -> Tuple[str, str]:
        """
        단일 등락률 → (market_status, volatility) 분류.
        get_market_condition(평균 기반)과 get_dynamic_gap_for_market(종목 소속시장 기반)이 공유.
        """
        if change >= 1.0:
            status = "bullish"
        elif change <= -1.0:
            status = "bearish"
        else:
            status = "neutral"

        if abs(change) >= 2.0:
            volatility = "high"
        elif abs(change) <= 0.5:
            volatility = "low"
        else:
            volatility = "normal"

        return status, volatility

    def _band_from_status(self, status: str, volatility: str) -> Tuple[float, float]:
        """market_status/volatility → (max_gap_up, max_gap_down) 밴드 계산 (단일 진실 공급원)."""
        rule = self.rules.get(status, self.rules["neutral"])
        max_gap_up = max(1.0, self.base_gap_up + rule["gap_up_adjust"])
        max_gap_down = max(1.0, self.base_gap_down + rule["gap_down_adjust"])

        # 고변동성 시장: 추가 완화
        if volatility == "high":
            max_gap_up += 0.5
            max_gap_down += 0.5

        return max_gap_up, max_gap_down
    
    def get_market_condition(self, use_cache: bool = True) -> MarketCondition:
        """
        현재 시장 상태 조회
        
        Args:
            use_cache: 캐시 사용 여부
            
        Returns:
            MarketCondition
        """
        if use_cache and self._cached_condition:
            return self._cached_condition
        
        kospi_change = 0.0
        kosdaq_change = 0.0
        
        try:
            if self.kis_api:
                # KOSPI 지수 조회 (종목코드: 0001)
                kospi_data = self.kis_api.get_index_price("0001")
                if kospi_data:
                    kospi_change = kospi_data.get("change_rate", 0.0)
                
                # KOSDAQ 지수 조회 (종목코드: 1001)
                kosdaq_data = self.kis_api.get_index_price("1001")
                if kosdaq_data:
                    kosdaq_change = kosdaq_data.get("change_rate", 0.0)
                    
        except Exception as e:
            logger.warning(f"시장 지수 조회 실패: {e}")
        
        # 시장 상태 판단 (두 지수 평균)
        avg_change = (kospi_change + kosdaq_change) / 2
        status, volatility = self._classify_status(avg_change)

        condition = MarketCondition(
            kospi_change=kospi_change,
            kosdaq_change=kosdaq_change,
            market_status=status,
            volatility=volatility
        )
        
        self._cached_condition = condition
        
        logger.info(
            f"📈 시장 상태: {status.upper()} "
            f"(KOSPI {kospi_change:+.2f}%, KOSDAQ {kosdaq_change:+.2f}%)"
        )
        
        return condition
    
    def get_dynamic_gap(
        self,
        market_condition: MarketCondition = None
    ) -> DynamicGapConfig:
        """
        동적 갭 기준 계산
        
        Args:
            market_condition: 시장 상태 (없으면 조회)
            
        Returns:
            DynamicGapConfig
        """
        if not self.enable_dynamic:
            return DynamicGapConfig(
                max_gap_up=self.base_gap_up,
                max_gap_down=self.base_gap_down,
                reason="동적 조정 비활성화"
            )
        
        # 시장 상태 조회
        if market_condition is None:
            market_condition = self.get_market_condition()
        
        status = market_condition.market_status
        max_gap_up, max_gap_down = self._band_from_status(status, market_condition.volatility)
        reason = self._get_reason(status, market_condition)

        logger.info(
            f"📊 동적 갭 기준: 상승 ±{max_gap_up}%, 하락 ±{max_gap_down}% ({reason})"
        )

        return DynamicGapConfig(
            max_gap_up=max_gap_up,
            max_gap_down=max_gap_down,
            reason=reason
        )

    def get_dynamic_gap_for_market(
        self,
        market: str,
        market_condition: MarketCondition = None
    ) -> DynamicGapConfig:
        """
        종목 소속시장(kospi/kosdaq)의 개별 지수 등락률로 갭 기준 계산.

        분열장(예: 코스피 강세 + 코스닥 약세)에서 두 지수 평균 regime의 왜곡을 제거.
        GAP_REGIME_PER_MARKET=True 일 때 morning_screener가 사용.

        Args:
            market: "kospi" 또는 "kosdaq" (classify_market() 반환값)
            market_condition: 시장 상태 (없으면 조회) — kospi_change/kosdaq_change 개별 사용

        Returns:
            DynamicGapConfig
        """
        if not self.enable_dynamic:
            return DynamicGapConfig(
                max_gap_up=self.base_gap_up,
                max_gap_down=self.base_gap_down,
                reason="동적 조정 비활성화"
            )

        if market_condition is None:
            market_condition = self.get_market_condition()

        # 종목 소속시장의 등락률만 사용
        if market == "kosdaq":
            change = market_condition.kosdaq_change
            label = "KOSDAQ"
        else:  # kospi (기본/폴백)
            change = market_condition.kospi_change
            label = "KOSPI"

        status, volatility = self._classify_status(change)
        max_gap_up, max_gap_down = self._band_from_status(status, volatility)
        reason = f"{label} {change:+.1f}% → {status} (종목소속시장 regime)"

        return DynamicGapConfig(
            max_gap_up=max_gap_up,
            max_gap_down=max_gap_down,
            reason=reason
        )
    
    def _get_reason(self, status: str, condition: MarketCondition) -> str:
        """조정 사유 생성"""
        if status == "bullish":
            return f"강세장 (KOSPI {condition.kospi_change:+.1f}%) → 허용 완화"
        elif status == "bearish":
            return f"약세장 (KOSPI {condition.kospi_change:+.1f}%) → 허용 강화"
        else:
            return "중립 → 기본 설정"
    
    def get_gap_for_stock(
        self,
        stock_code: str,
        base_gap_up: float = None,
        base_gap_down: float = None
    ) -> Tuple[float, float]:
        """
        종목별 갭 기준 (시장 + 종목 특성 반영)
        
        Args:
            stock_code: 종목코드
            base_gap_up: 기본 갭상승 허용
            base_gap_down: 기본 갭하락 허용
            
        Returns:
            (max_gap_up, max_gap_down)
        """
        # 동적 갭 기준 (시장 기반)
        config = self.get_dynamic_gap()
        
        max_gap_up = base_gap_up or config.max_gap_up
        max_gap_down = base_gap_down or config.max_gap_down
        
        # 종목별 추가 조정 (선택사항)
        # 예: 대형주는 갭이 작으므로 기준 낮춤
        # 예: 테마주는 갭이 크므로 기준 높임
        
        return max_gap_up, max_gap_down


# ===== 간편 함수 =====

def classify_market(market_name: str) -> str:
    """
    KIS 대표시장한글명(rprs_mrkt_kor_name) → "kospi" / "kosdaq" 분류.

    KIS 반환 예: "KOSPI", "KOSPI200", "KOSDAQ", "KSQ150"(코스닥150), "KOSDAQ GLOBAL", "코스닥", "KONEX" 등.
    ⚠️ 코스닥150 구성종목은 "KSQ150"(KSQ=코스닥 약어)로 반환되어 "KOSDAQ" 문자열이 없음 → "KSQ"도 코스닥으로 분류.
    불명/빈 문자열은 "kospi"로 폴백(코스피 밴드 = 기존 동작에 가까운 보수적 기본).

    Args:
        market_name: KIS rprs_mrkt_kor_name 문자열

    Returns:
        "kospi" 또는 "kosdaq"
    """
    m = (market_name or "").upper()
    if "KOSDAQ" in m or "코스닥" in m or "KSQ" in m:
        return "kosdaq"
    return "kospi"


def get_market_adjusted_gap() -> Tuple[float, float]:
    """
    시장 상황에 맞는 갭 기준 반환
    
    Returns:
        (max_gap_up, max_gap_down)
    """
    calculator = DynamicGapCalculator()
    config = calculator.get_dynamic_gap()
    return config.max_gap_up, config.max_gap_down


def is_market_bullish() -> bool:
    """시장이 강세인지 확인"""
    calculator = DynamicGapCalculator()
    condition = calculator.get_market_condition()
    return condition.market_status == "bullish"


def is_market_bearish() -> bool:
    """시장이 약세인지 확인"""
    calculator = DynamicGapCalculator()
    condition = calculator.get_market_condition()
    return condition.market_status == "bearish"


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 동적 갭 기준 테스트")
    print("=" * 60)
    
    calculator = DynamicGapCalculator()
    
    # 다양한 시장 상황 시뮬레이션
    test_conditions = [
        MarketCondition(kospi_change=+1.5, kosdaq_change=+2.0, market_status="bullish"),
        MarketCondition(kospi_change=-1.5, kosdaq_change=-2.0, market_status="bearish"),
        MarketCondition(kospi_change=+0.3, kosdaq_change=-0.2, market_status="neutral"),
        MarketCondition(kospi_change=+2.5, kosdaq_change=+3.0, market_status="bullish", volatility="high"),
    ]
    
    print("\n시장 상황별 갭 기준:\n")
    
    for condition in test_conditions:
        calculator._cached_condition = condition
        config = calculator.get_dynamic_gap(condition)
        
        print(f"  {condition.market_status.upper():8s} "
              f"(KOSPI {condition.kospi_change:+.1f}%): "
              f"갭상승 ±{config.max_gap_up:.1f}%, 갭하락 ±{config.max_gap_down:.1f}%")
    
    print("\n" + "=" * 60)
    print("✅ 동적 갭 기준 테스트 완료!")
    print("=" * 60)
