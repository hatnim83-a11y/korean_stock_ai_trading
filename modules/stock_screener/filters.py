"""
filters.py - 종목 필터링 모듈

이 파일은 종목을 수급, 기술적, 재무 조건으로 필터링합니다.

필터 조건:
1. 수급 필터: 외국인/기관 순매수
2. 기술적 필터: MA 정배열, 거래량, RSI
3. 재무 필터: 부채비율, 영업이익률

사용법:
    from modules.stock_screener.filters import (
        apply_supply_filter,
        apply_technical_filter,
        apply_fundamental_filter,
        apply_all_filters
    )
    
    # 수급 필터 적용
    passed = apply_supply_filter(stock_data)
"""

from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


# ===== 필터 기준 상수 =====

# 수급 필터 기준
MIN_FOREIGN_NET_BUY = 0  # 외국인 순매수 최소 (원), 0이면 순매도 아니어야 함
MIN_INSTITUTION_NET_BUY = 0  # 기관 순매수 최소 (원)
REQUIRE_BOTH_BUYING = False  # True면 외국인+기관 모두 매수여야 함

# 기술적 필터 기준
RSI_UPPER_LIMIT = 75.0  # RSI 상한 (과열 방지)
RSI_LOWER_LIMIT = 30.0  # RSI 하한 (과매도)
VOLUME_RATIO_MIN = 1.2  # 거래량 비율 하한 (20일 평균 대비)
REQUIRE_MA_BULLISH = True  # 정배열 필수 여부

# 재무 필터 기준
MAX_DEBT_RATIO = 200.0  # 최대 부채비율 (%)
MIN_OPERATING_MARGIN = 0.0  # 최소 영업이익률 (%)

# 거래 대금 필터
MIN_TRADE_VALUE = 5_000_000_000  # 최소 거래대금 (50억원)


# ===== 수급 필터 =====

def apply_supply_filter(
    stock: dict,
    min_foreign: float = MIN_FOREIGN_NET_BUY,
    min_institution: float = MIN_INSTITUTION_NET_BUY,
    require_both: bool = REQUIRE_BOTH_BUYING
) -> dict:
    """
    수급 필터 적용
    
    외국인 또는 기관이 순매수 중인 종목만 통과시킵니다.
    
    조건:
    - 외국인 5일 순매수 > 0 OR 기관 5일 순매수 > 0 (기본)
    - require_both=True면 둘 다 순매수여야 함
    
    Args:
        stock: 종목 정보 딕셔너리 (foreign_net, institution_net 필요)
        min_foreign: 외국인 순매수 최소 금액
        min_institution: 기관 순매수 최소 금액
        require_both: 둘 다 순매수 필수 여부
    
    Returns:
        필터 결과가 추가된 딕셔너리:
        {
            ...원본 데이터...,
            'supply_passed': True/False,
            'supply_score': 150.0,  # 수급 점수 (억원)
            'supply_reason': '외국인 +100억, 기관 +50억'
        }
        
    Example:
        >>> result = apply_supply_filter({'foreign_net': 10000000000, 'institution_net': 5000000000})
        >>> print(result['supply_passed'])
        True
    """
    result = {**stock}
    
    # 수급 데이터 추출
    foreign_net = stock.get("foreign_net", 0) or 0
    institution_net = stock.get("institution_net", 0) or 0
    
    # 수급 점수 계산 (억원 단위)
    supply_score = (foreign_net + institution_net) / 100_000_000
    result["supply_score"] = round(supply_score, 2)
    
    # 필터 조건 체크
    foreign_ok = foreign_net >= min_foreign
    institution_ok = institution_net >= min_institution
    
    if require_both:
        passed = foreign_ok and institution_ok
    else:
        # 둘 중 하나만 순매수여도 통과
        passed = (foreign_net > 0) or (institution_net > 0)
    
    result["supply_passed"] = passed
    
    # 사유 기록
    foreign_bil = foreign_net / 100_000_000
    institution_bil = institution_net / 100_000_000
    result["supply_reason"] = f"외국인 {foreign_bil:+.0f}억, 기관 {institution_bil:+.0f}억"
    
    if passed:
        logger.debug(f"[{stock.get('code', '?')}] 수급 통과: {result['supply_reason']}")
    else:
        logger.debug(f"[{stock.get('code', '?')}] 수급 미통과: {result['supply_reason']}")
    
    return result


