"""
selector.py - 테마 선정 모듈

이 파일은 분석된 테마 중에서 상위 N개 테마를 선정합니다.

주요 기능:
- 점수 기반 상위 테마 선정
- 테마 다양성 보장 (카테고리별 분산)
- 제외 조건 적용 (블랙리스트, 최소 점수)
- 일일 테마 분석 파이프라인 통합

사용법:
    from modules.theme_analyzer.selector import (
        select_top_themes,
        run_daily_theme_analysis
    )
    
    # 상위 5개 테마 선정
    top_themes = select_top_themes(scored_themes, count=5)
    
    # 일일 테마 분석 실행
    results = await run_daily_theme_analysis()
"""

import asyncio
from datetime import date, datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings


# ===== 설정 로드 =====

# 테마 블랙리스트 (config에서 가져오기)
THEME_BLACKLIST = settings.THEME_BLACKLIST

# 최소 점수 기준
MIN_SELECTION_SCORE = 15.0

# 최소 종목 수 기준
MIN_STOCK_COUNT = settings.MIN_THEME_STOCK_COUNT

# 카테고리별 최대 테마 수
MAX_THEMES_PER_CATEGORY = 2


def select_top_themes(
    scored_themes: list[dict],
    count: int = 5,
    min_score: float = MIN_SELECTION_SCORE,
    ensure_diversity: bool = True,
    blacklist: Optional[list[str]] = None
) -> list[dict]:
    """
    상위 N개 테마 선정
    
    점수 기준으로 상위 테마를 선정하되, 다양성과 안전성을 고려합니다.
    
    Args:
        scored_themes: 점수가 계산된 테마 리스트
            [{'name': '2차전지', 'total_score': 87.5, ...}, ...]
        count: 선정할 테마 수 (기본 5개)
        min_score: 최소 점수 기준 (기본 40점)
        ensure_diversity: 카테고리 다양성 보장 여부
        blacklist: 추가 블랙리스트 테마명
    
    Returns:
        선정된 테마 리스트 (점수 순)
        
    Example:
        >>> top = select_top_themes(scored_themes, count=5)
        >>> print(top[0]['name'])
        '2차전지'
    """
    if not scored_themes:
        logger.warning("선정할 테마가 없습니다")
        return []
    
    # 블랙리스트 병합
    full_blacklist = set(THEME_BLACKLIST)
    if blacklist:
        full_blacklist.update(blacklist)
    
    # 필터링: 블랙리스트 제외, 최소 점수 이상, 최소 종목 수 이상
    filtered = []
    for theme in scored_themes:
        theme_name = theme.get("name", "")
        score = theme.get("total_score", 0)
        stock_count = theme.get("stock_count", 0)
        
        # 블랙리스트 체크
        if any(bl.lower() in theme_name.lower() for bl in full_blacklist):
            logger.debug(f"[{theme_name}] 블랙리스트 - 제외")
            continue
        
        # 최소 점수 체크
        if score < min_score:
            logger.debug(f"[{theme_name}] 점수 부족 ({score:.1f} < {min_score}) - 제외")
            continue
        
        # 최소 종목 수 체크 (stock_count=0이면 장외시간 데이터 미제공이므로 스킵)
        if stock_count > 0 and stock_count < MIN_STOCK_COUNT:
            logger.debug(f"[{theme_name}] 종목수 부족 ({stock_count}개 < {MIN_STOCK_COUNT}개) - 제외")
            continue
        
        filtered.append(theme)
    
    logger.info(f"필터링 후 {len(filtered)}개 테마 남음 (원래 {len(scored_themes)}개)")
    
    # 점수 순 정렬
    filtered.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    
    # 다양성 확보 로직
    if ensure_diversity and len(filtered) > count:
        selected = _select_with_diversity(filtered, count)
    else:
        selected = filtered[:count]
    
    # 선정 결과 로깅
    logger.info(f"🎯 상위 {len(selected)}개 테마 선정 완료")
    for i, theme in enumerate(selected, 1):
        logger.info(
            f"  {i}. {theme['name']} "
            f"({theme.get('total_score', 0):.1f}점, {theme.get('grade', '?')})"
        )
    
    return selected


