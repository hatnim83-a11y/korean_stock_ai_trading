"""
performance_calculator.py - 성과 지표 계산 모듈

이 파일은 트레이딩 성과 지표를 계산합니다.

주요 기능:
- 일별/누적 수익률 계산
- MDD (Maximum Drawdown) 계산
- 샤프 비율 계산
- 승률 계산
- 손익비 계산

사용법:
    from modules.reporter.performance_calculator import PerformanceCalculator
    
    calc = PerformanceCalculator()
    metrics = calc.calculate_all_metrics(trades, portfolio_values)
"""

import math
from datetime import datetime, date, timedelta
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from database import Database


# ===== 상수 =====
TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.035  # 무위험 수익률 (연 3.5%)


class PerformanceCalculator:
    """
    성과 지표 계산기
    
    트레이딩 성과를 다양한 지표로 분석합니다.
    
    Attributes:
        trades: 매매 기록
        portfolio_values: 일별 포트폴리오 가치
        
    Example:
        >>> calc = PerformanceCalculator()
        >>> metrics = calc.calculate_all_metrics(trades, values)
        >>> print(f"수익률: {metrics['total_return']:.1%}")
    """
    
    def __init__(self):
        """계산기 초기화"""
        self.trades: list[dict] = []
        self.portfolio_values: list[dict] = []
        
        logger.debug("성과 계산기 초기화")
    
    # ===== 수익률 계산 =====
    
    def calculate_daily_return(
        self,
        today_value: float,
        yesterday_value: float
    ) -> float:
        """
        일별 수익률 계산
        
        Args:
            today_value: 오늘 평가액
            yesterday_value: 어제 평가액
        
        Returns:
            일별 수익률 (예: 0.02 = 2%)
        """
        if yesterday_value <= 0:
            return 0.0
        
        return (today_value - yesterday_value) / yesterday_value
    
    def calculate_total_return(
        self,
        initial_value: float,
        current_value: float
    ) -> float:
        """
        총 수익률 계산
        
        Args:
            initial_value: 초기 투자금
            current_value: 현재 평가액
        
        Returns:
            총 수익률
        """
        if initial_value <= 0:
            return 0.0
        
        return (current_value - initial_value) / initial_value
    
    def calculate_annualized_return(
        self,
        total_return: float,
        days: int
    ) -> float:
        """
        연환산 수익률 계산
        
        Args:
            total_return: 총 수익률
            days: 투자 일수
        
        Returns:
            연환산 수익률
        """
        if days <= 0:
            return 0.0
        
        # (1 + 총수익률)^(252/일수) - 1
        annualized = (1 + total_return) ** (TRADING_DAYS_PER_YEAR / days) - 1
        
        return annualized
    
    def calculate_daily_returns(
        self,
        portfolio_values: list[dict]
    ) -> list[float]:
        """
        일별 수익률 리스트 계산
        
        Args:
            portfolio_values: 일별 포트폴리오 가치
                [{'date': '2025-01-01', 'value': 10000000}, ...]
        
        Returns:
            일별 수익률 리스트
        """
        if len(portfolio_values) < 2:
            return []
        
        # 날짜순 정렬
        sorted_values = sorted(portfolio_values, key=lambda x: x.get("date", ""))
        
        returns = []
        for i in range(1, len(sorted_values)):
            prev_value = sorted_values[i - 1].get("value", 0)
            curr_value = sorted_values[i].get("value", 0)
            
            daily_ret = self.calculate_daily_return(curr_value, prev_value)
            returns.append(daily_ret)
        
        return returns
    
    # ===== MDD 계산 =====
    
    def calculate_mdd(
        self,
        portfolio_values: list[dict]
    ) -> dict:
        """
        MDD (Maximum Drawdown) 계산
        
        MDD = 고점 대비 최대 낙폭
        
        Args:
            portfolio_values: 일별 포트폴리오 가치
        
        Returns:
            {
                'mdd': -0.15,  # 최대 낙폭 (-15%)
                'mdd_start_date': '2025-01-15',  # 고점 날짜
                'mdd_end_date': '2025-01-20',    # 저점 날짜
                'recovery_date': '2025-01-25'    # 회복 날짜
            }
        """
        if not portfolio_values:
            return {"mdd": 0, "mdd_start_date": None, "mdd_end_date": None}
        
        # 날짜순 정렬
        sorted_values = sorted(portfolio_values, key=lambda x: x.get("date", ""))
        
        peak = sorted_values[0].get("value", 0)
        peak_date = sorted_values[0].get("date")
        
        mdd = 0
        mdd_start_date = None
        mdd_end_date = None
        
        for item in sorted_values:
            value = item.get("value", 0)
            current_date = item.get("date")
            
            # 새 고점 갱신
            if value > peak:
                peak = value
                peak_date = current_date
            
            # 낙폭 계산
            if peak > 0:
                drawdown = (value - peak) / peak
                
                if drawdown < mdd:
                    mdd = drawdown
                    mdd_start_date = peak_date
                    mdd_end_date = current_date
        
        return {
            "mdd": round(mdd, 4),
            "mdd_start_date": mdd_start_date,
            "mdd_end_date": mdd_end_date
        }
    
    # ===== 샤프 비율 =====
    
    def calculate_sharpe_ratio(
        self,
        daily_returns: list[float],
        risk_free_rate: float = RISK_FREE_RATE
    ) -> float:
        """
        샤프 비율 계산
        
        샤프 비율 = (수익률 - 무위험수익률) / 변동성
        
        Args:
            daily_returns: 일별 수익률
            risk_free_rate: 무위험 수익률 (연율)
        
        Returns:
            샤프 비율
        """
        if len(daily_returns) < 2:
            return 0.0
        
        # 평균 일별 수익률
        mean_return = sum(daily_returns) / len(daily_returns)
        
        # 일별 무위험 수익률
        daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
        
        # 초과 수익률
        excess_return = mean_return - daily_rf
        
        # 표준편차
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0.0
        
        # 연환산 샤프 비율
        sharpe = (excess_return / std_dev) * math.sqrt(TRADING_DAYS_PER_YEAR)
        
        return round(sharpe, 2)
    
    # ===== 승률/손익비 =====
    
    def calculate_win_rate(
        self,
        trades: list[dict]
    ) -> dict:
        """
        승률 및 손익 통계 계산
        
        Args:
            trades: 매매 기록 (청산된 거래만)
        
        Returns:
            {
                'total_trades': 20,
                'winning_trades': 14,
                'losing_trades': 6,
                'win_rate': 0.70,
                'avg_win': 0.08,
                'avg_loss': -0.04,
                'profit_factor': 2.0,
                'payoff_ratio': 2.0
            }
        """
        # 청산된 거래만 필터 (매도)
        closed_trades = [t for t in trades if t.get("action") == "sell"]
        
        if not closed_trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "profit_factor": 0,
                "payoff_ratio": 0
            }
        
        # 승/패 분리
        wins = []
        losses = []
        
        for trade in closed_trades:
            profit_rate = trade.get("profit_rate", 0)
            
            if profit_rate > 0:
                wins.append(profit_rate)
            elif profit_rate < 0:
                losses.append(profit_rate)
        
        total = len(closed_trades)
        win_count = len(wins)
        loss_count = len(losses)
        
        # 승률
        win_rate = win_count / total if total > 0 else 0
        
        # 평균 수익/손실
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        # 손익비 (Payoff Ratio)
        payoff_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        # 수익 팩터 (총 수익 / 총 손실)
        total_profit = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_profit / total_loss if total_loss > 0 else 0
        
        return {
            "total_trades": total,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "payoff_ratio": round(payoff_ratio, 2)
        }
    
    # ===== 종합 지표 =====
    
    def calculate_all_metrics(
        self,
        trades: list[dict],
        portfolio_values: list[dict],
        initial_capital: float = 10_000_000
    ) -> dict:
        """
        모든 성과 지표 계산
        
        Args:
            trades: 매매 기록
            portfolio_values: 일별 포트폴리오 가치
            initial_capital: 초기 자본금
        
        Returns:
            종합 성과 지표
        """
        # 기본 정보
        if portfolio_values:
            sorted_values = sorted(portfolio_values, key=lambda x: x.get("date", ""))
            start_date = sorted_values[0].get("date")
            end_date = sorted_values[-1].get("date")
            current_value = sorted_values[-1].get("value", initial_capital)
            trading_days = len(sorted_values)
        else:
            start_date = str(date.today())
            end_date = str(date.today())
            current_value = initial_capital
            trading_days = 0
        
        # 수익률
        total_return = self.calculate_total_return(initial_capital, current_value)
        
        # 연환산 수익률
        annualized_return = self.calculate_annualized_return(total_return, trading_days)
        
        # 일별 수익률
        daily_returns = self.calculate_daily_returns(portfolio_values)
        
        # 변동성
        volatility = 0
        if daily_returns:
            variance = sum((r - sum(daily_returns) / len(daily_returns)) ** 2 
                          for r in daily_returns) / len(daily_returns)
            volatility = math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
        
        # MDD
        mdd_info = self.calculate_mdd(portfolio_values)
        
        # 샤프 비율
        sharpe = self.calculate_sharpe_ratio(daily_returns)
        
        # 승률
        win_stats = self.calculate_win_rate(trades)
        
        # 총 수익금
        total_profit = current_value - initial_capital
        
        return {
            # 기본 정보
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": trading_days,
            "initial_capital": initial_capital,
            "current_value": current_value,
            # 수익률
            "total_profit": total_profit,
            "total_return": round(total_return, 4),
            "annualized_return": round(annualized_return, 4),
            # 리스크
            "volatility": round(volatility, 4),
            "mdd": mdd_info["mdd"],
            "mdd_start_date": mdd_info["mdd_start_date"],
            "mdd_end_date": mdd_info["mdd_end_date"],
            # 효율성
            "sharpe_ratio": sharpe,
            # 거래 통계
            **win_stats
        }
    
    # ===== 일일 성과 저장 =====
    
    def save_daily_performance(
        self,
        metrics: dict,
        db: Optional[Database] = None
    ) -> bool:
        """
        일일 성과 DB 저장
        
        Args:
            metrics: 성과 지표
            db: 데이터베이스 연결
        
        Returns:
            저장 성공 여부
        """
        close_db = False
        
        try:
            if db is None:
                db = Database()
                db.connect()
                close_db = True
            
            cursor = db.conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO performance (
                    date, total_value, daily_return, cumulative_return,
                    mdd, sharpe_ratio, win_rate, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(date.today()),
                metrics.get("current_value", 0),
                metrics.get("total_return", 0),
                metrics.get("total_return", 0),
                metrics.get("mdd", 0),
                metrics.get("sharpe_ratio", 0),
                metrics.get("win_rate", 0),
                metrics.get("total_trades", 0)
            ))
            
            db.conn.commit()
            logger.info("일일 성과 저장 완료")
            
            return True
            
        except Exception as e:
            logger.error(f"성과 저장 실패: {e}")
            return False
            
        finally:
            if close_db and db:
                db.close()


