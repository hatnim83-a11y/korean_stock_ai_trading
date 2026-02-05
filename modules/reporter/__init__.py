"""
reporter - 성과 리포팅 모듈

이 모듈은 트레이딩 성과 분석 및 리포팅 기능을 제공합니다.

주요 기능:
- 성과 지표 계산 (수익률, MDD, 샤프 등)
- 일일/주간 리포트 생성
- 텔레그램 알림 전송

사용법:
    from modules.reporter import (
        PerformanceCalculator,
        ReportGenerator,
        TelegramNotifier,
        generate_daily_report,
        send_telegram_message
    )
    
    # 성과 계산
    calc = PerformanceCalculator()
    metrics = calc.calculate_all_metrics(trades, portfolio_values)
    
    # 리포트 생성
    report = generate_daily_report(portfolio, trades, metrics, "telegram")
    
    # 텔레그램 전송
    notifier = TelegramNotifier()
    notifier.send_daily_report(portfolio, metrics)
"""

# 성과 계산
from modules.reporter.performance_calculator import (
    PerformanceCalculator,
    calculate_performance_metrics,
    TRADING_DAYS_PER_YEAR,
    RISK_FREE_RATE
)

# 리포트 생성
from modules.reporter.report_generator import (
    ReportGenerator,
    generate_daily_report,
    generate_weekly_report
)

# 텔레그램 알림
from modules.reporter.telegram_notifier import (
    TelegramNotifier,
    send_telegram_message,
    send_telegram_error
)


__all__ = [
    # 성과 계산
    "PerformanceCalculator",
    "calculate_performance_metrics",
    "TRADING_DAYS_PER_YEAR",
    "RISK_FREE_RATE",
    # 리포트 생성
    "ReportGenerator",
    "generate_daily_report",
    "generate_weekly_report",
    # 텔레그램
    "TelegramNotifier",
    "send_telegram_message",
    "send_telegram_error"
]