def calculate_supply_score(
    foreign_net: float,
    institution_net: float,
    foreign_weight: float = 0.6,
    institution_weight: float = 0.4
) -> float:
    """
    수급 점수 계산 (0-100 정규화)
    
    Args:
        foreign_net: 외국인 순매수 (원)
        institution_net: 기관 순매수 (원)
        foreign_weight: 외국인 가중치
        institution_weight: 기관 가중치
    
    Returns:
        정규화된 수급 점수 (0-100)
    """
    # 억원 단위로 변환
    foreign_bil = foreign_net / 100_000_000
    institution_bil = institution_net / 100_000_000
    
    # 가중 합계
    weighted_sum = (foreign_bil * foreign_weight) + (institution_bil * institution_weight)
    
    # 점수화: 50억 이상이면 만점 (100)
    # 선형 매핑: -50억 → 0, 0억 → 50, +50억 → 100
    score = ((weighted_sum + 50) / 100) * 100
    score = max(0, min(100, score))
    
    return round(score, 2)


# ===== 기술적 필터 =====

def apply_technical_filter(
    stock: dict,
    rsi_upper: float = RSI_UPPER_LIMIT,
    rsi_lower: float = RSI_LOWER_LIMIT,
    volume_ratio_min: float = VOLUME_RATIO_MIN,
    require_bullish: bool = REQUIRE_MA_BULLISH
) -> dict:
    """
    기술적 필터 적용
    
    조건:
    - RSI < 75 (과열 아님)
    - RSI > 30 (과매도 아님, 선택)
    - 거래량 > 20일 평균 × 1.2
    - 이동평균선 정배열 (MA5 > MA20 > MA60, 선택)
    
    Args:
        stock: 종목 정보 (rsi, volume_ratio, ma_alignment 필요)
        rsi_upper: RSI 상한
        rsi_lower: RSI 하한
        volume_ratio_min: 거래량 비율 하한
        require_bullish: 정배열 필수 여부
    
    Returns:
        필터 결과가 추가된 딕셔너리
    """
    result = {**stock}
    
    # 기술적 지표 추출
    rsi = stock.get("rsi", 50) or 50
    volume_ratio = stock.get("volume_ratio", 1.0) or 1.0
    ma_alignment = stock.get("ma_alignment", "neutral")
    
    # 조건 체크
    reasons = []
    passed = True
    
    # 1. RSI 과열 체크
    if rsi > rsi_upper:
        passed = False
        reasons.append(f"RSI 과열 ({rsi:.1f} > {rsi_upper})")
    elif rsi < rsi_lower:
        # 과매도는 경고만 (매수 기회일 수 있음)
        reasons.append(f"RSI 과매도 ({rsi:.1f})")
    else:
        reasons.append(f"RSI 정상 ({rsi:.1f})")
    
    # 2. 거래량 체크
    if volume_ratio < volume_ratio_min:
        passed = False
        reasons.append(f"거래량 부족 ({volume_ratio:.2f}x)")
    else:
        reasons.append(f"거래량 양호 ({volume_ratio:.2f}x)")
    
    # 3. 이동평균선 정배열 체크
    if require_bullish:
        if ma_alignment == "bearish":
            passed = False
            reasons.append("MA 역배열")
        elif ma_alignment == "bullish":
            reasons.append("MA 정배열")
        else:
            reasons.append("MA 중립")
    
    result["technical_passed"] = passed
    result["technical_reason"] = ", ".join(reasons)
    
    # 기술적 점수 계산 (0-100)
    tech_score = calculate_technical_score(rsi, volume_ratio, ma_alignment)
    result["technical_score"] = tech_score
    
    code = stock.get('code', '?')
    if passed:
        logger.debug(f"[{code}] 기술 통과: {result['technical_reason']}")
    else:
        logger.debug(f"[{code}] 기술 미통과: {result['technical_reason']}")
    
    return result


