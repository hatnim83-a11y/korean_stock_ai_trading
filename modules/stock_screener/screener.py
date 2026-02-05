"""
screener.py - 종합 스크리닝 파이프라인

이 파일은 테마별 종목을 스크리닝하여 투자 후보를 선정합니다.

파이프라인:
1. 테마별 종목 목록 수집
2. 각 종목 데이터 조회 (시세, 수급, 기술, 재무)
3. 필터 적용 (수급 → 기술 → 재무 → 유동성)
4. 점수 계산 및 순위화
5. 상위 N개 종목 반환

사용법:
    from modules.stock_screener.screener import (
        screen_stocks_in_theme,
        screen_all_themes,
        run_daily_screening
    )
    
    # 테마별 스크리닝
    candidates = screen_stocks_in_theme(theme, stock_codes)
    
    # 전체 스크리닝 실행
    results = run_daily_screening(top_themes)
"""

import asyncio
from datetime import date, datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


# ===== 스크리닝 상수 =====
MAX_STOCKS_PER_THEME = 10  # 테마당 최대 선정 종목 수
MAX_TOTAL_CANDIDATES = 30  # 전체 최대 후보 종목 수
MIN_FINAL_SCORE = 50.0  # 최소 최종 점수


def screen_stocks_in_theme(
    theme: dict,
    stock_codes: list[str],
    max_stocks: int = MAX_STOCKS_PER_THEME,
    kis_api: Optional["KISApi"] = None
) -> list[dict]:
    """
    특정 테마 내 종목 스크리닝
    
    테마에 속한 종목들의 데이터를 조회하고 필터를 적용합니다.
    
    Args:
        theme: 테마 정보 {'name': '2차전지', 'score': 87.5, ...}
        stock_codes: 종목 코드 리스트
        max_stocks: 최대 선정 종목 수
        kis_api: KIS API 인스턴스 (없으면 생성)
    
    Returns:
        스크리닝 통과 종목 리스트 (점수 순)
        
    Example:
        >>> theme = {'name': '2차전지', 'score': 87.5}
        >>> stocks = ['373220', '066970', '006400']
        >>> candidates = screen_stocks_in_theme(theme, stocks)
    """
    from .kis_api import KISApi
    from .filters import apply_all_filters
    
    theme_name = theme.get("name", "Unknown")
    theme_score = theme.get("total_score", theme.get("score", 50))
    
    logger.info(f"🔍 [{theme_name}] 테마 스크리닝 시작 ({len(stock_codes)}개 종목)")
    
    # KIS API 초기화
    if kis_api is None:
        kis_api = KISApi()
        should_close = True
    else:
        should_close = False
    
    candidates = []
    
    try:
        for code in stock_codes:
            try:
                # 종목 종합 정보 조회
                stock_info = kis_api.get_stock_full_info(code)
                
                if not stock_info:
                    logger.warning(f"[{code}] 종목 정보 조회 실패")
                    continue
                
                # 테마 정보 추가
                stock_info["theme"] = theme_name
                stock_info["theme_score"] = theme_score
                
                # 필터 적용
                filtered = apply_all_filters(stock_info)
                
                if filtered.get("all_passed"):
                    candidates.append(filtered)
                    
            except Exception as e:
                logger.warning(f"[{code}] 스크리닝 중 오류: {e}")
                continue
        
        # 점수 순 정렬
        candidates.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        
        # 최대 개수 제한
        candidates = candidates[:max_stocks]
        
        logger.info(
            f"✅ [{theme_name}] 스크리닝 완료: "
            f"{len(candidates)}/{len(stock_codes)}개 통과"
        )
        
    finally:
        if should_close:
            kis_api.close()
    
    return candidates


