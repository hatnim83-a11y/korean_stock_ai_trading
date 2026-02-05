"""
portfolio_optimizer - 포트폴리오 최적화 모듈

이 모듈은 AI 검증된 종목들을 기반으로 최적 포트폴리오를 구성합니다.

주요 기능:
- 변동성, ATR, 손절/익절 계산
- 가중치 계산 (동일가중, 점수기반, 리스크패리티)
- 포트폴리오 최적화
- 매수 주문 생성
- 포트폴리오 출력/리포팅

사용법:
    from modules.portfolio_optimizer import (
        optimize_portfolio,
        run_daily_optimization,
        display_portfolio,
        format_portfolio_for_telegram
    )
    
    # 포트폴리오 최적화
    result = run_daily_optimization(
        verified_stocks=verified,
        capital=10_000_000,
        strategy="score_based"
    )
    
    # 결과 출력
    display_portfolio(result['portfolio'])
"""

# 계산 함수들
from modules.portfolio_optimizer.calculators import (
    # 변동성
    calculate_volatility,
    calculate_volatility_from_prices,
    # ATR
    calculate_atr,
    calculate_atr_percentage,
    # 손절/익절
    calculate_stop_loss,
    calculate_take_profit,
    calculate_stop_take_profit,
    # 포지션 사이즈
    calculate_position_size,
    # 리스크
    calculate_risk_amount,
    calculate_daily_risk
)

# 최적화 함수들
from modules.portfolio_optimizer.optimizer import (
    # 가중치 계산
    calculate_equal_weights,
    calculate_score_based_weights,
    calculate_risk_parity_weights,
    # 포트폴리오 최적화
    optimize_portfolio,
    # 주문 생성
    generate_buy_orders,
    # DB 저장
    save_portfolio_to_db,
    # 일일 최적화
    run_daily_optimization
)

# 포맷팅 함수들
from modules.portfolio_optimizer.formatter import (
    # 콘솔 출력
    display_portfolio,
    display_orders,
    # 텔레그램
    format_portfolio_for_telegram,
    format_orders_for_telegram,
    # 요약/리포트
    generate_portfolio_summary,
    generate_optimization_report,
    # 내보내기
    export_portfolio_to_json,
    export_positions_to_csv
)

# 상수
from modules.portfolio_optimizer.calculators import (
    TRADING_DAYS_PER_YEAR,
    DEFAULT_ATR_PERIOD,
    DEFAULT_ATR_MULTIPLIER
)

from modules.portfolio_optimizer.optimizer import (
    MAX_POSITIONS,
    MIN_POSITION_WEIGHT,
    MAX_POSITION_WEIGHT,
    CASH_BUFFER
)


__all__ = [
    # 계산 함수
    "calculate_volatility",
    "calculate_volatility_from_prices",
    "calculate_atr",
    "calculate_atr_percentage",
    "calculate_stop_loss",
    "calculate_take_profit",
    "calculate_stop_take_profit",
    "calculate_position_size",
    "calculate_risk_amount",
    "calculate_daily_risk",
    # 가중치
    "calculate_equal_weights",
    "calculate_score_based_weights",
    "calculate_risk_parity_weights",
    # 최적화
    "optimize_portfolio",
    "generate_buy_orders",
    "save_portfolio_to_db",
    "run_daily_optimization",
    # 포맷팅
    "display_portfolio",
    "display_orders",
    "format_portfolio_for_telegram",
    "format_orders_for_telegram",
    "generate_portfolio_summary",
    "generate_optimization_report",
    "export_portfolio_to_json",
    "export_positions_to_csv",
    # 상수
    "TRADING_DAYS_PER_YEAR",
    "DEFAULT_ATR_PERIOD",
    "DEFAULT_ATR_MULTIPLIER",
    "MAX_POSITIONS",
    "MIN_POSITION_WEIGHT",
    "MAX_POSITION_WEIGHT",
    "CASH_BUFFER"
]
