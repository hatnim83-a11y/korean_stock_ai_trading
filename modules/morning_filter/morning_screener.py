"""
morning_screener.py - 장 초반 통합 스크리너

08:30 사전 분석으로 선정된 후보 종목에 대해
09:00-09:25 실시간 관찰 후 최종 매수 대상을 선정합니다.

프로세스:
1. 08:30 - 후보 종목 10-15개 선정 (기존 분석)
2. 09:00 - WebSocket 실시간 시세 구독 시작
3. 09:00-09:20 - 실시간 데이터 수집
4. 09:20 - 4가지 필터 적용
   - 시초가 갭 필터
   - 당일 수급 필터
   - 거래량 필터
   - 체결 강도 필터 (NEW)
5. 09:25 - 최종 매수 대상 확정

사용법:
    from modules.morning_filter import MorningScreener
    
    screener = MorningScreener()
    final_stocks = await screener.run_observation(candidates)
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, time as dtime
from typing import Optional
from dataclasses import dataclass, field
import time

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings

try:
    from .gap_filter import GapFilter
    from .supply_filter import SupplyFilter
    from .volume_filter import VolumeFilter
    from .realtime_monitor import StrengthFilter
    from .dynamic_gap import DynamicGapCalculator
except ImportError:
    from gap_filter import GapFilter
    from supply_filter import SupplyFilter
    from volume_filter import VolumeFilter
    from realtime_monitor import StrengthFilter
    from dynamic_gap import DynamicGapCalculator


@dataclass
class MorningScreenResult:
    """장 초반 스크리닝 결과"""
    success: bool                           # 성공 여부
    start_time: datetime                    # 시작 시간
    end_time: datetime                      # 종료 시간
    initial_count: int                      # 초기 후보 수
    final_count: int                        # 최종 선정 수
    passed_stocks: list = field(default_factory=list)     # 통과 종목
    excluded_stocks: list = field(default_factory=list)   # 제외 종목
    gap_excluded: int = 0                   # 갭 필터 제외 수
    supply_excluded: int = 0                # 수급 필터 제외 수
    volume_excluded: int = 0                # 거래량 필터 제외 수
    strength_excluded: int = 0              # 체결 강도 필터 제외 수


class MorningScreener:
    """
    장 초반 통합 스크리너
    
    후보 종목에 대해 실시간 데이터를 수집하고
    4가지 필터를 적용하여 최종 매수 대상을 선정합니다.
    
    필터:
    1. 시초가 갭 필터 - ±3% 초과 제외
    2. 당일 수급 필터 - 순매도 종목 제외
    3. 거래량 필터 - 평소 50% 미만 제외
    4. 체결 강도 필터 - 매도세 우위 제외
    """
    
    def __init__(
        self,
        enable_gap_filter: bool = True,
        enable_supply_filter: bool = True,
        enable_volume_filter: bool = True,
        enable_strength_filter: bool = True,
        enable_dynamic_gap: bool = True,
        observation_minutes: int = None
    ):
        """
        Args:
            enable_gap_filter: 갭 필터 활성화
            enable_supply_filter: 수급 필터 활성화
            enable_volume_filter: 거래량 필터 활성화
            enable_strength_filter: 체결 강도 필터 활성화
            enable_dynamic_gap: 동적 갭 기준 활성화 (시장 상황 반영)
            observation_minutes: 관찰 시간 (분)
        """
        self.enable_gap_filter = enable_gap_filter
        self.enable_supply_filter = enable_supply_filter
        self.enable_volume_filter = enable_volume_filter
        self.enable_strength_filter = enable_strength_filter
        self.enable_dynamic_gap = enable_dynamic_gap
        self.observation_minutes = observation_minutes or settings.MORNING_OBSERVATION_MINUTES
        
        # 동적 갭 계산기
        self.dynamic_gap_calculator = DynamicGapCalculator() if enable_dynamic_gap else None
        
        # 필터 인스턴스 (갭 필터는 동적으로 초기화)
        self.gap_filter = None  # 동적 갭 사용 시 나중에 초기화
        if enable_gap_filter and not enable_dynamic_gap:
            self.gap_filter = GapFilter()
            
        self.supply_filter = SupplyFilter() if enable_supply_filter else None
        self.volume_filter = VolumeFilter() if enable_volume_filter else None
        self.strength_filter = StrengthFilter(min_strength=45.0) if enable_strength_filter else None
        
        # KIS API (지연 로딩)
        self._kis_api = None
        
        logger.info(
            f"🔍 장 초반 스크리너 초기화 (관찰 {self.observation_minutes}분)\n"
            f"   - 갭 필터: {'ON' if enable_gap_filter else 'OFF'}"
            f" (동적: {'ON' if enable_dynamic_gap else 'OFF'})\n"
            f"   - 수급 필터: {'ON' if enable_supply_filter else 'OFF'}\n"
            f"   - 거래량 필터: {'ON' if enable_volume_filter else 'OFF'}\n"
            f"   - 체결 강도 필터: {'ON' if enable_strength_filter else 'OFF'}"
        )
    
    @property
    def kis_api(self):
        """KIS API 지연 로딩"""
        if self._kis_api is None:
            from modules.stock_screener.kis_api import KISApi
            self._kis_api = KISApi()
        return self._kis_api
    
    def _fetch_realtime_data(self, stocks: list[dict]) -> list[dict]:
        """
        실시간 데이터 수집
        
        Args:
            stocks: 후보 종목 리스트
            
        Returns:
            실시간 데이터가 추가된 종목 리스트
        """
        logger.info(f"📡 {len(stocks)}개 종목 실시간 데이터 수집 중...")
        
        for i, stock in enumerate(stocks):
            stock_code = stock.get("code", "")
            stock_name = stock.get("name", "")
            
            try:
                # 현재가 조회
                price_data = self.kis_api.get_current_price(stock_code)
                if price_data:
                    stock["current_price"] = price_data.get("price", 0)
                    stock["open_price"] = price_data.get("open_price", stock["current_price"])
                    stock["prev_close"] = price_data.get("prev_close", stock.get("prev_close", 0))
                    stock["current_volume"] = price_data.get("volume", 0)
                    stock["change_rate"] = price_data.get("change_rate", 0)
                
                # 수급 조회
                supply_data = self.kis_api.get_investor_trading(stock_code)
                if supply_data:
                    stock["foreign_net_buy"] = supply_data.get("foreign_net_buy", 0)
                    stock["institution_net_buy"] = supply_data.get("institution_net_buy", 0)
                
                logger.debug(f"[{stock_name}] 데이터 수집 완료")
                
            except Exception as e:
                logger.warning(f"[{stock_name}] 데이터 수집 실패: {e}")
            
            # API 호출 제한
            time.sleep(settings.KIS_API_DELAY)
            
            # 진행률 표시
            if (i + 1) % 5 == 0:
                logger.info(f"   진행: {i + 1}/{len(stocks)}")
        
        logger.info(f"✅ {len(stocks)}개 종목 데이터 수집 완료")
        return stocks
    
    def filter_candidates(
        self,
        candidates: list[dict],
        trading_minutes: int = 20
    ) -> MorningScreenResult:
        """
        후보 종목 필터링 (동기)
        
        Args:
            candidates: 후보 종목 리스트
            trading_minutes: 장 시작 후 경과 시간 (분)
            
        Returns:
            MorningScreenResult: 스크리닝 결과
        """
        start_time = datetime.now()
        initial_count = len(candidates)
        
        logger.info("=" * 60)
        logger.info(f"🔍 장 초반 필터링 시작 (후보 {initial_count}개)")
        logger.info("=" * 60)
        
        gap_excluded = 0
        supply_excluded = 0
        volume_excluded = 0
        strength_excluded = 0
        all_excluded = []
        
        current_stocks = candidates.copy()
        
        # 1. 시초가 갭 필터 (동적 갭 적용)
        if self.enable_gap_filter and current_stocks:
            logger.info("\n📊 Step 1: 시초가 갭 필터")
            
            # 동적 갭 기준 계산
            if self.enable_dynamic_gap and self.dynamic_gap_calculator:
                gap_config = self.dynamic_gap_calculator.get_dynamic_gap()
                self.gap_filter = GapFilter(
                    max_gap_up=gap_config.max_gap_up,
                    max_gap_down=gap_config.max_gap_down
                )
                logger.info(f"   동적 기준 적용: ±{gap_config.max_gap_up:.1f}% ({gap_config.reason})")
            elif not self.gap_filter:
                self.gap_filter = GapFilter()
            
            passed, excluded = self.gap_filter.check_multiple(current_stocks)
            gap_excluded = len(excluded)
            all_excluded.extend(excluded)
            current_stocks = passed
            logger.info(f"   결과: {len(passed)}개 통과, {gap_excluded}개 제외")
        
        # 2. 당일 수급 필터
        if self.supply_filter and current_stocks:
            logger.info("\n📊 Step 2: 당일 수급 필터")
            passed, excluded = self.supply_filter.check_multiple(current_stocks, delay=0)
            supply_excluded = len(excluded)
            all_excluded.extend(excluded)
            current_stocks = passed
            logger.info(f"   결과: {len(passed)}개 통과, {supply_excluded}개 제외")
        
        # 3. 거래량 필터
        if self.volume_filter and current_stocks:
            logger.info("\n📊 Step 3: 거래량 필터")
            passed, excluded = self.volume_filter.check_multiple(
                current_stocks, trading_minutes=trading_minutes
            )
            volume_excluded = len(excluded)
            all_excluded.extend(excluded)
            current_stocks = passed
            logger.info(f"   결과: {len(passed)}개 통과, {volume_excluded}개 제외")
        
        # 4. 체결 강도 필터
        if self.strength_filter and current_stocks:
            logger.info("\n💪 Step 4: 체결 강도 필터")
            passed, excluded = self.strength_filter.check_multiple(current_stocks)
            strength_excluded = len(excluded)
            all_excluded.extend(excluded)
            current_stocks = passed
            logger.info(f"   결과: {len(passed)}개 통과, {strength_excluded}개 제외")
        
        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds()
        
        # 결과 로깅
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ 장 초반 필터링 완료 ({elapsed:.1f}초)")
        logger.info(f"   초기 후보: {initial_count}개")
        logger.info(f"   최종 선정: {len(current_stocks)}개")
        logger.info(f"   제외 합계: {len(all_excluded)}개")
        logger.info(f"     - 갭 필터: {gap_excluded}개")
        logger.info(f"     - 수급 필터: {supply_excluded}개")
        logger.info(f"     - 거래량 필터: {volume_excluded}개")
        logger.info(f"     - 체결 강도 필터: {strength_excluded}개")
        logger.info("=" * 60)
        
        if current_stocks:
            logger.info("\n🎯 최종 선정 종목:")
            for i, stock in enumerate(current_stocks, 1):
                logger.info(
                    f"   {i}. {stock.get('name', stock.get('code'))} "
                    f"(갭 {stock.get('gap_percent', 0):+.2f}%, "
                    f"강도 {stock.get('strength', 50):.0f}%, "
                    f"수급 {stock.get('morning_total_net', 0)/1e8:.1f}억)"
                )
        
        return MorningScreenResult(
            success=len(current_stocks) > 0,
            start_time=start_time,
            end_time=end_time,
            initial_count=initial_count,
            final_count=len(current_stocks),
            passed_stocks=current_stocks,
            excluded_stocks=all_excluded,
            gap_excluded=gap_excluded,
            supply_excluded=supply_excluded,
            volume_excluded=volume_excluded,
            strength_excluded=strength_excluded
        )
    
    async def run_observation(
        self,
        candidates: list[dict],
        wait_until: dtime = None
    ) -> MorningScreenResult:
        """
        장 초반 관찰 실행 (비동기)
        
        09:00 장 시작 후 지정된 시간까지 대기한 후
        실시간 데이터를 수집하고 필터링합니다.
        
        Args:
            candidates: 후보 종목 리스트
            wait_until: 대기 시간 (기본: 09:20)
            
        Returns:
            MorningScreenResult: 스크리닝 결과
        """
        if wait_until is None:
            # 09:00 + observation_minutes
            wait_until = dtime(9, self.observation_minutes)
        
        now = datetime.now()
        target_time = datetime.combine(now.date(), wait_until)
        
        # 현재 시간이 09:00 이전이면 09:00까지 대기
        market_open = datetime.combine(now.date(), dtime(9, 0))
        if now < market_open:
            wait_seconds = (market_open - now).total_seconds()
            logger.info(f"⏰ 장 시작까지 {wait_seconds/60:.1f}분 대기...")
            await asyncio.sleep(wait_seconds)
        
        # 관찰 시간까지 대기
        now = datetime.now()
        if now < target_time:
            wait_seconds = (target_time - now).total_seconds()
            logger.info(f"⏰ 관찰 시간까지 {wait_seconds/60:.1f}분 대기...")
            await asyncio.sleep(wait_seconds)
        
        # 장 시작 후 경과 시간
        trading_minutes = self.observation_minutes
        
        # 실시간 데이터 수집
        stocks_with_data = await asyncio.to_thread(
            self._fetch_realtime_data, candidates
        )
        
        # 필터링 실행
        result = await asyncio.to_thread(
            self.filter_candidates, stocks_with_data, trading_minutes
        )
        
        return result


def run_morning_observation(
    candidates: list[dict],
    enable_gap: bool = True,
    enable_supply: bool = True,
    enable_volume: bool = True,
    trading_minutes: int = 20
) -> MorningScreenResult:
    """
    장 초반 관찰 실행 (동기, 간편 함수)
    
    Args:
        candidates: 후보 종목 리스트
        enable_gap: 갭 필터 활성화
        enable_supply: 수급 필터 활성화
        enable_volume: 거래량 필터 활성화
        trading_minutes: 장 시작 후 경과 시간 (분)
        
    Returns:
        MorningScreenResult: 스크리닝 결과
        
    Example:
        >>> result = run_morning_observation(candidates)
        >>> print(f"최종 {result.final_count}개 선정")
    """
    screener = MorningScreener(
        enable_gap_filter=enable_gap,
        enable_supply_filter=enable_supply,
        enable_volume_filter=enable_volume
    )
    
    return screener.filter_candidates(candidates, trading_minutes)


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🔍 장 초반 통합 스크리너 테스트")
    print("=" * 60)
    
    # 테스트 후보 종목 (모의 데이터)
    test_candidates = [
        {
            "code": "005930", "name": "삼성전자",
            "prev_close": 75000, "open_price": 76500,  # 갭 +2%
            "current_price": 76500, "current_volume": 1_000_000,
            "avg_volume": 10_000_000,
            "foreign_net_buy": 50_000_000_000, "institution_net_buy": 30_000_000_000
        },
        {
            "code": "000660", "name": "SK하이닉스",
            "prev_close": 180000, "open_price": 188000,  # 갭 +4.4% (제외)
            "current_price": 188000, "current_volume": 500_000,
            "avg_volume": 5_000_000,
            "foreign_net_buy": 20_000_000_000, "institution_net_buy": 10_000_000_000
        },
        {
            "code": "051910", "name": "LG에너지",
            "prev_close": 420000, "open_price": 418000,  # 갭 -0.5%
            "current_price": 418000, "current_volume": 100_000,
            "avg_volume": 1_000_000,
            "foreign_net_buy": -30_000_000_000, "institution_net_buy": -10_000_000_000  # 수급 약세 (제외)
        },
        {
            "code": "006400", "name": "삼성SDI",
            "prev_close": 320000, "open_price": 322000,  # 갭 +0.6%
            "current_price": 322000, "current_volume": 80_000,
            "avg_volume": 2_000_000,
            "foreign_net_buy": 5_000_000_000, "institution_net_buy": 3_000_000_000
        },
        {
            "code": "035420", "name": "NAVER",
            "prev_close": 220000, "open_price": 221000,  # 갭 +0.45%
            "current_price": 221000, "current_volume": 10_000,  # 거래량 부족 (제외)
            "avg_volume": 3_000_000,
            "foreign_net_buy": 10_000_000_000, "institution_net_buy": 5_000_000_000
        },
    ]
    
    print(f"\n📋 초기 후보: {len(test_candidates)}개")
    for stock in test_candidates:
        print(f"   - {stock['name']}")
    
    # 스크리너 실행
    screener = MorningScreener()
    result = screener.filter_candidates(test_candidates, trading_minutes=20)
    
    print("\n" + "=" * 60)
    print("📊 최종 결과")
    print("=" * 60)
    print(f"성공: {result.success}")
    print(f"초기: {result.initial_count}개 → 최종: {result.final_count}개")
    print(f"제외: 갭({result.gap_excluded}) + 수급({result.supply_excluded}) + 거래량({result.volume_excluded})")
    
    if result.passed_stocks:
        print("\n✅ 최종 매수 대상:")
        for stock in result.passed_stocks:
            print(f"   - {stock['name']}: 갭 {stock.get('gap_percent', 0):+.2f}%")
    
    print("\n" + "=" * 60)
