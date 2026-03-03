"""
scorer.py - 테마 점수 계산 모듈

이 파일은 수집된 테마 데이터를 바탕으로 투자 매력도 점수를 계산합니다.

점수 계산 로직 (0-100점):
- 모멘텀 점수 (40점): 테마 내 평균 5일 수익률 (KIS API 우선, 크롤링 폴백)
- 뉴스 화제성 (20점): 최근 3일 뉴스 언급 횟수
- AI 감성 (15점): Claude AI 테마 전망 분석
- 종목수 보너스 (10점): 테마 규모 반영
- 기본 점수 (15점): 고정

사용법:
    from modules.theme_analyzer.scorer import (
        calculate_momentum_score,
        calculate_theme_total_score
    )

    momentum = calculate_momentum_score(avg_return_5d=5.2)
"""

from datetime import time
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import now_kst
from logger import logger


# ===== 점수 배점 상수 =====
MAX_MOMENTUM_SCORE = 40.0    # 모멘텀 최대 40점
MAX_NEWS_SCORE = 20.0        # 뉴스 화제성 최대 20점
MAX_AI_SCORE = 15.0          # AI 감성 최대 15점
MAX_SIZE_BONUS = 10.0        # 종목수 보너스 최대 10점
BASE_SCORE = 15.0            # 기본 점수 15점

# 하위 호환용 (기존 함수 참조)
MAX_SUPPLY_SCORE = 25.0

TOTAL_MAX_SCORE = 100.0


# ===== 모멘텀 점수 계산 =====

def calculate_momentum_score(
    avg_return_5d: float,
    avg_return_20d: Optional[float] = None,
    weight_5d: float = 0.7,
    weight_20d: float = 0.3
) -> float:
    """
    모멘텀 점수 계산 (최대 40점)

    테마의 평균 수익률을 바탕으로 모멘텀 점수를 계산합니다.
    선형 매핑 공식 사용.

    계산 로직:
    - 5일 수익률 +15% 이상: 40점 (만점)
    - 5일 수익률 0%: 20점 (중간)
    - 5일 수익률 -15% 이하: 0점 (최저)

    Args:
        avg_return_5d: 5일 평균 수익률 (%, 예: 5.2)
        avg_return_20d: 20일 평균 수익률 (%, 선택)
        weight_5d: 5일 수익률 가중치 (기본 70%)
        weight_20d: 20일 수익률 가중치 (기본 30%)

    Returns:
        모멘텀 점수 (0 ~ 40)
    """
    # 범위 제한 (-15% ~ +15%)
    clamped_5d = max(-15.0, min(15.0, avg_return_5d))

    # 점수 계산 (선형 매핑)
    # -15 → 0, 0 → 30, +15 → 60
    score_5d = ((clamped_5d + 15) / 30) * MAX_MOMENTUM_SCORE

    # 20일 수익률이 있으면 가중 평균
    if avg_return_20d is not None:
        clamped_20d = max(-15.0, min(15.0, avg_return_20d))
        score_20d = ((clamped_20d + 15) / 30) * MAX_MOMENTUM_SCORE

        final_score = (score_5d * weight_5d) + (score_20d * weight_20d)
    else:
        final_score = score_5d

    # 범위 보정
    final_score = max(0.0, min(MAX_MOMENTUM_SCORE, final_score))

    logger.debug(f"모멘텀 점수: {final_score:.1f}/{MAX_MOMENTUM_SCORE:.0f} (5일 수익률: {avg_return_5d:+.2f}%)")

    return round(final_score, 2)


# ===== 수급 점수 계산 =====

def calculate_supply_score(
    foreign_buy_ratio: float,
    institution_buy_ratio: float,
    foreign_weight: float = 0.6,
    institution_weight: float = 0.4
) -> float:
    """
    수급 점수 계산 (최대 25점)
    
    테마 내 종목 중 외국인/기관이 순매수하는 종목 비율로 점수 계산
    
    Args:
        foreign_buy_ratio: 외국인 순매수 종목 비율 (0~100%)
        institution_buy_ratio: 기관 순매수 종목 비율 (0~100%)
        foreign_weight: 외국인 가중치 (기본 60%)
        institution_weight: 기관 가중치 (기본 40%)
    
    Returns:
        수급 점수 (0 ~ 25)
        
    Example:
        >>> calculate_supply_score(70.0, 50.0)
        15.5  # (70*0.6 + 50*0.4) / 100 * 25 = 15.5
    """
    # 비율 범위 제한 (0 ~ 100)
    foreign = max(0.0, min(100.0, foreign_buy_ratio))
    institution = max(0.0, min(100.0, institution_buy_ratio))
    
    # 가중 평균
    weighted_ratio = (foreign * foreign_weight) + (institution * institution_weight)
    
    # 점수 변환 (0~100% → 0~25점)
    score = (weighted_ratio / 100) * MAX_SUPPLY_SCORE
    
    logger.debug(
        f"수급 점수: {score:.1f}/25 "
        f"(외국인: {foreign:.0f}%, 기관: {institution:.0f}%)"
    )
    
    return round(score, 2)


