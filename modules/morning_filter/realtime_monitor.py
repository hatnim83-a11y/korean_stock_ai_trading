"""
realtime_monitor.py - 장 초반 실시간 모니터링

WebSocket을 통해 후보 종목의 실시간 시세를 수집합니다.

기능:
- 09:00 장 시작 시 후보 종목 구독
- 실시간 시초가, 거래량, 체결 강도 수집
- 09:25 필터링 시점에 수집된 데이터 제공

사용법:
    from modules.morning_filter.realtime_monitor import MorningMonitor
    
    monitor = MorningMonitor()
    await monitor.start_monitoring(candidates)
    
    # 09:25 시점
    realtime_data = monitor.get_collected_data()
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, time as dtime
from typing import Optional, Callable
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings

# WebSocket 모듈 임포트
try:
    from modules.trading_engine.kis_websocket import (
        KISWebSocket, 
        MockWebSocket, 
        PriceData
    )
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logger.warning("WebSocket 모듈 임포트 실패")


@dataclass
class RealtimeStockData:
    """실시간 수집 데이터"""
    stock_code: str
    stock_name: str = ""
    
    # 가격 데이터
    prev_close: int = 0           # 전일 종가
    open_price: int = 0           # 시초가
    current_price: int = 0        # 현재가
    high_price: int = 0           # 고가
    low_price: int = 0            # 저가
    
    # 갭 데이터
    gap_percent: float = 0.0      # 시초가 갭 (%)
    
    # 거래량
    current_volume: int = 0       # 당일 누적 거래량
    avg_volume: int = 0           # 20일 평균 거래량
    volume_ratio: float = 0.0     # 거래량 비율
    
    # 체결 강도
    buy_volume: int = 0           # 매수 체결량
    sell_volume: int = 0          # 매도 체결량
    strength: float = 50.0        # 체결 강도 (0-100%)
    
    # 수급 데이터 (API 조회 필요)
    foreign_net_buy: int = 0
    institution_net_buy: int = 0
    
    # 메타
    update_count: int = 0         # 업데이트 횟수
    last_update: str = ""         # 마지막 업데이트 시간


class MorningMonitor:
    """
    장 초반 실시간 모니터
    
    WebSocket으로 후보 종목의 실시간 데이터를 수집합니다.
    """
    
    def __init__(
        self,
        use_mock: bool = None,
        enable_websocket: bool = True
    ):
        """
        Args:
            use_mock: 모의투자 모드
            enable_websocket: WebSocket 사용 여부
        """
        self.use_mock = use_mock if use_mock is not None else settings.IS_MOCK
        self.enable_websocket = enable_websocket and WEBSOCKET_AVAILABLE
        
        # WebSocket 클라이언트
        self._ws: Optional[KISWebSocket | MockWebSocket] = None
        self._ws_task: Optional[asyncio.Task] = None
        
        # 수집 데이터
        self.realtime_data: dict[str, RealtimeStockData] = {}
        self.candidates: list[dict] = []
        
        # 상태
        self._running = False
        self._start_time: Optional[datetime] = None
        
        # 콜백
        self.on_data_update: Optional[Callable[[RealtimeStockData], None]] = None
        
        logger.info(f"📡 장 초반 모니터 초기화 (WebSocket: {'ON' if self.enable_websocket else 'OFF'})")
    
    def _create_websocket(self) -> KISWebSocket | MockWebSocket:
        """WebSocket 클라이언트 생성"""
        if self.use_mock or not WEBSOCKET_AVAILABLE:
            return MockWebSocket()
        else:
            return KISWebSocket(is_mock=self.use_mock)
    
    def _init_stock_data(self, stock: dict) -> RealtimeStockData:
        """종목 데이터 초기화"""
        return RealtimeStockData(
            stock_code=stock.get("code", stock.get("stock_code", "")),
            stock_name=stock.get("name", stock.get("stock_name", "")),
            prev_close=stock.get("prev_close", stock.get("current_price", 0)),
            avg_volume=stock.get("avg_volume", stock.get("volume_20_avg", 0)),
            foreign_net_buy=stock.get("foreign_net_buy", 0),
            institution_net_buy=stock.get("institution_net_buy", 0)
        )
    
    def _on_price_update(self, price: PriceData) -> None:
        """실시간 가격 업데이트 콜백"""
        code = price.stock_code
        
        if code not in self.realtime_data:
            return
        
        data = self.realtime_data[code]
        
        # 가격 업데이트
        if price.open_price > 0 and data.open_price == 0:
            data.open_price = price.open_price
            # 갭 계산
            if data.prev_close > 0:
                data.gap_percent = ((data.open_price - data.prev_close) / data.prev_close) * 100
        
        data.current_price = price.current_price
        data.high_price = max(data.high_price, price.high_price) if price.high_price > 0 else data.high_price
        data.low_price = price.low_price if price.low_price > 0 else data.low_price
        
        # 거래량
        data.current_volume = price.volume
        if data.avg_volume > 0:
            # 예상 거래량 대비 비율 (장 시작 후 경과 시간 고려)
            elapsed_minutes = self._get_trading_minutes()
            expected_volume = data.avg_volume * (elapsed_minutes / 390)  # 390분 = 하루
            data.volume_ratio = data.current_volume / expected_volume if expected_volume > 0 else 0
        
        # 체결 강도 (근사값 - 실제로는 체결 데이터 필요)
        # 상승 = 매수 우위, 하락 = 매도 우위로 간주
        if data.prev_close > 0:
            change_rate = ((data.current_price - data.prev_close) / data.prev_close) * 100
            # 등락률 기준 체결 강도 추정
            data.strength = min(100, max(0, 50 + change_rate * 5))
        
        data.update_count += 1
        data.last_update = datetime.now().strftime("%H:%M:%S")
        
        # 콜백 호출
        if self.on_data_update:
            self.on_data_update(data)
    
    def _get_trading_minutes(self) -> int:
        """장 시작 후 경과 분"""
        if not self._start_time:
            return 20  # 기본값
        
        now = datetime.now()
        market_open = datetime.combine(now.date(), dtime(9, 0))
        
        if now < market_open:
            return 0
        
        return min(int((now - market_open).total_seconds() / 60), 390)
    
    async def start_monitoring(
        self,
        candidates: list[dict],
        duration_minutes: int = None
    ) -> None:
        """
        실시간 모니터링 시작
        
        Args:
            candidates: 후보 종목 리스트
            duration_minutes: 모니터링 시간 (분)
        """
        if not candidates:
            logger.warning("모니터링할 종목이 없습니다")
            return
        
        duration = duration_minutes or settings.MORNING_OBSERVATION_MINUTES
        
        logger.info("=" * 60)
        logger.info(f"📡 실시간 모니터링 시작 ({len(candidates)}개 종목)")
        logger.info("=" * 60)
        
        self.candidates = candidates
        self._running = True
        self._start_time = datetime.now()
        
        # 종목 데이터 초기화
        for stock in candidates:
            data = self._init_stock_data(stock)
            self.realtime_data[data.stock_code] = data
        
        if self.enable_websocket:
            # WebSocket 모니터링
            await self._start_websocket_monitoring(duration)
        else:
            # API 폴링 모니터링
            await self._start_polling_monitoring(duration)
    
    async def _start_websocket_monitoring(self, duration_minutes: int) -> None:
        """WebSocket 기반 모니터링"""
        self._ws = self._create_websocket()
        
        # 구독 설정
        stock_codes = list(self.realtime_data.keys())
        self._ws.subscribe(stock_codes)
        self._ws.on_price_update = self._on_price_update
        
        logger.info(f"   📊 WebSocket 구독: {len(stock_codes)}개 종목")
        
        # 백그라운드 실행
        self._ws_task = asyncio.create_task(self._ws.start())
        
        # 지정 시간 동안 대기
        logger.info(f"   ⏰ {duration_minutes}분간 모니터링...")
        
        try:
            await asyncio.sleep(duration_minutes * 60)
        finally:
            await self.stop_monitoring()
    
    async def _start_polling_monitoring(self, duration_minutes: int) -> None:
        """API 폴링 기반 모니터링 (WebSocket 불가 시)"""
        from modules.stock_screener.kis_api import KISApi
        kis_api = KISApi()
        
        logger.info("   📊 API 폴링 모드")
        
        end_time = datetime.now().timestamp() + (duration_minutes * 60)
        
        while self._running and datetime.now().timestamp() < end_time:
            for code in self.realtime_data.keys():
                if not self._running:
                    break
                
                try:
                    # 현재가 조회
                    price_data = kis_api.get_current_price(code)
                    if price_data:
                        data = self.realtime_data[code]
                        
                        if data.open_price == 0 and price_data.get("open_price", 0) > 0:
                            data.open_price = price_data["open_price"]
                            if data.prev_close > 0:
                                data.gap_percent = ((data.open_price - data.prev_close) / data.prev_close) * 100
                        
                        data.current_price = price_data.get("price", 0)
                        data.current_volume = price_data.get("volume", 0)
                        data.update_count += 1
                        data.last_update = datetime.now().strftime("%H:%M:%S")
                    
                    await asyncio.sleep(settings.KIS_API_DELAY)
                    
                except Exception as e:
                    logger.debug(f"[{code}] 가격 조회 실패: {e}")
            
            # 30초마다 반복
            await asyncio.sleep(30)
    
    async def stop_monitoring(self) -> None:
        """모니터링 종료"""
        self._running = False
        
        if self._ws:
            await self._ws.stop()
            self._ws = None
        
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        
        logger.info("📡 실시간 모니터링 종료")
    
    def get_collected_data(self) -> dict[str, RealtimeStockData]:
        """수집된 데이터 반환"""
        return self.realtime_data
    
    def update_candidates_with_realtime(self, candidates: list[dict]) -> list[dict]:
        """
        후보 종목에 실시간 데이터 추가
        
        Args:
            candidates: 후보 종목 리스트
            
        Returns:
            실시간 데이터가 추가된 종목 리스트
        """
        for stock in candidates:
            code = stock.get("code", stock.get("stock_code", ""))
            
            if code in self.realtime_data:
                rt_data = self.realtime_data[code]
                
                # 실시간 데이터 추가
                stock["open_price"] = rt_data.open_price
                stock["current_price"] = rt_data.current_price
                stock["current_volume"] = rt_data.current_volume
                stock["gap_percent"] = rt_data.gap_percent
                stock["volume_ratio"] = rt_data.volume_ratio
                stock["strength"] = rt_data.strength
                stock["realtime_update_count"] = rt_data.update_count
        
        return candidates
    
    def print_summary(self) -> None:
        """수집 데이터 요약 출력"""
        logger.info("\n📊 실시간 수집 데이터 요약:")
        logger.info("-" * 60)
        
        for code, data in self.realtime_data.items():
            logger.info(
                f"   {data.stock_name or code}: "
                f"갭 {data.gap_percent:+.2f}%, "
                f"체결강도 {data.strength:.0f}%, "
                f"업데이트 {data.update_count}회"
            )


# ===== 체결 강도 필터 =====

@dataclass
class StrengthCheckResult:
    """체결 강도 필터 결과"""
    passed: bool
    stock_code: str
    strength: float
    reason: str


class StrengthFilter:
    """
    체결 강도 필터
    
    매수세가 우위인 종목만 선정합니다.
    """
    
    def __init__(
        self,
        min_strength: float = 50.0,
        prefer_strong: bool = True
    ):
        """
        Args:
            min_strength: 최소 체결 강도 (%, 50 = 중립)
            prefer_strong: 강한 종목 우선 정렬
        """
        self.min_strength = min_strength
        self.prefer_strong = prefer_strong
        
        logger.info(f"💪 체결 강도 필터 초기화: 최소 {min_strength:.0f}%")
    
    def check(
        self,
        stock_code: str,
        strength: float,
        stock_name: str = ""
    ) -> StrengthCheckResult:
        """
        체결 강도 체크
        
        Args:
            stock_code: 종목 코드
            strength: 체결 강도 (0-100%)
            stock_name: 종목명
            
        Returns:
            StrengthCheckResult
        """
        name_str = f"[{stock_name}]" if stock_name else f"[{stock_code}]"
        
        if strength < self.min_strength:
            logger.debug(f"{name_str} 체결 강도 부족 ({strength:.0f}% < {self.min_strength:.0f}%) - 제외")
            return StrengthCheckResult(
                passed=False,
                stock_code=stock_code,
                strength=strength,
                reason=f"체결 강도 부족 ({strength:.0f}%)"
            )
        
        logger.debug(f"{name_str} 체결 강도 양호 ({strength:.0f}%) - 통과")
        return StrengthCheckResult(
            passed=True,
            stock_code=stock_code,
            strength=strength,
            reason=f"체결 강도 양호 ({strength:.0f}%)"
        )
    
    def check_multiple(
        self,
        stocks: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        여러 종목 일괄 체크
        
        Args:
            stocks: 종목 리스트
            
        Returns:
            (통과 종목, 제외 종목)
        """
        passed = []
        excluded = []
        
        for stock in stocks:
            result = self.check(
                stock_code=stock.get("code", ""),
                strength=stock.get("strength", 50.0),
                stock_name=stock.get("name", "")
            )
            
            stock["strength_result"] = result
            
            if result.passed:
                passed.append(stock)
            else:
                excluded.append(stock)
        
        # 강한 종목 우선 정렬
        if self.prefer_strong:
            passed.sort(key=lambda x: x.get("strength", 50.0), reverse=True)
        
        logger.info(f"💪 체결 강도 필터 결과: {len(passed)}개 통과, {len(excluded)}개 제외")
        
        return passed, excluded


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📡 실시간 모니터 테스트")
    print("=" * 60)
    
    async def test_monitor():
        # 테스트 후보
        candidates = [
            {"code": "005930", "name": "삼성전자", "prev_close": 75000, "avg_volume": 10_000_000},
            {"code": "000660", "name": "SK하이닉스", "prev_close": 195000, "avg_volume": 5_000_000},
        ]
        
        monitor = MorningMonitor(use_mock=True)
        
        # 짧은 시간만 테스트
        print("\n📊 3초간 모니터링...")
        
        monitor_task = asyncio.create_task(
            monitor.start_monitoring(candidates, duration_minutes=1)
        )
        
        await asyncio.sleep(3)
        await monitor.stop_monitoring()
        
        # 결과 출력
        monitor.print_summary()
        
        # 체결 강도 필터 테스트
        print("\n💪 체결 강도 필터 테스트:")
        
        updated = monitor.update_candidates_with_realtime(candidates)
        
        strength_filter = StrengthFilter(min_strength=45)
        passed, excluded = strength_filter.check_multiple(updated)
        
        print(f"\n통과: {len(passed)}개")
        for s in passed:
            print(f"   - {s['name']}: {s.get('strength', 50):.0f}%")
    
    asyncio.run(test_monitor())
    
    print("\n" + "=" * 60)
