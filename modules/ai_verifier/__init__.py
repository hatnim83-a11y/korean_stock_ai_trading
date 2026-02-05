"""
ai_verifier - AI 검증 모듈

스크리닝된 종목에 대해 뉴스/공시 기반 AI 검증을 수행합니다.

주요 기능:
- 네이버 금융 뉴스 크롤링
- DART 공시 수집
- Claude AI 종목 분석
- AI 검증 파이프라인

사용법:
    from modules.ai_verifier import (
        fetch_stock_news,
        analyze_stock,
        verify_stocks,
        run_daily_verification
    )
    
    # 뉴스 수집
    news = fetch_stock_news("005930", days=7)
    
    # 종목 AI 분석
    result = analyze_stock(stock, news_text, disclosure_text)
    
    # 일일 검증 실행
    verified = run_daily_verification(candidates)
"""

# 뉴스 크롤러
from .news_crawler import (
    fetch_stock_news,
    fetch_news_content,
    fetch_stock_news_with_content,
    fetch_multiple_stocks_news,
    format_news_for_ai,
)

# DART API
from .dart_api import (
    fetch_dart_disclosures,
    fetch_important_disclosures,
    analyze_disclosure_sentiment,
    format_disclosures_for_ai,
    get_mock_disclosures,
)

# Claude 분석
from .claude_analyzer import (
    analyze_stock,
    analyze_stocks_batch,
    analyze_stocks_sync,
)

# 검증 파이프라인
from .verifier import (
    verify_single_stock,
    verify_stocks_async,
    verify_stocks,
    calculate_final_score_with_ai,
    format_verification_report,
    run_daily_verification,
)


__all__ = [
    # 뉴스
    "fetch_stock_news",
    "fetch_news_content",
    "fetch_stock_news_with_content",
    "fetch_multiple_stocks_news",
    "format_news_for_ai",
    # DART
    "fetch_dart_disclosures",
    "fetch_important_disclosures",
    "analyze_disclosure_sentiment",
    "format_disclosures_for_ai",
    "get_mock_disclosures",
    # Claude
    "analyze_stock",
    "analyze_stocks_batch",
    "analyze_stocks_sync",
    # 검증
    "verify_single_stock",
    "verify_stocks_async",
    "verify_stocks",
    "calculate_final_score_with_ai",
    "format_verification_report",
    "run_daily_verification",
]
