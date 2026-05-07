"""
scorer.py - 테마 점수 계산 모듈

이 파일은 수집된 테마 데이터를 바탕으로 투자 매력도 점수를 계산합니다.

점수 계산 로직 (0~65점):
- 모멘텀 점수 (25점): 테마 내 평균 5일 수익률 (KIS API 우선, 크롤링 폴백)
- 과열 감점 (0~-15점): 5일 수익률 +8% 이상 시 감점 (급등 테마 고점매수 방지)
- 뉴스 화제성 (15점): 최근 3일 뉴스 언급 횟수
- AI 감성 (10점): Claude AI 테마 전망 분석
- 종목수 보너스 (5점): 테마 규모 반영
- 기본 점수 (10점): 고정

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

from config import now_kst, settings
from logger import logger


# ===== 점수 배점 상수 =====
MAX_MOMENTUM_SCORE = 25.0    # 모멘텀 최대 25점
MAX_NEWS_SCORE = 15.0        # 뉴스 화제성 최대 15점
MAX_AI_SCORE = 10.0          # AI 감성 최대 10점
MAX_SIZE_BONUS = 5.0         # 종목수 보너스 최대 5점
BASE_SCORE = 10.0            # 기본 점수 10점
MAX_OVERHEAT_PENALTY = -15.0 # 과열 감점 최대 -15점

# 하위 호환용 (기존 함수 참조)
MAX_SUPPLY_SCORE = 25.0

TOTAL_MAX_SCORE = 65.0  # 모멘텀(25) + 뉴스(15) + AI(10) + 종목수(5) + 기본(10)


# ===== 모멘텀 점수 계산 =====

def calculate_momentum_score(
    avg_return_5d: float,
    avg_return_20d: Optional[float] = None,
    weight_5d: float = 0.7,
    weight_20d: float = 0.3
) -> float:
    """
    모멘텀 점수 계산 (최대 25점)

    테마의 평균 수익률을 바탕으로 모멘텀 점수를 계산합니다.
    선형 매핑 공식 사용.

    계산 로직:
    - 5일 수익률 +10% 이상: 25점 (만점)
    - 5일 수익률 0%: 12.5점 (중간)
    - 5일 수익률 -10% 이하: 0점 (최저)

    Args:
        avg_return_5d: 5일 평균 수익률 (%, 예: 5.2)
        avg_return_20d: 20일 평균 수익률 (%, 선택)
        weight_5d: 5일 수익률 가중치 (기본 70%)
        weight_20d: 20일 수익률 가중치 (기본 30%)

    Returns:
        모멘텀 점수 (0 ~ 25)
    """
    # 범위 제한 (-10% ~ +10%)
    clamped_5d = max(-10.0, min(10.0, avg_return_5d))

    # 점수 계산 (선형 매핑)
    # -10 → 0, 0 → 12.5, +10 → 25
    score_5d = ((clamped_5d + 10) / 20) * MAX_MOMENTUM_SCORE

    # 20일 수익률이 있으면 가중 평균
    if avg_return_20d is not None:
        clamped_20d = max(-10.0, min(10.0, avg_return_20d))
        score_20d = ((clamped_20d + 10) / 20) * MAX_MOMENTUM_SCORE

        final_score = (score_5d * weight_5d) + (score_20d * weight_20d)
    else:
        final_score = score_5d

    # 범위 보정
    final_score = max(0.0, min(MAX_MOMENTUM_SCORE, final_score))

    logger.debug(f"모멘텀 점수: {final_score:.1f}/{MAX_MOMENTUM_SCORE:.0f} (5일 수익률: {avg_return_5d:+.2f}%)")

    return round(final_score, 2)


# ===== 과열 감점 계산 =====

def calculate_overheat_penalty(
    avg_return_5d: float,
    avg_return_3d: Optional[float] = None
) -> float:
    """
    과열 감점 계산 (0 ~ -15점)

    5일 수익률이 +8% 이상이면 감점 시작, +15%에서 최대 -15점.
    3일 수익률이 5일의 80% 이상(급가속)이면 추가 -3점.

    Args:
        avg_return_5d: 5일 평균 수익률 (%)
        avg_return_3d: 3일 평균 수익률 (%, 선택)

    Returns:
        과열 감점 (-15 ~ 0)
    """
    OVERHEAT_THRESHOLD = 8.0   # 감점 시작 기준
    OVERHEAT_MAX = 15.0        # 최대 감점 기준 수익률
    PENALTY_MAX = 15.0         # 최대 감점 점수
    ACCEL_BONUS_PENALTY = 3.0  # 급가속 추가 감점

    if avg_return_5d < OVERHEAT_THRESHOLD:
        return 0.0

    # 선형 감점: 8% → 0점, 15% → -15점
    ratio = min(1.0, (avg_return_5d - OVERHEAT_THRESHOLD) / (OVERHEAT_MAX - OVERHEAT_THRESHOLD))
    penalty = -ratio * PENALTY_MAX

    # 급가속 감점: 3일 수익률이 5일의 80% 이상이면 추가 -3점
    if avg_return_3d is not None and avg_return_5d > 0:
        accel_ratio = avg_return_3d / avg_return_5d
        if accel_ratio >= 0.8 - 1e-9:
            penalty -= ACCEL_BONUS_PENALTY

    # 최대 감점 제한
    penalty = max(MAX_OVERHEAT_PENALTY, penalty)

    logger.debug(f"과열 감점: {penalty:.1f}점 (5일: {avg_return_5d:+.1f}%, 3일: {avg_return_3d})")

    return round(penalty, 2)


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
    뉴스 화제성 점수 계산 (최대 15점)

    최근 3일 뉴스 언급 횟수를 바탕으로 화제성 점수 계산

    계산 로직:
    - 100건 이상: 15점 (만점)
    - 50건: 7.5점 (중간)
    - 10건 이하: 1.5점 (최저)

    Args:
        news_count: 뉴스 언급 횟수
        threshold_high: 만점 기준 (기본 100건)
        threshold_mid: 중간 기준 (기본 50건)

    Returns:
        뉴스 점수 (0 ~ 15)
        
    Example:
        >>> calculate_news_score(127)
        15.0  # 100건 이상 → 만점

        >>> calculate_news_score(50)
        7.5  # 중간
    """
    if news_count <= 0:
        return 0.0
    
    half_max = MAX_NEWS_SCORE / 2.0
    if news_count >= threshold_high:
        score = MAX_NEWS_SCORE
    elif news_count >= threshold_mid:
        # 50~100건: half~max점 (선형)
        ratio = (news_count - threshold_mid) / (threshold_high - threshold_mid)
        score = half_max + (ratio * half_max)
    else:
        # 0~50건: 0~half점 (선형)
        ratio = news_count / threshold_mid
        score = ratio * half_max

    # 최소 점수 보장 (뉴스가 있으면 최소 1점)
    if news_count > 0:
        score = max(1.0, score)

    logger.debug(f"뉴스 점수: {score:.1f}/{MAX_NEWS_SCORE:.0f} (뉴스 {news_count}건)")
    
    return round(score, 2)


