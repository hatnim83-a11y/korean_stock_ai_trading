"""
portfolio_monitor.py - 포트폴리오 실시간 모니터링 모듈

이 파일은 보유 종목의 실시간 모니터링 및 손익 관리 기능을 제공합니다.

주요 기능:
- 실시간 가격 모니터링
- 손절 체크 및 실행
- 익절 체크 및 실행
- 트레일링 스탑
- 수급 이탈 감지
- 알림 발송

사용법:
    from modules.trading_engine.portfolio_monitor import PortfolioMonitor
    
    monitor = PortfolioMonitor()
    await monitor.start_monitoring()
"""

import asyncio
from datetime import datetime, time as dt_time
from typing import Optional, Callable
from dataclasses import dataclass, field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings
from database import Database
from modules.trading_engine.kis_websocket import KISWebSocket, MockWebSocket, PriceData
from modules.trading_engine.trading_engine import TradingEngine


# ===== 상수 정의 =====
CHECK_INTERVAL = 1  # 체크 간격 (초)
# 트레일링 스탑 설정은 settings에서 로드
TRAILING_STOP_ACTIVATION = settings.MIN_PROFIT_FOR_LONG_HOLD  # 트레일링 스탑 활성화 (수익률)
TRAILING_STOP_DISTANCE = settings.TRAILING_STOP_PERCENT  # 트레일링 스탑 거리


@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    stock_name: str
    shares: int
    buy_price: float
    stop_loss_price: float
    take_profit_price: float
    current_price: float = 0
    highest_price: float = 0  # 트레일링용
    trailing_stop: Optional[float] = None
    theme: str = ""
    
    @property
    def profit(self) -> float:
        """현재 수익금"""
        return (self.current_price - self.buy_price) * self.shares
    
    @property
    def profit_rate(self) -> float:
        """현재 수익률"""
        if self.buy_price > 0:
            return (self.current_price - self.buy_price) / self.buy_price
        return 0
    
    @property
    def value(self) -> float:
        """현재 평가금액"""
        return self.current_price * self.shares


@dataclass
class MonitoringResult:
    """모니터링 결과"""
    timestamp: datetime = field(default_factory=datetime.now)
    total_value: float = 0
    total_profit: float = 0
    total_profit_rate: float = 0
    stop_loss_triggered: list = field(default_factory=list)
    take_profit_triggered: list = field(default_factory=list)
    trailing_stop_triggered: list = field(default_factory=list)