def calculate_supply_score_from_amount(
    foreign_net_buy: float,
    institution_net_buy: float,
    threshold_billion: float = 50.0
) -> float:
    """
    순매수 금액 기반 수급 점수 계산 (최대 25점)
    
    Args:
        foreign_net_buy: 외국인 순매수 금액 (억원)
        institution_net_buy: 기관 순매수 금액 (억원)
        threshold_billion: 최대 점수 기준 금액 (기본 50억원)
    
    Returns:
        수급 점수 (0 ~ 25)
        
    Example:
        >>> calculate_supply_score_from_amount(30, 20)
        25.0  # (30 + 20) >= 50 → 만점
        
        >>> calculate_supply_score_from_amount(10, 5)
        7.5  # (10 + 5) / 50 * 25 = 7.5
    """
    # 총 순매수 금액 (음수면 0으로)
    total_net_buy = max(0.0, foreign_net_buy + institution_net_buy)
    
    # 점수 계산 (50억 이상이면 만점)
    ratio = min(1.0, total_net_buy / threshold_billion)
    score = ratio * MAX_SUPPLY_SCORE
    
    logger.debug(
        f"수급 점수: {score:.1f}/25 "
        f"(외국인: {foreign_net_buy:+.0f}억, 기관: {institution_net_buy:+.0f}억)"
    )
    
    return round(score, 2)


# ===== 뉴스 화제성 점수 계산 =====

def calculate_news_score(
    news_count: int,
    threshold_high: int = 100,
    threshold_mid: int = 50
) -> float:
    """
    뉴스 화제성 점수 계산 (최대 20점)
    
    최근 3일 뉴스 언급 횟수를 바탕으로 화제성 점수 계산
    
    계산 로직:
    - 100건 이상: 20점 (만점)
    - 50건: 10점 (중간)
    - 10건 이하: 2점 (최저)
    
    Args:
        news_count: 뉴스 언급 횟수
        threshold_high: 만점 기준 (기본 100건)
        threshold_mid: 중간 기준 (기본 50건)
    
    Returns:
        뉴스 점수 (0 ~ 20)
        
    Example:
        >>> calculate_news_score(127)
        20.0  # 100건 이상 → 만점
        
        >>> calculate_news_score(50)
        10.0  # 중간
    """
    if news_count <= 0:
        return 0.0
    
    if news_count >= threshold_high:
        score = MAX_NEWS_SCORE
    elif news_count >= threshold_mid:
        # 50~100건: 10~20점 (선형)
        ratio = (news_count - threshold_mid) / (threshold_high - threshold_mid)
        score = 10.0 + (ratio * 10.0)
    else:
        # 0~50건: 0~10점 (선형)
        ratio = news_count / threshold_mid
        score = ratio * 10.0
    
    # 최소 점수 보장 (뉴스가 있으면 최소 1점)
    if news_count > 0:
        score = max(1.0, score)
    
    logger.debug(f"뉴스 점수: {score:.1f}/20 (뉴스 {news_count}건)")
    
    return round(score, 2)


# ===== AI 감성 점수 계산 =====

def calculate_ai_sentiment_score(ai_sentiment: float) -> float:
    """
    AI 감성 분석 점수 계산 (최대 15점)

    Claude AI가 분석한 감성 점수(0-10)를 15점 만점으로 변환

    Args:
        ai_sentiment: AI 감성 점수 (0 ~ 10)

    Returns:
        AI 점수 (0 ~ 15)

    Example:
        >>> calculate_ai_sentiment_score(8.5)
        12.75  # 8.5 / 10 * 15 = 12.75
    """
    # 범위 제한 (0 ~ 10)
    clamped = max(0.0, min(10.0, ai_sentiment))

    # 점수 변환 (0~10 → 0~15)
    score = (clamped / 10.0) * MAX_AI_SCORE

    logger.debug(f"AI 점수: {score:.1f}/15 (감성: {ai_sentiment:.1f}/10)")

    return round(score, 2)


