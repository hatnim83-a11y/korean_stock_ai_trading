"""
backtest_engine.py - 통합 백테스트 엔진

우리 트레이딩 시스템의 전략을 백테스트합니다.

기능:
- 테마 기반 스크리닝 전략 백테스트
- 여러 종목 동시 백테스트
- 포트폴리오 백테스트
- 파라미터 최적화

사용법:
    from modules.backtester.backtest_engine import BacktestEngine
    
    engine = BacktestEngine()
    result = engine.run_theme_strategy("2023-01-01", "2023-12-31")
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd

from logger import logger
from config import settings

try:
    from .data_loader import DataLoader, MarketData
    from .strategy_simulator import StrategySimulator, BacktestResult, BacktestConfig
except ImportError:
    from data_loader import DataLoader, MarketData
    from strategy_simulator import StrategySimulator, BacktestResult, BacktestConfig


@dataclass
class PortfolioBacktestResult:
    """포트폴리오 백테스트 결과"""
    start_date: datetime
    end_date: datetime
    total_days: int
    
    # 전체 성과
    initial_capital: float
    final_capital: float
    total_return: float
    cagr: float
    
    # 개별 종목 결과
    individual_results: Dict[str, BacktestResult] = field(default_factory=dict)
    
    # 포트폴리오 지표
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    
    # 상세 데이터
    equity_curve: pd.Series = field(default_factory=pd.Series)
    returns: pd.Series = field(default_factory=pd.Series)


class BacktestEngine:
    """
    통합 백테스트 엔진
    
    우리 시스템의 전략을 백테스트합니다.
    """
    
    def __init__(
        self,
        config: BacktestConfig = None
    ):
        """
        Args:
            config: 백테스트 설정
        """
        self.config = config or BacktestConfig()
        self.data_loader = DataLoader()
        self.simulator = StrategySimulator(self.config)
        
        logger.info("🎯 백테스트 엔진 초기화 완료")
    
    def run_simple_ma_strategy(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        ma_period: int = 20
    ) -> BacktestResult:
        """
        간단한 이동평균 전략 백테스트
        
        Args:
            symbol: 종목 코드
            start_date: 시작일
            end_date: 종료일
            ma_period: 이동평균 기간
            
        Returns:
            BacktestResult
        """
        logger.info(f"📈 MA{ma_period} 전략 백테스트: {symbol}")
        
        # 데이터 로드
        market_data = self.data_loader.load_stock_data(symbol, start_date, end_date)
        data = market_data.data.copy()
        
        # 이동평균 계산 (동적)
        data[f'ma_{ma_period}'] = data['close'].rolling(window=ma_period).mean()
        
        # 시그널 생성: 종가 > MA
        signals = pd.Series(0, index=data.index)
        
        for i in range(ma_period, len(data)):
            # 골든크로스: 매수
            if (data['close'].iloc[i] > data[f'ma_{ma_period}'].iloc[i] and 
                data['close'].iloc[i-1] <= data[f'ma_{ma_period}'].iloc[i-1]):
                signals.iloc[i] = 1
            # 데드크로스: 매도
            elif (data['close'].iloc[i] < data[f'ma_{ma_period}'].iloc[i] and 
                  data['close'].iloc[i-1] >= data[f'ma_{ma_period}'].iloc[i-1]):
                signals.iloc[i] = -1
        
        # 백테스트 실행
        result = self.simulator.run(data, signals, symbol)
        
        return result
    
    def run_momentum_strategy(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        lookback: int = 20
    ) -> BacktestResult:
        """
        모멘텀 전략 백테스트
        
        Args:
            symbol: 종목 코드
            start_date: 시작일
            end_date: 종료일
            lookback: 모멘텀 기간
            
        Returns:
            BacktestResult
        """
        logger.info(f"🚀 모멘텀 전략 백테스트: {symbol} ({lookback}일)")
        
        # 데이터 로드
        market_data = self.data_loader.load_stock_data(symbol, start_date, end_date)
        data = market_data.data.copy()
        
        # 모멘텀 계산
        data['momentum'] = data['close'].pct_change(lookback)
        
        # 시그널: 모멘텀 상위 매수
        signals = pd.Series(0, index=data.index)
        threshold = 0.05  # 5% 이상 상승 시 매수
        
        for i in range(lookback, len(data)):
            if data['momentum'].iloc[i] > threshold:
                signals.iloc[i] = 1
            elif signals.iloc[i-1] == 1:  # 보유 중
                # 모멘텀 하락 시 매도
                if data['momentum'].iloc[i] < 0:
                    signals.iloc[i] = -1
        
        # 백테스트
        result = self.simulator.run(data, signals, symbol)
        
        return result
    
    def run_portfolio_backtest(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        strategy: str = "ma_crossover"
    ) -> PortfolioBacktestResult:
        """
        포트폴리오 백테스트 (여러 종목 동시)
        
        Args:
            symbols: 종목 리스트
            start_date: 시작일
            end_date: 종료일
            strategy: 전략 ("ma_crossover", "momentum")
            
        Returns:
            PortfolioBacktestResult
        """
        logger.info(f"📊 포트폴리오 백테스트: {len(symbols)}개 종목")
        
        # 종목별 자본 할당
        capital_per_stock = self.config.initial_capital / len(symbols)
        stock_config = BacktestConfig(
            initial_capital=capital_per_stock,
            commission_rate=self.config.commission_rate,
            slippage_rate=self.config.slippage_rate,
            stop_loss=self.config.stop_loss,
            take_profit=self.config.take_profit,
            max_holding_days=self.config.max_holding_days
        )
        
        # 각 종목 백테스트
        individual_results = {}
        
        for symbol in symbols:
            try:
                if strategy == "ma_crossover":
                    # 임시 설정
                    temp_simulator = StrategySimulator(stock_config)
                    market_data = self.data_loader.load_stock_data(symbol, start_date, end_date)
                    data = self.data_loader.add_technical_indicators(market_data.data)
                    
                    signals = pd.Series(0, index=data.index)
                    for i in range(20, len(data)):
                        if data['close'].iloc[i] > data['ma_20'].iloc[i]:
                            if data['close'].iloc[i-1] <= data['ma_20'].iloc[i-1]:
                                signals.iloc[i] = 1
                        elif data['close'].iloc[i] < data['ma_20'].iloc[i]:
                            if data['close'].iloc[i-1] >= data['ma_20'].iloc[i-1]:
                                signals.iloc[i] = -1
                    
                    result = temp_simulator.run(data, signals, symbol)
                    
                elif strategy == "momentum":
                    result = self.run_momentum_strategy(symbol, start_date, end_date)
                else:
                    logger.warning(f"알 수 없는 전략: {strategy}")
                    continue
                
                individual_results[symbol] = result
                
            except Exception as e:
                logger.error(f"[{symbol}] 백테스트 실패: {e}")
        
        # 포트폴리오 결과 집계
        portfolio_result = self._aggregate_portfolio_results(
            individual_results,
            start_date,
            end_date
        )
        
        logger.info(
            f"✅ 포트폴리오 백테스트 완료: "
            f"수익률 {portfolio_result.total_return:+.2f}%, "
            f"승률 {portfolio_result.win_rate:.1f}%"
        )
        
        return portfolio_result
    
    def _aggregate_portfolio_results(
        self,
        individual_results: Dict[str, BacktestResult],
        start_date: str,
        end_date: str
    ) -> PortfolioBacktestResult:
        """포트폴리오 결과 집계"""
        
        if not individual_results:
            logger.warning("백테스트 결과가 없습니다")
            return None
        
        # 전체 자본
        initial_capital = sum(r.initial_capital for r in individual_results.values())
        final_capital = sum(r.final_capital for r in individual_results.values())
        total_return = ((final_capital - initial_capital) / initial_capital) * 100
        
        # CAGR
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        years = (end_dt - start_dt).days / 365.25
        
        if years > 0:
            cagr = (((final_capital / initial_capital) ** (1 / years)) - 1) * 100
        else:
            cagr = 0.0
        
        # 거래 통계
        total_trades = sum(r.total_trades for r in individual_results.values())
        winning_trades = sum(r.winning_trades for r in individual_results.values())
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # 포트폴리오 자본 곡선 (종목별 합산)
        equity_curves = [r.equity_curve for r in individual_results.values()]
        
        # 모든 날짜 가져오기
        all_dates = set()
        for ec in equity_curves:
            all_dates.update(ec.index)
        all_dates = sorted(all_dates)
        
        # 날짜별 자본 합산
        portfolio_equity = pd.Series(index=all_dates, dtype=float)
        
        for date in all_dates:
            daily_total = sum(
                ec.get(date, ec.iloc[-1] if date > ec.index[-1] else ec.iloc[0])
                for ec in equity_curves
            )
            portfolio_equity[date] = daily_total
        
        # MDD 계산
        cummax = portfolio_equity.expanding().max()
        drawdown = (portfolio_equity - cummax) / cummax * 100
        max_drawdown = drawdown.min()
        
        # Sharpe Ratio
        returns = portfolio_equity.pct_change().dropna()
        if len(returns) > 0 and returns.std() > 0:
            sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)
        else:
            sharpe_ratio = 0.0
        
        return PortfolioBacktestResult(
            start_date=start_dt,
            end_date=end_dt,
            total_days=len(all_dates),
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            cagr=cagr,
            individual_results=individual_results,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            total_trades=total_trades,
            equity_curve=portfolio_equity,
            returns=returns
        )
    
    def run_parameter_optimization(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        param_ranges: Dict
    ) -> pd.DataFrame:
        """
        파라미터 최적화
        
        Args:
            symbol: 종목 코드
            start_date: 시작일
            end_date: 종료일
            param_ranges: 파라미터 범위 {'ma_period': [10, 20, 60]}
            
        Returns:
            최적화 결과 DataFrame
        """
        logger.info(f"🔧 파라미터 최적화: {symbol}")
        
        results = []
        
        # MA 기간 최적화 예시
        if 'ma_period' in param_ranges:
            for ma_period in param_ranges['ma_period']:
                result = self.run_simple_ma_strategy(symbol, start_date, end_date, ma_period)
                
                results.append({
                    'ma_period': ma_period,
                    'total_return': result.total_return,
                    'cagr': result.cagr,
                    'mdd': result.max_drawdown,
                    'sharpe': result.sharpe_ratio,
                    'win_rate': result.win_rate,
                    'total_trades': result.total_trades
                })
        
        results_df = pd.DataFrame(results)
        
        # 최적 파라미터 찾기 (샤프 비율 기준)
        best_idx = results_df['sharpe'].idxmax()
        best_params = results_df.loc[best_idx]
        
        logger.info(f"✅ 최적 파라미터: {best_params.to_dict()}")
        
        return results_df


# ===== 간편 함수 =====

def quick_backtest(symbol: str, start_date: str, end_date: str) -> BacktestResult:
    """빠른 백테스트 (기본 전략)"""
    engine = BacktestEngine()
    return engine.run_simple_ma_strategy(symbol, start_date, end_date)


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 70)
    print("🎯 백테스트 엔진 테스트")
    print("=" * 70)
    
    engine = BacktestEngine()
    
    # 1. 단일 종목 백테스트
    print("\n1️⃣ 단일 종목 MA 전략:")
    result = engine.run_simple_ma_strategy("005930", "2023-01-01", "2023-12-31")
    
    print(f"   수익률: {result.total_return:+.2f}%")
    print(f"   MDD: {result.max_drawdown:.2f}%")
    print(f"   Sharpe: {result.sharpe_ratio:.2f}")
    print(f"   승률: {result.win_rate:.1f}%")
    
    # 2. 포트폴리오 백테스트
    print("\n2️⃣ 포트폴리오 백테스트 (3개 종목):")
    symbols = ["005930", "000660", "035420"]
    portfolio_result = engine.run_portfolio_backtest(
        symbols, "2023-01-01", "2023-12-31", strategy="ma_crossover"
    )
    
    print(f"   총 수익률: {portfolio_result.total_return:+.2f}%")
    print(f"   CAGR: {portfolio_result.cagr:.2f}%")
    print(f"   MDD: {portfolio_result.max_drawdown:.2f}%")
    print(f"   Sharpe: {portfolio_result.sharpe_ratio:.2f}")
    print(f"   총 거래: {portfolio_result.total_trades}건")
    print(f"   승률: {portfolio_result.win_rate:.1f}%")
    
    print("\n   개별 종목 성과:")
    for symbol, res in portfolio_result.individual_results.items():
        print(f"     {symbol}: {res.total_return:+.2f}% (거래 {res.total_trades}건)")
    
    # 3. 파라미터 최적화
    print("\n3️⃣ MA 기간 최적화:")
    opt_results = engine.run_parameter_optimization(
        "005930",
        "2023-01-01",
        "2023-12-31",
        param_ranges={'ma_period': [10, 20, 60]}
    )
    
    print(f"\n{opt_results.to_string(index=False)}")
    
    print("\n" + "=" * 70)
    print("✅ 백테스트 엔진 테스트 완료!")
    print("=" * 70)
