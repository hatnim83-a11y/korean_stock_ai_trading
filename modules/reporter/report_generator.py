"""
report_generator.py - 리포트 생성 모듈

이 파일은 일별/주간 성과 리포트를 생성합니다.

주요 기능:
- 일일 성과 리포트
- 주간 성과 리포트
- 포트폴리오 현황 리포트
- 텍스트/마크다운/HTML 포맷

사용법:
    from modules.reporter.report_generator import ReportGenerator
    
    generator = ReportGenerator()
    daily_report = generator.generate_daily_report(portfolio, trades, metrics)
"""

from datetime import datetime, date, timedelta
from config import now_kst
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from database import Database
from modules.reporter.performance_calculator import PerformanceCalculator


class ReportGenerator:
    """
    리포트 생성기
    
    트레이딩 성과 리포트를 다양한 형식으로 생성합니다.
    
    Example:
        >>> generator = ReportGenerator()
        >>> report = generator.generate_daily_report(portfolio, trades, metrics)
        >>> print(report)
    """
    
    def __init__(self):
        """리포트 생성기 초기화"""
        self.calc = PerformanceCalculator()
        logger.debug("리포트 생성기 초기화")
    
    # ===== 일일 리포트 =====
    
    def generate_daily_report(
        self,
        portfolio: list[dict],
        trades: Optional[list[dict]] = None,
        metrics: Optional[dict] = None,
        format_type: str = "text",
        themes: Optional[list[dict]] = None,
        ai_analysis: Optional[list[dict]] = None
    ) -> str:
        """
        일일 성과 리포트 생성

        Args:
            portfolio: 현재 포트폴리오
            trades: 오늘 거래 내역
            metrics: 성과 지표
            format_type: 포맷 유형 ("text", "markdown", "telegram")
            themes: 선정된 테마 리스트 (선정 이유 포함)
            ai_analysis: AI 분석 결과 리스트 (선정 이유 포함)

        Returns:
            리포트 문자열
        """
        today = now_kst().date()
        
        # 포트폴리오 통계
        total_value = sum(p.get("current_amount", 0) or p.get("amount", 0) for p in portfolio)
        total_cost = sum(p.get("buy_amount", 0) or p.get("shares", 0) * p.get("buy_price", 0) for p in portfolio)
        total_profit = total_value - total_cost
        profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0
        
        # 오늘 거래
        today_trades = trades or []
        today_buys = [t for t in today_trades if t.get("action") == "buy"]
        today_sells = [t for t in today_trades if t.get("action") == "sell"]
        
        # 수익/손실 상위 종목
        sorted_by_profit = sorted(
            portfolio,
            key=lambda x: x.get("profit_rate", 0),
            reverse=True
        )
        
        best_3 = sorted_by_profit[:3]
        worst_3 = sorted_by_profit[-3:] if len(sorted_by_profit) >= 3 else sorted_by_profit
        
        if format_type == "telegram":
            return self._format_daily_telegram(
                today, portfolio, total_value, total_cost, total_profit, profit_rate,
                today_buys, today_sells, best_3, worst_3, metrics, themes, ai_analysis
            )
        elif format_type == "markdown":
            return self._format_daily_markdown(
                today, portfolio, total_value, total_cost, total_profit, profit_rate,
                today_buys, today_sells, best_3, worst_3, metrics
            )
        else:
            return self._format_daily_text(
                today, portfolio, total_value, total_cost, total_profit, profit_rate,
                today_buys, today_sells, best_3, worst_3, metrics, themes, ai_analysis
            )
    
    def _format_daily_text(
        self, today, portfolio, total_value, total_cost, total_profit, profit_rate,
        buys, sells, best_3, worst_3, metrics, themes=None, ai_analysis=None
    ) -> str:
        """텍스트 포맷"""
        lines = [
            "=" * 60,
            f"📊 일일 성과 리포트 ({today})",
            "=" * 60,
            "",
            "💰 포트폴리오 현황",
            "-" * 40,
            f"  총 평가액:   {total_value:>15,}원",
            f"  총 투자액:   {total_cost:>15,}원",
            f"  총 수익금:   {total_profit:>+15,}원",
            f"  수익률:      {profit_rate:>+14.2f}%",
            f"  보유 종목:   {len(portfolio):>15}개",
            ""
        ]

        # 테마 선정 이유 (신규 추가)
        if themes:
            lines.extend([
                "🎯 오늘의 테마 (선정 이유)",
                "-" * 40
            ])
            for i, t in enumerate(themes[:5], 1):
                theme_name = t.get("theme", t.get("name", ""))
                score = t.get("total_score", t.get("score", 0))
                grade = t.get("grade", "")
                reason = t.get("selection_reason", "")
                lines.append(f"  {i}. {theme_name} ({score:.1f}점, {grade}등급)")
                if reason:
                    lines.append(f"     └ {reason}")
            lines.append("")

        # 오늘 거래 + AI 선정 이유
        if buys or sells:
            lines.extend([
                "📈 오늘 거래",
                "-" * 40
            ])
            if buys:
                lines.append(f"  매수: {len(buys)}건")
                for t in buys:
                    stock_name = t.get('stock_name', '')
                    shares = t.get('shares', 0)
                    # AI 분석 이유 찾기
                    ai_reason = ""
                    if ai_analysis:
                        for a in ai_analysis:
                            if a.get("stock_code") == t.get("stock_code") or a.get("stock_name") == stock_name:
                                ai_reason = a.get("ai_summary", a.get("reason", ""))[:50]
                                break
                    lines.append(f"    - {stock_name}: {shares}주")
                    if ai_reason:
                        lines.append(f"      └ AI: {ai_reason}")
            if sells:
                lines.append(f"  매도: {len(sells)}건")
                for t in sells:
                    lines.append(f"    - {t.get('stock_name')}: {t.get('reason', '')}")
            lines.append("")

        # AI 종목 선정 상세 (신규 추가)
        if ai_analysis and not buys:
            lines.extend([
                "🤖 AI 종목 분석",
                "-" * 40
            ])
            for i, a in enumerate(ai_analysis[:5], 1):
                stock_name = a.get("stock_name", "")
                ai_score = a.get("ai_score", 0)
                decision = a.get("decision", "")
                reason = a.get("ai_summary", a.get("reason", ""))[:60]
                lines.append(f"  {i}. {stock_name} (AI:{ai_score}/10, {decision})")
                if reason:
                    lines.append(f"     └ {reason}")
            lines.append("")

        # Best/Worst
        if best_3:
            lines.extend([
                "🔥 수익 Top 3",
                "-" * 40
            ])
            for i, p in enumerate(best_3, 1):
                pct = p.get("profit_rate", 0)
                lines.append(f"  {i}. {p.get('stock_name', '')}: {pct:+.2f}%")
            lines.append("")

        if worst_3:
            lines.extend([
                "😰 손실 Top 3",
                "-" * 40
            ])
            for i, p in enumerate(reversed(worst_3), 1):
                pct = p.get("profit_rate", 0)
                lines.append(f"  {i}. {p.get('stock_name', '')}: {pct:+.2f}%")
            lines.append("")

        # 성과 지표
        if metrics:
            lines.extend([
                "📈 성과 지표",
                "-" * 40,
                f"  샤프 비율:   {metrics.get('sharpe_ratio', 0):>10.2f}",
                f"  MDD:         {metrics.get('mdd', 0):>10.2%}",
                f"  승률:        {metrics.get('win_rate', 0):>10.1%}",
                ""
            ])

        lines.append("=" * 60)

        return "\n".join(lines)
    
    def _format_daily_markdown(
        self, today, portfolio, total_value, total_cost, total_profit, profit_rate,
        buys, sells, best_3, worst_3, metrics
    ) -> str:
        """마크다운 포맷"""
        lines = [
            f"# 📊 일일 성과 리포트",
            f"**{today}**",
            "",
            "## 💰 포트폴리오 현황",
            "",
            "| 항목 | 금액 |",
            "|------|------|",
            f"| 총 평가액 | {total_value:,}원 |",
            f"| 총 투자액 | {total_cost:,}원 |",
            f"| 총 수익금 | {total_profit:+,}원 |",
            f"| 수익률 | {profit_rate:+.2f}% |",
            f"| 보유 종목 | {len(portfolio)}개 |",
            ""
        ]
        
        if best_3:
            lines.append("## 🔥 수익 Top 3")
            for i, p in enumerate(best_3, 1):
                lines.append(f"{i}. **{p.get('stock_name', '')}**: {p.get('profit_rate', 0):+.2f}%")
            lines.append("")
        
        if worst_3:
            lines.append("## 😰 손실 Top 3")
            for i, p in enumerate(reversed(worst_3), 1):
                lines.append(f"{i}. **{p.get('stock_name', '')}**: {p.get('profit_rate', 0):+.2f}%")
            lines.append("")
        
        return "\n".join(lines)
    
    def _format_daily_telegram(
        self, today, portfolio, total_value, total_cost, total_profit, profit_rate,
        buys, sells, best_3, worst_3, metrics, themes=None, ai_analysis=None
    ) -> str:
        """텔레그램 마크다운 포맷"""
        # 이모지로 상태 표시
        status_emoji = "📈" if profit_rate >= 0 else "📉"

        lines = [
            f"📊 *일일 성과 리포트*",
            f"📅 {today}",
            "",
            f"💰 *포트폴리오*",
            "```",
            f"총 평가액: {total_value:>12,}원",
            f"총 투자액: {total_cost:>12,}원",
            f"수익금:    {total_profit:>+12,}원",
            f"수익률:    {profit_rate:>+11.2f}%",
            "```",
            ""
        ]

        # 테마 선정 이유 (신규)
        if themes:
            lines.append("🎯 *오늘의 테마*")
            for i, t in enumerate(themes[:3], 1):
                theme_name = t.get("theme", t.get("name", ""))
                score = t.get("total_score", t.get("score", 0))
                reason = t.get("selection_reason", "")[:30]
                lines.append(f"  {i}. {theme_name} ({score:.0f}점)")
                if reason:
                    lines.append(f"     └ {reason}")
            lines.append("")

        # 오늘 거래 + AI 이유
        if buys or sells:
            lines.append("📈 *오늘 거래*")
            if buys:
                for t in buys[:4]:
                    stock_name = t.get('stock_name', '')
                    # AI 분석 이유 찾기
                    ai_reason = ""
                    if ai_analysis:
                        for a in ai_analysis:
                            if a.get("stock_code") == t.get("stock_code") or a.get("stock_name") == stock_name:
                                ai_reason = a.get("ai_summary", a.get("reason", ""))[:35]
                                break
                    lines.append(f"  🟢 {stock_name}")
                    if ai_reason:
                        lines.append(f"     └ {ai_reason}")
            if sells:
                for t in sells[:3]:
                    lines.append(f"  🔴 {t.get('stock_name')}: {t.get('reason', '')[:20]}")
            lines.append("")

        # Best 3
        if best_3:
            lines.append("🔥 *Best 3*")
            for i, p in enumerate(best_3, 1):
                pct = p.get("profit_rate", 0)
                emoji = "🚀" if pct > 5 else "📈"
                lines.append(f"  {emoji} {p.get('stock_name', '')}: {pct:+.1f}%")
            lines.append("")

        # Worst 3
        if worst_3 and any(p.get("profit_rate", 0) < 0 for p in worst_3):
            lines.append("😰 *Worst 3*")
            for i, p in enumerate(reversed(worst_3), 1):
                pct = p.get("profit_rate", 0)
                if pct < 0:
                    lines.append(f"  📉 {p.get('stock_name', '')}: {pct:+.1f}%")
            lines.append("")

        # 요약
        lines.append(f"{status_emoji} 현재 {len(portfolio)}개 종목 보유 중")

        return "\n".join(lines)
    
    # ===== 주간 리포트 =====
    
    def generate_weekly_report(
        self,
        weekly_data: dict,
        format_type: str = "text"
    ) -> str:
        """
        주간 성과 리포트 생성
        
        Args:
            weekly_data: 주간 데이터
                {
                    'start_date': '2025-01-27',
                    'end_date': '2025-01-31',
                    'start_value': 10000000,
                    'end_value': 10500000,
                    'trades': [...],
                    'metrics': {...}
                }
            format_type: 포맷 유형
        
        Returns:
            주간 리포트
        """
        start_date = weekly_data.get("start_date", "")
        end_date = weekly_data.get("end_date", "")
        start_value = weekly_data.get("start_value", 0)
        end_value = weekly_data.get("end_value", 0)
        trades = weekly_data.get("trades", [])
        metrics = weekly_data.get("metrics", {})
        
        weekly_return = (end_value - start_value) / start_value if start_value > 0 else 0
        weekly_profit = end_value - start_value
        
        # 주간 거래 통계
        buy_count = len([t for t in trades if t.get("action") == "buy"])
        sell_count = len([t for t in trades if t.get("action") == "sell"])
        
        if format_type == "telegram":
            return self._format_weekly_telegram(
                start_date, end_date, start_value, end_value,
                weekly_profit, weekly_return, buy_count, sell_count, metrics
            )
        else:
            return self._format_weekly_text(
                start_date, end_date, start_value, end_value,
                weekly_profit, weekly_return, buy_count, sell_count, metrics
            )
    
    def _format_weekly_text(
        self, start_date, end_date, start_value, end_value,
        weekly_profit, weekly_return, buy_count, sell_count, metrics
    ) -> str:
        """주간 텍스트 포맷"""
        lines = [
            "=" * 60,
            f"📊 주간 성과 리포트",
            f"   {start_date} ~ {end_date}",
            "=" * 60,
            "",
            "💰 주간 성과",
            "-" * 40,
            f"  주초 평가액:  {start_value:>15,}원",
            f"  주말 평가액:  {end_value:>15,}원",
            f"  주간 수익금:  {weekly_profit:>+15,}원",
            f"  주간 수익률:  {weekly_return:>+14.2%}",
            "",
            "📈 거래 현황",
            "-" * 40,
            f"  매수: {buy_count}건",
            f"  매도: {sell_count}건",
            ""
        ]
        
        if metrics:
            lines.extend([
                "📊 누적 성과",
                "-" * 40,
                f"  총 수익률:    {metrics.get('total_return', 0):>+10.2%}",
                f"  샤프 비율:    {metrics.get('sharpe_ratio', 0):>10.2f}",
                f"  MDD:          {metrics.get('mdd', 0):>10.2%}",
                f"  승률:         {metrics.get('win_rate', 0):>10.1%}",
                f"  손익비:       {metrics.get('payoff_ratio', 0):>10.2f}",
                ""
            ])
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def _format_weekly_telegram(
        self, start_date, end_date, start_value, end_value,
        weekly_profit, weekly_return, buy_count, sell_count, metrics
    ) -> str:
        """주간 텔레그램 포맷"""
        emoji = "📈" if weekly_return >= 0 else "📉"
        
        lines = [
            f"📊 *주간 성과 리포트*",
            f"📅 {start_date} ~ {end_date}",
            "",
            f"{emoji} *주간 성과*",
            "```",
            f"주초: {start_value:>12,}원",
            f"주말: {end_value:>12,}원",
            f"수익: {weekly_profit:>+12,}원",
            f"수익률: {weekly_return:>+9.2%}",
            "```",
            "",
            f"📈 *거래*: 매수 {buy_count}건 / 매도 {sell_count}건",
            ""
        ]
        
        if metrics:
            lines.extend([
                "📊 *누적 성과*",
                f"  • 총 수익률: {metrics.get('total_return', 0):+.2%}",
                f"  • 샤프: {metrics.get('sharpe_ratio', 0):.2f}",
                f"  • MDD: {metrics.get('mdd', 0):.2%}",
                f"  • 승률: {metrics.get('win_rate', 0):.1%}"
            ])
        
        return "\n".join(lines)
    
    # ===== 포트폴리오 상세 =====
    
    def generate_portfolio_detail(
        self,
        portfolio: list[dict],
        format_type: str = "text"
    ) -> str:
        """
        포트폴리오 상세 리포트
        
        Args:
            portfolio: 포트폴리오 리스트
            format_type: 포맷 유형
        
        Returns:
            상세 리포트
        """
        lines = [
            "=" * 80,
            "📋 포트폴리오 상세",
            "=" * 80,
            "",
            f"{'No.':<4} {'종목':<12} {'수량':>8} {'매수가':>10} {'현재가':>10} {'수익률':>8} {'손절':>10}",
            "-" * 80
        ]
        
        total_value = 0
        total_cost = 0
        
        for i, p in enumerate(portfolio, 1):
            name = p.get("stock_name", "")[:10]
            shares = p.get("shares", 0)
            buy_price = p.get("buy_price", 0)
            current_price = p.get("current_price", buy_price)
            profit_rate = p.get("profit_rate", 0)
            stop_loss = p.get("stop_loss", 0)
            
            cost = shares * buy_price
            value = shares * current_price
            total_cost += cost
            total_value += value
            
            lines.append(
                f"{i:<4} {name:<12} {shares:>7}주 "
                f"{buy_price:>9,} {current_price:>9,} "
                f"{profit_rate:>+7.1f}% {stop_loss:>9,}"
            )
        
        lines.extend([
            "-" * 80,
            f"총계: {len(portfolio)}종목 / 투자: {total_cost:,}원 / 평가: {total_value:,}원",
            "=" * 80
        ])
        
        return "\n".join(lines)