# ===== 총점 계산 =====

def calculate_theme_total_score(
    momentum_score: Optional[float] = None,
    supply_score: Optional[float] = None,
    news_score: Optional[float] = None,
    ai_score: Optional[float] = None,
    # 또는 원본 데이터로 계산
    avg_return_5d: Optional[float] = None,
    foreign_buy_ratio: Optional[float] = None,
    institution_buy_ratio: Optional[float] = None,
    news_count: Optional[int] = None,
    ai_sentiment: Optional[float] = None
) -> dict:
    """
    테마 종합 점수 계산 (최대 100점)
    
    4가지 요소의 점수를 합산하여 총점 계산
    
    Args:
        momentum_score: 이미 계산된 모멘텀 점수 (0~60)
        supply_score: 이미 계산된 수급 점수 (0~25)
        news_score: 이미 계산된 뉴스 점수 (0~20)
        ai_score: 이미 계산된 AI 점수 (0~25)
        
        또는 원본 데이터:
        avg_return_5d: 5일 평균 수익률 (%)
        foreign_buy_ratio: 외국인 순매수 비율 (%)
        institution_buy_ratio: 기관 순매수 비율 (%)
        news_count: 뉴스 언급 횟수
        ai_sentiment: AI 감성 점수 (0~10)
    
    Returns:
        점수 상세 정보 딕셔너리:
        {
            'total_score': 87.5,
            'momentum_score': 22.8,
            'supply_score': 17.5,
            'news_score': 20.0,
            'ai_score': 21.25,
            'grade': 'A'  # S, A, B, C, D
        }
        
    Example:
        >>> result = calculate_theme_total_score(
        >>>     avg_return_5d=5.2,
        >>>     foreign_buy_ratio=70.0,
        >>>     institution_buy_ratio=50.0,
        >>>     news_count=127,
        >>>     ai_sentiment=8.5
        >>> )
        >>> print(result['total_score'])
        81.55
    """
    # 점수 계산 (주어진 값 사용 또는 원본 데이터로 계산)
    
    # 모멘텀 점수
    if momentum_score is not None:
        m_score = max(0, min(MAX_MOMENTUM_SCORE, momentum_score))
    elif avg_return_5d is not None:
        m_score = calculate_momentum_score(avg_return_5d)
    else:
        m_score = 0.0
    
    # 수급 점수
    if supply_score is not None:
        s_score = max(0, min(MAX_SUPPLY_SCORE, supply_score))
    elif foreign_buy_ratio is not None and institution_buy_ratio is not None:
        s_score = calculate_supply_score(foreign_buy_ratio, institution_buy_ratio)
    else:
        s_score = 0.0
    
    # 뉴스 점수
    if news_score is not None:
        n_score = max(0, min(MAX_NEWS_SCORE, news_score))
    elif news_count is not None:
        n_score = calculate_news_score(news_count)
    else:
        n_score = 0.0
    
    # AI 점수
    if ai_score is not None:
        a_score = max(0, min(MAX_AI_SCORE, ai_score))
    elif ai_sentiment is not None:
        a_score = calculate_ai_sentiment_score(ai_sentiment)
    else:
        a_score = 0.0
    
    # 총점 계산
    total = m_score + s_score + n_score + a_score
    
    # 등급 산정 (score_themes와 동일 기준)
    if total >= 80:
        grade = "S"  # 최상위
    elif total >= 65:
        grade = "A"
    elif total >= 50:
        grade = "B"
    elif total >= 40:
        grade = "C"
    else:
        grade = "D"
    
    result = {
        "total_score": round(total, 2),
        "momentum_score": round(m_score, 2),
        "supply_score": round(s_score, 2),
        "news_score": round(n_score, 2),
        "ai_score": round(a_score, 2),
        "grade": grade
    }
    
    logger.debug(
        f"총점: {total:.1f}/100 ({grade}) - "
        f"모멘텀:{m_score:.1f}, 수급:{s_score:.1f}, "
        f"뉴스:{n_score:.1f}, AI:{a_score:.1f}"
    )
    
    return result


def _get_kis_api():
    """KIS API 인스턴스 반환 (실패 시 None)"""
    try:
        from modules.stock_screener.kis_api import KISApi
        return KISApi()
    except Exception:
        return None