def calculate_technical_score(
    rsi: float,
    volume_ratio: float,
    ma_alignment: str
) -> float:
    """
    기술적 점수 계산 (0-100)
    
    Args:
        rsi: RSI 값
        volume_ratio: 거래량 비율
        ma_alignment: 이동평균선 상태 ('bullish', 'neutral', 'bearish')
    
    Returns:
        기술적 점수 (0-100)
    """
    score = 0
    
    # RSI 점수 (40점 만점)
    # RSI 50 근처가 최적, 30 이하/70 이상은 감점
    if 40 <= rsi <= 60:
        rsi_score = 40  # 최적
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        rsi_score = 30  # 양호
    elif rsi < 30:
        rsi_score = 20  # 과매도 (기회)
    else:  # rsi > 70
        rsi_score = 10  # 과열 (위험)
    score += rsi_score
    
    # 거래량 점수 (30점 만점)
    if volume_ratio >= 2.0:
        vol_score = 30  # 폭발적
    elif volume_ratio >= 1.5:
        vol_score = 25  # 우수
    elif volume_ratio >= 1.2:
        vol_score = 20  # 양호
    elif volume_ratio >= 1.0:
        vol_score = 15  # 보통
    else:
        vol_score = 5  # 부족
    score += vol_score
    
    # 이동평균선 점수 (30점 만점)
    if ma_alignment == "bullish":
        ma_score = 30
    elif ma_alignment == "neutral":
        ma_score = 15
    else:  # bearish
        ma_score = 0
    score += ma_score
    
    return round(score, 2)


# ===== 재무 필터 =====

def apply_fundamental_filter(
    stock: dict,
    max_debt_ratio: float = MAX_DEBT_RATIO,
    min_operating_margin: float = MIN_OPERATING_MARGIN
) -> dict:
    """
    재무 필터 적용
    
    조건:
    - 부채비율 < 200%
    - 영업이익률 > 0%
    
    Args:
        stock: 종목 정보 (debt_ratio, operating_margin 필요)
        max_debt_ratio: 최대 부채비율
        min_operating_margin: 최소 영업이익률
    
    Returns:
        필터 결과가 추가된 딕셔너리
    """
    result = {**stock}
    
    # 재무 지표 추출 (없으면 기본값으로 통과 처리)
    debt_ratio = stock.get("debt_ratio")
    operating_margin = stock.get("operating_margin")
    
    reasons = []
    passed = True
    
    # 부채비율 체크
    if debt_ratio is not None:
        if debt_ratio > max_debt_ratio:
            passed = False
            reasons.append(f"부채비율 초과 ({debt_ratio:.0f}%)")
        else:
            reasons.append(f"부채비율 양호 ({debt_ratio:.0f}%)")
    else:
        reasons.append("부채비율 정보 없음")
    
    # 영업이익률 체크
    if operating_margin is not None:
        if operating_margin < min_operating_margin:
            passed = False
            reasons.append(f"영업이익률 미달 ({operating_margin:.1f}%)")
        else:
            reasons.append(f"영업이익률 양호 ({operating_margin:.1f}%)")
    else:
        reasons.append("영업이익률 정보 없음")
    
    result["fundamental_passed"] = passed
    result["fundamental_reason"] = ", ".join(reasons)
    
    code = stock.get('code', '?')
    if passed:
        logger.debug(f"[{code}] 재무 통과: {result['fundamental_reason']}")
    else:
        logger.debug(f"[{code}] 재무 미통과: {result['fundamental_reason']}")
    
    return result


# ===== 거래대금 필터 =====

