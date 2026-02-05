"""
trading_engine - 자동 매매 엔진 모듈

이 모듈은 주식 자동 매매 기능을 제공합니다.

주요 기능:
- KIS API 주문 (매수/매도)
- 실시간 시세 WebSocket
- 포트폴리오 실행
- 실시간 모니터링 (손절/익절)
- 트레일링 스탑

사용법:
    from modules.trading_engine import (
        TradingEngine,
        PortfolioMonitor,
        KISOrderApi,
        KISWebSocket
    )
    
    # 매매 실행
    engine = TradingEngine()
    result = engine.execute_portfolio(orders)
    
    # 모니터링
    monitor = PortfolioMonitor()
    await monitor.start_monitoring()
"""

# 주문 API
from modules.trading_engine.kis_order_api import (
    KISOrderApi,
    MockOrderApi,
    # 상수
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT
)

# WebSocket
from modules.trading_engine.kis_websocket import (
    KISWebSocket,
    MockWebSocket,
    PriceData,
    OrderbookData
)

# 매매 엔진
from modules.trading_engine.trading_engine import (
    TradingEngine,
    MAX_RETRY,
    ORDER_CHECK_INTERVAL,
    ORDER_TIMEOUT
)

# 모니터링
from modules.trading_engine.portfolio_monitor import (
    PortfolioMonitor,
    Position,
    MonitoringResult,
    CHECK_INTERVAL,
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE
)

# 모니터링 V2 (개선된 버전)
from modules.trading_engine.portfolio_monitor_v2 import (
    PortfolioMonitorV2,
    SellReason
)


__all__ = [
    # 주문 API
    "KISOrderApi",
    "MockOrderApi",
    "ORDER_TYPE_MARKET",
    "ORDER_TYPE_LIMIT",
    # WebSocket
    "KISWebSocket",
    "MockWebSocket",
    "PriceData",
    "OrderbookData",
    # 매매 엔진
    "TradingEngine",
    "MAX_RETRY",
    "ORDER_CHECK_INTERVAL",
    "ORDER_TIMEOUT",
    # 모니터링
    "PortfolioMonitor",
    "Position",
    "MonitoringResult",
    "CHECK_INTERVAL",
    "TRAILING_STOP_ACTIVATION",
    "TRAILING_STOP_DISTANCE",
    # 모니터링 V2
    "PortfolioMonitorV2",
    "SellReason"
]