def _calculate_theme_momentum(theme: dict, kis) -> float:
    """
    KIS API로 테마 종목 5일 수익률 계산, 실패 시 크롤링 데이터 폴백

    Args:
        theme: 테마 정보 (stocks 리스트 포함)
        kis: KISApi 인스턴스 또는 None

    Returns:
        평균 5일 수익률 (%)
    """
    # 1차: KIS API (장중에만 가능)
    if kis and theme.get("stocks"):
        returns = []
        for code in theme["stocks"][:5]:  # 최대 5종목
            try:
                daily = kis.get_daily_price(code, count=10)
                if daily and len(daily) >= 6:
                    close_today = daily[0]["close"]
                    close_5d_ago = daily[5]["close"]
                    if close_5d_ago > 0:
                        ret_5d = (close_today - close_5d_ago) / close_5d_ago * 100
                        returns.append(ret_5d)
            except Exception as e:
                logger.debug(f"KIS API 종목 {code} 조회 실패: {e}")
                continue
        if returns:
            avg = sum(returns) / len(returns)
            logger.debug(f"KIS API 모멘텀: {avg:+.2f}% ({len(returns)}종목)")
            return avg

    # 2차: 크롤링 데이터 폴백
    avg_change = theme.get("avg_change_rate")
    three_day = theme.get("three_day_rate")

    # 장중(09:00~15:30)이면 당일 등락률 사용, 장전이면 3일 등락률 우선
    current_time = now_kst().time()
    is_market_open = time(9, 0) <= current_time <= time(15, 30)

    if is_market_open and avg_change is not None:
        return avg_change
    # 장전: 3일 등락률 우선 → 당일 등락률 폴백
    if three_day is not None and three_day != 0.0:
        return three_day
    if avg_change is not None:
        return avg_change
    return 0


def score_themes(themes: list[dict], include_news: bool = False, include_ai: bool = False) -> list[dict]:
    """
    여러 테마에 대해 점수 일괄 계산

    배점: 모멘텀(40) + 뉴스(20) + AI감성(15) + 종목수 보너스(10) + 기본점수(15) = 100
    뉴스/AI는 include_news/include_ai 플래그로 활성화 (17:00 일별 수집 시).

    Args:
        themes: 테마 정보 리스트
        include_news: 뉴스 점수 수집 및 반영 여부
        include_ai: AI 감성 점수 수집 및 반영 여부

    Returns:
        점수가 추가된 테마 리스트 (점수 내림차순 정렬)
    """
    scored_themes = []

    # KIS API 인스턴스 (테마 종목 가격 조회용)
    kis = _get_kis_api()

    # 뉴스 건수 수집 (상위 테마 대상)
    news_counts = {}
    if include_news:
        news_counts = _collect_news_counts(themes)

    for theme in themes:
        theme_name = theme.get("name", theme.get("theme", ""))

        # 1. 모멘텀: KIS API 5일 수익률 (우선) → 크롤링 폴백
        avg_return = _calculate_theme_momentum(theme, kis)

        # 모멘텀 점수: ((avg_return + 15) / 30) * 40
        clamped = max(-15.0, min(15.0, avg_return))
        momentum_score = ((clamped + 15) / 30) * MAX_MOMENTUM_SCORE

        # 2. 뉴스 점수 (최대 20점)
        news_count = news_counts.get(theme_name, theme.get("news_count", 0) or 0)
        news_score = calculate_news_score(news_count) if news_count > 0 else 0.0

        # 3. AI 감성 점수 (최대 15점) — 이미 theme에 있으면 재사용
        ai_sentiment = theme.get("ai_sentiment", 0) or 0
        ai_score = calculate_ai_sentiment_score(ai_sentiment) if ai_sentiment > 0 else 0.0

        # 4. 종목수 보너스 (최대 10점)
        stock_count = theme.get("stock_count", len(theme.get("stocks", [])))
        size_bonus = min(MAX_SIZE_BONUS, stock_count * 2)

        # 5. 기본점수 15점
        total = momentum_score + news_score + ai_score + size_bonus + BASE_SCORE

        # 등급 산정
        if total >= 80:
            grade = "S"
        elif total >= 65:
            grade = "A"
        elif total >= 50:
            grade = "B"
        elif total >= 40:
            grade = "C"
        else:
            grade = "D"

        # 선정 이유 생성
        reasons = []
        if momentum_score >= 27:
            reasons.append(f"강한모멘텀({avg_return:+.1f}%)")
        elif momentum_score >= 20:
            reasons.append(f"양호한모멘텀({avg_return:+.1f}%)")
        elif avg_return != 0:
            reasons.append(f"모멘텀({avg_return:+.1f}%)")
        if news_score >= 10:
            reasons.append(f"화제({news_count}건)")
        if ai_score >= 8:
            reasons.append(f"AI긍정({ai_sentiment:.0f})")
        if stock_count >= 5:
            reasons.append(f"{stock_count}종목")

        selection_reason = ", ".join(reasons) if reasons else "기본조건충족"

        # 원본 테마 정보에 점수 추가
        scored_theme = {
            **theme,
            "theme": theme_name,
            "avg_change_rate": round(avg_return, 2),
            "total_score": round(total, 2),
            "score": round(total, 2),
            "momentum": round(momentum_score, 2),
            "momentum_score": round(momentum_score, 2),
            "supply_score": 0,
            "news_score": round(news_score, 2),
            "news_count": news_count,
            "ai_score": round(ai_score, 2),
            "ai_sentiment": round(ai_sentiment, 2) if ai_sentiment else 0,
            "bonus_score": round(size_bonus, 2),
            "grade": grade,
            "selection_reason": selection_reason
        }
        scored_themes.append(scored_theme)

    # 총점 기준 내림차순 정렬
    scored_themes.sort(key=lambda x: x["total_score"], reverse=True)

    active = []
    if include_news:
        active.append("뉴스")
    if include_ai:
        active.append("AI")
    mode = f"모멘텀+{'+'.join(active)}" if active else "모멘텀"
    logger.info(f"📊 {len(scored_themes)}개 테마 점수 계산 완료 ({mode})")

    return scored_themes


