"""
backtest_hybrid_strategy.py - 개선된 하이브리드 전략 백테스트

분할 익절, 트레일링 스탑, 보유 기간 관리를 포함한
최적화된 하이브리드 전략의 백테스트를 실행합니다.

사용법:
    python scripts/backtest_hybrid_strategy.py
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import logger
from config import settings
from modules.backtester.data_loader import DataLoader
from modules.backtester.backtest_engine import BacktestConfig


class HybridStrategySimulator:
    """
    개선된 하이브리드 전략 시뮬레이터
    
    - 분할 익절 (3단계: 10%/15%/20%)
    - 트레일링 스탑 (최고가 -5%)
    - 보유 기간 관리 (수익 14일, 손실 7일)
    """
    
    def __init__(self, config: BacktestConfig = None):
        """초기화"""
        self.config = config or BacktestConfig()
        
        # 개선된 전략 설정 로드
        self.take_profit_1 = settings.TAKE_PROFIT_1
        self.take_profit_2 = settings.TAKE_PROFIT_2
        self.take_profit_3 = settings.TAKE_PROFIT_3
        self.partial_sell_ratio_1 = settings.PARTIAL_SELL_RATIO_1
        self.partial_sell_ratio_2 = settings.PARTIAL_SELL_RATIO_2
        self.partial_sell_ratio_3 = getattr(settings, 'PARTIAL_SELL_RATIO_3', 0.20)
        self.trailing_stop_percent = settings.TRAILING_STOP_PERCENT
        self.max_hold_days_profit = settings.MAX_HOLD_DAYS_PROFIT
        self.max_hold_days_loss = settings.MAX_HOLD_DAYS_LOSS
        self.min_profit_for_long_hold = settings.MIN_PROFIT_FOR_LONG_HOLD
        
        logger.info("🎯 하이브리드 전략 시뮬레이터 초기화")
        logger.info(f"  분할 익절: {self.take_profit_1:.0%}/{self.take_profit_2:.0%}/{self.take_profit_3:.0%}")
        logger.info(f"  트레일링 스탑: {self.trailing_stop_percent:.0%}")
        logger.info(f"  보유 기간: 수익 {self.max_hold_days_profit}일 / 손실 {self.max_hold_days_loss}일")
    
    def run(self, data: pd.DataFrame, symbol: str = "") -> dict:
        """
        하이브리드 전략 백테스트 실행
        
        Args:
            data: OHLCV + 지표 DataFrame
            symbol: 종목 코드
            
        Returns:
            백테스트 결과 딕셔너리
        """
        logger.info(f"🚀 하이브리드 백테스트 시작: {symbol} ({len(data)}일)")
        
        # 데이터 준비
        prices = data['close'].values
        highs = data['high'].values
        lows = data['low'].values
        dates = data.index
        n_days = len(prices)
        
        # 초기 상태
        capital = self.config.initial_capital
        position = None  # None 또는 {'shares', 'buy_price', 'buy_date', 'highest', 'partial1', 'partial2'}
        
        # 결과 추적
        trades = []
        equity_curve = []
        daily_returns = []
        
        for i in range(n_days):
            current_price = prices[i]
            current_high = highs[i]
            current_low = lows[i]
            current_date = dates[i]
            
            # 포지션 없음 - 매수 시그널 체크
            if position is None:
                # 간단한 모멘텀 매수 시그널
                # 조건: 가격이 상승 추세 (5일 평균 > 20일 평균)
                if i >= 20:
                    ma_5 = np.mean(prices[i-5:i])
                    ma_20 = np.mean(prices[i-20:i])
                    
                    # 상승 추세 && 급등하지 않음 (RSI 대신 간단한 체크)
                    recent_return = (prices[i] - prices[i-1]) / prices[i-1]
                    if ma_5 > ma_20 and recent_return < 0.05:  # 5% 미만 상승
                        # 매수!
                        shares = int((capital * 0.95) / current_price)  # 95% 투자 (수수료 고려)
                        if shares > 0:
                            buy_cost = shares * current_price * (1 + self.config.commission_rate)
                            capital -= buy_cost
                            
                            position = {
                                'shares': shares,
                                'remaining_shares': shares,
                                'buy_price': current_price,
                                'buy_date': current_date,
                                'buy_idx': i,
                                'highest_price': current_price,
                                'partial_1_executed': False,
                                'partial_2_executed': False,
                                'trailing_stop': None
                            }
                            
                            logger.debug(f"[{current_date.date()}] 매수: {shares}주 @ {current_price:,.0f}원")
            
            # 포지션 있음 - 매도 시그널 체크
            else:
                profit_rate = (current_price - position['buy_price']) / position['buy_price']
                hold_days = i - position['buy_idx']
                
                # 최고가 업데이트
                if current_high > position['highest_price']:
                    position['highest_price'] = current_high
                    
                    # 트레일링 스탑 업데이트 (수익 중일 때)
                    if profit_rate > 0:
                        trailing_stop = position['highest_price'] * (1 - self.trailing_stop_percent)
                        if position['trailing_stop'] is None or trailing_stop > position['trailing_stop']:
                            position['trailing_stop'] = trailing_stop
                
                # 매도 조건 체크
                sell_shares = 0
                sell_reason = None
                
                # 1. 손절 체크
                stop_loss_price = position['buy_price'] * (1 + self.config.stop_loss)
                if current_low <= stop_loss_price:
                    sell_shares = position['remaining_shares']
                    sell_reason = "손절"
                
                # 2. 분할 익절 체크
                elif not position.get('partial_3_executed') and profit_rate >= self.take_profit_3:
                    # 3차 익절 (20%)
                    sell_shares = int(position['shares'] * self.partial_sell_ratio_3)
                    if sell_shares <= 0 and position['remaining_shares'] >= 2:
                        sell_shares = 1
                    if sell_shares > position['remaining_shares']:
                        sell_shares = position['remaining_shares']
                    position['partial_3_executed'] = True
                    sell_reason = "3차 익절"

                elif not position['partial_2_executed'] and profit_rate >= self.take_profit_2:
                    # 2차 익절 (20%)
                    sell_shares = int(position['shares'] * self.partial_sell_ratio_2)
                    if sell_shares > position['remaining_shares']:
                        sell_shares = position['remaining_shares']
                    position['partial_2_executed'] = True
                    sell_reason = "2차 익절"
                
                elif not position['partial_1_executed'] and profit_rate >= self.take_profit_1:
                    # 1차 익절 (30%)
                    sell_shares = int(position['shares'] * self.partial_sell_ratio_1)
                    position['partial_1_executed'] = True
                    sell_reason = "1차 익절"
                
                # 3. 트레일링 스탑 체크
                elif position['trailing_stop'] is not None and current_low <= position['trailing_stop']:
                    sell_shares = position['remaining_shares']
                    sell_reason = "트레일링 스탑"
                
                # 4. 최대 보유 기간 체크
                elif hold_days >= self.max_hold_days_profit if profit_rate >= self.min_profit_for_long_hold else hold_days >= self.max_hold_days_loss:
                    sell_shares = position['remaining_shares']
                    sell_reason = f"최대 보유 기간 ({hold_days}일)"
                
                # 매도 실행
                if sell_shares > 0:
                    sell_amount = sell_shares * current_price * (1 - self.config.commission_rate - self.config.slippage_rate)
                    capital += sell_amount
                    
                    # 거래 기록
                    profit = sell_shares * (current_price - position['buy_price'])
                    profit_rate_trade = (current_price - position['buy_price']) / position['buy_price']
                    
                    trades.append({
                        'buy_date': position['buy_date'],
                        'sell_date': current_date,
                        'buy_price': position['buy_price'],
                        'sell_price': current_price,
                        'shares': sell_shares,
                        'profit': profit,
                        'profit_rate': profit_rate_trade * 100,
                        'hold_days': hold_days,
                        'reason': sell_reason
                    })
                    
                    logger.debug(
                        f"[{current_date.date()}] {sell_reason}: {sell_shares}주 @ {current_price:,.0f}원 "
                        f"(수익률: {profit_rate_trade:.1%}, 보유: {hold_days}일)"
                    )
                    
                    # 포지션 업데이트
                    position['remaining_shares'] -= sell_shares
                    
                    # 전량 매도 시 포지션 종료
                    if position['remaining_shares'] <= 0:
                        position = None
            
            # 평가액 계산
            if position:
                equity = capital + position['remaining_shares'] * current_price
            else:
                equity = capital
            
            equity_curve.append(equity)
            
            # 일간 수익률
            if i > 0:
                daily_return = (equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                daily_returns.append(daily_return)
        
        # 최종 포지션 정리
        if position:
            final_price = prices[-1]
            sell_amount = position['remaining_shares'] * final_price * (1 - self.config.commission_rate)
            capital += sell_amount
            
            profit = position['remaining_shares'] * (final_price - position['buy_price'])
            profit_rate = (final_price - position['buy_price']) / position['buy_price']
            
            trades.append({
                'buy_date': position['buy_date'],
                'sell_date': dates[-1],
                'buy_price': position['buy_price'],
                'sell_price': final_price,
                'shares': position['remaining_shares'],
                'profit': profit,
                'profit_rate': profit_rate * 100,
                'hold_days': len(dates) - position['buy_idx'],
                'reason': '기간 종료'
            })
        
        # 결과 계산
        final_capital = capital + (position['remaining_shares'] * prices[-1] if position else 0)
        total_return = (final_capital - self.config.initial_capital) / self.config.initial_capital * 100
        
        # CAGR 계산
        years = (dates[-1] - dates[0]).days / 365.25
        cagr = (pow(final_capital / self.config.initial_capital, 1 / years) - 1) * 100 if years > 0 else 0
        
        # MDD 계산
        equity_series = pd.Series(equity_curve)
        running_max = equity_series.expanding().max()
        drawdown = (equity_series - running_max) / running_max * 100
        max_drawdown = drawdown.min()
        
        # 거래 통계
        winning_trades = [t for t in trades if t['profit'] > 0]
        losing_trades = [t for t in trades if t['profit'] <= 0]
        
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        avg_win = np.mean([t['profit_rate'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['profit_rate'] for t in losing_trades]) if losing_trades else 0
        
        # Sharpe Ratio
        if daily_returns:
            sharpe_ratio = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if np.std(daily_returns) > 0 else 0
        else:
            sharpe_ratio = 0
        
        result = {
            'symbol': symbol,
            'start_date': dates[0],
            'end_date': dates[-1],
            'days': len(dates),
            'years': years,
            'initial_capital': self.config.initial_capital,
            'final_capital': final_capital,
            'total_return': total_return,
            'cagr': cagr,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'trades': trades,
            'equity_curve': equity_curve
        }
        
        return result


def print_result(result: dict, strategy_name: str = ""):
    """결과 출력"""
    print("\n" + "=" * 80)
    print(f"📊 {strategy_name} 백테스트 결과")
    print("=" * 80)
    print(f"종목: {result['symbol']}")
    print(f"기간: {result['start_date'].date()} ~ {result['end_date'].date()} ({result['days']}일, {result['years']:.2f}년)")
    print("-" * 80)
    print(f"초기 자본: {result['initial_capital']:>15,.0f}원")
    print(f"최종 자본: {result['final_capital']:>15,.0f}원")
    print(f"총 수익률: {result['total_return']:>15.2f}%")
    print(f"CAGR:     {result['cagr']:>15.2f}%")
    print("-" * 80)
    print(f"총 거래:   {result['total_trades']:>15}회")
    print(f"수익 거래: {result['winning_trades']:>15}회")
    print(f"손실 거래: {result['losing_trades']:>15}회")
    print(f"승률:     {result['win_rate']:>15.2f}%")
    print(f"평균 수익: {result['avg_win']:>15.2f}%")
    print(f"평균 손실: {result['avg_loss']:>15.2f}%")
    print("-" * 80)
    print(f"MDD:      {result['max_drawdown']:>15.2f}%")
    print(f"Sharpe:   {result['sharpe_ratio']:>15.2f}")
    print("=" * 80)


def run_portfolio_backtest(symbols: list, start_date: str, end_date: str):
    """포트폴리오 백테스트"""
    print("\n" + "=" * 80)
    print("🎯 하이브리드 전략 포트폴리오 백테스트")
    print("=" * 80)
    print(f"종목: {', '.join(symbols)}")
    print(f"기간: {start_date} ~ {end_date}")
    print("=" * 80)
    
    # 데이터 로더
    data_loader = DataLoader()
    
    # 백테스트 설정
    config = BacktestConfig(
        initial_capital=10_000_000,
        commission_rate=0.0015,
        slippage_rate=0.001,
        stop_loss=-0.05,  # -5%
        take_profit=0.10  # +10% (기본, 분할 익절에서 재정의됨)
    )
    
    simulator = HybridStrategySimulator(config)
    
    # 각 종목 백테스트
    results = []
    for symbol in symbols:
        print(f"\n{'='*80}")
        print(f"📈 {symbol} 백테스트 중...")
        print(f"{'='*80}")
        
        # 데이터 로드
        market_data = data_loader.load_stock_data(symbol, start_date, end_date)
        
        if market_data is None or market_data.data is None or len(market_data.data) == 0:
            logger.warning(f"{symbol} 데이터 로드 실패")
            continue
        
        data = market_data.data  # DataFrame 추출
        
        # 백테스트 실행
        result = simulator.run(data, symbol=symbol)
        results.append(result)
        
        # 결과 출력
        print_result(result, strategy_name=f"{symbol} 하이브리드 전략")
    
    if not results:
        print("\n❌ 백테스트 결과가 없습니다")
        return
    
    # 포트폴리오 통합 결과
    print("\n" + "=" * 80)
    print("📊 포트폴리오 통합 결과 (Equal Weight)")
    print("=" * 80)
    
    # 평균 성과
    avg_total_return = np.mean([r['total_return'] for r in results])
    avg_cagr = np.mean([r['cagr'] for r in results])
    avg_mdd = np.mean([r['max_drawdown'] for r in results])
    avg_sharpe = np.mean([r['sharpe_ratio'] for r in results])
    
    total_trades = sum([r['total_trades'] for r in results])
    total_winning = sum([r['winning_trades'] for r in results])
    total_losing = sum([r['losing_trades'] for r in results])
    avg_win_rate = np.mean([r['win_rate'] for r in results])
    
    print(f"종목 수:   {len(results):>15}개")
    print(f"총 수익률: {avg_total_return:>15.2f}% (평균)")
    print(f"CAGR:     {avg_cagr:>15.2f}% (평균)")
    print(f"MDD:      {avg_mdd:>15.2f}% (평균)")
    print(f"Sharpe:   {avg_sharpe:>15.2f} (평균)")
    print("-" * 80)
    print(f"총 거래:   {total_trades:>15}회")
    print(f"승률:     {avg_win_rate:>15.2f}% (평균)")
    print("=" * 80)
    
    # 개별 종목 요약
    print("\n" + "=" * 80)
    print("📋 개별 종목 요약")
    print("=" * 80)
    print(f"{'종목':<15} {'수익률':>10} {'CAGR':>10} {'MDD':>10} {'거래':>8} {'승률':>8}")
    print("-" * 80)
    
    for r in results:
        print(f"{r['symbol']:<15} "
              f"{r['total_return']:>9.2f}% "
              f"{r['cagr']:>9.2f}% "
              f"{r['max_drawdown']:>9.2f}% "
              f"{r['total_trades']:>7}회 "
              f"{r['win_rate']:>7.2f}%")
    
    print("=" * 80)


if __name__ == "__main__":
    print("=" * 80)
    print("🚀 하이브리드 전략 백테스트 시스템")
    print("=" * 80)
    
    # 테스트 종목
    symbols = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "373220",  # LG에너지솔루션
        "086520",  # 에코프로
        "247540"   # 에코프로비엠
    ]
    
    # 백테스트 기간 (3년)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=3*365)
    
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    
    # 포트폴리오 백테스트 실행
    run_portfolio_backtest(symbols, start_date_str, end_date_str)
    
    print("\n" + "=" * 80)
    print("✅ 하이브리드 전략 백테스트 완료!")
    print("=" * 80)