def screen_all_themes(
    themes: list[dict],
    theme_stocks: dict[str, list[str]],
    max_per_theme: int = MAX_STOCKS_PER_THEME,
    max_total: int = MAX_TOTAL_CANDIDATES
) -> list[dict]:
    """
    모든 테마에 대해 종목 스크리닝
    
    Args:
        themes: 테마 리스트 [{'name': '2차전지', 'score': 87.5}, ...]
        theme_stocks: 테마별 종목 코드 {'2차전지': ['373220', ...], ...}
        max_per_theme: 테마당 최대 종목 수
        max_total: 전체 최대 종목 수
    
    Returns:
        전체 스크리닝 통과 종목 (점수 순)
    """
    from .kis_api import KISApi
    
    logger.info(f"🔄 전체 테마 스크리닝 시작 ({len(themes)}개 테마)")
    
    all_candidates = []
    kis_api = KISApi()
    
    try:
        for theme in themes:
            theme_name = theme.get("name", "Unknown")
            stocks = theme_stocks.get(theme_name, [])
            
            if not stocks:
                logger.warning(f"[{theme_name}] 종목 목록이 없습니다")
                continue
            
            candidates = screen_stocks_in_theme(
                theme=theme,
                stock_codes=stocks,
                max_stocks=max_per_theme,
                kis_api=kis_api
            )
            
            all_candidates.extend(candidates)
        
        # 전체 점수 순 정렬
        all_candidates.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        
        # 중복 종목 제거 (같은 종목이 여러 테마에 속할 수 있음)
        seen_codes = set()
        unique_candidates = []
        for candidate in all_candidates:
            code = candidate.get("code")
            if code not in seen_codes:
                seen_codes.add(code)
                unique_candidates.append(candidate)
        
        # 최대 개수 제한
        unique_candidates = unique_candidates[:max_total]
        
        logger.info(f"✅ 전체 스크리닝 완료: {len(unique_candidates)}개 후보")
        
    finally:
        kis_api.close()
    
    return unique_candidates


def screen_with_mock_data(
    themes: list[dict],
    use_naver_stocks: bool = True
) -> list[dict]:
    """
    모의 데이터로 스크리닝 (API 없이 테스트용)
    
    실제 API 대신 네이버에서 크롤링한 데이터나 
    더미 데이터를 사용합니다.
    
    Args:
        themes: 테마 리스트
        use_naver_stocks: 네이버에서 종목 가져올지 여부
    
    Returns:
        스크리닝 결과 (더미)
    """
    from .filters import apply_all_filters
    
    logger.info("📊 모의 데이터로 스크리닝 (테스트 모드)")
    
    # 샘플 종목 데이터 (테스트용)
    sample_stocks = [
        {
            "code": "373220", "name": "LG에너지솔루션",
            "price": 420000, "theme": "2차전지",
            "foreign_net": 50_000_000_000, "institution_net": 30_000_000_000,
            "rsi": 52, "volume_ratio": 1.5, "ma_alignment": "bullish",
            "debt_ratio": 80, "operating_margin": 8,
            "trade_value": 200_000_000_000
        },
        {
            "code": "006400", "name": "삼성SDI",
            "price": 380000, "theme": "2차전지",
            "foreign_net": 20_000_000_000, "institution_net": 15_000_000_000,
            "rsi": 48, "volume_ratio": 1.3, "ma_alignment": "bullish",
            "debt_ratio": 60, "operating_margin": 10,
            "trade_value": 150_000_000_000
        },
        {
            "code": "066970", "name": "엘앤에프",
            "price": 180000, "theme": "2차전지",
            "foreign_net": 15_000_000_000, "institution_net": 10_000_000_000,
            "rsi": 55, "volume_ratio": 1.8, "ma_alignment": "bullish",
            "debt_ratio": 120, "operating_margin": 5,
            "trade_value": 80_000_000_000
        },
        {
            "code": "000660", "name": "SK하이닉스",
            "price": 150000, "theme": "AI반도체",
            "foreign_net": 100_000_000_000, "institution_net": 50_000_000_000,
            "rsi": 58, "volume_ratio": 2.0, "ma_alignment": "bullish",
            "debt_ratio": 40, "operating_margin": 20,
            "trade_value": 500_000_000_000
        },
        {
            "code": "005930", "name": "삼성전자",
            "price": 75000, "theme": "AI반도체",
            "foreign_net": 80_000_000_000, "institution_net": 40_000_000_000,
            "rsi": 45, "volume_ratio": 1.4, "ma_alignment": "neutral",
            "debt_ratio": 35, "operating_margin": 12,
            "trade_value": 800_000_000_000
        },
        {
            "code": "012450", "name": "한화에어로스페이스",
            "price": 250000, "theme": "K-방산",
            "foreign_net": 30_000_000_000, "institution_net": 25_000_000_000,
            "rsi": 62, "volume_ratio": 1.6, "ma_alignment": "bullish",
            "debt_ratio": 90, "operating_margin": 15,
            "trade_value": 100_000_000_000
        },
    ]
    
    # 테마에 맞는 종목만 필터링
    theme_names = {t.get("name") for t in themes}
    filtered_stocks = [s for s in sample_stocks if s.get("theme") in theme_names]
    
    # 테마 점수 추가
    theme_scores = {t.get("name"): t.get("total_score", t.get("score", 50)) for t in themes}
    for stock in filtered_stocks:
        stock["theme_score"] = theme_scores.get(stock.get("theme"), 50)
    
    # 필터 적용
    candidates = []
    for stock in filtered_stocks:
        result = apply_all_filters(stock)
        if result.get("all_passed"):
            candidates.append(result)
    
    # 점수 순 정렬
    candidates.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    
    logger.info(f"✅ 모의 스크리닝 완료: {len(candidates)}개 후보")
    
    return candidates