# ===== AI 감성 점수 계산 =====

def calculate_ai_sentiment_score(ai_sentiment: float) -> float:
    """
    AI 감성 분석 점수 계산 (최대 10점)

    Claude AI가 분석한 감성 점수(0-10)를 10점 만점으로 변환

    Args:
        ai_sentiment: AI 감성 점수 (0 ~ 10)

    Returns:
        AI 점수 (0 ~ 10)

    Example:
        >>> calculate_ai_sentiment_score(8.5)
        8.5  # 8.5 / 10 * 10 = 8.5
    """
    # 범위 제한 (0 ~ 10)
    clamped = max(0.0, min(10.0, ai_sentiment))

    # 점수 변환 (0~10 → 0~10)
    score = (clamped / 10.0) * MAX_AI_SCORE

    logger.debug(f"AI 점수: {score:.1f}/{MAX_AI_SCORE:.0f} (감성: {ai_sentiment:.1f}/10)")

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
    테마 종합 점수 계산 (최대 65점)
    
    4가지 요소의 점수를 합산하여 총점 계산
    
    Args:
        momentum_score: 이미 계산된 모멘텀 점수 (0~25)
        supply_score: 이미 계산된 수급 점수 (0~25)
        news_score: 이미 계산된 뉴스 점수 (0~15)
        ai_score: 이미 계산된 AI 점수 (0~10)
        
        또는 원본 데이터:
        avg_return_5d: 5일 평균 수익률 (%)
        foreign_buy_ratio: 외국인 순매수 비율 (%)
        institution_buy_ratio: 기관 순매수 비율 (%)
        news_count: 뉴스 언급 횟수
        ai_sentiment: AI 감성 점수 (0~10)
    
    Returns:
        점수 상세 정보 딕셔너리:
        {
            'total_score': 58.0,
            'momentum_score': 19.0,
            'supply_score': 15.5,
            'news_score': 15.0,
            'ai_score': 8.5,
            'grade': 'S'  # S(>=58), A(>=48), B(>=38), C(>=30), D
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
        58.0
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
    
    # 등급 산정 (최대 65점 기준)
    if total >= 58:
        grade = "S"
    elif total >= 48:
        grade = "A"
    elif total >= 38:
        grade = "B"
    elif total >= 30:
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
        f"총점: {total:.1f}/65 ({grade}) - "
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
    return 0.0


def calculate_liquidity_penalty(
    theme_name: str,
    pass_rates: dict,
    min_pass_rate: float = 0.15,
    low_pass_rate: float = 0.25,
    penalty_max: float = 12.0,
    min_data_days: int = 3
) -> tuple[float, str]:
    """
    테마 통과율 기반 유동성 보정 점수 계산

    과거 screening_log 통과율이 낮은 테마에 감점을 적용합니다.

    Args:
        theme_name: 테마명
        pass_rates: {theme: {"pass_rate": float, "days_data": int, ...}}
        min_pass_rate: 최소 통과율 (이하면 최대 감점)
        low_pass_rate: 저유동성 기준 (이하면 비례 감점)
        penalty_max: 최대 감점 (절대값)
        min_data_days: 최소 데이터 일수 (미만이면 판단 보류)

    Returns:
        (penalty: float, note: str)
    """
    data = pass_rates.get(theme_name)

    # 히스토리 없음 또는 데이터 부족 → 비례 기본 감점
    if not data or data.get("days_data", 0) < min_data_days:
        default_penalty = -round(penalty_max * 0.25, 1)
        return default_penalty, "신규테마(데이터부족)"

    pass_rate = data.get("pass_rate", 0.0)

    # 통과율 충분 → 감점 없음
    if pass_rate >= low_pass_rate:
        return 0.0, ""

    # 통과율 < min_pass_rate → 최대 감점
    if pass_rate <= min_pass_rate:
        return -penalty_max, f"유동성탈락({pass_rate:.0%})"

    # 통과율 min~low 구간 → 비례 감점
    denom = low_pass_rate - min_pass_rate
    if denom <= 0:
        return -penalty_max, f"유동성주의({pass_rate:.0%})"

    ratio = (low_pass_rate - pass_rate) / denom
    penalty = -ratio * penalty_max
    return round(penalty, 1), f"유동성주의({pass_rate:.0%})"


def score_themes(themes: list[dict], include_news: bool = False, include_ai: bool = False,
                 pass_rates: dict = None) -> list[dict]:
    """
    여러 테마에 대해 점수 일괄 계산

    배점: 모멘텀(25) + 과열(0~-15) + 뉴스(15) + AI감성(10) + 종목수(5) + 기본(10) + 유동성(0~-12) = 최대 65점
    과열 감점: 5일 수익률 +8% 이상 시 감점 시작, 급등 테마 고점매수 방지.
    유동성 감점: screening_log 통과율이 낮은 테마에 감점 (소형주 테마 방지).
    뉴스/AI는 include_news/include_ai 플래그로 활성화 (17:00 일별 수집 시).

    Args:
        themes: 테마 정보 리스트
        include_news: 뉴스 점수 수집 및 반영 여부
        include_ai: AI 감성 점수 수집 및 반영 여부
        pass_rates: 테마별 스크리닝 통과율 (None이면 유동성 검증 스킵)

    Returns:
        점수가 추가된 테마 리스트 (점수 내림차순 정렬)
    """
    scored_themes = []

    # KIS API 인스턴스 (테마 종목 가격 조회용)
    kis = _get_kis_api()

    # 종목 매핑 (URL 있는 테마에 stocks 추가 → KIS API 모멘텀 활성화)
    if include_news:
        _enrich_theme_stocks(themes)

    # 뉴스 건수+텍스트 수집 (상위 테마 대상)
    news_data = {}  # {theme_name: {"count": int, "text": str}}
    if include_news:
        news_data = _collect_news_data(themes)

    for theme in themes:
        theme_name = theme.get("name", theme.get("theme", ""))

        # 1. 모멘텀: KIS API 5일 수익률 (우선) → 크롤링 폴백
        avg_return = _calculate_theme_momentum(theme, kis)

        # 모멘텀 점수: ((avg_return + 10) / 20) * 25
        clamped = max(-10.0, min(10.0, avg_return))
        momentum_score = ((clamped + 10) / 20) * MAX_MOMENTUM_SCORE

        # 2. 뉴스 점수 (최대 15점)
        theme_news = news_data.get(theme_name, {})
        news_count = theme_news.get("count", 0) if theme_news else (theme.get("news_count", 0) or 0)
        news_text = theme_news.get("text", "") if theme_news else ""
        news_score = calculate_news_score(news_count) if news_count > 0 else 0.0

        # 3. AI 감성 점수 (최대 10점) — 이미 theme에 있으면 재사용
        ai_sentiment = theme.get("ai_sentiment", 0) or 0
        ai_score = calculate_ai_sentiment_score(ai_sentiment) if ai_sentiment > 0 else 0.0

        # 4. 종목수 보너스 (최대 5점)
        stock_count = theme.get("stock_count", len(theme.get("stocks", [])))
        size_bonus = min(MAX_SIZE_BONUS, stock_count * 1)

        # 5. 과열 감점 (0 ~ -15점)
        three_day = theme.get("three_day_rate") or avg_return
        overheat = calculate_overheat_penalty(avg_return, three_day)

        # 6. 유동성 보정 (0 ~ -8점) — pass_rates 없으면 스킵
        liquidity_penalty = 0.0
        liquidity_note = ""
        if pass_rates:
            liquidity_penalty, liquidity_note = calculate_liquidity_penalty(
                theme_name, pass_rates,
                min_pass_rate=settings.THEME_MIN_PASS_RATE,
                low_pass_rate=settings.THEME_LOW_PASS_RATE,
                penalty_max=settings.THEME_PASS_RATE_PENALTY_MAX,
                min_data_days=settings.THEME_PASS_RATE_MIN_DATA_DAYS,
            )

        # 7. 기본점수 10점
        total = momentum_score + overheat + news_score + ai_score + size_bonus + BASE_SCORE + liquidity_penalty

        # 등급 산정 (최대 65점 기준)
        if total >= 58:
            grade = "S"
        elif total >= 48:
            grade = "A"
        elif total >= 38:
            grade = "B"
        elif total >= 30:
            grade = "C"
        else:
            grade = "D"

        # 선정 이유 생성
        reasons = []
        if momentum_score >= 20:
            reasons.append(f"강한모멘텀({avg_return:+.1f}%)")
        elif momentum_score >= 15:
            reasons.append(f"양호한모멘텀({avg_return:+.1f}%)")
        elif avg_return != 0:
            reasons.append(f"모멘텀({avg_return:+.1f}%)")
        if overheat < 0:
            reasons.append(f"과열감점({overheat:+.1f})")
        if liquidity_penalty < 0:
            reasons.append(liquidity_note)
        if news_score >= 8:
            reasons.append(f"화제({news_count}건)")
        if ai_score >= 6:
            reasons.append(f"AI긍정({ai_sentiment:.0f})")
        if stock_count >= 5:
            reasons.append(f"{stock_count}종목")

        selection_reason = ", ".join(reasons) if reasons else "기본조건충족"

        # 원본 테마 정보에 점수 추가
        # 주의: main.py:_pick_momentum 폴백 체인이 momentum/momentum_score 양쪽 키 존재에 의존.
        #       한쪽 키만 남기는 변경은 main.py 수정과 동시 진행 필수 (회귀 방지).
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
            "news": news_text,  # AI 감성분석용 뉴스 텍스트
            "ai_score": round(ai_score, 2),
            "ai_sentiment": round(ai_sentiment, 2) if ai_sentiment else 0,
            "overheat_penalty": round(overheat, 2),
            "liquidity_penalty": round(liquidity_penalty, 2),
            "liquidity_note": liquidity_note,
            "pass_rate": pass_rates.get(theme_name, {}).get("pass_rate") if pass_rates else None,
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


def _collect_news_data(themes: list[dict]) -> dict[str, dict]:
    """
    상위 30개 테마의 뉴스 건수 + 텍스트 일괄 수집

    Returns:
        {theme_name: {"count": int, "text": str}}
    """
    try:
        from modules.theme_analyzer.crawlers import crawl_theme_news, _random_delay
    except ImportError:
        logger.warning("[scorer] crawl_theme_news 임포트 실패")
        return {}

    results = {}
    for theme in themes[:30]:
        theme_name = theme.get("name", theme.get("theme", ""))
        if not theme_name:
            continue
        try:
            data = crawl_theme_news(theme_name, days=3)
            if data and data.get("count", 0) > 0:
                results[theme_name] = data
            _random_delay()
        except Exception as e:
            logger.debug(f"[scorer] 뉴스 수집 실패 ({theme_name}): {e}")
    logger.info(f"📰 뉴스 수집: {len(results)}개 테마 (텍스트 포함)")
    return results


def calculate_theme_supply_ratio(theme_url: str, kis) -> float:
    """
    테마 URL로부터 종목 목록을 크롤링하고, KIS API로 수급 데이터를 조회하여
    외국인+기관 순매수인 종목의 비율(%)을 반환한다.

    Args:
        theme_url: 네이버 테마 상세 페이지 URL
        kis: KIS API 인스턴스 (get_investor_trading 메서드 필요)

    Returns:
        수급 양호 종목 비율 (0.0~100.0). 조회 실패 시 0.0
    """
    if not theme_url or not kis:
        return 0.0

    try:
        from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks
    except ImportError:
        logger.warning("[scorer] crawl_naver_theme_stocks 임포트 실패")
        return 0.0

    # 종목 크롤링 (별도 로컬 변수 — theme["stocks"] 수정하지 않음)
    try:
        stock_list = crawl_naver_theme_stocks(theme_url)
    except Exception as e:
        logger.debug(f"[supply] 종목 크롤링 실패: {e}")
        return 0.0

    codes = [s["code"] for s in stock_list[:8] if s.get("code")]
    if not codes:
        return 0.0

    valid_count = 0
    positive_count = 0
    consecutive_failures = 0

    for code in codes:
        # circuit breaker: 연속 5회 API 실패 시 중단
        if consecutive_failures >= 5:
            logger.warning(f"[supply] circuit breaker: 연속 {consecutive_failures}회 API 실패, 중단")
            break

        try:
            investor = kis.get_investor_trading(code, days=5)
        except Exception as e:
            logger.debug(f"[supply] {code} 수급 조회 예외: {e}")
            consecutive_failures += 1
            continue

        if investor is None:
            consecutive_failures += 1
            continue

        # 성공 시 연속 실패 카운터 리셋
        consecutive_failures = 0
        valid_count += 1

        # 외국인+기관 순매수 합계가 양수이면 수급 양호
        foreign_net = investor.get("foreign_net", 0)
        institution_net = investor.get("institution_net", 0)
        if (foreign_net + institution_net) > 0:
            positive_count += 1

    if valid_count == 0:
        return 0.0

    ratio = round((positive_count / valid_count) * 100, 1)
    logger.debug(f"[supply] 수급비율: {positive_count}/{valid_count} = {ratio}%")
    return ratio


def _enrich_theme_stocks(themes: list[dict], max_stocks: int = 3) -> None:
    """
    URL 있는 테마에 종목코드 리스트 추가 (in-place)

    상위 15개 테마의 URL에서 crawl_naver_theme_stocks()로 종목 코드를 추출하여
    theme["stocks"] = [code1, code2, ...] 세팅. 실패 시 빈 리스트.

    Args:
        themes: 점수화 대상 테마 리스트
        max_stocks: 테마당 최대 종목 수
    """
    try:
        from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks, _random_delay
    except ImportError:
        logger.warning("[scorer] crawl_naver_theme_stocks 임포트 실패")
        return

    enriched = 0
    for theme in themes[:15]:
        url = theme.get("url", "")
        if not url:
            theme.setdefault("stocks", [])
            continue
        try:
            stocks = crawl_naver_theme_stocks(url)
            theme["stocks"] = [s["code"] for s in stocks[:max_stocks]]
            if theme["stocks"]:
                enriched += 1
                logger.debug(f"[{theme.get('name', '')}] 종목 매핑: {theme['stocks']}")
            _random_delay()
        except Exception as e:
            theme["stocks"] = []
            logger.debug(f"[scorer] 종목 매핑 실패 ({theme.get('name', '')}): {e}")

    logger.info(f"🔗 종목 매핑: {enriched}/{min(len(themes), 15)}개 테마")


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
    print(f"   총점: {result['total_score']}/65 ({result['grade']})")
    print(f"   - 모멘텀: {result['momentum_score']}/25")
    print(f"   - 수급: {result['supply_score']}/25")
    print(f"   - 뉴스: {result['news_score']}/15")
    print(f"   - AI: {result['ai_score']}/10")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