class PortfolioMonitor:
    """
    포트폴리오 실시간 모니터링
    
    보유 종목의 손익을 실시간으로 모니터링하고
    손절/익절 조건 충족 시 자동으로 매도합니다.
    
    Attributes:
        positions: 보유 포지션 딕셔너리
        websocket: WebSocket 클라이언트
        trading_engine: 매매 엔진
        
    Example:
        >>> monitor = PortfolioMonitor()
        >>> monitor.load_positions()
        >>> await monitor.start_monitoring()
    """
    
    def __init__(
        self,
        use_mock: bool = True
    ):
        """
        모니터 초기화
        
        Args:
            use_mock: 모의 모드 사용
        """
        self.use_mock = use_mock
        
        # 포지션 관리
        self.positions: dict[str, Position] = {}
        
        # WebSocket
        if use_mock:
            self.websocket = MockWebSocket()
        else:
            self.websocket = KISWebSocket()
        
        # 매매 엔진
        self.trading_engine = TradingEngine(use_mock_api=use_mock)
        
        # 콜백
        self.on_stop_loss: Optional[Callable[[Position, float], None]] = None
        self.on_take_profit: Optional[Callable[[Position, float], None]] = None
        self.on_trailing_stop: Optional[Callable[[Position, float], None]] = None
        self.on_price_update: Optional[Callable[[str, float], None]] = None
        
        # 상태
        self._running = False
        self._last_check = datetime.now()
        
        logger.info(f"포트폴리오 모니터 초기화 ({'모의' if use_mock else '실전'})")
    
    # ===== 포지션 관리 =====
    
    def add_position(
        self,
        stock_code: str,
        stock_name: str,
        shares: int,
        buy_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        theme: str = ""
    ) -> None:
        """
        포지션 추가
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            shares: 보유 수량
            buy_price: 매수가
            stop_loss_price: 손절가
            take_profit_price: 익절가
            theme: 테마
        """
        position = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            shares=shares,
            buy_price=buy_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            current_price=buy_price,
            highest_price=buy_price,
            theme=theme
        )
        
        self.positions[stock_code] = position
        logger.info(f"포지션 추가: {stock_name} ({stock_code}) {shares}주 @ {buy_price:,}원")
    
    def remove_position(self, stock_code: str) -> None:
        """포지션 제거"""
        if stock_code in self.positions:
            pos = self.positions[stock_code]
            logger.info(f"포지션 제거: {pos.stock_name}")
            del self.positions[stock_code]
    
    def load_positions_from_db(self) -> int:
        """
        DB에서 보유 포지션 로드
        
        Returns:
            로드된 포지션 수
        """
        try:
            db = Database()
            db.connect()
            
            portfolio = db.get_portfolio(status="holding")
            
            for item in portfolio:
                self.add_position(
                    stock_code=item["stock_code"],
                    stock_name=item["stock_name"],
                    shares=item["shares"],
                    buy_price=item["buy_price"],
                    stop_loss_price=item["stop_loss"],
                    take_profit_price=item["take_profit"],
                    theme=item.get("theme", "")
                )
            
            db.close()
            
            logger.info(f"포지션 로드: {len(self.positions)}개")
            return len(self.positions)
            
        except Exception as e:
            logger.error(f"포지션 로드 실패: {e}")
            return 0
    
    def update_position(self, stock_code: str, **kwargs) -> None:
        """포지션 정보 업데이트"""
        if stock_code in self.positions:
            pos = self.positions[stock_code]
            for key, value in kwargs.items():
                if hasattr(pos, key):
                    setattr(pos, key, value)
    
    # ===== 모니터링 =====
    
    async def start_monitoring(self) -> None:
        """
        실시간 모니터링 시작
        
        장 시간 동안 실시간 가격을 모니터링하고
        손절/익절 조건 체크 후 자동 매도합니다.
        """
        if not self.positions:
            logger.warning("모니터링할 포지션이 없습니다")
            return
        
        logger.info("=" * 60)
        logger.info("📊 포트폴리오 모니터링 시작")
        logger.info(f"   포지션: {len(self.positions)}개")
        logger.info("=" * 60)
        
        self._running = True
        
        # WebSocket 구독
        stock_codes = list(self.positions.keys())
        self.websocket.subscribe(stock_codes)
        
        # 가격 업데이트 콜백
        self.websocket.on_price_update = self._on_price_update
        
        # 병렬 실행
        await asyncio.gather(
            self.websocket.start(),
            self._monitor_loop()
        )
    
    async def stop_monitoring(self) -> None:
        """모니터링 중지"""
        self._running = False
        await self.websocket.stop()
        logger.info("모니터링 중지")
    
    async def _monitor_loop(self) -> None:
        """모니터링 루프"""
        while self._running:
            await asyncio.sleep(CHECK_INTERVAL)
            
            # 장 시간 체크 (09:00 ~ 15:30)
            if not self._is_market_hours():
                continue
            
            # 손익 체크
            await self._check_all_positions()
    
    def _is_market_hours(self) -> bool:
        """장 시간 여부"""
        now = datetime.now().time()
        market_open = dt_time(9, 0)
        market_close = dt_time(15, 30)
        
        return market_open <= now <= market_close
    
    def _on_price_update(self, price_data: PriceData) -> None:
        """
        가격 업데이트 콜백
        
        Args:
            price_data: 실시간 가격 데이터
        """
        stock_code = price_data.stock_code
        current_price = price_data.current_price
        
        if stock_code not in self.positions:
            return
        
        pos = self.positions[stock_code]
        
        # 현재가 업데이트
        pos.current_price = current_price
        
        # 최고가 업데이트 (트레일링 스탑용)
        if current_price > pos.highest_price:
            pos.highest_price = current_price
            self._update_trailing_stop(pos)
        
        # 콜백 호출
        if self.on_price_update:
            self.on_price_update(stock_code, current_price)
    
    async def _check_all_positions(self) -> MonitoringResult:
        """모든 포지션 체크"""
        result = MonitoringResult()
        
        for stock_code, pos in list(self.positions.items()):
            if pos.current_price <= 0:
                continue
            
            # 1. 손절 체크
            if self._check_stop_loss(pos):
                await self._execute_stop_loss(pos)
                result.stop_loss_triggered.append(stock_code)
                continue
            
            # 2. 익절 체크
            if self._check_take_profit(pos):
                await self._execute_take_profit(pos)
                result.take_profit_triggered.append(stock_code)
                continue
            
            # 3. 트레일링 스탑 체크
            if self._check_trailing_stop(pos):
                await self._execute_trailing_stop(pos)
                result.trailing_stop_triggered.append(stock_code)
                continue
            
            # 수익 집계
            result.total_value += pos.value
            result.total_profit += pos.profit
        
        if result.total_value > 0:
            total_cost = sum(p.buy_price * p.shares for p in self.positions.values())
            result.total_profit_rate = (result.total_value - total_cost) / total_cost if total_cost > 0 else 0
        
        return result
    
    # ===== 손절 =====
    
    def _check_stop_loss(self, pos: Position) -> bool:
        """
        손절 조건 체크
        
        Args:
            pos: 포지션
        
        Returns:
            손절 필요 여부
        """
        return pos.current_price <= pos.stop_loss_price
    
    async def _execute_stop_loss(self, pos: Position) -> None:
        """손절 실행"""
        logger.warning(f"🔻 손절 발동: {pos.stock_name}")
        logger.warning(f"   현재가 {pos.current_price:,}원 <= 손절가 {pos.stop_loss_price:,}원")
        logger.warning(f"   손실: {pos.profit_rate:.1%}")
        
        # 매도 실행
        result = self.trading_engine.execute_stop_loss(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )
        
        if result.get("success"):
            self.remove_position(pos.stock_code)
        
        # 콜백
        if self.on_stop_loss:
            self.on_stop_loss(pos, pos.current_price)
    
    # ===== 익절 =====
    
    def _check_take_profit(self, pos: Position) -> bool:
        """
        익절 조건 체크
        
        Args:
            pos: 포지션
        
        Returns:
            익절 필요 여부
        """
        return pos.current_price >= pos.take_profit_price
    
    async def _execute_take_profit(self, pos: Position) -> None:
        """익절 실행"""
        logger.info(f"🔺 익절 발동: {pos.stock_name}")
        logger.info(f"   현재가 {pos.current_price:,}원 >= 익절가 {pos.take_profit_price:,}원")
        logger.info(f"   수익: {pos.profit_rate:.1%}")
        
        result = self.trading_engine.execute_take_profit(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )
        
        if result.get("success"):
            self.remove_position(pos.stock_code)
        
        if self.on_take_profit:
            self.on_take_profit(pos, pos.current_price)
    
    # ===== 트레일링 스탑 =====
    
    def _update_trailing_stop(self, pos: Position) -> None:
        """
        트레일링 스탑 업데이트
        
        수익률이 활성화 조건을 넘으면 트레일링 스탑 설정
        """
        profit_rate = pos.profit_rate
        
        # 활성화 조건 (5% 이상 수익)
        if profit_rate >= TRAILING_STOP_ACTIVATION:
            # 트레일링 스탑가 = 최고가 × (1 - 거리)
            trailing_stop = pos.highest_price * (1 - TRAILING_STOP_DISTANCE)
            
            # 기존 손절가보다 높아야 함
            if trailing_stop > pos.stop_loss_price:
                if pos.trailing_stop is None or trailing_stop > pos.trailing_stop:
                    pos.trailing_stop = trailing_stop
                    logger.debug(f"트레일링 스탑 업데이트: {pos.stock_name} @ {trailing_stop:,.0f}원")
    
    def _check_trailing_stop(self, pos: Position) -> bool:
        """트레일링 스탑 체크"""
        if pos.trailing_stop is None:
            return False
        
        return pos.current_price <= pos.trailing_stop
    
    async def _execute_trailing_stop(self, pos: Position) -> None:
        """트레일링 스탑 실행"""
        logger.info(f"📉 트레일링 스탑 발동: {pos.stock_name}")
        logger.info(f"   현재가 {pos.current_price:,}원 <= 트레일링 {pos.trailing_stop:,.0f}원")
        logger.info(f"   수익: {pos.profit_rate:.1%}")
        
        result = self.trading_engine.execute_take_profit(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )
        
        if result.get("success"):
            self.remove_position(pos.stock_code)
        
        if self.on_trailing_stop:
            self.on_trailing_stop(pos, pos.current_price)
    
    # ===== 상태 조회 =====
    
    def get_portfolio_status(self) -> dict:
        """
        포트폴리오 상태 조회
        
        Returns:
            {
                'total_value': 10000000,
                'total_cost': 9500000,
                'total_profit': 500000,
                'profit_rate': 5.26,
                'positions': [...]
            }
        """
        total_value = 0
        total_cost = 0
        positions_info = []
        
        for code, pos in self.positions.items():
            value = pos.value
            cost = pos.buy_price * pos.shares
            
            total_value += value
            total_cost += cost
            
            positions_info.append({
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.shares,
                "buy_price": pos.buy_price,
                "current_price": pos.current_price,
                "profit": pos.profit,
                "profit_rate": pos.profit_rate * 100,
                "stop_loss_price": pos.stop_loss_price,
                "take_profit_price": pos.take_profit_price,
                "trailing_stop": pos.trailing_stop
            })
        
        return {
            "total_value": total_value,
            "total_cost": total_cost,
            "total_profit": total_value - total_cost,
            "profit_rate": (total_value - total_cost) / total_cost * 100 if total_cost > 0 else 0,
            "position_count": len(self.positions),
            "positions": positions_info
        }
    
    def display_status(self) -> None:
        """현재 상태 출력"""
        status = self.get_portfolio_status()
        
        print("\n" + "=" * 70)
        print("📊 포트폴리오 현황")
        print("=" * 70)
        print(f"  총 평가액: {status['total_value']:>12,.0f}원")
        print(f"  총 투자액: {status['total_cost']:>12,.0f}원")
        print(f"  총 수익금: {status['total_profit']:>+12,.0f}원")
        print(f"  수익률:    {status['profit_rate']:>+12.2f}%")
        print("-" * 70)
        print(f"{'종목':<12} {'현재가':>10} {'수익률':>8} {'손절':>10} {'익절':>10}")
        print("-" * 70)
        
        for pos in status["positions"]:
            print(f"{pos['stock_name']:<10} "
                  f"{pos['current_price']:>10,} "
                  f"{pos['profit_rate']:>+7.2f}% "
                  f"{pos['stop_loss_price']:>10,} "
                  f"{pos['take_profit_price']:>10,}")
        
        print("=" * 70)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 포트폴리오 모니터 테스트")
    print("=" * 60)
    
    async def test_monitor():
        monitor = PortfolioMonitor(use_mock=True)
        
        # 테스트 포지션 추가
        monitor.add_position(
            stock_code="005930",
            stock_name="삼성전자",
            shares=10,
            buy_price=75000,
            stop_loss_price=69000,
            take_profit_price=87000,
            theme="AI반도체"
        )
        
        monitor.add_position(
            stock_code="000660",
            stock_name="SK하이닉스",
            shares=5,
            buy_price=195000,
            stop_loss_price=179000,
            take_profit_price=226000,
            theme="AI반도체"
        )
        
        # 상태 출력
        monitor.display_status()
        
        # 모의 가격 업데이트
        print("\n모의 가격 업데이트 (3초)...")
        
        update_count = 0
        def on_update(code, price):
            nonlocal update_count
            update_count += 1
        
        monitor.on_price_update = on_update
        
        # 3초간 모니터링
        task = asyncio.create_task(monitor.start_monitoring())
        await asyncio.sleep(3)
        await monitor.stop_monitoring()
        
        print(f"업데이트 횟수: {update_count}")
        
        # 최종 상태
        monitor.display_status()
    
    asyncio.run(test_monitor())
    
    print("\n" + "=" * 60)
    print("✅ 포트폴리오 모니터 테스트 완료!")
    print("=" * 60)