# ===== 편의 함수 =====

def generate_daily_report(
    portfolio: list[dict],
    trades: Optional[list[dict]] = None,
    metrics: Optional[dict] = None,
    format_type: str = "text",
    themes: Optional[list[dict]] = None,
    ai_analysis: Optional[list[dict]] = None
) -> str:
    """일일 리포트 생성 (편의 함수)"""
    generator = ReportGenerator()
    return generator.generate_daily_report(portfolio, trades, metrics, format_type, themes, ai_analysis)


def generate_weekly_report(
    weekly_data: dict,
    format_type: str = "text"
) -> str:
    """주간 리포트 생성 (편의 함수)"""
    generator = ReportGenerator()
    return generator.generate_weekly_report(weekly_data, format_type)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 리포트 생성기 테스트")
    print("=" * 60)
    
    generator = ReportGenerator()
    
    # 테스트 포트폴리오
    test_portfolio = [
        {
            "stock_name": "삼성전자", "stock_code": "005930",
            "shares": 10, "buy_price": 75000, "current_price": 77000,
            "profit_rate": 2.67, "stop_loss": 69000
        },
        {
            "stock_name": "SK하이닉스", "stock_code": "000660",
            "shares": 5, "buy_price": 195000, "current_price": 190000,
            "profit_rate": -2.56, "stop_loss": 179000
        },
        {
            "stock_name": "LG에너지솔루션", "stock_code": "373220",
            "shares": 2, "buy_price": 420000, "current_price": 450000,
            "profit_rate": 7.14, "stop_loss": 386000
        }
    ]
    
    test_trades = [
        {"stock_name": "삼성전자", "action": "buy", "shares": 10},
    ]
    
    test_metrics = {
        "sharpe_ratio": 1.85,
        "mdd": -0.08,
        "win_rate": 0.65,
        "total_return": 0.045
    }
    
    # 일일 리포트 (텍스트)
    print("\n1️⃣ 일일 리포트 (텍스트):")
    print(generator.generate_daily_report(test_portfolio, test_trades, test_metrics, "text"))
    
    # 일일 리포트 (텔레그램)
    print("\n2️⃣ 일일 리포트 (텔레그램):")
    print(generator.generate_daily_report(test_portfolio, test_trades, test_metrics, "telegram"))
    
    # 포트폴리오 상세
    print("\n3️⃣ 포트폴리오 상세:")
    print(generator.generate_portfolio_detail(test_portfolio))
    
    print("\n✅ 리포트 생성기 테스트 완료!")