def _collect_news_counts(themes: list[dict]) -> dict[str, int]:
    """상위 30개 테마의 뉴스 건수 일괄 수집"""
    try:
        from modules.theme_analyzer.crawlers import crawl_theme_news_count
    except ImportError:
        logger.warning("[scorer] crawl_theme_news_count 임포트 실패")
        return {}

    counts = {}
    for theme in themes[:30]:
        theme_name = theme.get("name", theme.get("theme", ""))
        if not theme_name:
            continue
        try:
            count = crawl_theme_news_count(theme_name, days=3)
            if count and count > 0:
                counts[theme_name] = count
        except Exception as e:
            logger.debug(f"[scorer] 뉴스 수집 실패 ({theme_name}): {e}")
    logger.info(f"📰 뉴스 건수 수집: {len(counts)}개 테마")
    return counts


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 테마 점수 계산 테스트")
    print("=" * 60)
    
    # 개별 점수 테스트
    print("\n1️⃣ 모멘텀 점수 테스트:")
    test_returns = [10.0, 5.0, 0.0, -3.0, -10.0]
    for ret in test_returns:
        score = calculate_momentum_score(ret)
        print(f"   수익률 {ret:+.1f}% → {score:.1f}점")
    
    print("\n2️⃣ 수급 점수 테스트:")
    score = calculate_supply_score(70.0, 50.0)
    print(f"   외국인 70%, 기관 50% → {score:.1f}점")
    
    score = calculate_supply_score_from_amount(30, 20)
    print(f"   외국인 +30억, 기관 +20억 → {score:.1f}점")
    
    print("\n3️⃣ 뉴스 점수 테스트:")
    test_counts = [0, 20, 50, 100, 200]
    for count in test_counts:
        score = calculate_news_score(count)
        print(f"   뉴스 {count}건 → {score:.1f}점")
    
    print("\n4️⃣ AI 점수 테스트:")
    test_sentiments = [10.0, 8.5, 5.0, 2.0, 0.0]
    for sent in test_sentiments:
        score = calculate_ai_sentiment_score(sent)
        print(f"   감성 {sent:.1f}/10 → {score:.1f}점")
    
    print("\n5️⃣ 종합 점수 테스트:")
    result = calculate_theme_total_score(
        avg_return_5d=5.2,
        foreign_buy_ratio=70.0,
        institution_buy_ratio=50.0,
        news_count=127,
        ai_sentiment=8.5
    )
    print(f"   총점: {result['total_score']}/100 ({result['grade']})")
    print(f"   - 모멘텀: {result['momentum_score']}/30")
    print(f"   - 수급: {result['supply_score']}/25")
    print(f"   - 뉴스: {result['news_score']}/20")
    print(f"   - AI: {result['ai_score']}/25")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
