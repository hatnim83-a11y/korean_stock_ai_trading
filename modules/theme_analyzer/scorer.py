"""
scorer.py - 테마 점수 계산 모듈

이 파일은 수집된 테마 데이터를 바탕으로 투자 매력도 점수를 계산합니다.

점수 계산 로직 (0-100점):
- 모멘텀 점수 (30점): 테마 내 평균 5일 수익률
- 수급 점수 (25점): 외국인+기관 순매수 종목 비율
- 뉴스 화제성 (20점): 최근 3일 뉴스 언급 빈도
- AI 감성 분석 (25점): Claude가 평가한 테마 전망 (0-10점 × 2.5)

사용법:
    from modules.theme_analyzer.scorer import (
        calculate_momentum_score,
        calculate_news_score,
        calculate_theme_total_score
    )
    
    momentum = calculate_momentum_score(avg_return=5.2)
    news = calculate_news_score(news_count=127)
"""

from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


# ===== 점수 배점 상수 =====
MAX_MOMENTUM_SCORE = 30.0    # 모멘텀 최대 30점
MAX_SUPPLY_SCORE = 25.0      # 수급 최대 25점
MAX_NEWS_SCORE = 20.0        # 뉴스 화제성 최대 20점
MAX_AI_SCORE = 25.0          # AI 감성 최대 25점

TOTAL_MAX_SCORE = 100.0


# ===== 모멘텀 점수 계산 =====

