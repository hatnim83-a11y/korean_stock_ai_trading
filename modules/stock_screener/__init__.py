"""
stock_screener - 수급 스크리닝 모듈

테마별 종목을 수급, 기술적, 재무 조건으로 필터링합니다.

주요 기능:
- KIS API 연동 (시세, 수급, 재무)
- 수급/기술적/재무 필터
- 종합 스크리닝 파이프라인

사용법:
    from modules.stock_screener import (
        KISApi,
        apply_all_filters,
        run_daily_screening
    )
    
    # KIS API로 종목 정보 조회
    kis = KISApi()
    info = kis.get_stock_full_info("005930")
    
    # 필터 적용
    result = apply_all_filters(info)
    
    # 일일 스크리닝 실행
    candidates = run_daily_screening(themes)
"""

# KIS API
from .kis_api import (
    KISApi,
    get_kis_api,
)

# 필터
from .filters import (
    apply_supply_filter,
    apply_technical_filter,
    apply_fundamental_filter,
    apply_liquidity_filter,
    apply_all_filters,
    calculate_supply_score,
    calculate_technical_score,
    calculate_final_score,
    filter_stocks,
)

# 스크리너
from .screener import (
    screen_stocks_in_theme,
    screen_all_themes,
    screen_with_mock_data,
    format_screening_report,
    run_daily_screening,
)


__all__ = [
    # KIS API
    "KISApi",
    "get_kis_api",
    # 필터
    "apply_supply_filter",
    "apply_technical_filter",
    "apply_fundamental_filter",
    "apply_liquidity_filter",
    "apply_all_filters",
    "calculate_supply_score",
    "calculate_technical_score",
    "calculate_final_score",
    "filter_stocks",
    # 스크리너
    "screen_stocks_in_theme",
    "screen_all_themes",
    "screen_with_mock_data",
    "format_screening_report",
    "run_daily_screening",
]
