"""
strategy_simulator.py - 벡터화된 전략 시뮬레이터

NumPy 벡터 연산을 사용하여 고속으로 백테스트를 실행합니다.

기능:
- 매수/매도 시그널 벡터화 계산
- 포지션 관리 (롱/숏)
- 수익률 계산 (일간, 누적)
- 슬리피지 및 수수료 적용

사용법:
    from modules.backtester.strategy_simulator import StrategySimulator
    
    simulator = StrategySimulator()
    result = simulator.run(market_data, signals)
"""

import sys
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd

from logger import logger
from config import settings


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    initial_capital: float = 10_000_000      # 초기 자본 (1천만원)
    commission_rate: float = 0.0015          # 수수료율 (0.15%)
    slippage_rate: float = 0.001             # 슬리피지 (0.1%)
    position_size: float = 1.0               # 포지션 크기 (100% = 전액)
    
    # 리스크 관리
    stop_loss: Optional[float] = -0.05       # 손절 (-5%)
    take_profit: Optional[float] = 0.10      # 익절 (+10%)
    max_holding_days: Optional[int] = 7      # 최대 보유 일수


@dataclass
class BacktestResult:
    """백테스트 결과"""
    # 기본 정보
    symbol: str
    start_date: datetime
    end_date: datetime
    total_days: int
    
    # 자본 및 수익
    initial_capital: float
    final_capital: float
    total_return: float                      # 총 수익률 (%)
    cagr: float = 0.0                        # 연평균 수익률
    
    # 포지션
    total_trades: int = 0                    # 총 거래 수
    winning_trades: int = 0                  # 수익 거래 수
    losing_trades: int = 0                   # 손실 거래 수
    win_rate: float = 0.0                    # 승률 (%)
    
    # 수익 분석
    avg_win: float = 0.0                     # 평균 수익
    avg_loss: float = 0.0                    # 평균 손실
    profit_factor: float = 0.0               # 손익비
    
    # 리스크
    max_drawdown: float = 0.0                # 최대 낙폭 (MDD, %)
    sharpe_ratio: float = 0.0                # 샤프 비율
    
    # 상세 데이터
    equity_curve: pd.Series = field(default_factory=pd.Series)  # 자본 곡선
    returns: pd.Series = field(default_factory=pd.Series)       # 일간 수익률
    positions: pd.Series = field(default_factory=pd.Series)     # 포지션 이력
    trades: list = field(default_factory=list)                  # 거래 이력
    
    def __post_init__(self):
        """추가 계산"""
        if self.total_trades > 0:
            self.win_rate = (self.winning_trades / self.total_trades) * 100