def _select_with_diversity(
    themes: list[dict],
    count: int,
    max_per_category: int = MAX_THEMES_PER_CATEGORY
) -> list[dict]:
    """
    카테고리 다양성을 고려한 테마 선정
    
    같은 카테고리에서 너무 많은 테마가 선정되지 않도록 합니다.
    
    Args:
        themes: 점수 순 정렬된 테마 리스트
        count: 선정할 테마 수
        max_per_category: 카테고리당 최대 테마 수
    
    Returns:
        다양성이 확보된 테마 리스트
    """
    selected = []
    category_count = {}
    
    for theme in themes:
        if len(selected) >= count:
            break
        
        category = theme.get("category", "기타")
        
        # 카테고리별 제한 체크
        if category_count.get(category, 0) >= max_per_category:
            logger.debug(f"[{theme['name']}] 카테고리 제한 ({category}) - 스킵")
            continue
        
        selected.append(theme)
        category_count[category] = category_count.get(category, 0) + 1
    
    # 아직 count 미달이면 나머지 채우기
    if len(selected) < count:
        for theme in themes:
            if len(selected) >= count:
                break
            if theme not in selected:
                selected.append(theme)
    
    return selected


def format_theme_report(themes: list[dict]) -> str:
    """
    테마 분석 결과를 보기 좋게 포맷팅
    
    Args:
        themes: 선정된 테마 리스트
    
    Returns:
        포맷팅된 리포트 문자열
    """
    if not themes:
        return "선정된 테마가 없습니다."
    
    lines = []
    lines.append("━" * 55)
    lines.append(f"📊 테마 분석 결과 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    lines.append("━" * 55)
    
    for i, theme in enumerate(themes, 1):
        name = theme.get("name", "Unknown")
        score = theme.get("total_score", 0)
        grade = theme.get("grade", "?")
        
        # 점수 상세
        momentum = theme.get("momentum_score", 0)
        supply = theme.get("supply_score", 0)
        news = theme.get("news_score", 0)
        ai = theme.get("ai_score", 0)
        
        # AI 분석 정보
        outlook = theme.get("outlook", theme.get("ai_outlook", ""))
        reason = theme.get("reason", theme.get("ai_reason", ""))
        
        lines.append("")
        lines.append(f"🎯 {i}. {name} (점수: {score:.1f}/100, 등급: {grade})")
        lines.append("─" * 55)
        lines.append(
            f"   모멘텀: {momentum:.1f}/30 | 수급: {supply:.1f}/25 | "
            f"뉴스: {news:.1f}/20 | AI: {ai:.1f}/25"
        )
        
        if outlook:
            lines.append(f"   전망: {outlook}")
        if reason:
            # 긴 이유는 줄여서
            reason_short = reason[:80] + "..." if len(reason) > 80 else reason
            lines.append(f"   근거: {reason_short}")
    
    lines.append("")
    lines.append("━" * 55)
    
    return "\n".join(lines)


# ===== 일일 테마 분석 파이프라인 =====

async def run_daily_theme_analysis(
    top_count: int = 5,
    include_ai_analysis: bool = True,
    save_to_db: bool = True
) -> list[dict]:
    """
    일일 테마 분석 파이프라인 실행
    
    전체 테마 분석 프로세스를 실행합니다:
    1. 테마 데이터 크롤링 (네이버, 한경)
    2. 뉴스 언급 횟수 수집
    3. AI 감성 분석 (선택)
    4. 점수 계산 및 순위화
    5. 상위 테마 선정
    6. DB 저장 (선택)
    
    Args:
        top_count: 선정할 상위 테마 수
        include_ai_analysis: AI 분석 포함 여부
        save_to_db: DB 저장 여부
    
    Returns:
        선정된 테마 리스트
        
    Example:
        >>> themes = await run_daily_theme_analysis(top_count=5)
        >>> print(themes[0]['name'])
        '2차전지'
    """
    logger.info("=" * 60)
    logger.info("🚀 일일 테마 분석 시작")
    logger.info("=" * 60)
    
    start_time = datetime.now()
    
    try:
        # 1. 테마 데이터 크롤링
        logger.info("📊 Step 1: 테마 데이터 수집")
        from .crawlers import crawl_all_themes, crawl_multiple_theme_news
        
        all_themes = crawl_all_themes()
        logger.info(f"   수집된 테마: {len(all_themes)}개")
        
        if not all_themes:
            logger.error("테마 데이터 수집 실패")
            return []
        
        # 2. 뉴스 언급 횟수 수집
        logger.info("📰 Step 2: 뉴스 데이터 수집")
        theme_names = [t["name"] for t in all_themes[:30]]  # 상위 30개만
        news_counts = crawl_multiple_theme_news(theme_names, days=3)
        
        # 테마에 뉴스 카운트 추가
        for theme in all_themes:
            theme["news_count"] = news_counts.get(theme["name"], 0)
        
        # 3. AI 감성 분석 (선택)
        if include_ai_analysis:
            logger.info("🤖 Step 3: AI 감성 분석")
            from .ai_analyzer import analyze_themes_batch
            from .crawlers import crawl_theme_news_count
            
            # 상위 20개 테마에 대해서만 AI 분석 (비용 절감)
            # 간단한 뉴스 텍스트 생성 (실제로는 뉴스 본문 크롤링 필요)
            themes_for_ai = []
            for theme in all_themes[:20]:
                themes_for_ai.append({
                    "name": theme["name"],
                    "news": f"{theme['name']} 관련 최근 뉴스입니다. (뉴스 수: {theme.get('news_count', 0)}건)"
                })
            
            try:
                ai_results = await analyze_themes_batch(themes_for_ai, concurrent_limit=5)
                
                # AI 결과를 테마에 병합
                ai_dict = {r["theme_name"]: r for r in ai_results if "theme_name" in r}
                for theme in all_themes:
                    if theme["name"] in ai_dict:
                        ai_data = ai_dict[theme["name"]]
                        theme["ai_sentiment"] = ai_data.get("score", 5.0)
                        theme["ai_outlook"] = ai_data.get("outlook", "중립")
                        theme["ai_reason"] = ai_data.get("reason", "")
                        
            except Exception as e:
                logger.warning(f"AI 분석 실패, 기본값 사용: {e}")
                for theme in all_themes:
                    theme["ai_sentiment"] = 5.0
        else:
            logger.info("⏭️  Step 3: AI 분석 스킵")
            for theme in all_themes:
                theme["ai_sentiment"] = 5.0
        
        # 4. 점수 계산
        logger.info("🔢 Step 4: 점수 계산")
        from .scorer import score_themes
        
        scored_themes = score_themes(all_themes)
        
        # 5. 상위 테마 선정
        logger.info(f"🎯 Step 5: 상위 {top_count}개 테마 선정")
        top_themes = select_top_themes(scored_themes, count=top_count)
        
        # 6. DB 저장
        if save_to_db and top_themes:
            logger.info("💾 Step 6: DB 저장")
            try:
                from database import get_database
                db = get_database()
                
                # 테마 점수 저장
                themes_to_save = [
                    {
                        "theme": t["name"],
                        "score": t.get("total_score", 0),
                        "momentum": t.get("momentum_score", 0),
                        "supply_ratio": t.get("supply_score", 0),
                        "news_count": t.get("news_count", 0),
                        "ai_sentiment": t.get("ai_sentiment", 0)
                    }
                    for t in top_themes
                ]
                
                db.save_theme_scores(themes_to_save, date.today())
                db.close()
                
            except Exception as e:
                logger.error(f"DB 저장 실패: {e}")
        
        # 완료 로그
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"✅ 일일 테마 분석 완료 ({elapsed:.1f}초)")
        logger.info("=" * 60)
        
        # 결과 리포트 출력
        report = format_theme_report(top_themes)
        print(report)
        
        return top_themes
        
    except Exception as e:
        logger.error(f"일일 테마 분석 실패: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return []


def run_daily_theme_analysis_sync(
    top_count: int = 5,
    include_ai_analysis: bool = True,
    save_to_db: bool = True
) -> list[dict]:
    """
    일일 테마 분석 파이프라인 (동기 래퍼)
    
    asyncio를 직접 다루지 않고 동기 함수처럼 호출할 수 있습니다.
    
    Args:
        top_count: 선정할 상위 테마 수
        include_ai_analysis: AI 분석 포함 여부
        save_to_db: DB 저장 여부
    
    Returns:
        선정된 테마 리스트
    """
    return asyncio.run(
        run_daily_theme_analysis(top_count, include_ai_analysis, save_to_db)
    )


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🎯 테마 선정 모듈 테스트")
    print("=" * 60)
    
    # 테스트 데이터
    test_themes = [
        {
            "name": "2차전지",
            "category": "신성장",
            "total_score": 87.5,
            "momentum_score": 25.0,
            "supply_score": 20.0,
            "news_score": 18.0,
            "ai_score": 24.5,
            "grade": "S",
            "outlook": "상승",
            "reason": "글로벌 전기차 수요 증가"
        },
        {
            "name": "AI반도체",
            "category": "반도체",
            "total_score": 82.3,
            "momentum_score": 22.0,
            "supply_score": 18.5,
            "news_score": 19.0,
            "ai_score": 22.8,
            "grade": "A",
            "outlook": "상승",
            "reason": "AI 서버 수요 폭발"
        },
        {
            "name": "K-방산",
            "category": "방위산업",
            "total_score": 79.1,
            "momentum_score": 20.0,
            "supply_score": 19.0,
            "news_score": 17.0,
            "ai_score": 23.1,
            "grade": "A",
            "outlook": "상승",
            "reason": "폴란드 수출 계약 체결"
        },
        {
            "name": "HBM",
            "category": "반도체",
            "total_score": 78.5,
            "momentum_score": 21.0,
            "supply_score": 17.0,
            "news_score": 18.5,
            "ai_score": 22.0,
            "grade": "A",
            "outlook": "상승",
            "reason": "엔비디아 공급 확대"
        },
        {
            "name": "로봇",
            "category": "신성장",
            "total_score": 75.0,
            "momentum_score": 18.0,
            "supply_score": 16.0,
            "news_score": 19.0,
            "ai_score": 22.0,
            "grade": "A",
            "outlook": "상승",
            "reason": "휴머노이드 로봇 상용화"
        },
        {
            "name": "원자력",
            "category": "에너지",
            "total_score": 72.0,
            "momentum_score": 17.0,
            "supply_score": 15.0,
            "news_score": 18.0,
            "ai_score": 22.0,
            "grade": "A",
            "outlook": "상승",
            "reason": "SMR 정책 지원"
        },
        {
            "name": "작전주테마",  # 블랙리스트 테스트
            "category": "기타",
            "total_score": 95.0,
            "grade": "S"
        },
        {
            "name": "저점수테마",  # 최소 점수 테스트
            "category": "기타",
            "total_score": 30.0,
            "grade": "D"
        }
    ]
    
    print("\n1️⃣ 전체 테마 (점수 순):")
    for t in sorted(test_themes, key=lambda x: x.get("total_score", 0), reverse=True):
        print(f"   - {t['name']}: {t.get('total_score', 0)}점 ({t.get('grade', '?')})")
    
    print("\n2️⃣ 상위 5개 테마 선정 (다양성 적용):")
    top_5 = select_top_themes(test_themes, count=5, ensure_diversity=True)
    for i, t in enumerate(top_5, 1):
        print(f"   {i}. {t['name']}: {t.get('total_score', 0)}점")
    
    print("\n3️⃣ 리포트 포맷:")
    report = format_theme_report(top_5[:3])
    print(report)
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