# ===== 편의 함수 =====

def calculate_performance_metrics(
    trades: list[dict],
    portfolio_values: list[dict],
    initial_capital: float = 10_000_000
) -> dict:
    """성과 지표 계산 (편의 함수)"""
    calc = PerformanceCalculator()
    return calc.calculate_all_metrics(trades, portfolio_values, initial_capital)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 성과 계산기 테스트")
    print("=" * 60)
    
    calc = PerformanceCalculator()
    
    # 테스트 포트폴리오 가치
    test_values = [
        {"date": "2025-01-01", "value": 10_000_000},
        {"date": "2025-01-02", "value": 10_150_000},
        {"date": "2025-01-03", "value": 10_050_000},
        {"date": "2025-01-06", "value": 10_300_000},
        {"date": "2025-01-07", "value": 9_900_000},
        {"date": "2025-01-08", "value": 10_200_000},
        {"date": "2025-01-09", "value": 10_500_000},
        {"date": "2025-01-10", "value": 10_450_000},
    ]
    
    # 테스트 거래
    test_trades = [
        {"action": "sell", "profit_rate": 0.08},
        {"action": "sell", "profit_rate": -0.03},
        {"action": "sell", "profit_rate": 0.12},
        {"action": "sell", "profit_rate": 0.05},
        {"action": "sell", "profit_rate": -0.05},
        {"action": "sell", "profit_rate": 0.07},
    ]
    
    # 지표 계산
    metrics = calc.calculate_all_metrics(test_trades, test_values, 10_000_000)
    
    print(f"\n📈 성과 지표:")
    print(f"  - 시작일: {metrics['start_date']}")
    print(f"  - 종료일: {metrics['end_date']}")
    print(f"  - 거래일: {metrics['trading_days']}일")
    print()
    print(f"  💰 수익률:")
    print(f"     총 수익금: {metrics['total_profit']:+,}원")
    print(f"     총 수익률: {metrics['total_return']:+.2%}")
    print(f"     연환산 수익률: {metrics['annualized_return']:+.2%}")
    print()
    print(f"  ⚠️ 리스크:")
    print(f"     변동성: {metrics['volatility']:.2%}")
    print(f"     MDD: {metrics['mdd']:.2%}")
    print()
    print(f"  📊 효율성:")
    print(f"     샤프 비율: {metrics['sharpe_ratio']:.2f}")
    print()
    print(f"  🎯 거래 통계:")
    print(f"     총 거래: {metrics['total_trades']}건")
    print(f"     승률: {metrics['win_rate']:.1%}")
    print(f"     평균 수익: {metrics['avg_win']:+.2%}")
    print(f"     평균 손실: {metrics['avg_loss']:+.2%}")
    print(f"     손익비: {metrics['payoff_ratio']:.2f}")
    
    print("\n" + "=" * 60)
    print("✅ 성과 계산기 테스트 완료!")
    print("=" * 60)