def calculate_momentum_score(
    avg_return_5d: float,
    avg_return_20d: Optional[float] = None,
    weight_5d: float = 0.7,
    weight_20d: float = 0.3
) -> float:
    """
    모멘텀 점수 계산 (최대 30점)
    
    테마의 평균 수익률을 바탕으로 모멘텀 점수를 계산합니다.
    
    계산 로직:
    - 5일 수익률 10% 이상: 30점 (만점)
    - 5일 수익률 0%: 15점 (중간)
    - 5일 수익률 -10% 이하: 0점 (최저)
    
    Args:
        avg_return_5d: 5일 평균 수익률 (%, 예: 5.2)
        avg_return_20d: 20일 평균 수익률 (%, 선택)
        weight_5d: 5일 수익률 가중치 (기본 70%)
        weight_20d: 20일 수익률 가중치 (기본 30%)
    
    Returns:
        모멘텀 점수 (0 ~ 30)
        
    Example:
        >>> calculate_momentum_score(5.2)
        22.8  # (5.2 + 10) / 20 * 30 = 22.8
        
        >>> calculate_momentum_score(-3.0)
        10.5  # (-3.0 + 10) / 20 * 30 = 10.5
    """
    # 5일 수익률 점수 (-10% ~ +10% 범위를 0 ~ 30점으로 매핑)
    # 선형 변환: score = (return + 10) / 20 * 30
    # -10% → 0점, 0% → 15점, +10% → 30점
    
    # 범위 제한 (-15% ~ +15%)
    clamped_5d = max(-15.0, min(15.0, avg_return_5d))
    
    # 점수 계산 (선형 매핑)
    # -15 → 0, 0 → 15, +15 → 30
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
    
    logger.debug(f"모멘텀 점수: {final_score:.1f}/30 (5일 수익률: {avg_return_5d:+.2f}%)")
    
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
    AI 감성 분석 점수 계산 (최대 25점)
    
    Claude AI가 분석한 감성 점수(0-10)를 25점 만점으로 변환
    
    Args:
        ai_sentiment: AI 감성 점수 (0 ~ 10)
    
    Returns:
        AI 점수 (0 ~ 25)
        
    Example:
        >>> calculate_ai_sentiment_score(8.5)
        21.25  # 8.5 / 10 * 25 = 21.25
    """
    # 범위 제한 (0 ~ 10)
    clamped = max(0.0, min(10.0, ai_sentiment))
    
    # 점수 변환 (0~10 → 0~25)
    score = (clamped / 10.0) * MAX_AI_SCORE
    
    logger.debug(f"AI 점수: {score:.1f}/25 (감성: {ai_sentiment:.1f}/10)")
    
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
        momentum_score: 이미 계산된 모멘텀 점수 (0~30)
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
    
    # 등급 산정
    if total >= 85:
        grade = "S"  # 최상위
    elif total >= 70:
        grade = "A"
    elif total >= 55:
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


def score_themes(themes: list[dict]) -> list[dict]:
    """
    여러 테마에 대해 점수 일괄 계산
    
    Args:
        themes: 테마 정보 리스트
            [
                {
                    'name': '2차전지',
                    'avg_change_rate': 5.2,  # 또는 'avg_return_5d'
                    'foreign_buy_ratio': 70.0,
                    'institution_buy_ratio': 50.0,
                    'news_count': 127,
                    'ai_sentiment': 8.5
                },
                ...
            ]
    
    Returns:
        점수가 추가된 테마 리스트 (점수 내림차순 정렬)
        
    Example:
        >>> scored = score_themes(themes)
        >>> print(scored[0]['total_score'])
        87.5
    """
    scored_themes = []
    
    # 핵심 대형 테마 정의 (보너스 점수 +10)
    MAJOR_THEMES = {
        "반도체": 10, "2차전지": 10, "AI": 10, "인공지능": 10,
        "배터리": 8, "자율주행": 8, "로봇": 8, "바이오": 8,
        "방산": 7, "원자력": 7, "조선": 7, "건설": 6,
        "플랫폼": 6, "클라우드": 6, "게임": 5, "엔터": 5
    }

    for theme in themes:
        theme_name = theme.get("name", theme.get("theme", ""))

        # 필드명 호환성 처리
        avg_return = theme.get("avg_return_5d") or theme.get("avg_change_rate", 0)

        # 1. 모멘텀 점수 (30점)
        m_score = calculate_momentum_score(avg_return) if avg_return else 0

        # 2. 수급 점수 (25점) - 실제 데이터 사용
        foreign_ratio = theme.get("foreign_buy_ratio", 0)
        inst_ratio = theme.get("institution_buy_ratio", 0)
        foreign_amt = theme.get("foreign_net_buy", 0)
        inst_amt = theme.get("institution_net_buy", 0)

        if foreign_ratio or inst_ratio:
            s_score = calculate_supply_score(foreign_ratio, inst_ratio)
        elif foreign_amt or inst_amt:
            s_score = calculate_supply_score_from_amount(foreign_amt, inst_amt)
        else:
            # 수급 데이터 없으면 종목수 기반 기본 점수 (대형 테마 우대)
            stock_count = theme.get("stock_count", len(theme.get("stocks", [])))
            s_score = min(15, stock_count * 0.8) if stock_count >= 10 else 5

        # 3. 뉴스 점수 (20점)
        news_count = theme.get("news_count", 0)
        n_score = calculate_news_score(news_count) if news_count else 5  # 기본 5점

        # 4. AI 감성 점수 (25점)
        ai_sentiment = theme.get("ai_sentiment", 0)
        a_score = calculate_ai_sentiment_score(ai_sentiment) if ai_sentiment else 10  # 기본 10점

        # 5. 대형 테마 보너스 점수
        bonus = 0
        bonus_reason = ""
        for major_name, major_bonus in MAJOR_THEMES.items():
            if major_name in theme_name:
                bonus = major_bonus
                bonus_reason = f"핵심테마({major_name})"
                break

        total = m_score + s_score + n_score + a_score + bonus

        # 등급 산정 (보너스 포함 기준 상향)
        if total >= 50:
            grade = "A"
        elif total >= 40:
            grade = "B"
        elif total >= 30:
            grade = "C"
        else:
            grade = "D"

        # 선정 이유 생성
        reasons = []
        if m_score >= 20:
            reasons.append(f"강한모멘텀({avg_return:+.1f}%)")
        elif m_score >= 15:
            reasons.append(f"양호한모멘텀({avg_return:+.1f}%)")
        if s_score >= 15:
            reasons.append("외국인/기관순매수")
        if n_score >= 15:
            reasons.append(f"높은화제성({news_count}건)")
        if bonus_reason:
            reasons.append(bonus_reason)

        selection_reason = ", ".join(reasons) if reasons else "기본조건충족"

        # 원본 테마 정보에 점수 추가
        scored_theme = {
            **theme,
            "theme": theme_name,
            "total_score": round(total, 2),
            "score": round(total, 2),
            "momentum": round(m_score, 2),
            "momentum_score": round(m_score, 2),
            "supply_score": round(s_score, 2),
            "news_score": round(n_score, 2),
            "ai_score": round(a_score, 2),
            "bonus_score": bonus,
            "grade": grade,
            "selection_reason": selection_reason
        }
        scored_themes.append(scored_theme)
    
    # 총점 기준 내림차순 정렬
    scored_themes.sort(key=lambda x: x["total_score"], reverse=True)
    
    logger.info(f"📊 {len(scored_themes)}개 테마 점수 계산 완료")
    
    return scored_themes


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