def apply_liquidity_filter(
    stock: dict,
    min_trade_value: int = MIN_TRADE_VALUE
) -> dict:
    """
    유동성 필터 적용
    
    조건:
    - 거래대금 > 50억원 (유동성 확보)
    
    Args:
        stock: 종목 정보 (trade_value 필요)
        min_trade_value: 최소 거래대금 (원)
    
    Returns:
        필터 결과가 추가된 딕셔너리
    """
    result = {**stock}
    
    trade_value = stock.get("trade_value", 0) or 0
    trade_value_bil = trade_value / 100_000_000
    
    passed = trade_value >= min_trade_value
    
    result["liquidity_passed"] = passed
    result["liquidity_reason"] = f"거래대금 {trade_value_bil:.0f}억원"
    
    return result


# ===== 통합 필터 =====

def apply_all_filters(
    stock: dict,
    # 수급 필터 옵션
    supply_require_both: bool = False,
    # 기술적 필터 옵션
    rsi_upper: float = RSI_UPPER_LIMIT,
    volume_ratio_min: float = VOLUME_RATIO_MIN,
    require_bullish: bool = True,
    # 재무 필터 옵션
    max_debt_ratio: float = MAX_DEBT_RATIO,
    min_operating_margin: float = MIN_OPERATING_MARGIN,
    # 유동성 필터 옵션
    min_trade_value: int = MIN_TRADE_VALUE
) -> dict:
    """
    모든 필터 통합 적용
    
    수급 → 기술적 → 재무 → 유동성 순으로 필터를 적용합니다.
    
    Args:
        stock: 종목 정보 딕셔너리
        ... (각 필터별 옵션)
    
    Returns:
        모든 필터 결과가 추가된 딕셔너리:
        {
            ...원본 데이터...,
            'supply_passed': True,
            'technical_passed': True,
            'fundamental_passed': True,
            'liquidity_passed': True,
            'all_passed': True,
            'final_score': 85.5
        }
    """
    result = {**stock}
    
    # 1. 수급 필터
    result = apply_supply_filter(result, require_both=supply_require_both)
    
    # 2. 기술적 필터
    result = apply_technical_filter(
        result,
        rsi_upper=rsi_upper,
        volume_ratio_min=volume_ratio_min,
        require_bullish=require_bullish
    )
    
    # 3. 재무 필터
    result = apply_fundamental_filter(
        result,
        max_debt_ratio=max_debt_ratio,
        min_operating_margin=min_operating_margin
    )
    
    # 4. 유동성 필터
    result = apply_liquidity_filter(result, min_trade_value=min_trade_value)
    
    # 전체 통과 여부
    all_passed = (
        result.get("supply_passed", False) and
        result.get("technical_passed", False) and
        result.get("fundamental_passed", False) and
        result.get("liquidity_passed", False)
    )
    result["all_passed"] = all_passed
    
    # 최종 점수 계산 (통과한 경우만)
    if all_passed:
        final_score = calculate_final_score(result)
        result["final_score"] = final_score
    else:
        result["final_score"] = 0
    
    code = stock.get('code', '?')
    name = stock.get('name', '?')
    
    if all_passed:
        logger.info(f"✅ [{code}] {name} 모든 필터 통과 (점수: {result['final_score']:.1f})")
    else:
        # 실패한 필터 목록
        failed = []
        if not result.get("supply_passed"): failed.append("수급")
        if not result.get("technical_passed"): failed.append("기술")
        if not result.get("fundamental_passed"): failed.append("재무")
        if not result.get("liquidity_passed"): failed.append("유동성")
        logger.debug(f"❌ [{code}] {name} 필터 미통과: {', '.join(failed)}")
    
    return result


def calculate_final_score(stock: dict) -> float:
    """
    최종 점수 계산
    
    가중치:
    - 수급 점수: 40%
    - 기술적 점수: 30%
    - 테마 점수: 20% (있는 경우)
    - AI 점수: 10% (있는 경우)
    
    Args:
        stock: 필터가 적용된 종목 정보
    
    Returns:
        최종 점수 (0-100)
    """
    # 수급 점수 (억원 → 정규화)
    supply_score = stock.get("supply_score", 0)
    # 50억 = 100점 기준
    supply_normalized = min(100, (supply_score / 50) * 100) if supply_score > 0 else 0
    
    # 기술적 점수
    technical_score = stock.get("technical_score", 50)
    
    # 테마 점수 (있으면 사용, 없으면 50)
    theme_score = stock.get("theme_score", 50)
    
    # AI 점수 (있으면 사용, 없으면 50)
    ai_sentiment = stock.get("ai_sentiment", 5) * 10  # 0-10 → 0-100
    
    # 가중 평균
    final = (
        supply_normalized * 0.40 +
        technical_score * 0.30 +
        theme_score * 0.20 +
        ai_sentiment * 0.10
    )
    
    return round(final, 2)


