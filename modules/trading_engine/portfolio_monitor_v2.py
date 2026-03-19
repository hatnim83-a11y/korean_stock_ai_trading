"""
portfolio_monitor_v2.py - 개선된 포트폴리오 실시간 모니터링 모듈

이 파일은 최적화된 하이브리드 전략의 손익 관리 기능을 제공합니다.

주요 개선사항:
- 분할 익절 (3단계: +10%, +15%, +20%)
- 향상된 트레일링 스탑 (최고가 -5%)
- 수익 중 수급 이탈 무시 (10% 이상)
- 보유 기간 관리 (수익 시 10영업일, 손실 시 5영업일)

사용법:
    from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2
    
    monitor = PortfolioMonitorV2()
    await monitor.start_monitoring()
"""

import asyncio
from datetime import datetime, time as dt_time
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst, is_trading_day, count_trading_days
from database import Database
from modules.trading_engine.kis_websocket import KISWebSocket, MockWebSocket, PriceData
from modules.trading_engine.trading_engine import TradingEngine


# ===== 상수 정의 =====
CHECK_INTERVAL = 1  # 체크 간격 (초)


class SellReason(Enum):
    """매도 사유"""
    STOP_LOSS = "손절"
    TAKE_PROFIT_1 = "1차 익절"
    TAKE_PROFIT_2 = "2차 익절"
    TAKE_PROFIT_3 = "3차 익절"
    TRAILING_STOP = "트레일링 스탑"
    TRAILING_L1 = "트레일링L1"  # +8%~15%
    TRAILING_L2 = "트레일링L2"  # +15%~25%
    TRAILING_L3 = "트레일링L3"  # +25%+
    MAX_HOLD_DAYS = "최대 보유 기간"
    SUPPLY_EXIT = "수급 이탈"


@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    stock_name: str
    shares: int  # 원본 수량
    remaining_shares: int  # 남은 수량
    buy_price: float
    stop_loss_price: float
    current_price: float = 0
    highest_price: float = 0  # 트레일링용
    trailing_stop: Optional[float] = None
    theme: str = ""
    buy_date: datetime = field(default_factory=now_kst)

    # 분할 익절 상태
    partial_1_executed: bool = False
    partial_2_executed: bool = False
    partial_3_executed: bool = False

    # 이익 추종 전략 상태 (Let Profits Run)
    trailing_active: bool = False  # 트레일링 활성화 여부
    trailing_level: int = 0  # 트레일링 레벨 (0=미활성, 1=5%, 2=3%, 3=2%)
    max_profit_rate: float = 0.0  # 최대 수익률 기록

    # WebSocket 가격 수신 확인 (재시작 시 오발동 방지)
    price_confirmed: bool = False
    
    @property
    def profit(self) -> float:
        """현재 수익금 (남은 수량 기준)"""
        return (self.current_price - self.buy_price) * self.remaining_shares
    
    @property
    def profit_rate(self) -> float:
        """현재 수익률"""
        if self.buy_price > 0:
            return (self.current_price - self.buy_price) / self.buy_price
        return 0
    
    @property
    def value(self) -> float:
        """현재 평가금액 (남은 수량 기준)"""
        return self.current_price * self.remaining_shares
    
    @property
    def hold_days(self) -> int:
        """보유 영업일수 (주말/공휴일 제외)"""
        return count_trading_days(self.buy_date)


@dataclass
class MonitoringResult:
    """모니터링 결과"""
    timestamp: datetime = field(default_factory=now_kst)
    total_value: float = 0
    total_profit: float = 0
    total_profit_rate: float = 0
    stop_loss_triggered: list = field(default_factory=list)
    partial_profit_triggered: list = field(default_factory=list)
    trailing_stop_triggered: list = field(default_factory=list)