class StrategySimulator:
    """
    벡터화된 전략 시뮬레이터
    
    NumPy 벡터 연산으로 빠른 백테스트를 수행합니다.
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
        
        logger.info(
            f"🎯 시뮬레이터 초기화 (자본: {self.config.initial_capital:,.0f}원, "
            f"수수료: {self.config.commission_rate*100:.2f}%)"
        )
    
    def run(
        self,
        data: pd.DataFrame,
        signals: pd.Series,
        symbol: str = ""
    ) -> BacktestResult:
        """
        백테스트 실행 (벡터화)
        
        Args:
            data: OHLCV DataFrame
            signals: 매수 시그널 Series (1=매수, 0=홀드, -1=매도)
            symbol: 종목 코드
            
        Returns:
            BacktestResult
        """
        logger.info(f"🚀 백테스트 시작: {symbol} ({len(data)}일)")
        
        # 데이터 준비
        prices = data['close'].values
        n_days = len(prices)
        
        # 포지션 및 자본 초기화
        positions = np.zeros(n_days)        # 포지션 (1=보유, 0=없음)
        shares = np.zeros(n_days)           # 주식 수
        capital = np.full(n_days, self.config.initial_capital)  # 자본
        equity = np.full(n_days, self.config.initial_capital)   # 평가액
        
        # 거래 이력
        trades = []
        entry_price = 0
        entry_date_idx = 0
        holding_days = 0
        
        # 시뮬레이션 (루프 - 조건부 로직 필요)
        for i in range(1, n_days):
            prev_position = positions[i-1]
            current_signal = signals.iloc[i]
            current_price = prices[i]
            
            # 기본값 복사
            positions[i] = prev_position
            shares[i] = shares[i-1]
            capital[i] = capital[i-1]
            
            # 포지션 진입
            if prev_position == 0 and current_signal == 1:
                # 매수
                buy_price = current_price * (1 + self.config.slippage_rate)
                invest_amount = capital[i] * self.config.position_size
                commission = invest_amount * self.config.commission_rate
                
                shares[i] = (invest_amount - commission) / buy_price
                capital[i] = capital[i] - invest_amount
                positions[i] = 1
                
                entry_price = buy_price
                entry_date_idx = i
                holding_days = 0
                
                logger.debug(f"  [{i}] 매수: {shares[i]:.2f}주 @ {buy_price:,.0f}원")
            
            # 포지션 청산
            elif prev_position == 1:
                holding_days += 1
                
                # 수익률 계산
                current_return = (current_price - entry_price) / entry_price
                
                # 청산 조건 체크
                should_exit = False
                exit_reason = ""
                
                if current_signal == -1:
                    should_exit = True
                    exit_reason = "시그널"
                elif self.config.stop_loss and current_return <= self.config.stop_loss:
                    should_exit = True
                    exit_reason = "손절"
                elif self.config.take_profit and current_return >= self.config.take_profit:
                    should_exit = True
                    exit_reason = "익절"
                elif self.config.max_holding_days and holding_days >= self.config.max_holding_days:
                    should_exit = True
                    exit_reason = "기간만료"
                
                if should_exit:
                    # 매도
                    sell_price = current_price * (1 - self.config.slippage_rate)
                    sell_amount = shares[i-1] * sell_price
                    commission = sell_amount * self.config.commission_rate
                    
                    capital[i] = capital[i] + sell_amount - commission
                    positions[i] = 0
                    shares[i] = 0
                    
                    # 거래 기록
                    profit = sell_amount - (shares[i-1] * entry_price)
                    profit_rate = (sell_price - entry_price) / entry_price
                    
                    trades.append({
                        'entry_date': data.index[entry_date_idx],
                        'exit_date': data.index[i],
                        'entry_price': entry_price,
                        'exit_price': sell_price,
                        'shares': shares[i-1],
                        'profit': profit,
                        'profit_rate': profit_rate * 100,
                        'holding_days': holding_days,
                        'reason': exit_reason
                    })
                    
                    logger.debug(
                        f"  [{i}] 매도: {shares[i-1]:.2f}주 @ {sell_price:,.0f}원 "
                        f"({profit_rate*100:+.2f}%, {exit_reason})"
                    )
            
            # 평가액 계산
            stock_value = shares[i] * current_price
            equity[i] = capital[i] + stock_value
        
        # 마지막 포지션 강제 청산
        if positions[-1] == 1:
            sell_price = prices[-1]
            sell_amount = shares[-1] * sell_price
            commission = sell_amount * self.config.commission_rate
            capital[-1] = capital[-1] + sell_amount - commission
            equity[-1] = capital[-1]
            
            profit = sell_amount - (shares[-1] * entry_price)
            profit_rate = (sell_price - entry_price) / entry_price
            
            trades.append({
                'entry_date': data.index[entry_date_idx],
                'exit_date': data.index[-1],
                'entry_price': entry_price,
                'exit_price': sell_price,
                'shares': shares[-1],
                'profit': profit,
                'profit_rate': profit_rate * 100,
                'holding_days': holding_days,
                'reason': '종료'
            })
        
        # 결과 생성
        result = self._generate_result(
            symbol=symbol,
            data=data,
            equity=equity,
            trades=trades,
            positions=positions
        )
        
        logger.info(
            f"✅ 백테스트 완료: 수익률 {result.total_return:+.2f}%, "
            f"거래 {result.total_trades}건, 승률 {result.win_rate:.1f}%"
        )
        
        return result
    
    def _generate_result(
        self,
        symbol: str,
        data: pd.DataFrame,
        equity: np.ndarray,
        trades: list,
        positions: np.ndarray
    ) -> BacktestResult:
        """결과 생성"""
        
        # 기본 정보
        start_date = data.index[0]
        end_date = data.index[-1]
        total_days = len(data)
        
        # 수익
        initial_capital = self.config.initial_capital
        final_capital = equity[-1]
        total_return = ((final_capital - initial_capital) / initial_capital) * 100
        
        # CAGR 계산
        years = (end_date - start_date).days / 365.25
        if years > 0:
            cagr = (((final_capital / initial_capital) ** (1 / years)) - 1) * 100
        else:
            cagr = 0.0
        
        # 거래 통계
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t['profit'] > 0)
        losing_trades = sum(1 for t in trades if t['profit'] <= 0)
        
        # 수익 분석
        if winning_trades > 0:
            avg_win = np.mean([t['profit_rate'] for t in trades if t['profit'] > 0])
        else:
            avg_win = 0.0
        
        if losing_trades > 0:
            avg_loss = np.mean([t['profit_rate'] for t in trades if t['profit'] <= 0])
        else:
            avg_loss = 0.0
        
        # Profit Factor
        total_profit = sum(t['profit'] for t in trades if t['profit'] > 0)
        total_loss = abs(sum(t['profit'] for t in trades if t['profit'] <= 0))
        profit_factor = total_profit / total_loss if total_loss > 0 else 0
        
        # 일간 수익률
        returns = pd.Series(
            np.diff(equity) / equity[:-1],
            index=data.index[1:]
        )
        
        # MDD 계산
        cummax = np.maximum.accumulate(equity)
        drawdown = (equity - cummax) / cummax * 100
        max_drawdown = np.min(drawdown)
        
        # Sharpe Ratio
        if len(returns) > 0 and returns.std() > 0:
            sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(252)  # 연율화
        else:
            sharpe_ratio = 0.0
        
        return BacktestResult(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            initial_capital=initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            cagr=cagr,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=(winning_trades / total_trades * 100) if total_trades > 0 else 0,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            equity_curve=pd.Series(equity, index=data.index),
            returns=returns,
            positions=pd.Series(positions, index=data.index),
            trades=trades
        )


# ===== 간편 함수 =====

def run_simple_backtest(
    data: pd.DataFrame,
    entry_condition: Callable,
    exit_condition: Callable = None,
    symbol: str = ""
) -> BacktestResult:
    """
    간단한 백테스트 (조건 기반)
    
    Args:
        data: OHLCV DataFrame
        entry_condition: 진입 조건 함수 (DataFrame → bool)
        exit_condition: 청산 조건 함수
        symbol: 종목 코드
        
    Returns:
        BacktestResult
    """
    # 시그널 생성
    signals = pd.Series(0, index=data.index)
    
    for i in range(len(data)):
        row = data.iloc[:i+1]
        if entry_condition(row):
            signals.iloc[i] = 1
        elif exit_condition and exit_condition(row):
            signals.iloc[i] = -1
    
    # 시뮬레이션
    simulator = StrategySimulator()
    return simulator.run(data, signals, symbol)


# ===== 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🎯 전략 시뮬레이터 테스트")
    print("=" * 60)
    
    # 모의 데이터 생성
    from modules.backtester.data_loader import DataLoader
    
    loader = DataLoader()
    market_data = loader.load_stock_data("005930", "2023-01-01", "2023-12-31")
    data = loader.add_technical_indicators(market_data.data)
    
    # 간단한 전략: MA 크로스오버
    print("\n📈 전략: 20일 이평선 돌파 매수")
    
    signals = pd.Series(0, index=data.index)
    
    for i in range(20, len(data)):
        # 매수: 종가가 20일 이평선 위로
        if data['close'].iloc[i] > data['ma_20'].iloc[i] and data['close'].iloc[i-1] <= data['ma_20'].iloc[i-1]:
            signals.iloc[i] = 1
        # 매도: 종가가 20일 이평선 아래로
        elif data['close'].iloc[i] < data['ma_20'].iloc[i] and data['close'].iloc[i-1] >= data['ma_20'].iloc[i-1]:
            signals.iloc[i] = -1
    
    # 백테스트 실행
    simulator = StrategySimulator()
    result = simulator.run(data, signals, "삼성전자")
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("📊 백테스트 결과")
    print("=" * 60)
    print(f"\n기간: {result.start_date.date()} ~ {result.end_date.date()} ({result.total_days}일)")
    print(f"\n💰 수익:")
    print(f"  - 초기 자본: {result.initial_capital:,.0f}원")
    print(f"  - 최종 자본: {result.final_capital:,.0f}원")
    print(f"  - 총 수익률: {result.total_return:+.2f}%")
    print(f"  - 연평균 수익률 (CAGR): {result.cagr:.2f}%")
    
    print(f"\n📈 거래:")
    print(f"  - 총 거래: {result.total_trades}건")
    print(f"  - 수익 거래: {result.winning_trades}건")
    print(f"  - 손실 거래: {result.losing_trades}건")
    print(f"  - 승률: {result.win_rate:.1f}%")
    
    print(f"\n💹 수익 분석:")
    print(f"  - 평균 수익: {result.avg_win:+.2f}%")
    print(f"  - 평균 손실: {result.avg_loss:+.2f}%")
    print(f"  - Profit Factor: {result.profit_factor:.2f}")
    
    print(f"\n⚠️  리스크:")
    print(f"  - MDD: {result.max_drawdown:.2f}%")
    print(f"  - Sharpe Ratio: {result.sharpe_ratio:.2f}")
    
    print("\n📋 거래 이력 (최근 5건):")
    for trade in result.trades[-5:]:
        print(f"  {trade['entry_date'].date()} → {trade['exit_date'].date()}: "
              f"{trade['profit_rate']:+.2f}% ({trade['holding_days']}일, {trade['reason']})")
    
    print("\n" + "=" * 60)
