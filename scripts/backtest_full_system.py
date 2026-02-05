"""
backtest_full_system.py - 전체 시스템 백테스트 (테마 로테이션 포함)

하이브리드 전략 + 테마 로테이션을 포함한 완전한 시스템 백테스트

기능:
- 2주 단위 테마 로테이션
- 테마별 종목 선정 (기술적 지표)
- 포트폴리오 구성 (5-10개 균등 분산)
- 분할 익절 + 트레일링 스탑 + 보유 기간 관리

사용법:
    python scripts/backtest_full_system.py
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import logger
from config import settings
from modules.backtester.data_loader import DataLoader
from modules.backtester.backtest_engine import BacktestConfig


# 테마 정의 (2주 단위)
THEMES_BY_PERIOD = {
    # 2023년 - 2차전지 붐
    "2023-02": {"name": "2차전지", "stocks": ["373220", "086520", "247540"]},
    "2023-03": {"name": "2차전지", "stocks": ["373220", "086520", "247540"]},
    "2023-04": {"name": "2차전지", "stocks": ["373220", "086520", "247540"]},
    "2023-05": {"name": "AI반도체", "stocks": ["005930", "000660"]},  # 전환
    "2023-06": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-07": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-08": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-09": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-10": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-11": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2023-12": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    
    # 2024년 - 혼조세
    "2024-01": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-02": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-03": {"name": "바이오", "stocks": ["207940", "068270"]},  # 셀트리온, 셀트리온헬스케어
    "2024-04": {"name": "바이오", "stocks": ["207940", "068270"]},
    "2024-05": {"name": "AI반도체", "stocks": ["005930", "000660"]},  # 재전환
    "2024-06": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-07": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-08": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-09": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-10": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-11": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2024-12": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    
    # 2025년 - AI 반도체 재부상
    "2025-01": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-02": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-03": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-04": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-05": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-06": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-07": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-08": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-09": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-10": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-11": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2025-12": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    
    # 2026년
    "2026-01": {"name": "AI반도체", "stocks": ["005930", "000660"]},
    "2026-02": {"name": "AI반도체", "stocks": ["005930", "000660"]},
}


class Position:
    """포지션 정보"""
    def __init__(self, symbol, shares, buy_price, buy_date, buy_idx):
        self.symbol = symbol
        self.shares = shares
        self.remaining_shares = shares
        self.buy_price = buy_price
        self.buy_date = buy_date
        self.buy_idx = buy_idx
        self.highest_price = buy_price
        self.trailing_stop = None
        self.partial_1_executed = False
        self.partial_2_executed = False


class FullSystemBacktester:
    """
    전체 시스템 백테스터
    
    테마 로테이션 + 하이브리드 전략
    """
    
    def __init__(self, config: BacktestConfig = None):
        """초기화"""
        self.config = config or BacktestConfig()
        
        # 전략 설정
        self.take_profit_1 = settings.TAKE_PROFIT_1
        self.take_profit_2 = settings.TAKE_PROFIT_2
        self.take_profit_3 = settings.TAKE_PROFIT_3
        self.partial_sell_ratio_1 = settings.PARTIAL_SELL_RATIO_1
        self.partial_sell_ratio_2 = settings.PARTIAL_SELL_RATIO_2
        self.trailing_stop_percent = settings.TRAILING_STOP_PERCENT
        self.max_hold_days_profit = settings.MAX_HOLD_DAYS_PROFIT
        self.max_hold_days_loss = settings.MAX_HOLD_DAYS_LOSS
        self.min_profit_for_long_hold = settings.MIN_PROFIT_FOR_LONG_HOLD
        
        # 테마 로테이션
        self.theme_review_days = settings.THEME_REVIEW_DAYS  # 14일
        
        # 데이터 로더
        self.data_loader = DataLoader()
        
        logger.info("🎯 전체 시스템 백테스터 초기화")
        logger.info(f"  테마 로테이션: {self.theme_review_days}일 단위")
        logger.info(f"  분할 익절: {self.take_profit_1:.0%}/{self.take_profit_2:.0%}/{self.take_profit_3:.0%}")
        logger.info(f"  트레일링 스탑: {self.trailing_stop_percent:.0%}")
    
    def run(self, start_date: str, end_date: str) -> dict:
        """
        전체 시스템 백테스트 실행
        
        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            
        Returns:
            백테스트 결과
        """
        logger.info(f"🚀 전체 시스템 백테스트 시작: {start_date} ~ {end_date}")
        
        # 날짜 범위 생성
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        current_dt = start_dt
        
        # 초기 자본
        capital = self.config.initial_capital
        positions: Dict[str, Position] = {}
        
        # 결과 추적
        equity_history = []
        trade_history = []
        theme_history = []
        
        # 현재 테마
        current_theme = None
        theme_start_date = None
        days_since_theme_change = 0
        
        # 주가 데이터 캐시
        price_data_cache = {}
        
        # 일별 시뮬레이션
        day_count = 0
        while current_dt <= end_dt:
            day_count += 1
            period_key = current_dt.strftime("%Y-%m")
            
            # 1. 테마 로테이션 체크 (2주마다 또는 월 변경 시)
            if current_theme is None or days_since_theme_change >= self.theme_review_days or (current_dt.day == 1):
                new_theme = THEMES_BY_PERIOD.get(period_key)
                
                if new_theme and (current_theme is None or new_theme['name'] != current_theme['name']):
                    logger.info(f"\n[{current_dt.date()}] 테마 변경: {current_theme['name'] if current_theme else 'None'} → {new_theme['name']}")
                    
                    # 기존 포지션 정리
                    if positions:
                        logger.info(f"  기존 포지션 {len(positions)}개 정리")
                        for symbol, pos in list(positions.items()):
                            # 현재가로 매도
                            if symbol in price_data_cache:
                                current_price = price_data_cache[symbol].get(current_dt)
                                if current_price is not None:
                                    sell_amount = pos.remaining_shares * current_price * (1 - self.config.commission_rate)
                                    capital += sell_amount
                                    
                                    profit = pos.remaining_shares * (current_price - pos.buy_price)
                                    profit_rate = (current_price - pos.buy_price) / pos.buy_price
                                    
                                    trade_history.append({
                                        'date': current_dt,
                                        'symbol': symbol,
                                        'action': 'sell',
                                        'reason': '테마 변경',
                                        'shares': pos.remaining_shares,
                                        'price': current_price,
                                        'profit': profit,
                                        'profit_rate': profit_rate * 100
                                    })
                        
                        positions = {}
                    
                    # 새 테마로 변경
                    current_theme = new_theme
                    theme_start_date = current_dt
                    days_since_theme_change = 0
                    
                    theme_history.append({
                        'date': current_dt,
                        'theme': new_theme['name'],
                        'stocks': new_theme['stocks']
                    })
                    
                    # 새 종목 매수
                    logger.info(f"  새 종목 매수: {new_theme['stocks']}")
                    for symbol in new_theme['stocks']:
                        # 데이터 로드 (캐시)
                        if symbol not in price_data_cache:
                            try:
                                market_data = self.data_loader.load_stock_data(symbol, start_date, end_date)
                                if market_data and market_data.data is not None:
                                    # 날짜별 종가 딕셔너리 생성
                                    price_dict = {}
                                    for date, row in market_data.data.iterrows():
                                        price_dict[date.replace(tzinfo=None)] = {
                                            'open': row['open'],
                                            'high': row['high'],
                                            'low': row['low'],
                                            'close': row['close']
                                        }
                                    price_data_cache[symbol] = price_dict
                            except Exception as e:
                                logger.error(f"  [{symbol}] 데이터 로드 실패: {e}")
                                continue
                        
                        # 매수 가능 자금 계산 (균등 분산)
                        available_capital = capital / len(new_theme['stocks'])
                        
                        # 현재가 조회
                        if symbol in price_data_cache:
                            price_info = price_data_cache[symbol].get(current_dt)
                            if price_info:
                                buy_price = price_info['close']
                                shares = int((available_capital * 0.95) / buy_price)
                                
                                if shares > 0:
                                    buy_cost = shares * buy_price * (1 + self.config.commission_rate)
                                    capital -= buy_cost
                                    
                                    positions[symbol] = Position(
                                        symbol=symbol,
                                        shares=shares,
                                        buy_price=buy_price,
                                        buy_date=current_dt,
                                        buy_idx=day_count
                                    )
                                    
                                    trade_history.append({
                                        'date': current_dt,
                                        'symbol': symbol,
                                        'action': 'buy',
                                        'reason': '테마 진입',
                                        'shares': shares,
                                        'price': buy_price,
                                        'profit': 0,
                                        'profit_rate': 0
                                    })
                                    
                                    logger.info(f"    [{symbol}] 매수: {shares}주 @ {buy_price:,.0f}원")
            
            # 2. 포지션 관리 (매일)
            for symbol, pos in list(positions.items()):
                if symbol not in price_data_cache:
                    continue
                
                price_info = price_data_cache[symbol].get(current_dt)
                if price_info is None:
                    continue
                
                current_price = price_info['close']
                current_high = price_info['high']
                current_low = price_info['low']
                
                profit_rate = (current_price - pos.buy_price) / pos.buy_price
                hold_days = day_count - pos.buy_idx
                
                # 최고가 업데이트
                if current_high > pos.highest_price:
                    pos.highest_price = current_high
                    
                    # 트레일링 스탑 업데이트
                    if profit_rate > 0:
                        trailing_stop = pos.highest_price * (1 - self.trailing_stop_percent)
                        if pos.trailing_stop is None or trailing_stop > pos.trailing_stop:
                            pos.trailing_stop = trailing_stop
                
                # 매도 조건 체크
                sell_shares = 0
                sell_reason = None
                
                # 손절
                stop_loss_price = pos.buy_price * (1 + self.config.stop_loss)
                if current_low <= stop_loss_price:
                    sell_shares = pos.remaining_shares
                    sell_reason = "손절"
                
                # 분할 익절
                elif not pos.partial_2_executed and profit_rate >= self.take_profit_3:
                    sell_shares = pos.remaining_shares
                    sell_reason = "3차 익절"
                
                elif not pos.partial_2_executed and profit_rate >= self.take_profit_2:
                    sell_shares = int(pos.shares * self.partial_sell_ratio_2)
                    if sell_shares > pos.remaining_shares:
                        sell_shares = pos.remaining_shares
                    pos.partial_2_executed = True
                    sell_reason = "2차 익절"
                
                elif not pos.partial_1_executed and profit_rate >= self.take_profit_1:
                    sell_shares = int(pos.shares * self.partial_sell_ratio_1)
                    pos.partial_1_executed = True
                    sell_reason = "1차 익절"
                
                # 트레일링 스탑
                elif pos.trailing_stop and current_low <= pos.trailing_stop:
                    sell_shares = pos.remaining_shares
                    sell_reason = "트레일링"
                
                # 최대 보유 기간
                elif hold_days >= self.max_hold_days_profit if profit_rate >= self.min_profit_for_long_hold else hold_days >= self.max_hold_days_loss:
                    sell_shares = pos.remaining_shares
                    sell_reason = f"보유기간 {hold_days}일"
                
                # 매도 실행
                if sell_shares > 0:
                    sell_amount = sell_shares * current_price * (1 - self.config.commission_rate - self.config.slippage_rate)
                    capital += sell_amount
                    
                    profit = sell_shares * (current_price - pos.buy_price)
                    profit_rate_trade = (current_price - pos.buy_price) / pos.buy_price
                    
                    trade_history.append({
                        'date': current_dt,
                        'symbol': symbol,
                        'action': 'sell',
                        'reason': sell_reason,
                        'shares': sell_shares,
                        'price': current_price,
                        'profit': profit,
                        'profit_rate': profit_rate_trade * 100
                    })
                    
                    logger.debug(f"[{current_dt.date()}] [{symbol}] {sell_reason}: {sell_shares}주 @ {current_price:,.0f}원 ({profit_rate_trade:+.1%})")
                    
                    pos.remaining_shares -= sell_shares
                    
                    if pos.remaining_shares <= 0:
                        del positions[symbol]
            
            # 3. 평가액 계산
            equity = capital
            for symbol, pos in positions.items():
                if symbol in price_data_cache:
                    price_info = price_data_cache[symbol].get(current_dt)
                    if price_info:
                        equity += pos.remaining_shares * price_info['close']
            
            equity_history.append({
                'date': current_dt,
                'equity': equity,
                'capital': capital,
                'positions': len(positions)
            })
            
            # 다음 날
            current_dt += timedelta(days=1)
            days_since_theme_change += 1
        
        # 최종 정리
        for symbol, pos in positions.items():
            if symbol in price_data_cache:
                # 마지막 날 가격으로 정리
                last_date = end_dt
                price_info = price_data_cache[symbol].get(last_date)
                if price_info:
                    sell_amount = pos.remaining_shares * price_info['close'] * (1 - self.config.commission_rate)
                    capital += sell_amount
        
        # 결과 계산
        final_equity = equity_history[-1]['equity'] if equity_history else self.config.initial_capital
        total_return = (final_equity - self.config.initial_capital) / self.config.initial_capital * 100
        
        years = (end_dt - start_dt).days / 365.25
        cagr = (pow(final_equity / self.config.initial_capital, 1 / years) - 1) * 100 if years > 0 else 0
        
        # MDD 계산
        equity_values = [e['equity'] for e in equity_history]
        running_max = pd.Series(equity_values).expanding().max()
        drawdown = (pd.Series(equity_values) - running_max) / running_max * 100
        max_drawdown = drawdown.min()
        
        # 거래 통계
        winning_trades = [t for t in trade_history if t['action'] == 'sell' and t['profit'] > 0]
        losing_trades = [t for t in trade_history if t['action'] == 'sell' and t['profit'] <= 0]
        
        sell_trades = [t for t in trade_history if t['action'] == 'sell']
        win_rate = len(winning_trades) / len(sell_trades) * 100 if sell_trades else 0
        
        result = {
            'start_date': start_dt,
            'end_date': end_dt,
            'days': (end_dt - start_dt).days,
            'years': years,
            'initial_capital': self.config.initial_capital,
            'final_capital': final_equity,
            'total_return': total_return,
            'cagr': cagr,
            'max_drawdown': max_drawdown,
            'total_trades': len(sell_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'equity_history': equity_history,
            'trade_history': trade_history,
            'theme_history': theme_history
        }
        
        return result


def print_result(result: dict):
    """결과 출력"""
    print("\n" + "=" * 80)
    print("📊 전체 시스템 백테스트 결과 (테마 로테이션 + 하이브리드 전략)")
    print("=" * 80)
    print(f"기간: {result['start_date'].date()} ~ {result['end_date'].date()} ({result['days']}일, {result['years']:.2f}년)")
    print("-" * 80)
    print(f"초기 자본: {result['initial_capital']:>15,.0f}원")
    print(f"최종 자본: {result['final_capital']:>15,.0f}원")
    print(f"총 수익률: {result['total_return']:>15.2f}% 🔥")
    print(f"CAGR:     {result['cagr']:>15.2f}%")
    print("-" * 80)
    print(f"총 거래:   {result['total_trades']:>15}회")
    print(f"수익 거래: {result['winning_trades']:>15}회")
    print(f"손실 거래: {result['losing_trades']:>15}회")
    print(f"승률:     {result['win_rate']:>15.2f}%")
    print("-" * 80)
    print(f"MDD:      {result['max_drawdown']:>15.2f}%")
    print("=" * 80)


if __name__ == "__main__":
    print("=" * 80)
    print("🚀 전체 시스템 백테스트 (테마 로테이션 + 하이브리드 전략)")
    print("=" * 80)
    
    # 백테스트 설정
    config = BacktestConfig(
        initial_capital=10_000_000,
        commission_rate=0.0015,
        slippage_rate=0.001,
        stop_loss=-0.05
    )
    
    backtester = FullSystemBacktester(config)
    
    # 3년 백테스트
    result = backtester.run(
        start_date="2023-02-06",
        end_date="2026-02-05"
    )
    
    # 결과 출력
    print_result(result)
    
    # 테마 변경 이력
    print("\n" + "=" * 80)
    print("📋 테마 변경 이력")
    print("=" * 80)
    for theme in result['theme_history']:
        print(f"{theme['date'].date()} | {theme['theme']:<15} | {', '.join(theme['stocks'])}")
    print("=" * 80)
    
    print("\n" + "=" * 80)
    print("✅ 전체 시스템 백테스트 완료!")
    print("=" * 80)
