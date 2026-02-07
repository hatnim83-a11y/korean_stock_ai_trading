"""
theme_analyzer - 테마 분석 모듈

한국 주식시장의 테마를 분석하고 상위 테마를 선정하는 모듈입니다.

주요 기능:
- 네이버/한경 테마 크롤링
- 테마 점수 계산 (모멘텀, 수급, 뉴스, AI)
- Claude AI 감성 분석
- 상위 테마 선정

사용법:
    from modules.theme_analyzer import (
        crawl_all_themes,
        score_themes,
        select_top_themes,
        run_daily_theme_analysis_sync
    )
    
    # 전체 파이프라인 실행
    top_themes = run_daily_theme_analysis_sync(top_count=5)
    
    # 또는 개별 함수 사용
    themes = crawl_all_themes()
    scored = score_themes(themes)
    top = select_top_themes(scored, count=5)
"""

# 크롤러
from .crawlers import (
    crawl_naver_themes,
    crawl_krx_themes,
    crawl_naver_theme_stocks,
    crawl_theme_news_count,
    crawl_multiple_theme_news,
    crawl_all_themes,
    get_predefined_themes,
)

# 점수 계산
from .scorer import (
    calculate_momentum_score,
    calculate_supply_score,
    calculate_supply_score_from_amount,
    calculate_news_score,
    calculate_ai_sentiment_score,
    calculate_theme_total_score,
    score_themes,
)

# AI 분석
from .ai_analyzer import (
    analyze_theme_sentiment,
    analyze_themes_batch,
    analyze_themes_sync,
)

# 테마 선정
from .selector import (
    select_top_themes,
    format_theme_report,
    run_daily_theme_analysis,
    run_daily_theme_analysis_sync,
)

# 테마 로테이션
from .theme_rotator import (
    ThemeRotator,
    ThemeSnapshot,
    MainTheme,
)


__all__ = [
    # 크롤러
    "crawl_naver_themes",
    "crawl_krx_themes",
    "crawl_naver_theme_stocks",
    "crawl_theme_news_count",
    "crawl_multiple_theme_news",
    "crawl_all_themes",
    "get_predefined_themes",
    # 점수 계산
    "calculate_momentum_score",
    "calculate_supply_score",
    "calculate_supply_score_from_amount",
    "calculate_news_score",
    "calculate_ai_sentiment_score",
    "calculate_theme_total_score",
    "score_themes",
    # AI 분석
    "analyze_theme_sentiment",
    "analyze_themes_batch",
    "analyze_themes_sync",
    # 테마 선정
    "select_top_themes",
    "format_theme_report",
    "run_daily_theme_analysis",
    "run_daily_theme_analysis_sync",
    # 테마 로테이션
    "ThemeRotator",
    "ThemeSnapshot",
    "MainTheme",
]
