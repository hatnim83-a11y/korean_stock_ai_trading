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
import json
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
from modules.trading_engine.sell_lock import sell_lock
from modules.trading_engine.trading_engine import TradingEngine


# ===== 상수 정의 =====
CHECK_INTERVAL = 1  # 체크 간격 (초)
MONITOR_STATE_FILENAME = "monitor_state.json"  # 트레일링 상태 JSON 파일명 (DATABASE_PATH 와 같은 디렉토리)
_RESIDUE_SANITY_RATIO = 1.02  # JSON 폴백 잔재 판정 임계 (highest_price > buy_price × 1.02 → 직전 사이클 잔재)


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
    """포지션 정보 (v17: 분할 진입 + 불타기 + ATR 트레일링 지원)

    정책 분기 (v17 핵심):
    - profit_rate (first_buy_price 기준): 손절/BE/트레일링/2차 트리거
    - profit_rate_avg (avg_buy_price 기준): 분할 익절 트리거
    - buy_price는 first_buy_price의 alias로 유지 (deprecated, 호환성용)
    """
    stock_code: str
    stock_name: str
    shares: int  # 현재 보유 수량 (분할 진입 시 1차+2차 합산)
    remaining_shares: int  # 분할 익절 후 잔여 수량
    buy_price: float  # ⚠️ DEPRECATED: first_buy_price와 동일 유지 (호환성 alias)
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
    trailing_level: int = 0  # 트레일링 레벨 (0=미활성, 1=L1, 2=L2, 3=L3)
    max_profit_rate: float = 0.0  # 최대 수익률 기록 (first_buy_price 기준)

    # WebSocket 가격 수신 확인 (재시작 시 오발동 방지)
    price_confirmed: bool = False

    # ===== v17: 분할 진입 + 불타기 + ATR 트레일링 =====
    # 1차 진입가 (불변, 손절/BE/2차 트리거/트레일링 기준)
    first_buy_price: float = 0.0
    # 평균단가 (2차 진입 시 가중평균 갱신, 분할 익절 트리거 기준)
    avg_buy_price: float = 0.0
    # 진입 회차 (1=1차만, 2=2차까지 완료)
    tranche_count: int = 1
    # 2차 진입 완료 플래그 (중복 발주 방지)
    second_tranche_executed: bool = False
    # 2차 진입 대기 상태 (신규 매수 시 True, 2차 완료/매도 시 False)
    second_tranche_pending: bool = False
    # ATR(N일) 박제값 (트레일링 폭 계산용, 0.0이면 고정값 폴백)
    atr_at_buy: float = 0.0
    # ATR 기간 (감사용)
    atr_period: int = 14

    def __post_init__(self) -> None:
        """first_buy_price 미설정 시 buy_price와 동기화 (마이그레이션 호환).

        기존 코드에서 Position(buy_price=X)로 생성한 경우 first/avg를 buy_price로 폴백.
        """
        if self.first_buy_price <= 0:
            self.first_buy_price = self.buy_price
        if self.avg_buy_price <= 0:
            # avg = first 폴백 (catastrophic bug 방어 — Coder P2)
            self.avg_buy_price = self.first_buy_price

    @property
    def profit(self) -> float:
        """현재 수익금 (남은 수량 기준, avg_buy_price 기준 — 실제 손익)"""
        return (self.current_price - self.avg_buy_price) * self.remaining_shares

    @property
    def profit_rate(self) -> float:
        """현재 수익률 (first_buy_price 기준 — 손절/BE/트레일링/2차 트리거 통일).

        ⚠️ 익절 트리거는 profit_rate_avg를 사용해야 함 (정책 분기).
        """
        if self.first_buy_price > 0:
            return (self.current_price - self.first_buy_price) / self.first_buy_price
        return 0

    @property
    def profit_rate_avg(self) -> float:
        """평균단가 기준 수익률 (분할 익절 트리거 전용 — v17 정책 분기).

        2차 진입 후 평균단가가 상승하면 익절 기준도 동기 상향 → 실제 수익 실현 직관.
        """
        if self.avg_buy_price > 0:
            return (self.current_price - self.avg_buy_price) / self.avg_buy_price
        return 0

    def effective_trailing_pct(self, fixed_pct: float) -> float:
        """트레일링 폭 계산: max(고정값, min(ATR_MULTIPLIER × atr_at_buy / first_buy_price, cap)).

        Args:
            fixed_pct: 레벨별 고정 트레일링 비율 (예: TRAIL_LEVEL1_PCT=0.04)

        Returns:
            실제 적용할 트레일링 비율. ATR 데이터 누락 시 고정값 폴백.

        예시 (cap=TRAILING_ATR_CAP_PCT=0.08):
            - 변동성 큰 종목: ATR=2000, first=50000, MULT=2.0 → atr_pct=8% > 4%(L1) → 8% 적용
            - 안정 종목: ATR=500, first=50000, MULT=2.0 → atr_pct=2% < 4%(L1) → 4% 적용
            - 초고변동 종목: ATR=14878, first=132300, MULT=2.0 → atr_pct=22.5% → cap 8%로 클램프 (피에스케이홀딩스 사건)
            - ATR 누락 (atr_at_buy=0): atr_pct=0 → max(고정, 0) = 고정값 안전 폴백
            - cap=0: 상한 없음(롤백) → 기존 max(고정, atr_pct)
        """
        if not settings.TRAILING_USE_ATR or self.atr_at_buy <= 0 or self.first_buy_price <= 0:
            return fixed_pct
        atr_pct = (settings.ATR_MULTIPLIER * self.atr_at_buy) / self.first_buy_price
        # ATR 폭 상한(cap): 고ATR 종목에서 trailing 폭이 폭발(예 22.5%)해 손절처럼 작동하는 것 차단.
        # cap=0이면 상한 없음(롤백). min()을 무조건 적용하면 cap=0 시 ATR이 0으로 억제되는 역동작이라 가드.
        cap = getattr(settings, 'TRAILING_ATR_CAP_PCT', 0.08)
        if cap > 0:
            atr_pct = min(atr_pct, cap)
        return max(fixed_pct, atr_pct)

    @property
    def value(self) -> float:
        """현재 평가금액 (남은 수량 기준)"""
        return self.current_price * self.remaining_shares

    @property
    def hold_days(self) -> int:
        """보유 영업일수 (주말/공휴일 제외, 1차 매수일 기준 — 2차 진입 후에도 리셋 X)"""
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
        # 누락 종목 지각 편입 콜백 (code, name) — main.py가 텔레그램 1회 경보에 사용
        self.on_position_recovered: Optional[Callable[[str, str], None]] = None

        # 상태
        self._running = False
        self._last_check = now_kst()

        # 누락 종목 자동 편입 스윕 상태 (2026-06-10 매수↔모니터 재시작 레이스 방어)
        # 이미 경보를 보낸 종목은 재편입(예: 매도 후 재진입)되기 전까지 중복 경보하지 않는다.
        self._recovered_alerted: set[str] = set()

        # 매도 실패 카운터 (2026-05-18 Phase 2)
        # KIS 잔량 불일치/JSON 잔재로 매도가 반복 실패할 때 무한 도배를 봉쇄한다.
        # 임계(settings.MAX_SELL_FAILURES) 도달 시 강제 remove_position + 1회 알림.
        self.sell_failure_counts: dict[str, int] = {}

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

        # BE 손절 프리-트레일링 (+5% 도달 시 매수가 -1% 손절)
        self.enable_be_stop = getattr(settings, 'TRAIL_BE_ENABLED', True)
        self.trail_be_activate_pct = getattr(settings, 'TRAIL_BE_ACTIVATE_PCT', 0.05)
        self.trail_be_stop_pct = getattr(settings, 'TRAIL_BE_STOP_PCT', -0.01)

        # BE 설정값 방어 (설정 실수로 인한 대량 오청산 방지)
        if self.enable_be_stop:
            if self.trail_be_stop_pct >= 0:
                logger.error(
                    f"TRAIL_BE_STOP_PCT={self.trail_be_stop_pct}이 0 이상입니다. "
                    f"양수이면 매수가 위로 손절가가 설정되어 즉시 청산 위험. BE 비활성화."
                )
                self.enable_be_stop = False
            elif self.trail_be_activate_pct <= 0:
                logger.error(
                    f"TRAIL_BE_ACTIVATE_PCT={self.trail_be_activate_pct}이 0 이하입니다. "
                    f"매수 즉시 BE 활성화됨. BE 비활성화."
                )
                self.enable_be_stop = False
            elif self.trail_be_activate_pct >= self.trail_activation_pct:
                logger.warning(
                    f"TRAIL_BE_ACTIVATE_PCT({self.trail_be_activate_pct:.0%}) >= "
                    f"TRAIL_ACTIVATION_PCT({self.trail_activation_pct:.0%}): "
                    f"BE 활성화 즉시 L1이 덮어쓰므로 BE 효과 제한적"
                )

        # 트레일링 활성 시 BE 바닥 보존 (ATR 폭 폭발로 trailing_stop < BE 되면 양보 안 함)
        self.trail_be_floor_enabled = getattr(settings, 'TRAIL_BE_FLOOR_ENABLED', True)

        # ATR 트레일링 폭 상한(cap) 설정값 방어 — cap이 고정 레벨보다 작으면 고정값 하한이 무력화될 위험 고지
        atr_cap = getattr(settings, 'TRAILING_ATR_CAP_PCT', 0.08)
        max_fixed_level = max(self.trail_level1_pct, self.trail_level2_pct, self.trail_level3_pct)
        if atr_cap <= 0:
            logger.info("TRAILING_ATR_CAP_PCT<=0: ATR 트레일링 폭 상한 없음(무제한). 고ATR 종목 trailing 폭 폭발 가능.")
        elif atr_cap < max_fixed_level:
            logger.warning(
                f"TRAILING_ATR_CAP_PCT({atr_cap:.0%}) < 최대 고정 레벨({max_fixed_level:.0%}): "
                f"cap이 고정값보다 작아 일부 레벨에서 ATR 항이 항상 클램프됨(max로 고정값 하한은 보장). 설정 확인 권장."
            )

        # 손절
        self.stop_loss = settings.DEFAULT_STOP_LOSS
        self.stop_loss_fast = settings.STOP_LOSS_FAST

        # 손절 보호기간 (Grace Period)
        self.grace_period_days = getattr(settings, 'GRACE_PERIOD_DAYS', 1)
        self.grace_period_stop_loss = getattr(settings, 'GRACE_PERIOD_STOP_LOSS', -0.08)

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
        buy_date: Optional[datetime] = None,
        # v17: 분할 진입 + 불타기 + ATR 필드 (2026-05-27 hotfix)
        # DB→메모리 복원 누락으로 2차 진입 항상 차단되던 버그 fix
        first_buy_price: Optional[float] = None,
        avg_buy_price: Optional[float] = None,
        tranche_count: int = 1,
        second_tranche_executed: bool = False,
        second_tranche_pending: bool = False,
        atr_at_buy: float = 0.0,
        atr_period: int = 14,
    ) -> None:
        """
        포지션 추가 (v17: 분할 진입/불타기/ATR 필드 포함)

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            shares: 보유 수량
            buy_price: 매수가
            stop_loss_price: 손절가
            theme: 테마
            buy_date: 매수일
            first_buy_price: 1차 진입가 (None이면 buy_price 폴백)
            avg_buy_price: 평균단가 (None이면 first_buy_price 폴백 — Position.__post_init__)
            tranche_count: 진입 회차 (default 1)
            second_tranche_executed: 2차 완료 플래그
            second_tranche_pending: 2차 대기 상태 (1차 진입 후 신규 매수면 True)
            atr_at_buy: ATR(14일) 박제값
            atr_period: ATR 기간
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
            buy_date=buy_date or now_kst(),
            # v17 필드
            first_buy_price=first_buy_price if first_buy_price is not None else buy_price,
            avg_buy_price=avg_buy_price if avg_buy_price is not None else (first_buy_price or buy_price),
            tranche_count=tranche_count,
            second_tranche_executed=second_tranche_executed,
            second_tranche_pending=second_tranche_pending,
            atr_at_buy=atr_at_buy,
            atr_period=atr_period,
        )

        self.positions[stock_code] = position
        logger.info(
            f"포지션 추가: {stock_name} ({stock_code}) {shares}주 @ {buy_price:,}원 "
            f"(tranche={tranche_count}, pending={second_tranche_pending}, atr={atr_at_buy})"
        )
    
    def remove_position(self, stock_code: str) -> None:
        """포지션 제거 + position_state DB/JSON 동기 삭제

        매도 시 메모리/DB/JSON 3중 동기화로 monitor_state.json 잔재 데이터로 인한
        BE 손절 오발동(2026-05-12 한화오션 사건)을 차단한다.
        """
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
            # monitor_state.json 잔재 키 동기 삭제 (BE 손절 오발동 차단)
            state_path = Path(settings.DATABASE_PATH).parent / MONITOR_STATE_FILENAME
            try:
                if state_path.exists():
                    with open(state_path) as f:
                        state = json.load(f)
                    if stock_code in state:
                        del state[stock_code]
                        with open(state_path, "w") as f:
                            json.dump(state, f, ensure_ascii=False)
            except Exception as e:
                logger.debug(f"monitor_state.json 잔재 정리 실패: {e}")
            # 매도 실패 카운터도 함께 정리 (재진입 시 0부터 시작)
            self.sell_failure_counts.pop(stock_code, None)
            # 누락 편입 경보 dedup도 정리 — 매도 후 같은 종목 재진입 시 누락되면 다시 경보 허용
            # (__init__ 우회 인스턴스 — 일부 테스트 헬퍼 __new__ — 에서도 안전하도록 hasattr 가드)
            if hasattr(self, "_recovered_alerted"):
                self._recovered_alerted.discard(stock_code)

    def _record_sell_failure(self, pos: "Position", sell_type: str, err: str) -> None:
        """매도 실패 후처리: 카운터 증가 + 임계 도달 시 강제 모니터링 제거.

        2026-05-18 Phase 2: KIS 잔량 불일치/JSON 잔재로 매도가 반복 실패할 때 텔레그램
        도배를 봉쇄한다. 임계(`settings.MAX_SELL_FAILURES`) 도달 시:
          - 강제 `remove_position()` (메모리/position_state DB/JSON 동기 정리)
          - `on_sell_failed` 콜백을 "최종 포기" 사유로 1회만 발사
        portfolio.status 자체는 손대지 않는다 — DB 정합성은 별도 매도 잡/수동 정리에 위임.
        """
        self.sell_failure_counts[pos.stock_code] = (
            self.sell_failure_counts.get(pos.stock_code, 0) + 1
        )
        count = self.sell_failure_counts[pos.stock_code]
        threshold = getattr(settings, "MAX_SELL_FAILURES", 3)

        if count >= threshold:
            logger.error(
                f"🚨 매도 연속 {count}회 실패 — {pos.stock_name}({pos.stock_code}) "
                f"모니터링 강제 제거. 마지막 사유: {err}"
            )
            if self.on_sell_failed:
                try:
                    self.on_sell_failed(
                        pos, sell_type,
                        f"연속 {count}회 실패로 모니터링 제거 (마지막 사유: {err})"
                    )
                except Exception as e:
                    logger.error(f"on_sell_failed 콜백 오류: {e}")
            try:
                self.remove_position(pos.stock_code)  # 내부에서 카운터도 pop
            except Exception as e:
                logger.error(f"강제 remove_position 실패: {e}")
                # remove_position 예외 시에도 카운터는 명시 정리 — 다음 사이클 잔존 차단
                self.sell_failure_counts.pop(pos.stock_code, None)
        else:
            logger.warning(
                f"⚠️ {pos.stock_name} 매도 실패 {count}/{threshold}: {err}"
            )
            if self.on_sell_failed:
                try:
                    self.on_sell_failed(pos, sell_type, err)
                except Exception as e:
                    logger.error(f"on_sell_failed 콜백 오류: {e}")

    def _add_position_from_db_item(self, item: dict) -> None:
        """DB portfolio 행(dict) 하나를 메모리 Position으로 편입한다.

        load_positions_from_db(재시작 전량 적재)와 _sweep_missing_positions(누락분만
        편입) 두 경로가 공유하는 v17 필드 추출 로직. 코드 중복을 막아 2026-05-27
        삼현 케이스(필드 누락 → second_tranche_pending=False 강제) 회귀를 양쪽에서 차단한다.
        """
        # buy_date: buy_date 컬럼 → date 컬럼 폴백 → now_kst()
        buy_date = item.get("buy_date") or item.get("date") or None
        if isinstance(buy_date, str):
            try:
                buy_date = datetime.strptime(buy_date, "%Y-%m-%d").replace(
                    tzinfo=now_kst().tzinfo
                )
            except ValueError:
                buy_date = None

        # v17: DB의 분할 진입/불타기/ATR 필드를 메모리 Position에 복원
        # (2026-05-27 hotfix — 누락 시 second_tranche_pending=False로 default 되어
        # _check_and_execute_pyramid_in 1단계 가드에서 즉시 차단됨)
        self.add_position(
            stock_code=item["stock_code"],
            stock_name=item["stock_name"],
            shares=item["shares"],
            buy_price=item["buy_price"],
            stop_loss_price=item["stop_loss"],
            theme=item.get("theme", ""),
            buy_date=buy_date or now_kst(),
            first_buy_price=item.get("first_buy_price") or item.get("buy_price"),
            avg_buy_price=item.get("avg_buy_price") or item.get("buy_price"),
            tranche_count=int(item.get("tranche_count") or 1),
            second_tranche_executed=bool(item.get("second_tranche_executed") or 0),
            second_tranche_pending=bool(item.get("second_tranche_pending") or 0),
            atr_at_buy=float(item.get("atr_at_buy") or 0.0),
            atr_period=int(item.get("atr_period") or 14),
        )

    def load_positions_from_db(self, only_codes: Optional[set] = None) -> int:
        """
        DB에서 보유 포지션 로드

        Args:
            only_codes: None(기본, 재시작 경로) — 기존 동작 그대로. holding 전량을
                적재(self.positions를 통째로 갈아끼우지는 않지만 모든 holding을
                add_position) + `_restore_trailing_state()`로 전체 트레일링 상태 복원.
                set 지정(스윕 경로) — only_codes에 속하고 아직 메모리에 없는 종목만
                add_position하고, **그 종목들에 대해서만** 트레일링 상태를 복원한다.
                이미 메모리에 있는 기존 포지션의 실시간 highest_price/trailing 상태는
                절대 건드리지 않는다(스윕이 트레일링을 마지막 DB 저장 시점으로
                후퇴시키던 회귀 차단 — 2026-06-10).

        Returns:
            메모리에 적재된(현재 self.positions) 포지션 총 수
        """
        db = None
        try:
            db = Database()
            db.connect()

            portfolio = db.get_portfolio(status="holding")

            # 스윕 경로: 누락분(only_codes ∩ holding ∖ 메모리)만 편입 대상
            restored_codes = set()
            for item in portfolio:
                code = item["stock_code"]
                if only_codes is not None:
                    if code not in only_codes or code in self.positions:
                        continue
                self._add_position_from_db_item(item)
                restored_codes.add(code)

            # 트레일링 상태 복원
            # - 재시작 경로(only_codes is None): 전체 복원(기존 동작)
            # - 스윕 경로: 신규 편입 종목(restored_codes)만 복원 → 기존 포지션 무손상
            if only_codes is None:
                self._restore_trailing_state()
            elif restored_codes:
                self._restore_trailing_state(only_codes=restored_codes)

            logger.info(f"포지션 로드: {len(self.positions)}개")
            return len(self.positions)

        except Exception as e:
            logger.error(f"포지션 로드 실패: {e}")
            return 0
        finally:
            if db:
                db.close()

    def _filter_state_by_holding_whitelist(self, state: dict) -> dict:
        """JSON 폴백 state에서 portfolio.status='holding' 종목만 남긴다.

        2026-05-18 Phase 2: DB가 매도 시 즉시 status='closed'로 갱신되므로,
        JSON에 남은 closed 종목 키는 모두 잔재로 간주한다.
        DB 조회 실패 시 안전을 위해 state를 그대로 반환한다 (기존 ×1.02 sanity가 2차 방어).
        """
        db = None
        try:
            db = Database()
            db.connect()
            holding_codes = {row["stock_code"] for row in db.get_portfolio("holding")}
        except Exception as e:
            logger.warning(f"holding 화이트리스트 조회 실패 → JSON state 그대로 사용: {e}")
            return state
        finally:
            if db:
                db.close()

        residue = [code for code in state.keys() if code not in holding_codes]
        if residue:
            logger.warning(
                f"🚮 JSON 잔재 키 {len(residue)}개 화이트리스트 차단: {residue}"
            )
        return {code: s for code, s in state.items() if code in holding_codes}

    def _restore_trailing_state(self, only_codes: Optional[set] = None) -> None:
        """DB에서 트레일링 상태 복원 (재시작 시). DB 우선, JSON 폴백.

        Args:
            only_codes: None(기본) — self.positions 전체를 복원(재시작 경로).
                set 지정 — 해당 종목만 복원(스윕 누락 편입 경로). 기존 메모리
                포지션의 실시간 트레일링 상태를 후퇴시키지 않기 위한 필터다.
        """
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
            state_path = Path(settings.DATABASE_PATH).parent / MONITOR_STATE_FILENAME
            try:
                if state_path.exists():
                    with open(state_path) as f:
                        state = json.load(f)
            except Exception as e:
                logger.debug(f"monitor_state.json 로드 실패: {e}")

            # JSON 폴백 전용: portfolio.status='holding' 화이트리스트 1차 필터링
            # (2026-05-18 Phase 2) 기아(000270) 사건 — closed 종목의 JSON 잔재가
            # ×1.02 sanity 임계를 우회한 케이스 차단. DB가 single source of truth.
            if state:
                state = self._filter_state_by_holding_whitelist(state)

        if not state:
            return

        restored = 0
        skipped_residue = 0
        for code, pos in self.positions.items():
            # 스윕 경로: 신규 편입 종목만 복원 (기존 포지션 트레일링 상태 무손상)
            if only_codes is not None and code not in only_codes:
                continue
            if code not in state:
                continue
            s = state[code]

            # 잔재 데이터 sanity check (JSON 폴백 전용)
            # 동일 stock_code가 self.positions와 state 양쪽에 있어도,
            # state의 highest_price가 현재 buy_price 대비 비현실적으로 크면
            # 직전 매수 사이클의 잔재로 판단하고 스킵 (2026-05-12 한화오션 사건 차단).
            # DB 경로는 _dump_monitor_state로 매 30초 갱신되므로 잔재 가능성 없음.
            #
            # v17 (Coder P2): tranche_count==2 인 경우 highest > first × 1.02 가 정상 데이터.
            #   - 2차 진입 후 첫 거래일에 +5% 이상 도달은 평범
            #   - threshold를 max(first×1.02, avg×1.02)로 완화하여 정상 데이터 보호
            #   - tranche_count==1 인 경우 기존 임계 그대로 유지
            if not db_source:
                saved_highest_check = s.get("highest_price", 0) or 0
                saved_tranche_count = s.get("tranche_count", pos.tranche_count) or 1
                saved_avg = s.get("avg_buy_price", 0) or 0
                base_first = pos.first_buy_price if pos.first_buy_price > 0 else pos.buy_price
                if saved_tranche_count >= 2 and saved_avg > 0:
                    threshold = max(
                        base_first * _RESIDUE_SANITY_RATIO,
                        saved_avg * _RESIDUE_SANITY_RATIO,
                    )
                else:
                    threshold = base_first * _RESIDUE_SANITY_RATIO
                if saved_highest_check > threshold:
                    logger.warning(
                        f"🚮 JSON 잔재 무시: {pos.stock_name} ({code}) "
                        f"highest={saved_highest_check:,}원 > "
                        f"threshold={threshold:,.0f}원 (tranche={saved_tranche_count}) "
                        f"→ 직전 매수 사이클 잔재"
                    )
                    skipped_residue += 1
                    continue

            # v17: 분할 진입 상태 복원 (avg_buy_price/tranche_count/executed/pending/atr)
            # 폴백: avg <= 0 → avg = first 강제 (catastrophic bug 방어 — Coder P2)
            #
            # 2026-05-28 hotfix (옵션 A): DB source 경로(`position_state` 테이블)에는
            # v17 필드 미저장(DDL에 없음)이라 s.get(..., default) 호출이 항상 default 반환 →
            # add_position이 portfolio 테이블에서 복원한 정상값을 덮어쓰는 회귀 위험.
            # → DB source 경로에서는 v17 필드 키 존재 여부 체크 후 키 있을 때만 덮어쓰기.
            # JSON 폴백 경로는 _dump_monitor_state가 v17 키 모두 dump하므로 그대로.
            saved_first = s.get("first_buy_price", 0) or 0
            if saved_first > 0:
                pos.first_buy_price = saved_first
            else:
                # 마이그레이션 호환: 구 JSON에 first 없으면 buy_price로 폴백
                if pos.first_buy_price <= 0:
                    pos.first_buy_price = pos.buy_price

            saved_avg = s.get("avg_buy_price", 0) or 0
            if saved_avg <= 0:
                # 폴백: avg = first (분할 익절 트리거 무한대 방지)
                pos.avg_buy_price = pos.first_buy_price
                if not db_source:
                    logger.warning(
                        f"⚠️ {pos.stock_name} avg_buy_price 누락 → first로 폴백 "
                        f"(={pos.first_buy_price:,.0f})"
                    )
            else:
                pos.avg_buy_price = saved_avg

            # tranche_count / executed / pending / atr — DB source 경로에서는 키 존재 시에만 덮어쓰기
            # (position_state 테이블에 v17 컬럼 없으면 add_position 복원값 보존)
            if "tranche_count" in s and s.get("tranche_count"):
                pos.tranche_count = s.get("tranche_count") or 1
            if "second_tranche_executed" in s and s.get("second_tranche_executed") is not None:
                pos.second_tranche_executed = bool(s.get("second_tranche_executed"))
            if "second_tranche_pending" in s and s.get("second_tranche_pending") is not None:
                pos.second_tranche_pending = bool(s.get("second_tranche_pending"))
            saved_atr = s.get("atr_at_buy", None)
            if saved_atr is not None and saved_atr > 0:
                pos.atr_at_buy = saved_atr

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

                # ATR cap 즉시 소급 (2026-06-16): 복원된 trailing_stop은 cap 도입 이전에 계산된
                # 옛값(예 ATR폭 22%)일 수 있다. effective_trailing_pct(cap 적용)로 재계산해 더 타이트하면
                # 상향(트레일링은 올라가기만). cap=0이면 폭 동일 → NO-OP. 신고가 갱신을 기다리지 않고
                # 기존 활성 포지션(롯데쇼핑/대주전자재료)에도 cap 적용.
                if pos.highest_price > 0:
                    if pos.trailing_level == 3:
                        fixed_pct = self.trail_level3_pct
                    elif pos.trailing_level == 2:
                        fixed_pct = self.trail_level2_pct
                    else:
                        fixed_pct = self.trail_level1_pct
                    capped_stop = pos.highest_price * (1 - pos.effective_trailing_pct(fixed_pct))
                    if pos.trailing_stop is None or capped_stop > pos.trailing_stop:
                        old_stop = pos.trailing_stop
                        pos.trailing_stop = capped_stop
                        if pos.trailing_stop > pos.stop_loss_price:
                            pos.stop_loss_price = pos.trailing_stop
                        if old_stop is not None and capped_stop > old_stop:
                            logger.info(
                                f"   🔧 ATR cap 소급 상향: {pos.stock_name} "
                                f"트레일링 {old_stop:,.0f} → {capped_stop:,.0f}원 "
                                f"(고점 {pos.highest_price:,.0f} × {1 - pos.effective_trailing_pct(fixed_pct):.1%})"
                            )

                restored += 1
                stop_str = f"{pos.trailing_stop:,.0f}원" if pos.trailing_stop is not None else "미설정"
                logger.info(
                    f"   트레일링 복원: {pos.stock_name} L{pos.trailing_level} "
                    f"(최고가 {pos.highest_price:,}원, 스탑 {stop_str}, "
                    f"현재가 {pos.current_price:,}원)"
                )
            else:
                # BE only 상태 복원 (trailing_active=False지만 과거 +5% 이상 도달 이력 있음)
                # → 재시작 후에도 BE 손절 유지
                raw_rate = s.get("max_profit_rate", 0)
                restored_rate = raw_rate if db_source else raw_rate / 100
                if restored_rate > 0:
                    pos.max_profit_rate = restored_rate
                    saved_highest = s.get("highest_price", 0)
                    if saved_highest > pos.highest_price:
                        pos.highest_price = saved_highest
                    # BE 손절가 즉시 재적용
                    if self.enable_be_stop and pos.max_profit_rate >= self.trail_be_activate_pct:
                        be_stop = pos.buy_price * (1 + self.trail_be_stop_pct)
                        if pos.stop_loss_price < be_stop:
                            pos.stop_loss_price = be_stop
                            logger.info(
                                f"   🛡️ BE 손절 복원: {pos.stock_name} "
                                f"stop → {be_stop:,.0f}원 (max {pos.max_profit_rate:.1%})"
                            )

        source_str = "DB" if db_source else "JSON"
        if restored:
            logger.info(f"트레일링 상태 복원: {restored}개 종목 (소스: {source_str})")
        if skipped_residue:
            logger.warning(f"잔재 데이터 스킵: {skipped_residue}개 종목 (JSON 폴백 sanity check)")
    
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
        """모니터링 중지 (정지 직전 최종 dump로 JSON 잔재 키 봉쇄)"""
        self._running = False
        await self.websocket.stop()
        # 정지 직전 최종 _dump_monitor_state: self.positions에서 제거된 종목 키가
        # JSON 파일에 잔존하지 않도록 마지막 동기화 보장 (2026-05-12 한화오션 사건 차단)
        try:
            self._dump_monitor_state()
        except Exception as e:
            logger.debug(f"stop_monitoring 최종 dump 실패: {e}")
        logger.info("모니터링 중지")
    
    async def _monitor_loop(self) -> None:
        """모니터링 루프"""
        import time as _time
        status_interval = 30 * 60  # 30분마다 상태 로그
        db_update_interval = 5 * 60  # 5분마다 DB 가격 갱신
        state_dump_interval = 30  # 30초마다 대시보드용 상태 덤프
        sweep_interval = getattr(settings, "MONITOR_MISSING_SWEEP_INTERVAL_SEC", 60)
        last_status_log = 0
        last_db_update = 0
        last_state_dump = 0
        last_sweep = 0

        while self._running:
            await asyncio.sleep(CHECK_INTERVAL)

            # 장 시간 체크 (09:00 ~ 15:30)
            if not self._is_market_hours():
                continue

            now_ts = _time.time()

            # 누락 종목 자동 편입 스윕 (손익 체크 前 — 편입 즉시 당일 청산 로직 적용)
            if getattr(settings, "MONITOR_MISSING_SWEEP_ENABLED", True) and \
               now_ts - last_sweep >= sweep_interval:
                last_sweep = now_ts
                self._sweep_missing_positions()

            # 손익 체크
            await self._check_all_positions()

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
    
    def _sweep_missing_positions(self) -> None:
        """DB holding ∖ 메모리 포지션 차집합을 탐지해 누락 종목을 지각 편입한다.

        2026-06-10 매수↔모니터 재시작 레이스 심층 방어(타이밍 무관):
        매수 완료 체이닝/cron 재시작이 어떤 이유로든 누락을 남겨도, 모니터 루프가
        주기적으로 DB와 메모리를 대조해 빠진 종목을 자동 복구한다.

        안전 원칙:
        - DB `status='holding'` 종목만 대상 (매도 직후 잔재 오편입 방지).
        - 편입은 `load_positions_from_db(only_codes=missing)`로 수행 — 누락분만 편입하고
          기존 DB 복원 경로(_add_position_from_db_item)를 공유해 v17 필드(first/avg_buy_price,
          tranche_count, second_tranche_pending/executed, atr_at_buy)를 누락 없이 복원한다
          (2026-05-27 삼현 케이스 회귀 차단).
        - **기존 메모리 포지션은 절대 건드리지 않는다.** only_codes 경로는 self.positions를
          초기화하지 않고, 트레일링 복원도 신규 편입 종목으로 한정한다 → 이미 모니터링
          중이던 종목의 실시간 highest_price/trailing 상태가 마지막 DB 저장 시점으로
          후퇴(=손절/트레일링 후퇴)하던 회귀를 차단(2026-06-10).
        - WebSocket 동적 SUBSCRIBE 미지원이라 신규 종목은 다음 cron/체이닝 재시작 또는
          DB 폴링 갱신으로 실시간 가격을 받는다. 본 스윕의 1차 목적은 "메모리 편입으로
          손절/트레일링/분할익절 로직이 작동하게 만드는 것"이다.
        - 경보는 종목별 1회만 (스팸 방지). 이미 메모리에 있던 종목은 대상 아님.
        """
        db = None
        try:
            db = Database()
            db.connect()
            holding = db.get_portfolio(status="holding")
        except Exception as e:
            logger.debug(f"누락 스윕 DB 조회 실패: {e}")
            return
        finally:
            if db:
                db.close()

        holding_codes = {item["stock_code"] for item in holding}
        missing = holding_codes - set(self.positions.keys())
        if not missing:
            return

        # 누락 종목명 매핑 (경보용)
        name_by_code = {item["stock_code"]: item.get("stock_name", "") for item in holding}
        logger.warning(
            f"🚨 모니터 누락 종목 감지 → 지각 편입: "
            f"{', '.join(f'{name_by_code.get(c, c)}({c})' for c in missing)} "
            f"(DB holding={len(holding_codes)} / 메모리={len(self.positions)})"
        )

        # 누락분(missing)만 편입한다. only_codes 지정 시 load_positions_from_db는
        # self.positions를 초기화하지 않고, missing ∩ holding ∖ 메모리 종목만
        # add_position + 그 종목들만 트레일링 복원한다. 이미 메모리에 있던 기존
        # 포지션의 실시간 highest_price/trailing 상태는 절대 건드리지 않는다
        # (전량 재적재로 트레일링이 마지막 DB 저장 시점까지 후퇴하던 회귀 차단 — 2026-06-10).
        try:
            self.load_positions_from_db(only_codes=missing)
        except Exception as e:
            logger.error(f"누락 스윕 재적재 실패: {e}")
            return

        # 실제 편입 성공분만 1회 경보 (콜백은 main.py가 텔레그램으로 전달)
        for code in missing:
            if code not in self.positions:
                # 재적재 후에도 미편입(드문 케이스) → 다음 사이클 재시도, 경보 보류
                continue
            if code in self._recovered_alerted:
                continue
            self._recovered_alerted.add(code)
            if self.on_position_recovered:
                try:
                    self.on_position_recovered(code, name_by_code.get(code, ""))
                except Exception as e:
                    logger.error(f"on_position_recovered 콜백 오류: {e}")

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
        """트레일링 상태를 DB + JSON에 동시 저장 (30초 간격).

        v17: 분할 진입 + 불타기 키 6개 추가 (first_buy_price, avg_buy_price,
        tranche_count, second_tranche_executed, second_tranche_pending, atr_at_buy)
        → 봇 재시작 후 복원 시 정책 분기 유지 + sanity 임계 정확 판정.
        """
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
                # v17: 분할 진입 + 불타기 + ATR 상태
                "first_buy_price": pos.first_buy_price,
                "avg_buy_price": pos.avg_buy_price,
                "tranche_count": pos.tranche_count,
                "second_tranche_executed": pos.second_tranche_executed,
                "second_tranche_pending": pos.second_tranche_pending,
                "atr_at_buy": pos.atr_at_buy,
            }

        # 1) JSON 파일 (대시보드 캐시용)
        state_path = Path(settings.DATABASE_PATH).parent / MONITOR_STATE_FILENAME
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
            
            # 2. 분할 익절 체크 (3단계) — v17: avg_buy_price 기준
            partial_sell = await self._check_and_execute_partial_profit(pos)
            if partial_sell:
                result.partial_profit_triggered.append(stock_code)
                continue  # 이번 주기 2차/트레일링 스킵 (우선순위: Sell > Buy)

            # 2-B. v17 2차 진입(불타기) 체크 — 분할익절 미발화 시에만 평가
            # 우선순위 가드: SellLock 점유 시 skip → 다음 사이클 재평가
            pyramid_executed = await self._check_and_execute_pyramid_in(pos)
            if pyramid_executed:
                continue  # 트레일링 체크 스킵 (avg 갱신 후 다음 사이클부터 적용)

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
                              sell_shares: int = 0, slippage: Optional[float] = None) -> None:
        """DB에서 포지션 청산 + 매도 기록 저장 + trade_review 생성

        Args:
            sell_shares: 매도 수량 (0이면 pos.remaining_shares 사용)
            slippage: 매도 슬리피지(%) — execute_stop_loss/execute_take_profit가 계산해 전달
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
                "slippage": slippage,
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
        self, pos: Position, sell_shares: int, stage: int, sell_price: float,
        slippage: Optional[float] = None
    ) -> None:
        """부분 매도 시 DB에 매도 기록 저장 + 보유 수량/partial 상태 업데이트 + trade_review

        Args:
            slippage: 매도 슬리피지(%) — execute_take_profit가 계산해 전달
        """
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
                "slippage": slippage,
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

        보호기간(Grace Period): 매수 후 GRACE_PERIOD_DAYS 이내에는
        넓은 손절선(-8%)을 적용하여 장중 노이즈에 의한 조기 손절을 방지.

        Args:
            pos: 포지션

        Returns:
            손절 필요 여부
        """
        # 트레일링이 활성화되어 stop_loss_price를 올린 경우 _check_trailing_stop에서 처리하도록 양보.
        # 단 ATR 폭 폭발로 trailing_stop이 stop_loss_price(BE 포함)보다 낮아지면 양보하지 않고
        # BE 손절 체크를 진행한다 (2026-06-16 피에스케이홀딩스: trailing 119,905 < BE 130,977 방치 사건).
        # TRAIL_BE_FLOOR_ENABLED=False면 기존 무조건 양보로 롤백.
        if (pos.trailing_active and pos.trailing_stop is not None
                and (not self.trail_be_floor_enabled or pos.trailing_stop >= pos.stop_loss_price)):
            return False

        # 보호기간: hold_days <= GRACE_PERIOD_DAYS이면 넓은 손절선 적용
        # 단 BE 활성화 후(+5% 도달 이력 있음)에는 BE 손절가가 우선
        # — 당일 +5% 찍고 하락하는 케이스(오이솔루션 등)를 방어
        if pos.hold_days <= self.grace_period_days:
            grace_stop_price = pos.buy_price * (1 + self.grace_period_stop_loss)
            be_active = (
                self.enable_be_stop
                and pos.max_profit_rate >= self.trail_be_activate_pct
            )
            # BE 활성 시 더 타이트한 BE 기준 적용, 아니면 넓은 grace 기준
            effective_stop = max(grace_stop_price, pos.stop_loss_price) if be_active else grace_stop_price
            if pos.current_price <= effective_stop:
                label = "BE 손절" if be_active and effective_stop > grace_stop_price else "보호기간 비상 손절"
                logger.info(
                    f"   ⚠️ {label}: {pos.stock_name} "
                    f"현재가 {pos.current_price:,} <= 손절가 {effective_stop:,.0f} "
                    f"(보유 {pos.hold_days}일)"
                )
                return True
            return False

        hit = pos.current_price <= pos.stop_loss_price
        if hit and pos.trailing_active and pos.trailing_stop is not None:
            # 트레일링 양보 해제(trailing_stop < stop_loss_price) 경로 — BE 바닥에서 청산.
            # 무음 청산 방지 + 제안서 관찰항목("양보 안 함 분기 발동 케이스 수집") 연동.
            # 실제 손절 발주가 뒤따르므로 warning (기존 손절 경로와 레벨 일관).
            logger.warning(
                f"   🛡️ BE 바닥 손절(트레일링 양보 해제): {pos.stock_name} "
                f"현재가 {pos.current_price:,} <= 손절가 {pos.stop_loss_price:,.0f} "
                f"(trailing_stop {pos.trailing_stop:,.0f} < 손절가, 보유 {pos.hold_days}일)"
            )
        return hit
    
    async def _execute_stop_loss(self, pos: Position) -> None:
        """손절 실행"""
        # SellLock: 09:00 조기 모니터링 도입 후 midweek/hold_period 매도 잡과의 race 봉쇄
        # acquire 실패 = 다른 매도 경로 진행 중 → 이 종목은 스킵
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            if not sell_lock.acquire(pos.stock_code, owner="monitor_stop_loss"):
                logger.info(
                    f"   [SellLock] {pos.stock_name} 손절 skip — "
                    f"{sell_lock.owner(pos.stock_code)} 이 매도 처리 중"
                )
                return

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
            self._close_position_in_db(
                pos, SellReason.STOP_LOSS.value, actual_sell_price,
                slippage=result.get("slippage"),
            )
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            self._record_sell_failure(pos, "손절", f"매도 주문 실패: {err}")

        # 콜백
        if self.on_stop_loss:
            actual_sell_price = result.get("sell_price", pos.current_price)
            self.on_stop_loss(pos, actual_sell_price)
    
    # ===== 분할 익절 =====
    
    async def _check_and_execute_partial_profit(self, pos: Position) -> bool:
        """
        분할 익절 체크 및 실행 (v17: avg_buy_price 기준 트리거 — 정책 분기)

        v17 변경:
        - 트리거 기준: `pos.profit_rate_avg` (평균단가 기준, 수익 실현 직관)
        - PARTIAL_PROFIT_BASE='first' 토글 시 기존 first 기준 복귀 (롤백 안전장치)
        - 임계값 +12/+20/+30%, 비율 25/20/20% (config에서 갱신)

        Returns:
            분할 매도 실행 여부
        """
        # v17: PARTIAL_PROFIT_BASE 토글로 트리거 기준 선택 (avg 권장 / first 롤백용)
        base_mode = getattr(settings, "PARTIAL_PROFIT_BASE", "avg")
        if base_mode == "first":
            profit_rate = pos.profit_rate
        else:
            profit_rate = pos.profit_rate_avg

        # SellLock: 매도 잡(midweek/hold_period)이 같은 종목을 처리 중이면 분할익절 skip
        # 트리거 조건이 충족된 경우만 잠금 시도 (건당 acquire 호출 비용 최소화)
        trigger_hit = (
            (not pos.partial_3_executed and profit_rate >= self.take_profit_3)
            or (not pos.partial_2_executed and profit_rate >= self.take_profit_2)
            or (not pos.partial_1_executed and profit_rate >= self.take_profit_1)
        )
        if trigger_hit and settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            if not sell_lock.acquire(pos.stock_code, owner="monitor_partial"):
                logger.info(
                    f"   [SellLock] {pos.stock_name} 분할익절 skip — "
                    f"{sell_lock.owner(pos.stock_code)} 이 매도 처리 중"
                )
                return False

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

            # 전량 매도 시 DB 포지션 청산 + 메모리/DB/JSON 동기 정리
            if pos.remaining_shares <= 0:
                reason = f"{stage}차 익절"
                self._close_position_in_db(
                    pos, reason, actual_sell_price, sell_shares,
                    slippage=result.get("slippage"),
                )
                # remove_position 호출로 monitor_state.json 잔재 봉쇄
                # (전량 익절 경로가 콜백 호출 외에 메모리/JSON 정리를 자체 수행하지 않아 누락되던 문제)
                self.remove_position(pos.stock_code)
            else:
                # 부분 매도: DB에 매도 기록 저장 + 보유 수량 업데이트
                self._save_partial_sell_to_db(
                    pos, sell_shares, stage, actual_sell_price,
                    slippage=result.get("slippage"),
                )

            # 콜백 (sell_shares 전달)
            if self.on_partial_profit:
                self.on_partial_profit(pos, actual_sell_price, stage, sell_shares)
            return True
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            reason = f"매도 주문 실패: {err}"
            logger.error(f"⚠️ {stage}차 익절 매도 실패: {pos.stock_name} - {reason}")
            # 분할 익절 실패는 카운터 미적용 — 일시적 호가 문제가 흔하며,
            # 트리거 조건(+10/15/20%)에 다시 도달해야 발화하므로 도배 위험이 낮다.
            # 손절/트레일링/보유기간 초과만 카운터로 무한 루프 차단.
            if self.on_sell_failed:
                try:
                    self.on_sell_failed(pos, f"{stage}차 익절", reason)
                except Exception as e:
                    logger.error(f"on_sell_failed 콜백 오류: {e}")
            return False
    
    # ===== v17: 2차 진입(불타기) =====

    async def _check_and_execute_pyramid_in(self, pos: Position) -> bool:
        """v17 2차 진입(불타기) 체크 및 실행.

        트리거 (모두 AND):
        - settings.TRANCHE_ENTRY_ENABLED 활성화
        - pos.second_tranche_pending == True
        - pos.second_tranche_executed == False
        - pos.profit_rate (first_buy_price 기준) >= PYRAMID_TRIGGER_PCT (+5%)

        사전 조건:
        - MarketGuard 재호출 (CRISIS/DANGER 시 스킵)
        - SellLock 미점유 (분할익절 발화 사이클이면 skip — 다음 사이클 재평가)
        - BuyLock acquire (try/finally)

        실행:
        - DRY_RUN_PYRAMID=True 면 KIS 실발주 차단, 알림만 발화
        - trading_engine.execute_portfolio([order], save_to_db=False) — 기존 row 유지
        - 체결 시: 가중평균 avg 계산 → DB UPDATE(update_portfolio_second_tranche)
                  → trades save → 메모리 Position 갱신 → dump_monitor_state → 텔레그램

        Returns:
            True=2차 진입 발주 시도(체결 결과 무관, 후속 트레일링 평가 skip),
            False=조건 미충족 → 트레일링 정상 평가
        """
        # 1) 토글 가드
        if not getattr(settings, "TRANCHE_ENTRY_ENABLED", False):
            return False

        # 1-B) 종가베팅 시간 보호 가드 — 15:15 이후 v17 2차 진입 차단 (2026-05-23 hotfix)
        # 종가베팅 phase1=15:18 / phase2=15:25 매수 시점 보호. 종가베팅 cap=50% 정책
        # 충돌 방지 (closing_bet_pool_cap 50% 정책 + absorb_swing_idle=true 환경).
        # 15:15~15:30 사이 +5% 도달 종목은 자연 만료(다음 거래일 재발화 또는 보유기간 종료).
        now = now_kst()
        if now.hour == 15 and now.minute >= 15:
            logger.debug(
                f"[v17] {pos.stock_name} 2차 진입 skip: 15:15+ 종가베팅 시간 보호"
            )
            return False

        # 2) 상태 가드
        if pos.second_tranche_executed or not pos.second_tranche_pending:
            return False

        # 3) 트리거 (first_buy_price 기준)
        trigger_pct = getattr(settings, "PYRAMID_TRIGGER_PCT", 0.05)
        if pos.profit_rate < trigger_pct:
            return False

        stock_code = pos.stock_code
        stock_name = pos.stock_name

        # 4) SellLock 우선 가드 — 분할익절 발화 사이클이면 skip (Coder P3)
        if sell_lock.is_locked(stock_code):
            logger.debug(
                f"[v17] {stock_name} 2차 진입 skip: SellLock 점유 중 (다음 사이클 재평가)"
            )
            return False

        # 5) MarketGuard 재호출 (5분 캐시 — market_guard 모듈 내부 캐시)
        try:
            from modules.market_guard import MarketGuard
            guard = MarketGuard()
            status, _ctx = await asyncio.to_thread(guard.check)
            # CRISIS/DANGER 면 2차 진입 스킵
            from modules.market_guard import MarketStatus
            if status in (MarketStatus.CRISIS, MarketStatus.DANGER):
                logger.info(
                    f"[v17] {stock_name} 2차 진입 스킵: MarketGuard {status.name}"
                )
                return False
        except Exception as e:
            # 가드 모듈 실패는 보수적으로 진행 (가드 미적용)
            logger.debug(f"[v17] MarketGuard 호출 실패 {stock_code}: {e}")

        # 6) BuyLock acquire
        from modules.trading_engine.buy_lock import buy_lock
        owner = "pyramid_in"
        if not buy_lock.acquire(stock_code, owner):
            logger.debug(f"[v17] {stock_name} 2차 진입 skip: BuyLock 이미 점유")
            return False

        try:
            # 7) 2차 매수 금액 산정
            mode = getattr(settings, "TRANCHE_SECOND_AMOUNT_MODE", "mirror_first")
            first_amount = pos.first_buy_price * pos.shares  # 1차 진입 금액 추정
            # tranche 비율로 환산 — first 진입은 shares×first_buy_price, 2차는 동일 비율
            second_ratio = getattr(settings, "TRANCHE_SECOND_RATIO", 0.5)
            first_ratio = getattr(settings, "TRANCHE_FIRST_RATIO", 0.5)
            if first_ratio <= 0:
                first_ratio = 0.5
            target_amount = first_amount * (second_ratio / first_ratio)

            if mode == "orderable_cash":
                try:
                    from modules.kis_api import KISApi
                    cash = KISApi().get_orderable_cash()
                    target_amount = min(target_amount, max(0, cash))
                except Exception as e:
                    logger.debug(f"[v17] orderable_cash 조회 실패, mirror_first 폴백: {e}")

            # 7-B) swing_capital_pool 한도 적용 (2026-05-23 hotfix)
            # 종가베팅 cap 50% 정책 정합 — v17 1차+2차 합쳐 swing_pool(90%) 한도 절대 안 넘김.
            # fund_guard.compute_capital_limit 의 swing_used 계산(cost_basis) 과 동일 식.
            # 환경변수 SWING_CAPITAL_RATIO 미설정 시 default 0.9 (settings.yaml과 동기화).
            try:
                import os as _os
                swing_capital_ratio = float(_os.getenv("SWING_CAPITAL_RATIO", "0.9"))
                # TOTAL_CAPITAL 이 동적이면 trading_engine.get_total_value 사용, 폴백 settings.TOTAL_CAPITAL
                total_capital_now = getattr(settings, "TOTAL_CAPITAL", 0)
                try:
                    if hasattr(self.trading_engine, "get_balance"):
                        bal = self.trading_engine.get_balance()
                        tv = bal.get("total_value") or 0
                        if tv > 0:
                            total_capital_now = tv
                except Exception:
                    pass
                if total_capital_now > 0:
                    swing_pool = int(total_capital_now * swing_capital_ratio)
                    swing_used = sum(
                        (p.first_buy_price or p.buy_price or 0) * (p.shares or 0)
                        for p in self.positions.values()
                    )
                    swing_available = max(0, swing_pool - swing_used)
                    if target_amount > swing_available:
                        logger.info(
                            f"[v17] {stock_name} 2차 진입 금액 축소: "
                            f"target={target_amount:,.0f}원 → swing_pool 잔여={swing_available:,}원 "
                            f"(used={swing_used:,}원 / pool={swing_pool:,}원, ratio={swing_capital_ratio:.0%})"
                        )
                        target_amount = float(swing_available)
            except Exception as e:
                logger.debug(f"[v17] swing_pool 한도 계산 실패 {stock_code}: {e}")

            # 8) 2차 매수 수량 계산
            second_qty = int(target_amount // max(1, pos.current_price))
            if second_qty <= 0:
                logger.warning(
                    f"[v17] {stock_name} 2차 진입 스킵: 산정 수량 0 "
                    f"(target_amount={target_amount}, cur_price={pos.current_price})"
                )
                return False

            # 9) DRY_RUN 게이트 — 시뮬레이션 모드면 실발주 차단
            if getattr(settings, "DRY_RUN_PYRAMID", False):
                logger.info(
                    f"[v17][DRY_RUN] 2차 진입 시뮬레이션: {stock_name} ({stock_code}) "
                    f"+{second_qty}주 @{pos.current_price:,.0f}원 "
                    f"(profit_rate={pos.profit_rate:+.2%}, target={target_amount:,.0f}원)"
                )
                # 메모리 상태는 갱신하지 않음 (드라이런)
                return True

            # 10) 실발주 (limit_aggressive — 매도 1호가 + 재시도)
            order = {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": second_qty,
                "order_type": "limit_aggressive",
                "price": int(pos.current_price),
                "expected_price": int(pos.current_price),
                "amount": int(target_amount),
                "theme": pos.theme,
            }
            result = await asyncio.to_thread(
                self.trading_engine.execute_portfolio,
                [order],
                False,  # save_to_db=False (기존 holding row 유지, 별도 update_portfolio_second_tranche 호출)
                True,   # wait_for_fill
            )

            # 11) 체결 결과 확인
            filled_qty = 0
            filled_price = 0.0
            executed_orders = result.get("orders", [])
            for o in executed_orders:
                if o.get("success") and o.get("stock_code") == stock_code:
                    filled_qty = o.get("quantity", 0) or 0
                    filled_price = o.get("filled_price") or o.get("price", 0) or 0
                    break

            if filled_qty <= 0 or filled_price <= 0:
                logger.warning(
                    f"[v17] {stock_name} 2차 진입 실패: filled_qty={filled_qty}, "
                    f"filled_price={filled_price}, result={result.get('failed_count')}건 실패"
                )
                return True  # 발주 시도는 했으므로 후속 트레일링은 skip

            # 12) 가중평균 avg_buy_price 계산
            total_qty_after = pos.shares + filled_qty
            new_avg = (
                pos.first_buy_price * pos.shares + filled_price * filled_qty
            ) / total_qty_after if total_qty_after > 0 else pos.first_buy_price

            # 13) DB UPDATE (기존 row 유지하며 누적)
            db = None
            try:
                db = Database()
                db.connect()
                rowcount = db.update_portfolio_second_tranche(
                    stock_code=stock_code,
                    added_shares=filled_qty,
                    second_filled_price=filled_price,
                    avg_buy_price=new_avg,
                )
                if rowcount == 0:
                    logger.warning(
                        f"[v17] {stock_name} 2차 진입 DB UPDATE 0 row — holding 없음 (race?)"
                    )
                # trades 테이블에 2차 진입 기록
                db.save_trade({
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "action": "buy",
                    "shares": filled_qty,
                    "price": filled_price,
                    "amount": filled_qty * filled_price,
                    "reason": "2차 진입 (불타기)",
                    "buy_price": filled_price,
                    "filled_price": filled_price,
                    "slippage": None,
                    "remaining_shares": total_qty_after,
                })
            except Exception as e:
                logger.error(f"[v17] {stock_name} 2차 진입 DB 기록 실패: {e}")
            finally:
                if db:
                    db.close()

            # 14) 메모리 Position 갱신
            pos.shares = total_qty_after
            pos.remaining_shares = pos.remaining_shares + filled_qty
            pos.avg_buy_price = new_avg
            pos.tranche_count = 2
            pos.second_tranche_executed = True
            pos.second_tranche_pending = False

            # 15) 3중 동기화 즉시 호출
            try:
                self._dump_monitor_state()
            except Exception as e:
                logger.debug(f"[v17] dump_monitor_state 실패 {stock_code}: {e}")

            # 16) 텔레그램 알림
            try:
                if hasattr(self, "notifier") and self.notifier:
                    self.notifier.send_message(
                        f"🔥 2/2 불타기 진입\n"
                        f"종목: {stock_name} ({stock_code})\n"
                        f"매수: {filled_qty}주 @ {filled_price:,.0f}원\n"
                        f"first={pos.first_buy_price:,.0f}원, "
                        f"avg={new_avg:,.0f}원 (가중평균)\n"
                        f"트리거: +{pos.profit_rate*100:.1f}% (first 기준)\n"
                        f"누적 보유: {total_qty_after}주"
                    )
            except Exception as e:
                logger.debug(f"[v17] 텔레그램 알림 실패: {e}")

            logger.info(
                f"✅ [v17] {stock_name} 2차 진입 완료: +{filled_qty}주 @{filled_price:,.0f}, "
                f"avg={new_avg:,.2f}, total={total_qty_after}주"
            )
            return True
        finally:
            buy_lock.release(stock_code)

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

        # ===== BE 손절 프리-트레일링 (+5% 도달 시 매수가 -1% 손절) =====
        # max_profit_rate 기준 → +5% 터치 후 조정/재시작에도 유지됨
        # L1(+8%) 활성화 시 stop_loss_price = buy_price(BE 0%)로 자연 상향 오버라이드
        if self.enable_be_stop and pos.max_profit_rate >= self.trail_be_activate_pct:
            be_stop = pos.buy_price * (1 + self.trail_be_stop_pct)
            if pos.stop_loss_price < be_stop:
                old_stop = pos.stop_loss_price
                pos.stop_loss_price = be_stop
                logger.info(
                    f"🛡️ {pos.stock_name} BE 손절 활성화: "
                    f"{old_stop:,.0f}원 → {be_stop:,.0f}원 "
                    f"(매수가 {self.trail_be_stop_pct*100:+.1f}%)"
                )

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

            # 트레일링 스탑 가격 계산 (레벨별 — v17: max(고정값, 2.0×ATR/first_buy_price))
            if pos.trailing_active:
                if pos.trailing_level == 3:
                    fixed_pct = self.trail_level3_pct  # 2%
                elif pos.trailing_level == 2:
                    fixed_pct = self.trail_level2_pct  # 3%
                else:
                    fixed_pct = self.trail_level1_pct  # 5%

                # v17: ATR 기반 동적 폭 (atr_at_buy=0 시 고정값 폴백)
                trail_pct = pos.effective_trailing_pct(fixed_pct)

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
        # SellLock: 매도 잡(midweek/hold_period)이 동일 종목을 처리 중이면 skip
        if settings.PARTIAL_PROFIT_EARLY_MONITORING_ENABLED:
            if not sell_lock.acquire(pos.stock_code, owner="monitor_trailing"):
                logger.info(
                    f"   [SellLock] {pos.stock_name} 트레일링 skip — "
                    f"{sell_lock.owner(pos.stock_code)} 이 매도 처리 중"
                )
                return

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
            self._close_position_in_db(
                pos, reason, actual_sell_price,
                slippage=result.get("slippage"),
            )
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            level_label = f"트레일링L{pos.trailing_level}" if pos.trailing_level > 0 else "트레일링"
            self._record_sell_failure(pos, level_label, f"매도 주문 실패: {err}")

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
            self._close_position_in_db(
                pos, SellReason.MAX_HOLD_DAYS.value, actual_sell_price,
                slippage=result.get("slippage"),
            )
            # 콜백 (예외가 remove_position을 차단하지 않도록 보호)
            try:
                if self.on_max_hold_sell:
                    self.on_max_hold_sell(pos, actual_sell_price)
            except Exception as e:
                logger.error(f"보유기간 매도 콜백 오류: {e}")
            self.remove_position(pos.stock_code)
        else:
            err = result.get("message") or result.get("error", "알 수 없는 오류")
            self._record_sell_failure(pos, "보유기간 초과", f"매도 주문 실패: {err}")

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