class PortfolioMonitorV2:
    """
    개선된 포트폴리오 실시간 모니터링
    
    분할 익절, 트레일링 스탑, 보유 기간 관리 등
    최적화된 하이브리드 전략을 구현합니다.
    
    Attributes:
        positions: 보유 포지션 딕셔너리
        websocket: WebSocket 클라이언트
        trading_engine: 매매 엔진
        
    Example:
        >>> monitor = PortfolioMonitorV2()
        >>> monitor.load_positions_from_db()
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
        self.on_partial_profit: Optional[Callable[[Position, float, int, int], None]] = None  # pos, price, stage, sell_shares
        self.on_trailing_stop: Optional[Callable[[Position, float], None]] = None
        self.on_price_update: Optional[Callable[[str, float], None]] = None
        self.on_trailing_level_change: Optional[Callable[[Position, int, int], None]] = None
        self.on_max_hold_sell: Optional[Callable[[Position, float], None]] = None
        self.on_sell_failed: Optional[Callable[[Position, str, str], None]] = None
        
        # 상태
        self._running = False
        self._last_check = now_kst()

        # 설정 로드
        self._load_settings()
        
        logger.info(f"포트폴리오 모니터 V2 초기화 ({'모의' if use_mock else '실전'})")
    
    def _load_settings(self):
        """설정 로드"""
        # 익절 설정 (레거시 - 이익 추종 전략 비활성화 시 사용)
        self.take_profit_1 = settings.TAKE_PROFIT_1
        self.take_profit_2 = settings.TAKE_PROFIT_2
        self.take_profit_3 = settings.TAKE_PROFIT_3
        self.partial_sell_ratio_1 = settings.PARTIAL_SELL_RATIO_1
        self.partial_sell_ratio_2 = settings.PARTIAL_SELL_RATIO_2
        self.partial_sell_ratio_3 = getattr(settings, 'PARTIAL_SELL_RATIO_3', 0.20)

        # 기존 트레일링 스탑
        self.enable_trailing_stop = settings.ENABLE_TRAILING_STOP
        self.trailing_stop_percent = settings.TRAILING_STOP_PERCENT

        # 이익 추종 전략 (Let Profits Run) - 새 전략
        self.enable_profit_trailing = getattr(settings, 'ENABLE_PROFIT_TRAILING', True)
        self.trail_activation_pct = getattr(settings, 'TRAIL_ACTIVATION_PCT', 0.08)
        self.trail_level1_pct = getattr(settings, 'TRAIL_LEVEL1_PCT', 0.05)
        self.trail_level2_threshold = getattr(settings, 'TRAIL_LEVEL2_THRESHOLD', 0.15)
        self.trail_level2_pct = getattr(settings, 'TRAIL_LEVEL2_PCT', 0.03)
        self.trail_level3_threshold = getattr(settings, 'TRAIL_LEVEL3_THRESHOLD', 0.25)
        self.trail_level3_pct = getattr(settings, 'TRAIL_LEVEL3_PCT', 0.02)

        # 손절
        self.stop_loss = settings.DEFAULT_STOP_LOSS
        self.stop_loss_fast = settings.STOP_LOSS_FAST

        # 보유 기간
        self.max_hold_days_profit = settings.MAX_HOLD_DAYS_PROFIT
        self.max_hold_days_loss = settings.MAX_HOLD_DAYS_LOSS
        self.min_profit_for_long_hold = settings.MIN_PROFIT_FOR_LONG_HOLD

        # 수급 이탈 무시
        self.min_profit_to_ignore_supply = settings.MIN_PROFIT_TO_IGNORE_SUPPLY

        if self.enable_profit_trailing:
            logger.info("설정 로드: 이익 추종 전략 활성화")
            logger.info(f"  - 트레일링 시작: +{self.trail_activation_pct:.0%}")
            logger.info(f"  - L1: 5% | L2 (+{self.trail_level2_threshold:.0%}): 3% | L3 (+{self.trail_level3_threshold:.0%}): 2%")
        else:
            logger.info(f"설정 로드: 익절 {self.take_profit_1:.0%}/{self.take_profit_2:.0%}/{self.take_profit_3:.0%}")
            logger.info(f"설정 로드: 트레일링 스탑 {self.trailing_stop_percent:.0%}")
    
    # ===== 포지션 관리 =====
    
    def add_position(
        self,
        stock_code: str,
        stock_name: str,
        shares: int,
        buy_price: float,
        stop_loss_price: float,
        theme: str = "",
        buy_date: Optional[datetime] = None
    ) -> None:
        """
        포지션 추가
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            shares: 보유 수량
            buy_price: 매수가
            stop_loss_price: 손절가
            theme: 테마
            buy_date: 매수일
        """
        position = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            shares=shares,
            remaining_shares=shares,
            buy_price=buy_price,
            stop_loss_price=stop_loss_price,
            current_price=buy_price,
            highest_price=buy_price,
            theme=theme,
            buy_date=buy_date or now_kst()
        )
        
        self.positions[stock_code] = position
        logger.info(f"포지션 추가: {stock_name} ({stock_code}) {shares}주 @ {buy_price:,}원")
    
    def remove_position(self, stock_code: str) -> None:
        """포지션 제거 + position_state DB 삭제"""
        if stock_code in self.positions:
            pos = self.positions[stock_code]
            logger.info(f"포지션 제거: {pos.stock_name} (보유 {pos.hold_days}일)")
            del self.positions[stock_code]
            # position_state DB에서도 삭제
            db = None
            try:
                db = Database()
                db.connect()
                db.delete_position_state(stock_code)
            except Exception as e:
                logger.debug(f"position_state 삭제 실패: {e}")
            finally:
                if db:
                    db.close()
    
    def load_positions_from_db(self) -> int:
        """
        DB에서 보유 포지션 로드

        Returns:
            로드된 포지션 수
        """
        db = None
        try:
            db = Database()
            db.connect()

            portfolio = db.get_portfolio(status="holding")

            for item in portfolio:
                # buy_date: buy_date 컬럼 → date 컬럼 폴백 → now_kst()
                buy_date = item.get("buy_date") or item.get("date") or None
                if isinstance(buy_date, str):
                    try:
                        buy_date = datetime.strptime(buy_date, "%Y-%m-%d").replace(
                            tzinfo=now_kst().tzinfo
                        )
                    except ValueError:
                        buy_date = None

                self.add_position(
                    stock_code=item["stock_code"],
                    stock_name=item["stock_name"],
                    shares=item["shares"],
                    buy_price=item["buy_price"],
                    stop_loss_price=item["stop_loss"],
                    theme=item.get("theme", ""),
                    buy_date=buy_date or now_kst()
                )

            # monitor_state.json에서 트레일링 상태 복원
            self._restore_trailing_state()

            logger.info(f"포지션 로드: {len(self.positions)}개")
            return len(self.positions)

        except Exception as e:
            logger.error(f"포지션 로드 실패: {e}")
            return 0
        finally:
            if db:
                db.close()

    def _restore_trailing_state(self) -> None:
        """DB에서 트레일링 상태 복원 (재시작 시). DB 우선, JSON 폴백."""
        import json

        # 1) DB에서 position_state 로드 시도
        state = {}
        db_source = False
        db = None
        try:
            db = Database()
            db.connect()
            db_states = db.get_all_position_states()
            if db_states:
                state = db_states
                db_source = True
        except Exception as e:
            logger.debug(f"position_state DB 조회 실패: {e}")
        finally:
            if db:
                db.close()

        # 2) DB 비어있으면 JSON 폴백
        if not state:
            state_path = Path(settings.DATABASE_PATH).parent / "monitor_state.json"
            try:
                if state_path.exists():
                    with open(state_path) as f:
                        state = json.load(f)
            except Exception as e:
                logger.debug(f"monitor_state.json 로드 실패: {e}")

        if not state:
            return

        restored = 0
        for code, pos in self.positions.items():
            if code not in state:
                continue
            s = state[code]

            # current_price 복원 (WebSocket 수신 전 buy_price로 오발동 방지)
            saved_price = s.get("current_price", 0)
            if saved_price > 0:
                pos.current_price = saved_price

            # remaining_shares 복원 (DB source)
            saved_remaining = s.get("remaining_shares", 0)
            if saved_remaining > 0 and saved_remaining <= pos.shares:
                pos.remaining_shares = saved_remaining

            # 분할 익절 상태 복원 (이중 매도 방지)
            pos.partial_1_executed = bool(s.get("partial_1_executed", False))
            pos.partial_2_executed = bool(s.get("partial_2_executed", False))
            pos.partial_3_executed = bool(s.get("partial_3_executed", False))
            if any([pos.partial_1_executed, pos.partial_2_executed, pos.partial_3_executed]):
                logger.info(
                    f"   익절 상태 복원: {pos.stock_name} "
                    f"P1={pos.partial_1_executed} P2={pos.partial_2_executed} P3={pos.partial_3_executed}"
                )

            if s.get("trailing_active"):
                pos.trailing_level = s.get("trailing_level", 0)
                pos.trailing_active = True
                pos.trailing_stop = s.get("trailing_stop_price")
                # DB는 비율 그대로 저장, JSON은 %로 저장
                raw_rate = s.get("max_profit_rate", 0)
                if db_source:
                    pos.max_profit_rate = raw_rate  # 비율 그대로
                else:
                    pos.max_profit_rate = raw_rate / 100  # %→비율
                # highest_price: 저장값과 현재 buy_price 기반 중 큰 값
                saved_highest = s.get("highest_price", 0)
                if saved_highest > pos.highest_price:
                    pos.highest_price = saved_highest
                restored += 1
                stop_str = f"{pos.trailing_stop:,.0f}원" if pos.trailing_stop is not None else "미설정"
                logger.info(
                    f"   트레일링 복원: {pos.stock_name} L{pos.trailing_level} "
                    f"(최고가 {pos.highest_price:,}원, 스탑 {stop_str}, "
                    f"현재가 {pos.current_price:,}원)"
                )

        source_str = "DB" if db_source else "JSON"
        if restored:
            logger.info(f"트레일링 상태 복원: {restored}개 종목 (소스: {source_str})")
    
    # ===== 모니터링 =====
    
    async def start_monitoring(self) -> None:
        """
        실시간 모니터링 시작
        
        장 시간 동안 실시간 가격을 모니터링하고
        분할 익절, 손절, 트레일링 스탑 조건 체크 후 자동 매도합니다.
        """
        if not self.positions:
            logger.warning("모니터링할 포지션이 없습니다")
            return
        
        logger.info("=" * 70)
        logger.info("📊 포트폴리오 모니터링 V2 시작")
        logger.info(f"   포지션: {len(self.positions)}개")
        if self.enable_profit_trailing:
            logger.info("   전략: 이익 추종 (Let Profits Run)")
            logger.info(f"   트레일링 시작: +{self.trail_activation_pct:.0%}")
            logger.info(f"   L1: -{self.trail_level1_pct:.0%} | L2 (+{self.trail_level2_threshold:.0%}): -{self.trail_level2_pct:.0%} | L3 (+{self.trail_level3_threshold:.0%}): -{self.trail_level3_pct:.0%}")
        else:
            logger.info(f"   익절: {self.take_profit_1:.0%}/{self.take_profit_2:.0%}/{self.take_profit_3:.0%}")
            logger.info(f"   트레일링: 최고가 -{self.trailing_stop_percent:.0%}")
        logger.info("=" * 70)
        
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
        import time as _time
        status_interval = 30 * 60  # 30분마다 상태 로그
        db_update_interval = 5 * 60  # 5분마다 DB 가격 갱신
        state_dump_interval = 30  # 30초마다 대시보드용 상태 덤프
        last_status_log = 0
        last_db_update = 0
        last_state_dump = 0

        while self._running:
            await asyncio.sleep(CHECK_INTERVAL)

            # 장 시간 체크 (09:00 ~ 15:30)
            if not self._is_market_hours():
                continue

            # 손익 체크
            await self._check_all_positions()

            now_ts = _time.time()

            # 주기적 DB 가격 갱신 (5분마다)
            if now_ts - last_db_update >= db_update_interval:
                last_db_update = now_ts
                self._update_db_prices()

            # 대시보드용 상태 JSON 덤프 (30초마다)
            if now_ts - last_state_dump >= state_dump_interval:
                last_state_dump = now_ts
                self._dump_monitor_state()

            # 주기적 상태 로그 (30분마다)
            if now_ts - last_status_log >= status_interval:
                last_status_log = now_ts
                self._log_status()
    
    def _is_market_hours(self) -> bool:
        """장 시간 여부 (KST 기준, 휴장일 체크 포함)"""
        if not is_trading_day():
            return False
        now = now_kst().time()
        return dt_time(9, 0) <= now <= dt_time(15, 30)

    def _update_db_prices(self) -> None:
        """보유 종목 현재가를 DB에 주기적으로 갱신"""
        if not self.positions:
            return

        db = None
        try:
            db = Database()
            db.connect()
            for pos in self.positions.values():
                if pos.current_price > 0:
                    db.update_portfolio_price(
                        stock_code=pos.stock_code,
                        current_price=pos.current_price,
                        profit_rate=pos.profit_rate,
                        profit_amount=pos.profit
                    )
            logger.debug(f"DB 가격 갱신: {len(self.positions)}종목")
        except Exception as e:
            logger.error(f"DB 가격 갱신 실패: {e}")
        finally:
            if db:
                db.close()

    def _log_status(self) -> None:
        """주기적 모니터링 상태 로그 (30분 간격)"""
        if not self.positions:
            logger.info("📊 모니터링 중: 포지션 없음")
            return

        lines = [f"📊 모니터링 중: {len(self.positions)}종목"]
        for pos in self.positions.values():
            trail_info = ""
            if pos.trailing_active:
                trail_info = f" T.L{pos.trailing_level}({pos.trailing_stop:,.0f})"
            lines.append(
                f"   {pos.stock_name} {pos.current_price:,}원 "
                f"({pos.profit_rate:+.1%}) "
                f"보유{pos.hold_days}일 "
                f"최고{pos.highest_price:,}원"
                f"{trail_info}"
            )
        logger.info("\n".join(lines))

    def _dump_monitor_state(self) -> None:
        """트레일링 상태를 DB + JSON에 동시 저장 (30초 간격)"""
        import json
        state = {}
        for code, pos in self.positions.items():
            state[code] = {
                "trailing_level": pos.trailing_level,
                "trailing_active": pos.trailing_active,
                "trailing_stop_price": pos.trailing_stop,
                "highest_price": int(pos.highest_price) if pos.highest_price else 0,
                "max_profit_rate": round(pos.max_profit_rate * 100, 2),
                "current_price": int(pos.current_price) if pos.current_price else 0,
                "partial_1_executed": pos.partial_1_executed,
                "partial_2_executed": pos.partial_2_executed,
                "partial_3_executed": pos.partial_3_executed,
                "remaining_shares": pos.remaining_shares,
            }

        # 1) JSON 파일 (대시보드 캐시용)
        state_path = Path(settings.DATABASE_PATH).parent / "monitor_state.json"
        try:
            with open(state_path, "w") as f:
                json.dump(state, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"JSON 상태 덤프 실패: {e}")

        # 2) DB position_state (primary source of truth)
        db = None
        try:
            db = Database()
            db.connect()
            for code, s in state.items():
                db.upsert_position_state(code, {
                    "current_price": s["current_price"],
                    "highest_price": s["highest_price"],
                    "trailing_active": s["trailing_active"],
                    "trailing_level": s["trailing_level"],
                    "trailing_stop_price": s["trailing_stop_price"],
                    "max_profit_rate": s["max_profit_rate"] / 100,  # %→비율로 DB 저장
                    "partial_1_executed": s["partial_1_executed"],
                    "partial_2_executed": s["partial_2_executed"],
                    "partial_3_executed": s["partial_3_executed"],
                    "remaining_shares": s["remaining_shares"],
                })
        except Exception as e:
            logger.debug(f"DB position_state 저장 실패: {e}")
        finally:
            if db:
                db.close()

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
        pos.price_confirmed = True

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

            # WebSocket에서 실제 가격 수신 전이면 매매 판단 스킵 (재시작 오발동 방지)
            if not pos.price_confirmed:
                logger.debug(f"가격 미확인 스킵: {pos.stock_name} ({stock_code})")
                continue

            # 1. 손절 체크
            if self._check_stop_loss(pos):
                await self._execute_stop_loss(pos)
                result.stop_loss_triggered.append(stock_code)
                continue
            
            # 2. 분할 익절 체크 (3단계)
            partial_sell = await self._check_and_execute_partial_profit(pos)
            if partial_sell:
                result.partial_profit_triggered.append(stock_code)
                continue  # 이번 주기 트레일링 체크 스킵, 다음 주기에 처리

            # 3. 트레일링 스탑 체크
            if self._check_trailing_stop(pos):
                await self._execute_trailing_stop(pos)
                result.trailing_stop_triggered.append(stock_code)
                continue
            
            # 4. 보유 기간 체크
            if self._check_max_hold_days(pos):
                await self._execute_max_hold_sell(pos)
                continue
            
            # 수익 집계
            result.total_value += pos.value
            result.total_profit += pos.profit
        
        if result.total_value > 0:
            total_cost = sum(p.buy_price * p.remaining_shares for p in self.positions.values())
            result.total_profit_rate = (result.total_value - total_cost) / total_cost if total_cost > 0 else 0
        
        return result
    
    # ===== DB 반영 =====

    def _close_position_in_db(self, pos: Position, reason: str, sell_price: float,
                              sell_shares: int = 0) -> None:
        """DB에서 포지션 청산 + 매도 기록 저장 + trade_review 생성

        Args:
            sell_shares: 매도 수량 (0이면 pos.remaining_shares 사용)
        """
        shares = sell_shares if sell_shares > 0 else pos.remaining_shares
        db = None
        try:
            db = Database()
            db.connect()
            db.close_position(pos.stock_code, reason)
            actual_profit_rate = (sell_price - pos.buy_price) / pos.buy_price * 100 if pos.buy_price > 0 else 0
            actual_profit_amount = (sell_price - pos.buy_price) * shares
            trade_id = db.save_trade({
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "action": "sell",
                "shares": shares,
                "price": sell_price,
                "amount": shares * sell_price,
                "reason": reason,
                "profit_rate": actual_profit_rate,
                "profit_amount": actual_profit_amount,
                "buy_price": pos.buy_price,
                "filled_price": sell_price,
                "remaining_shares": 0,
            })

            # trade_review 생성
            buy_date_str = pos.buy_date.strftime("%Y-%m-%d") if hasattr(pos.buy_date, 'strftime') else str(pos.buy_date)
            db.save_trade_review({
                "trade_id": trade_id,
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "buy_date": buy_date_str,
                "sell_date": str(now_kst().date()),
                "buy_price": pos.buy_price,
                "sell_price": sell_price,
                "shares": shares,
                "hold_days": pos.hold_days,
                "profit_rate": actual_profit_rate,
                "profit_amount": actual_profit_amount,
                "sell_reason": reason,
                "strategy_type": self._classify_strategy(reason),
                "trailing_level": pos.trailing_level,
                "max_profit_during_hold": round(pos.max_profit_rate * 100, 2),
                "theme": pos.theme,
            })
        except Exception as e:
            logger.error(f"포지션 청산 DB 업데이트 실패: {e}")
        finally:
            if db:
                db.close()

    @staticmethod
    def _classify_strategy(reason: str) -> str:
        """매도 사유로 전략 유형 분류"""
        if "손절" in reason:
            return "stop_loss"
        elif "트레일링" in reason:
            return "trailing_stop"
        elif "익절" in reason:
            return "take_profit"
        elif "보유" in reason:
            return "max_hold"
        elif "수급" in reason:
            return "supply_exit"
        return "manual"

    def _save_partial_sell_to_db(
        self, pos: Position, sell_shares: int, stage: int, sell_price: float
    ) -> None:
        """부분 매도 시 DB에 매도 기록 저장 + 보유 수량/partial 상태 업데이트 + trade_review"""
        db = None
        try:
            db = Database()
            db.connect()
            # 매도 기록 저장
            actual_profit_rate = (sell_price - pos.buy_price) / pos.buy_price * 100 if pos.buy_price > 0 else 0
            partial_profit = (sell_price - pos.buy_price) * sell_shares
            trade_id = db.save_trade({
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "action": "sell",
                "shares": sell_shares,
                "price": sell_price,
                "amount": sell_shares * sell_price,
                "reason": f"{stage}차 분할익절",
                "profit_rate": actual_profit_rate,
                "profit_amount": partial_profit,
                "buy_price": pos.buy_price,
                "filled_price": sell_price,
                "remaining_shares": pos.remaining_shares,
            })
            # 포트폴리오 보유 수량 업데이트
            db.update_portfolio_shares(pos.stock_code, pos.remaining_shares)
            # portfolio partial/trailing 상태 업데이트
            db.update_portfolio_partial_state(
                pos.stock_code,
                pos.partial_1_executed, pos.partial_2_executed, pos.partial_3_executed,
                pos.trailing_active, pos.trailing_level,
                pos.trailing_stop, pos.highest_price, pos.max_profit_rate,
            )

            # trade_review 생성
            buy_date_str = pos.buy_date.strftime("%Y-%m-%d") if hasattr(pos.buy_date, 'strftime') else str(pos.buy_date)
            db.save_trade_review({
                "trade_id": trade_id,
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "buy_date": buy_date_str,
                "sell_date": str(now_kst().date()),
                "buy_price": pos.buy_price,
                "sell_price": sell_price,
                "shares": sell_shares,
                "hold_days": pos.hold_days,
                "profit_rate": actual_profit_rate,
                "profit_amount": partial_profit,
                "sell_reason": f"{stage}차 분할익절",
                "strategy_type": "take_profit",
                "trailing_level": pos.trailing_level,
                "max_profit_during_hold": round(pos.max_profit_rate * 100, 2),
                "theme": pos.theme,
            })
        except Exception as e:
            logger.error(f"분할 매도 DB 업데이트 실패: {e}")
        finally:
            if db:
                db.close()

    # ===== 손절 =====

    def _check_stop_loss(self, pos: Position) -> bool:
        """
        손절 조건 체크 (트레일링 활성 시 트레일링 스탑에서 처리)

        Args:
            pos: 포지션

        Returns:
            손절 필요 여부
        """
        # 트레일링이 활성화되어 stop_loss_price를 올린 경우,
        # _check_trailing_stop에서 처리하도록 양보
        if pos.trailing_active and pos.trailing_stop is not None:
            return False
        return pos.current_price <= pos.stop_loss_price
    
    async def _execute_stop_loss(self, pos: Position) -> None:
        """손절 실행"""
        logger.warning(f"🔻 손절 발동: {pos.stock_name}")
        logger.warning(f"   현재가 {pos.current_price:,}원 <= 손절가 {pos.stop_loss_price:,}원")
        pnl_label = "수익" if pos.profit_rate >= 0 else "손실"
        logger.warning(f"   {pnl_label}: {pos.profit_rate:.1%} (보유 {pos.hold_days}일)")
        
        # 매도 실행 (전량)
        result = self.trading_engine.execute_stop_loss(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.remaining_shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )
        
        if result.get("success"):
            actual_sell_price = result.get("sell_price", pos.current_price)
            self._close_position_in_db(pos, SellReason.STOP_LOSS.value, actual_sell_price)
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            if self.on_sell_failed:
                self.on_sell_failed(pos, "손절", f"매도 주문 실패: {err}")

        # 콜백
        if self.on_stop_loss:
            actual_sell_price = result.get("sell_price", pos.current_price)
            self.on_stop_loss(pos, actual_sell_price)
    
    # ===== 분할 익절 =====
    
    async def _check_and_execute_partial_profit(self, pos: Position) -> bool:
        """
        분할 익절 체크 및 실행
        
        Returns:
            분할 매도 실행 여부
        """
        profit_rate = pos.profit_rate
        executed = False
        
        # 3차 익절 (+20%) — 20%만 매도, 나머지는 트레일링으로 청산
        if not pos.partial_3_executed and profit_rate >= self.take_profit_3:
            sell_shares = int(pos.shares * self.partial_sell_ratio_3)
            if sell_shares <= 0 and pos.remaining_shares >= 2:
                sell_shares = 1
            if sell_shares > 0:
                success = await self._execute_partial_sell(pos, sell_shares, 3, profit_rate)
                if success:
                    pos.partial_3_executed = True
                    executed = True

        # 2차 익절 (+15%)
        elif not pos.partial_2_executed and profit_rate >= self.take_profit_2:
            sell_shares = int(pos.shares * self.partial_sell_ratio_2)
            if sell_shares <= 0 and pos.remaining_shares >= 2:
                sell_shares = 1  # 소수주: 최소 1주 매도
            if sell_shares > 0:
                success = await self._execute_partial_sell(pos, sell_shares, 2, profit_rate)
                if success:
                    pos.partial_2_executed = True
                    executed = True

        # 1차 익절 (+10%)
        elif not pos.partial_1_executed and profit_rate >= self.take_profit_1:
            sell_shares = int(pos.shares * self.partial_sell_ratio_1)
            if sell_shares <= 0 and pos.remaining_shares >= 2:
                sell_shares = 1  # 소수주: 최소 1주 매도
            if sell_shares > 0:
                success = await self._execute_partial_sell(pos, sell_shares, 1, profit_rate)
                if success:
                    pos.partial_1_executed = True
                    executed = True

        return executed
    
    async def _execute_partial_sell(
        self,
        pos: Position,
        sell_shares: int,
        stage: int,
        profit_rate: float
    ) -> bool:
        """
        분할 매도 실행

        Args:
            pos: 포지션
            sell_shares: 매도 수량
            stage: 익절 단계 (1, 2, 3)
            profit_rate: 수익률

        Returns:
            매도 성공 여부
        """
        if sell_shares <= 0:
            return False

        # 남은 수량보다 많으면 조정
        if sell_shares > pos.remaining_shares:
            sell_shares = pos.remaining_shares

        logger.info(f"🔺 {stage}차 익절 발동: {pos.stock_name}")
        logger.info(f"   현재가: {pos.current_price:,}원")
        logger.info(f"   수익률: {profit_rate:.1%}")
        logger.info(f"   매도 수량: {sell_shares}주 / {pos.remaining_shares}주")
        if pos.shares > 0:
            logger.info(f"   비율: {sell_shares/pos.shares:.0%}")

        # 매도 실행
        result = self.trading_engine.execute_take_profit(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": sell_shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )

        if result.get("success"):
            actual_sell_price = result.get("sell_price", pos.current_price)

            # 남은 수량 업데이트
            pos.remaining_shares -= sell_shares
            logger.info(f"   남은 수량: {pos.remaining_shares}주")

            # 전량 매도 시 DB 포지션 청산
            if pos.remaining_shares <= 0:
                reason = f"{stage}차 익절"
                self._close_position_in_db(pos, reason, actual_sell_price, sell_shares)
            else:
                # 부분 매도: DB에 매도 기록 저장 + 보유 수량 업데이트
                self._save_partial_sell_to_db(
                    pos, sell_shares, stage, actual_sell_price
                )

            # 콜백 (sell_shares 전달)
            if self.on_partial_profit:
                self.on_partial_profit(pos, actual_sell_price, stage, sell_shares)
            return True
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            reason = f"매도 주문 실패: {err}"
            logger.error(f"⚠️ {stage}차 익절 매도 실패: {pos.stock_name} - {reason}")
            if self.on_sell_failed:
                self.on_sell_failed(pos, f"{stage}차 익절", reason)
            return False
    
    # ===== 트레일링 스탑 =====

    def _update_trailing_stop(self, pos: Position) -> None:
        """
        트레일링 스탑 업데이트

        이익 추종 전략 활성화 시: 단계별 트레일링 (L1: 5%, L2: 3%, L3: 2%)
        비활성화 시: 기존 고정 트레일링 (5%)
        """
        profit_rate = pos.profit_rate

        # 최대 수익률 기록
        if profit_rate > pos.max_profit_rate:
            pos.max_profit_rate = profit_rate

        # ===== 이익 추종 전략 (Let Profits Run) =====
        if self.enable_profit_trailing:
            # 트레일링 레벨 업데이트
            old_level = pos.trailing_level

            if profit_rate >= self.trail_level3_threshold:
                # +25% 이상: 레벨 3 (2% 트레일링)
                if pos.trailing_level < 3:
                    pos.trailing_level = 3
                    pos.trailing_active = True
                    logger.info(f"🔥 {pos.stock_name} 레벨3 트레일링 활성화 (고점 -2%)")
            elif profit_rate >= self.trail_level2_threshold:
                # +15% 이상: 레벨 2 (3% 트레일링)
                if pos.trailing_level < 2:
                    pos.trailing_level = 2
                    pos.trailing_active = True
                    logger.info(f"📈 {pos.stock_name} 레벨2 트레일링 활성화 (고점 -3%)")
            elif profit_rate >= self.trail_activation_pct:
                # +8% 이상: 레벨 1 (5% 트레일링) + 본전 손절
                if pos.trailing_level < 1:
                    pos.trailing_level = 1
                    pos.trailing_active = True
                    pos.stop_loss_price = pos.buy_price  # 본전 손절로 이동
                    logger.info(f"📊 {pos.stock_name} 트레일링L1 활성화 (고점 -5%), 본전 손절")

            # 레벨 변경 콜백 (예외가 trailing 계산을 중단하지 않도록 보호)
            if old_level != pos.trailing_level and self.on_trailing_level_change:
                try:
                    self.on_trailing_level_change(pos, old_level, pos.trailing_level)
                except Exception as e:
                    logger.error(f"트레일링 레벨 변경 콜백 오류: {e}")

            # 트레일링 스탑 가격 계산 (레벨별)
            if pos.trailing_active:
                if pos.trailing_level == 3:
                    trail_pct = self.trail_level3_pct  # 2%
                elif pos.trailing_level == 2:
                    trail_pct = self.trail_level2_pct  # 3%
                else:
                    trail_pct = self.trail_level1_pct  # 5%

                new_trailing_stop = pos.highest_price * (1 - trail_pct)

                # 트레일링 스탑은 올라가기만 함 (내려가지 않음)
                if pos.trailing_stop is None or new_trailing_stop > pos.trailing_stop:
                    old_stop = pos.trailing_stop
                    pos.trailing_stop = new_trailing_stop

                    if old_stop and old_level == pos.trailing_level:
                        logger.debug(
                            f"트레일링 스탑 상향: {pos.stock_name} "
                            f"{old_stop:,.0f}원 → {new_trailing_stop:,.0f}원"
                        )

                # 트레일링 스탑이 손절가보다 높으면 손절가 상향
                if pos.trailing_stop and pos.trailing_stop > pos.stop_loss_price:
                    pos.stop_loss_price = pos.trailing_stop

        # ===== 기존 트레일링 스탑 (레거시) =====
        else:
            if not self.enable_trailing_stop:
                return

            # 수익 중일 때만 활성화
            if profit_rate > 0:
                trailing_stop = pos.highest_price * (1 - self.trailing_stop_percent)

                if trailing_stop > pos.stop_loss_price:
                    if pos.trailing_stop is None or trailing_stop > pos.trailing_stop:
                        old_stop = pos.trailing_stop
                        pos.trailing_stop = trailing_stop

                        if old_stop:
                            logger.debug(
                                f"트레일링 스탑 업데이트: {pos.stock_name} "
                                f"{old_stop:,.0f}원 → {trailing_stop:,.0f}원"
                            )
                        else:
                            logger.info(
                                f"트레일링 스탑 활성화: {pos.stock_name} @ {trailing_stop:,.0f}원 "
                                f"(수익률: {profit_rate:.1%})"
                            )
    
    def _check_trailing_stop(self, pos: Position) -> bool:
        """트레일링 스탑 체크"""
        if pos.trailing_stop is None:
            return False
        
        return pos.current_price <= pos.trailing_stop
    
    async def _execute_trailing_stop(self, pos: Position) -> None:
        """트레일링 스탑 실행"""
        # 트레일링 레벨에 따른 로그 메시지
        if pos.trailing_level == 3:
            level_str = "L3 (2%)"
        elif pos.trailing_level == 2:
            level_str = "L2 (3%)"
        elif pos.trailing_level == 1:
            level_str = "L1 (5%)"
        else:
            level_str = "기본"

        logger.info(f"📉 트레일링 스탑 발동: {pos.stock_name} [{level_str}]")
        logger.info(f"   현재가: {pos.current_price:,}원")
        logger.info(f"   트레일링: {pos.trailing_stop:,.0f}원")
        logger.info(f"   최고가: {pos.highest_price:,.0f}원 (최대수익 {pos.max_profit_rate:.1%})")
        logger.info(f"   청산수익: {pos.profit_rate:.1%} (보유 {pos.hold_days}일)")

        # 남은 수량 전량 매도
        result = self.trading_engine.execute_take_profit(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.remaining_shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )

        if result.get("success"):
            actual_sell_price = result.get("sell_price", pos.current_price)

            # 트레일링 레벨에 따른 매도 사유
            if pos.trailing_level >= 3:
                reason = SellReason.TRAILING_L3.value
            elif pos.trailing_level == 2:
                reason = SellReason.TRAILING_L2.value
            elif pos.trailing_level == 1:
                reason = SellReason.TRAILING_L1.value
            else:
                reason = SellReason.TRAILING_STOP.value
            self._close_position_in_db(pos, reason, actual_sell_price)
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            level_label = f"트레일링L{pos.trailing_level}" if pos.trailing_level > 0 else "트레일링"
            if self.on_sell_failed:
                self.on_sell_failed(pos, level_label, f"매도 주문 실패: {err}")

        if self.on_trailing_stop:
            self.on_trailing_stop(pos, actual_sell_price if result.get("success") else pos.current_price)

    # ===== 보유 기간 관리 =====
    
    def _check_max_hold_days(self, pos: Position) -> bool:
        """
        최대 보유 기간 체크 (영업일 기준)

        - 수익 +3% 이상: 최대 10영업일
        - 손실 중: 최대 5영업일
        """
        profit_rate = pos.profit_rate
        hold_days = pos.hold_days
        
        # 수익 중
        if profit_rate >= self.min_profit_for_long_hold:
            return hold_days >= self.max_hold_days_profit
        
        # 손실 중
        else:
            return hold_days >= self.max_hold_days_loss
    
    async def _execute_max_hold_sell(self, pos: Position) -> None:
        """최대 보유 기간 매도"""
        logger.warning(f"⏰ 최대 보유 기간 도달: {pos.stock_name}")
        logger.warning(f"   보유 일수: {pos.hold_days}일")
        logger.warning(f"   수익률: {pos.profit_rate:.1%}")
        
        # 남은 수량 전량 매도
        result = self.trading_engine.execute_take_profit(
            position={
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.remaining_shares,
                "buy_price": pos.buy_price
            },
            current_price=pos.current_price
        )
        
        if result.get("success"):
            actual_sell_price = result.get("sell_price", pos.current_price)
            self._close_position_in_db(pos, SellReason.MAX_HOLD_DAYS.value, actual_sell_price)
            # 콜백 (예외가 remove_position을 차단하지 않도록 보호)
            try:
                if self.on_max_hold_sell:
                    self.on_max_hold_sell(pos, actual_sell_price)
            except Exception as e:
                logger.error(f"보유기간 매도 콜백 오류: {e}")
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            if self.on_sell_failed:
                self.on_sell_failed(pos, "보유기간 초과", f"매도 주문 실패: {err}")

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
            cost = pos.buy_price * pos.remaining_shares
            
            total_value += value
            total_cost += cost
            
            positions_info.append({
                "stock_code": pos.stock_code,
                "stock_name": pos.stock_name,
                "shares": pos.shares,
                "remaining_shares": pos.remaining_shares,
                "buy_price": pos.buy_price,
                "current_price": pos.current_price,
                "highest_price": pos.highest_price,
                "profit": pos.profit,
                "profit_rate": pos.profit_rate * 100,
                "max_profit_rate": pos.max_profit_rate * 100,
                "stop_loss_price": pos.stop_loss_price,
                "trailing_stop": pos.trailing_stop,
                "trailing_level": pos.trailing_level,
                "trailing_active": pos.trailing_active,
                "hold_days": pos.hold_days,
                "partial_1": pos.partial_1_executed,
                "partial_2": pos.partial_2_executed,
                "partial_3": pos.partial_3_executed
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

        print("\n" + "=" * 90)
        print("📊 포트폴리오 현황 V2 (이익 추종 전략)")
        print("=" * 90)
        print(f"  총 평가액: {status['total_value']:>12,.0f}원")
        print(f"  총 투자액: {status['total_cost']:>12,.0f}원")
        print(f"  총 수익금: {status['total_profit']:>+12,.0f}원")
        print(f"  수익률:    {status['profit_rate']:>+12.2f}%")
        print("-" * 90)
        print(f"{'종목':<10} {'현재가':>10} {'수익률':>8} {'최대':>6} {'남은수량':>8} {'보유일':>6} {'트레일링':>12}")
        print("-" * 90)

        for pos in status["positions"]:
            # 트레일링 레벨 표시
            if pos.get("trailing_level", 0) == 3:
                trailing_str = f"L3 {pos['trailing_stop']:,.0f}" if pos['trailing_stop'] else "L3"
            elif pos.get("trailing_level", 0) == 2:
                trailing_str = f"L2 {pos['trailing_stop']:,.0f}" if pos['trailing_stop'] else "L2"
            elif pos.get("trailing_level", 0) == 1:
                trailing_str = f"L1 {pos['trailing_stop']:,.0f}" if pos['trailing_stop'] else "L1"
            elif pos['trailing_stop']:
                trailing_str = f"{pos['trailing_stop']:,.0f}"
            else:
                trailing_str = "-"

            max_profit = pos.get('max_profit_rate', pos['profit_rate'])

            print(f"{pos['stock_name']:<9} "
                  f"{pos['current_price']:>10,} "
                  f"{pos['profit_rate']:>+7.2f}% "
                  f"{max_profit:>+5.1f}% "
                  f"{pos['remaining_shares']:>6}/{pos['shares']:<2} "
                  f"{pos['hold_days']:>5}일 "
                  f"{trailing_str:>12}")

        print("=" * 90)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 70)
    print("📊 포트폴리오 모니터 V2 테스트")
    print("=" * 70)
    
    async def test_monitor():
        monitor = PortfolioMonitorV2(use_mock=True)
        
        # 테스트 포지션 추가
        monitor.add_position(
            stock_code="005930",
            stock_name="삼성전자",
            shares=100,
            buy_price=75000,
            stop_loss_price=71250,  # -5%
            theme="AI반도체"
        )
        
        monitor.add_position(
            stock_code="000660",
            stock_name="SK하이닉스",
            shares=50,
            buy_price=200000,
            stop_loss_price=190000,  # -5%
            theme="AI반도체"
        )
        
        # 상태 출력
        monitor.display_status()
        
        print("\n분할 익절 시뮬레이션...")
        
        # 1차 익절 시뮬레이션 (+10%)
        print("\n[가격 상승: +10%]")
        monitor.positions["005930"].current_price = 82500
        monitor.positions["005930"].highest_price = 82500
        await monitor._check_and_execute_partial_profit(monitor.positions["005930"])
        monitor.display_status()
        
        # 2차 익절 시뮬레이션 (+15%)
        print("\n[가격 상승: +15%]")
        monitor.positions["005930"].current_price = 86250
        monitor.positions["005930"].highest_price = 86250
        await monitor._check_and_execute_partial_profit(monitor.positions["005930"])
        monitor.display_status()
        
        # 트레일링 스탑 시뮬레이션
        print("\n[가격 상승: +20%, 트레일링 스탑 활성화]")
        monitor.positions["005930"].current_price = 90000
        monitor.positions["005930"].highest_price = 90000
        monitor._update_trailing_stop(monitor.positions["005930"])
        monitor.display_status()
        
        print("\n[가격 하락: 트레일링 스탑 발동]")
        monitor.positions["005930"].current_price = 85000
        if monitor._check_trailing_stop(monitor.positions["005930"]):
            await monitor._execute_trailing_stop(monitor.positions["005930"])
        monitor.display_status()
    
    asyncio.run(test_monitor())
    
    print("\n" + "=" * 70)
    print("✅ 포트폴리오 모니터 V2 테스트 완료!")
    print("=" * 70)
