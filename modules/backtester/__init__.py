"""
backtester 모듈

벡터화된 백테스트 시스템을 제공합니다.

주요 기능:
1. 과거 데이터 로드 및 전처리
2. 벡터화된 전략 시뮬레이션
3. 성과 지표 계산 (수익률, MDD, Sharpe ratio, 승률)
4. 결과 시각화 및 리포트

사용법:
    from modules.backtester import BacktestEngine
    
    engine = BacktestEngine()
    result = engine.run(strategy, start_date, end_date)
    result.plot()
"""

from .data_loader import DataLoader, MarketData
from .strategy_simulator import StrategySimulator, BacktestResult, BacktestConfig
from .backtest_engine import BacktestEngine, PortfolioBacktestResult

__all__ = [
    "DataLoader",
    "MarketData",
    "StrategySimulator",
    "BacktestResult",
    "BacktestConfig",
    "BacktestEngine",
    "PortfolioBacktestResult",
]