def format_screening_report(candidates: list[dict]) -> str:
    """
    스크리닝 결과를 보기 좋게 포맷팅
    
    Args:
        candidates: 스크리닝 통과 종목 리스트
    
    Returns:
        포맷팅된 리포트 문자열
    """
    if not candidates:
        return "스크리닝 통과 종목이 없습니다."
    
    lines = []
    lines.append("━" * 70)
    lines.append(f"📊 종목 스크리닝 결과 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    lines.append("━" * 70)
    
    # 테마별 그룹화
    by_theme = {}
    for stock in candidates:
        theme = stock.get("theme", "기타")
        if theme not in by_theme:
            by_theme[theme] = []
        by_theme[theme].append(stock)
    
    for theme, stocks in by_theme.items():
        lines.append("")
        lines.append(f"🎯 {theme} ({len(stocks)}개)")
        lines.append("─" * 70)
        
        for i, stock in enumerate(stocks, 1):
            code = stock.get("code", "?")
            name = stock.get("name", "?")
            price = stock.get("price", 0)
            score = stock.get("final_score", 0)
            
            # 수급 정보
            foreign = stock.get("foreign_net", 0) / 100_000_000
            institution = stock.get("institution_net", 0) / 100_000_000
            
            lines.append(
                f"  {i}. {name} ({code}) "
                f"| {price:,}원 | 점수: {score:.1f}"
            )
            lines.append(
                f"     수급: 외국인 {foreign:+.0f}억, 기관 {institution:+.0f}억 "
                f"| RSI: {stock.get('rsi', 0):.0f} "
                f"| MA: {stock.get('ma_alignment', '?')}"
            )
    
    lines.append("")
    lines.append("━" * 70)
    lines.append(f"총 {len(candidates)}개 종목")
    lines.append("━" * 70)
    
    return "\n".join(lines)


# ===== 일일 스크리닝 파이프라인 =====

def run_daily_screening(
    themes: list[dict],
    use_mock: bool = False,
    max_per_theme: int = MAX_STOCKS_PER_THEME,
    max_total: int = MAX_TOTAL_CANDIDATES,
    save_to_db: bool = True
) -> list[dict]:
    """
    일일 종목 스크리닝 실행
    
    테마 분석 결과를 바탕으로 종목 스크리닝을 수행합니다.
    
    Args:
        themes: 상위 테마 리스트 (테마 분석 모듈에서 전달)
        use_mock: 모의 데이터 사용 여부 (API 없이 테스트)
        max_per_theme: 테마당 최대 종목 수
        max_total: 전체 최대 종목 수
        save_to_db: DB 저장 여부
    
    Returns:
        스크리닝 통과 종목 리스트
        
    Example:
        >>> from modules.theme_analyzer import run_daily_theme_analysis_sync
        >>> themes = run_daily_theme_analysis_sync(top_count=5)
        >>> candidates = run_daily_screening(themes)
    """
    logger.info("=" * 60)
    logger.info("🔍 일일 종목 스크리닝 시작")
    logger.info("=" * 60)
    
    start_time = datetime.now()
    
    if not themes:
        logger.warning("스크리닝할 테마가 없습니다")
        return []
    
    logger.info(f"대상 테마: {len(themes)}개")
    for t in themes:
        logger.info(f"  - {t.get('name')} ({t.get('total_score', t.get('score', 0)):.1f}점)")
    
    try:
        if use_mock:
            # 모의 데이터로 스크리닝 (테스트용)
            candidates = screen_with_mock_data(themes)
        else:
            # 실제 API로 스크리닝
            # 테마별 종목 수집 (네이버 크롤링)
            from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks
            
            theme_stocks = {}
            for theme in themes:
                theme_name = theme.get("name")
                theme_url = theme.get("url")
                
                if theme_url:
                    stocks = crawl_naver_theme_stocks(theme_url)
                    stock_codes = [s.get("code") for s in stocks if s.get("code")]
                    theme_stocks[theme_name] = stock_codes[:20]  # 테마당 20개 제한
                else:
                    # URL이 없으면 빈 리스트
                    theme_stocks[theme_name] = []
            
            candidates = screen_all_themes(
                themes=themes,
                theme_stocks=theme_stocks,
                max_per_theme=max_per_theme,
                max_total=max_total
            )
        
        # 최소 점수 필터
        candidates = [c for c in candidates if c.get("final_score", 0) >= MIN_FINAL_SCORE]
        
        # DB 저장
        if save_to_db and candidates:
            try:
                from database import get_database
                
                db = get_database()
                
                stocks_to_save = [
                    {
                        "stock_code": c.get("code"),
                        "stock_name": c.get("name"),
                        "theme": c.get("theme"),
                        "supply_score": c.get("supply_score"),
                        "technical_score": c.get("technical_score"),
                        "ai_sentiment": c.get("ai_sentiment"),
                        "final_score": c.get("final_score"),
                        "selected": True
                    }
                    for c in candidates
                ]
                
                db.save_screened_stocks(stocks_to_save, date.today())
                db.close()
                
                logger.info(f"💾 스크리닝 결과 DB 저장 완료")
                
            except Exception as e:
                logger.error(f"DB 저장 실패: {e}")
        
        # 결과 리포트 출력
        report = format_screening_report(candidates)
        print(report)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"✅ 일일 스크리닝 완료 ({elapsed:.1f}초, {len(candidates)}개 종목)")
        logger.info("=" * 60)
        
        return candidates
        
    except Exception as e:
        logger.error(f"스크리닝 실패: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return []


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🔍 스크리닝 모듈 테스트")
    print("=" * 60)
    
    # 테스트 테마
    test_themes = [
        {"name": "2차전지", "total_score": 87.5, "grade": "S"},
        {"name": "AI반도체", "total_score": 82.3, "grade": "A"},
        {"name": "K-방산", "total_score": 79.1, "grade": "A"},
    ]
    
    print("\n테스트 테마:")
    for t in test_themes:
        print(f"  - {t['name']} ({t['total_score']}점)")
    
    print("\n모의 데이터로 스크리닝 테스트...")
    print("-" * 60)
    
    candidates = run_daily_screening(
        themes=test_themes,
        use_mock=True,  # 모의 데이터 사용
        save_to_db=False  # DB 저장 안 함
    )
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