def filter_stocks(
    stocks: list[dict],
    **filter_options
) -> list[dict]:
    """
    여러 종목에 대해 필터 일괄 적용
    
    Args:
        stocks: 종목 리스트
        **filter_options: apply_all_filters에 전달할 옵션
    
    Returns:
        필터를 통과한 종목 리스트 (점수 내림차순)
    """
    logger.info(f"📊 {len(stocks)}개 종목 필터링 시작")
    
    filtered = []
    passed_count = 0
    
    for stock in stocks:
        result = apply_all_filters(stock, **filter_options)
        
        if result.get("all_passed"):
            filtered.append(result)
            passed_count += 1
    
    # 점수 순 정렬
    filtered.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    
    logger.info(f"✅ 필터 통과: {passed_count}/{len(stocks)}개")
    
    return filtered


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🔍 필터 모듈 테스트")
    print("=" * 60)
    
    # 테스트 데이터
    test_stocks = [
        {
            "code": "005930",
            "name": "삼성전자",
            "price": 75000,
            "foreign_net": 50_000_000_000,  # +500억
            "institution_net": 30_000_000_000,  # +300억
            "rsi": 55,
            "volume_ratio": 1.5,
            "ma_alignment": "bullish",
            "debt_ratio": 35,
            "operating_margin": 12,
            "trade_value": 100_000_000_000,  # 1000억
        },
        {
            "code": "000660",
            "name": "SK하이닉스",
            "price": 150000,
            "foreign_net": -20_000_000_000,  # -200억 (매도)
            "institution_net": 10_000_000_000,  # +100억
            "rsi": 78,  # 과열
            "volume_ratio": 0.8,  # 거래량 부족
            "ma_alignment": "neutral",
            "debt_ratio": 80,
            "operating_margin": 15,
            "trade_value": 80_000_000_000,
        },
        {
            "code": "035420",
            "name": "NAVER",
            "price": 200000,
            "foreign_net": 10_000_000_000,
            "institution_net": 5_000_000_000,
            "rsi": 45,
            "volume_ratio": 1.3,
            "ma_alignment": "bullish",
            "debt_ratio": 250,  # 부채비율 초과
            "operating_margin": -5,  # 영업이익률 미달
            "trade_value": 50_000_000_000,
        }
    ]
    
    print("\n테스트 종목:")
    for stock in test_stocks:
        print(f"  - {stock['name']} ({stock['code']})")
    
    print("\n필터 적용 결과:")
    print("-" * 60)
    
    for stock in test_stocks:
        result = apply_all_filters(stock)
        
        status = "✅ 통과" if result["all_passed"] else "❌ 탈락"
        print(f"\n{result['name']} ({result['code']}): {status}")
        print(f"  수급: {'✓' if result['supply_passed'] else '✗'} - {result['supply_reason']}")
        print(f"  기술: {'✓' if result['technical_passed'] else '✗'} - {result['technical_reason']}")
        print(f"  재무: {'✓' if result['fundamental_passed'] else '✗'} - {result['fundamental_reason']}")
        print(f"  유동성: {'✓' if result['liquidity_passed'] else '✗'} - {result['liquidity_reason']}")
        
        if result["all_passed"]:
            print(f"  최종 점수: {result['final_score']:.1f}")
    
    print("\n" + "-" * 60)
    
    # 일괄 필터 테스트
    filtered = filter_stocks(test_stocks)
    print(f"\n필터 통과 종목: {len(filtered)}개")
    for stock in filtered:
        print(f"  - {stock['name']}: {stock['final_score']:.1f}점")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
