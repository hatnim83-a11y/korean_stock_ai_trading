"""
run_backtest.py - 우리 트레이딩 시스템 백테스트 실행

테마 기반 스크리닝 + 장 초반 관찰 전략을 백테스트합니다.

사용법:
    python scripts/run_backtest.py --start 2023-01-01 --end 2023-12-31
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from logger import logger
from config import settings
from modules.backtester import BacktestEngine, BacktestConfig

def run_theme_based_backtest(
    start_date: str,
    end_date: str,
    test_symbols: list = None
):
    """
    테마 기반 전략 백테스트
    
    전략:
    1. 모멘텀이 강한 종목 선정 (20일 수익률 > 5%)
    2. RSI 과열 구간 제외 (RSI < 70)
    3. 장 초반 갭 필터 (±3%)
    4. 5-7일 보유 후 리밸런싱
    """
    logger.info("=" * 70)
    logger.info("🚀 테마 기반 트레이딩 시스템 백테스트")
    logger.info("=" * 70)
    logger.info(f"기간: {start_date} ~ {end_date}")
    
    # 기본 테스트 종목 (실제로는 테마 기반 선정)
    if test_symbols is None:
        test_symbols = [
            "005930",  # 삼성전자
            "000660",  # SK하이닉스
            "035420",  # NAVER
            "051910",  # LG화학
            "006400",  # 삼성SDI
            "035720",  # 카카오
            "373220",  # LG에너지솔루션
            "207940",  # 삼성바이오로직스
        ]
    
    logger.info(f"테스트 종목: {len(test_symbols)}개")
    
    # 백테스트 설정 (우리 시스템 기준)
    config = BacktestConfig(
        initial_capital=10_000_000,      # 1천만원
        commission_rate=0.0015,          # 0.15%
        slippage_rate=0.001,             # 0.1%
        position_size=1.0,               # 전액 투자
        stop_loss=-0.05,                 # -5% 손절
        take_profit=0.10,                # +10% 익절
        max_holding_days=7               # 최대 7일 보유
    )
    
    # 엔진 초기화
    engine = BacktestEngine(config)
    
    # === 전략 1: 단순 모멘텀 전략 ===
    logger.info("\n" + "=" * 70)
    logger.info("📈 전략 1: 모멘텀 전략 (20일 기준)")
    logger.info("=" * 70)
    
    momentum_results = []
    
    for symbol in test_symbols[:5]:  # 처음 5개만 테스트
        try:
            result = engine.run_momentum_strategy(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                lookback=20
            )
            momentum_results.append({
                'symbol': symbol,
                'return': result.total_return,
                'cagr': result.cagr,
                'mdd': result.max_drawdown,
                'sharpe': result.sharpe_ratio,
                'win_rate': result.win_rate,
                'trades': result.total_trades
            })
        except Exception as e:
            logger.error(f"[{symbol}] 백테스트 실패: {e}")
    
    # 결과 출력
    if momentum_results:
        df = pd.DataFrame(momentum_results)
        logger.info("\n개별 종목 성과:")
        logger.info(df.to_string(index=False))
        
        logger.info("\n평균 성과:")
        logger.info(f"  평균 수익률: {df['return'].mean():+.2f}%")
        logger.info(f"  평균 CAGR: {df['cagr'].mean():.2f}%")
        logger.info(f"  평균 MDD: {df['mdd'].mean():.2f}%")
        logger.info(f"  평균 Sharpe: {df['sharpe'].mean():.2f}")
        logger.info(f"  평균 승률: {df['win_rate'].mean():.1f}%")
        logger.info(f"  총 거래: {df['trades'].sum()}건")
    
    # === 전략 2: 포트폴리오 전략 ===
    logger.info("\n" + "=" * 70)
    logger.info("📊 전략 2: 포트폴리오 전략 (분산투자)")
    logger.info("=" * 70)
    
    portfolio_result = engine.run_portfolio_backtest(
        symbols=test_symbols[:5],
        start_date=start_date,
        end_date=end_date,
        strategy="momentum"
    )
    
    logger.info("\n포트폴리오 전체 성과:")
    logger.info(f"  총 수익률: {portfolio_result.total_return:+.2f}%")
    logger.info(f"  CAGR: {portfolio_result.cagr:.2f}%")
    logger.info(f"  MDD: {portfolio_result.max_drawdown:.2f}%")
    logger.info(f"  Sharpe Ratio: {portfolio_result.sharpe_ratio:.2f}")
    logger.info(f"  승률: {portfolio_result.win_rate:.1f}%")
    logger.info(f"  총 거래: {portfolio_result.total_trades}건")
    
    logger.info("\n개별 종목 기여도:")
    for symbol, res in portfolio_result.individual_results.items():
        logger.info(
            f"  {symbol}: {res.total_return:+6.2f}% "
            f"(거래 {res.total_trades:2d}건, 승률 {res.win_rate:5.1f}%)"
        )
    
    # === 전략 3: MA 크로스오버 (비교용) ===
    logger.info("\n" + "=" * 70)
    logger.info("📉 전략 3: MA 크로스오버 (비교 전략)")
    logger.info("=" * 70)
    
    ma_portfolio = engine.run_portfolio_backtest(
        symbols=test_symbols[:5],
        start_date=start_date,
        end_date=end_date,
        strategy="ma_crossover"
    )
    
    logger.info("\nMA 크로스오버 포트폴리오 성과:")
    logger.info(f"  총 수익률: {ma_portfolio.total_return:+.2f}%")
    logger.info(f"  CAGR: {ma_portfolio.cagr:.2f}%")
    logger.info(f"  MDD: {ma_portfolio.max_drawdown:.2f}%")
    logger.info(f"  Sharpe Ratio: {ma_portfolio.sharpe_ratio:.2f}")
    logger.info(f"  승률: {ma_portfolio.win_rate:.1f}%")
    
    # === 비교 분석 ===
    logger.info("\n" + "=" * 70)
    logger.info("📊 전략 비교 분석")
    logger.info("=" * 70)
    
    comparison = pd.DataFrame({
        '지표': ['수익률 (%)', 'CAGR (%)', 'MDD (%)', 'Sharpe', '승률 (%)'],
        '모멘텀': [
            f"{portfolio_result.total_return:+.2f}",
            f"{portfolio_result.cagr:.2f}",
            f"{portfolio_result.max_drawdown:.2f}",
            f"{portfolio_result.sharpe_ratio:.2f}",
            f"{portfolio_result.win_rate:.1f}"
        ],
        'MA크로스': [
            f"{ma_portfolio.total_return:+.2f}",
            f"{ma_portfolio.cagr:.2f}",
            f"{ma_portfolio.max_drawdown:.2f}",
            f"{ma_portfolio.sharpe_ratio:.2f}",
            f"{ma_portfolio.win_rate:.1f}"
        ]
    })
    
    logger.info(f"\n{comparison.to_string(index=False)}")
    
    # === 월별 성과 분석 ===
    logger.info("\n" + "=" * 70)
    logger.info("📅 월별 수익률 분석 (모멘텀 전략)")
    logger.info("=" * 70)
    
    monthly_returns = portfolio_result.returns.resample('M').apply(
        lambda x: (1 + x).prod() - 1
    ) * 100
    
    if len(monthly_returns) > 0:
        logger.info("\n월별 수익률:")
        for date, ret in monthly_returns.items():
            logger.info(f"  {date.strftime('%Y-%m')}: {ret:+6.2f}%")
        
        positive_months = (monthly_returns > 0).sum()
        total_months = len(monthly_returns)
        logger.info(f"\n월간 승률: {positive_months}/{total_months} ({positive_months/total_months*100:.1f}%)")
    
    # === 최종 요약 ===
    logger.info("\n" + "=" * 70)
    logger.info("✅ 백테스트 완료 - 최종 요약")
    logger.info("=" * 70)
    
    logger.info(f"\n🎯 추천 전략: 모멘텀 포트폴리오")
    logger.info(f"   - 1년 수익률: {portfolio_result.total_return:+.2f}%")
    logger.info(f"   - 최대 낙폭: {portfolio_result.max_drawdown:.2f}%")
    logger.info(f"   - 리스크 대비 수익: Sharpe {portfolio_result.sharpe_ratio:.2f}")
    
    if portfolio_result.total_return > ma_portfolio.total_return:
        diff = portfolio_result.total_return - ma_portfolio.total_return
        logger.info(f"   - MA 크로스오버 대비 {diff:+.2f}% 우수 ⭐")
    
    logger.info("\n" + "=" * 70)
    
    return {
        'momentum_portfolio': portfolio_result,
        'ma_portfolio': ma_portfolio,
        'individual_results': momentum_results
    }


def main():
    """메인 실행"""
    parser = argparse.ArgumentParser(description='트레이딩 시스템 백테스트')
    parser.add_argument(
        '--start',
        type=str,
        default=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'),
        help='시작일 (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end',
        type=str,
        default=datetime.now().strftime('%Y-%m-%d'),
        help='종료일 (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--symbols',
        type=str,
        nargs='+',
        help='테스트 종목 코드 (공백으로 구분)'
    )
    
    args = parser.parse_args()
    
    # 백테스트 실행
    results = run_theme_based_backtest(
        start_date=args.start,
        end_date=args.end,
        test_symbols=args.symbols
    )
    
    logger.info("\n✅ 모든 백테스트 완료!")


if __name__ == "__main__":
    main()
