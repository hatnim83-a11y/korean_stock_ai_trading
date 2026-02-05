"""
calculators.py - 포트폴리오 계산 모듈

이 파일은 포트폴리오 최적화에 필요한 계산 함수들을 제공합니다.

주요 기능:
- 변동성 계산 (일별/연율화)
- ATR (Average True Range) 계산
- 손절가 계산
- 익절가 계산
- 리스크/리워드 비율 계산

사용법:
    from modules.portfolio_optimizer.calculators import (
        calculate_volatility,
        calculate_atr,
        calculate_stop_loss,
        calculate_take_profit
    )
    
    volatility = calculate_volatility(daily_returns, annualize=True)
    stop_loss = calculate_stop_loss(price, atr, multiplier=2.0)
"""

import math
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


# ===== 상수 정의 =====
TRADING_DAYS_PER_YEAR = 252  # 연간 거래일 수
DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_MULTIPLIER = 2.0

# 손절/익절 제한
MIN_STOP_LOSS_PCT = -0.12  # 최소 손절 -12%
MAX_STOP_LOSS_PCT = -0.05  # 최대 손절 -5% (너무 타이트하지 않게)
MIN_TAKE_PROFIT_PCT = 0.08  # 최소 익절 +8%
MAX_TAKE_PROFIT_PCT = 0.25  # 최대 익절 +25%


# ===== 변동성 계산 =====

def calculate_volatility(
    returns: list[float],
    annualize: bool = True
) -> float:
    """
    변동성(표준편차) 계산
    
    Args:
        returns: 일별 수익률 리스트 (예: [0.01, -0.02, 0.015, ...])
        annualize: 연율화 여부
    
    Returns:
        변동성 (소수점, 예: 0.25 = 25%)
        
    Example:
        >>> returns = [0.01, -0.02, 0.015, 0.005, -0.01]
        >>> vol = calculate_volatility(returns, annualize=True)
        >>> print(f"연간 변동성: {vol:.1%}")
        연간 변동성: 18.5%
    """
    if not returns or len(returns) < 2:
        return 0.0
    
    n = len(returns)
    
    # 평균 계산
    mean = sum(returns) / n
    
    # 분산 계산
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    
    # 표준편차
    std_dev = math.sqrt(variance)
    
    # 연율화 (루트 252)
    if annualize:
        std_dev *= math.sqrt(TRADING_DAYS_PER_YEAR)
    
    return round(std_dev, 4)


def calculate_volatility_from_prices(
    prices: list[float],
    annualize: bool = True
) -> float:
    """
    가격 리스트에서 변동성 계산
    
    Args:
        prices: 종가 리스트 (최신순)
        annualize: 연율화 여부
    
    Returns:
        변동성
    """
    if not prices or len(prices) < 2:
        return 0.0
    
    # 수익률 계산
    returns = []
    for i in range(len(prices) - 1):
        if prices[i + 1] > 0:
            ret = (prices[i] - prices[i + 1]) / prices[i + 1]
            returns.append(ret)
    
    return calculate_volatility(returns, annualize)


# ===== ATR 계산 =====

def calculate_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = DEFAULT_ATR_PERIOD
) -> float:
    """
    ATR (Average True Range) 계산
    
    ATR은 자산의 평균 변동폭을 측정합니다.
    손절가 계산에 사용됩니다.
    
    Args:
        highs: 고가 리스트 (최신순)
        lows: 저가 리스트 (최신순)
        closes: 종가 리스트 (최신순)
        period: ATR 계산 기간 (기본 14일)
    
    Returns:
        ATR 값 (원)
        
    Example:
        >>> atr = calculate_atr(highs, lows, closes, period=14)
        >>> print(f"ATR: {atr:,}원")
        ATR: 1,500원
    """
    if len(closes) < period + 1:
        # 데이터 부족 시 간단한 범위 사용
        if highs and lows:
            return (highs[0] - lows[0]) if len(highs) > 0 else 0
        return 0
    
    true_ranges = []
    
    for i in range(period):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i + 1]
        
        # True Range = max(고가-저가, |고가-전종가|, |저가-전종가|)
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    atr = sum(true_ranges) / period
    
    return round(atr, 2)


def calculate_atr_percentage(
    price: float,
    atr: float
) -> float:
    """
    ATR을 가격 대비 비율로 변환
    
    Args:
        price: 현재가
        atr: ATR 값
    
    Returns:
        ATR 비율 (예: 0.02 = 2%)
    """
    if price <= 0:
        return 0
    
    return round(atr / price, 4)


# ===== 손절가 계산 =====

def calculate_stop_loss(
    price: float,
    atr: Optional[float] = None,
    multiplier: float = DEFAULT_ATR_MULTIPLIER,
    min_pct: float = MIN_STOP_LOSS_PCT,
    max_pct: float = MAX_STOP_LOSS_PCT
) -> dict:
    """
    손절가 계산 (ATR 기반)
    
    손절가 = 현재가 - (ATR × multiplier)
    단, 최소/최대 손절률 범위 내
    
    Args:
        price: 현재가
        atr: ATR 값 (없으면 가격의 5% 사용)
        multiplier: ATR 배수 (기본 2배)
        min_pct: 최소 손절률 (기본 -12%)
        max_pct: 최대 손절률 (기본 -5%)
    
    Returns:
        {
            'stop_loss_price': 69500,  # 손절가
            'stop_loss_pct': -0.073,   # 손절률
            'risk_amount': 5500        # 손실 금액
        }
        
    Example:
        >>> result = calculate_stop_loss(75000, atr=1500, multiplier=2.0)
        >>> print(f"손절가: {result['stop_loss_price']:,}원 ({result['stop_loss_pct']:.1%})")
        손절가: 72,000원 (-4.0%)
    """
    if price <= 0:
        return {"stop_loss_price": 0, "stop_loss_pct": 0, "risk_amount": 0}
    
    # ATR이 없으면 가격의 5%를 기본값으로
    if atr is None:
        atr = price * 0.05
    
    # ATR 기반 손절 거리
    stop_distance = atr * multiplier
    stop_pct = -stop_distance / price
    
    # 범위 제한 (min_pct ~ max_pct)
    # min_pct = -0.12 (더 큰 손실 허용)
    # max_pct = -0.05 (최소 손절 거리)
    stop_pct = max(min_pct, min(max_pct, stop_pct))
    
    # 손절가 계산
    stop_loss_price = price * (1 + stop_pct)
    risk_amount = price - stop_loss_price
    
    return {
        "stop_loss_price": round(stop_loss_price),
        "stop_loss_pct": round(stop_pct, 4),
        "risk_amount": round(risk_amount)
    }


# ===== 익절가 계산 =====

def calculate_take_profit(
    price: float,
    stop_loss_pct: float,
    risk_reward_ratio: float = 2.0,
    ai_target_return: Optional[float] = None,
    min_pct: float = MIN_TAKE_PROFIT_PCT,
    max_pct: float = MAX_TAKE_PROFIT_PCT
) -> dict:
    """
    익절가 계산 (리스크/리워드 비율 기반)
    
    익절가 = 현재가 + (손절 거리 × risk_reward_ratio)
    AI 목표 수익률이 있으면 둘 중 낮은 값 선택
    
    Args:
        price: 현재가
        stop_loss_pct: 손절률 (음수, 예: -0.08)
        risk_reward_ratio: 리스크/리워드 비율 (기본 1:2)
        ai_target_return: AI 목표 수익률 (예: 0.15 = 15%)
        min_pct: 최소 익절률 (기본 +8%)
        max_pct: 최대 익절률 (기본 +25%)
    
    Returns:
        {
            'take_profit_price': 86000,  # 익절가
            'take_profit_pct': 0.147,    # 익절률
            'reward_amount': 11000       # 수익 금액
        }
        
    Example:
        >>> result = calculate_take_profit(75000, -0.08, risk_reward_ratio=2.0)
        >>> print(f"익절가: {result['take_profit_price']:,}원 ({result['take_profit_pct']:.1%})")
        익절가: 87,000원 (+16.0%)
    """
    if price <= 0:
        return {"take_profit_price": 0, "take_profit_pct": 0, "reward_amount": 0}
    
    # 손절 거리 기반 익절률 계산
    stop_distance = abs(stop_loss_pct)
    take_profit_pct = stop_distance * risk_reward_ratio
    
    # AI 목표 수익률과 비교 (낮은 값 선택)
    if ai_target_return is not None and ai_target_return > 0:
        take_profit_pct = min(take_profit_pct, ai_target_return / 100)
    
    # 범위 제한
    take_profit_pct = max(min_pct, min(max_pct, take_profit_pct))
    
    # 익절가 계산
    take_profit_price = price * (1 + take_profit_pct)
    reward_amount = take_profit_price - price
    
    return {
        "take_profit_price": round(take_profit_price),
        "take_profit_pct": round(take_profit_pct, 4),
        "reward_amount": round(reward_amount)
    }


# ===== 종합 손익 계산 =====

def calculate_stop_take_profit(
    price: float,
    atr: Optional[float] = None,
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
    risk_reward_ratio: float = 2.0,
    ai_target_return: Optional[float] = None
) -> dict:
    """
    손절/익절 종합 계산
    
    Args:
        price: 현재가
        atr: ATR 값
        atr_multiplier: ATR 배수
        risk_reward_ratio: 리스크/리워드 비율
        ai_target_return: AI 목표 수익률 (%)
    
    Returns:
        {
            'price': 75000,
            'stop_loss_price': 69500,
            'stop_loss_pct': -0.073,
            'take_profit_price': 86000,
            'take_profit_pct': 0.147,
            'risk_amount': 5500,
            'reward_amount': 11000,
            'risk_reward_ratio': 2.0
        }
    """
    # 손절 계산
    stop_result = calculate_stop_loss(price, atr, atr_multiplier)
    
    # 익절 계산
    take_result = calculate_take_profit(
        price,
        stop_result["stop_loss_pct"],
        risk_reward_ratio,
        ai_target_return
    )
    
    # 실제 리스크/리워드 비율
    actual_rr = 0
    if stop_result["risk_amount"] > 0:
        actual_rr = take_result["reward_amount"] / stop_result["risk_amount"]
    
    return {
        "price": price,
        "stop_loss_price": stop_result["stop_loss_price"],
        "stop_loss_pct": stop_result["stop_loss_pct"],
        "take_profit_price": take_result["take_profit_price"],
        "take_profit_pct": take_result["take_profit_pct"],
        "risk_amount": stop_result["risk_amount"],
        "reward_amount": take_result["reward_amount"],
        "risk_reward_ratio": round(actual_rr, 2)
    }


# ===== 포지션 사이즈 계산 =====

def calculate_position_size(
    capital: float,
    weight: float,
    price: float,
    lot_size: int = 1
) -> dict:
    """
    포지션 사이즈 (매수 수량) 계산
    
    Args:
        capital: 총 자본금
        weight: 투자 비중 (0-1)
        price: 매수 가격
        lot_size: 매매 단위 (기본 1주)
    
    Returns:
        {
            'shares': 45,           # 매수 수량
            'amount': 3375000,      # 투자 금액
            'actual_weight': 0.0675 # 실제 비중
        }
        
    Example:
        >>> result = calculate_position_size(10000000, 0.08, 75000)
        >>> print(f"매수 수량: {result['shares']}주, 금액: {result['amount']:,}원")
        매수 수량: 10주, 금액: 750,000원
    """
    if price <= 0 or capital <= 0 or weight <= 0:
        return {"shares": 0, "amount": 0, "actual_weight": 0}
    
    # 목표 투자 금액
    target_amount = capital * weight
    
    # 매수 가능 수량 (내림)
    shares = int(target_amount / price)
    
    # 매매 단위 맞춤
    shares = (shares // lot_size) * lot_size
    
    # 최소 1주
    shares = max(lot_size, shares)
    
    # 실제 투자 금액
    amount = shares * price
    
    # 실제 비중
    actual_weight = amount / capital
    
    return {
        "shares": shares,
        "amount": round(amount),
        "actual_weight": round(actual_weight, 4)
    }


# ===== 리스크 금액 계산 =====

def calculate_risk_amount(
    shares: int,
    buy_price: float,
    stop_loss_price: float
) -> float:
    """
    포지션의 최대 손실 금액 계산
    
    Args:
        shares: 보유 수량
        buy_price: 매수 가격
        stop_loss_price: 손절가
    
    Returns:
        최대 손실 금액 (양수)
    """
    if shares <= 0:
        return 0
    
    loss_per_share = buy_price - stop_loss_price
    total_loss = shares * loss_per_share
    
    return round(max(0, total_loss))


def calculate_daily_risk(
    portfolio: list[dict],
    max_daily_loss_pct: float = 0.02
) -> dict:
    """
    포트폴리오 일일 리스크 계산
    
    Args:
        portfolio: 포트폴리오 종목 리스트
        max_daily_loss_pct: 일일 최대 손실률
    
    Returns:
        {
            'total_risk': 500000,      # 총 리스크 금액
            'max_loss': 200000,        # 일일 최대 손실 허용
            'risk_ok': True            # 리스크 적정 여부
        }
    """
    total_risk = 0
    total_value = 0
    
    for position in portfolio:
        shares = position.get("shares", 0)
        buy_price = position.get("buy_price", 0)
        stop_loss = position.get("stop_loss_price", 0)
        
        if shares > 0 and buy_price > 0:
            position_value = shares * buy_price
            total_value += position_value
            
            if stop_loss > 0:
                risk = calculate_risk_amount(shares, buy_price, stop_loss)
                total_risk += risk
    
    max_loss = total_value * max_daily_loss_pct
    risk_ok = total_risk <= max_loss * 3  # 3일 치 손실까지 허용
    
    return {
        "total_risk": round(total_risk),
        "max_loss": round(max_loss),
        "risk_ok": risk_ok
    }


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 포트폴리오 계산 테스트")
    print("=" * 60)
    
    # 변동성 테스트
    print("\n1️⃣ 변동성 계산:")
    test_returns = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.008]
    vol = calculate_volatility(test_returns, annualize=True)
    print(f"   연간 변동성: {vol:.1%}")
    
    # ATR 테스트
    print("\n2️⃣ ATR 계산:")
    test_highs = [76000, 75500, 76200, 75800, 75000] * 3
    test_lows = [74000, 73500, 74200, 73800, 73000] * 3
    test_closes = [75000, 74500, 75200, 74800, 74000] * 3
    atr = calculate_atr(test_highs, test_lows, test_closes, period=14)
    print(f"   ATR: {atr:,}원")
    
    # 손절/익절 테스트
    print("\n3️⃣ 손절/익절 계산:")
    price = 75000
    result = calculate_stop_take_profit(
        price=price,
        atr=atr,
        atr_multiplier=2.0,
        risk_reward_ratio=2.0,
        ai_target_return=15
    )
    print(f"   현재가: {price:,}원")
    print(f"   손절가: {result['stop_loss_price']:,}원 ({result['stop_loss_pct']:.1%})")
    print(f"   익절가: {result['take_profit_price']:,}원 ({result['take_profit_pct']:.1%})")
    print(f"   리스크/리워드: 1:{result['risk_reward_ratio']:.1f}")
    
    # 포지션 사이즈 테스트
    print("\n4️⃣ 포지션 사이즈 계산:")
    pos = calculate_position_size(10_000_000, 0.08, price)
    print(f"   자본금: 10,000,000원")
    print(f"   비중: 8%")
    print(f"   매수 수량: {pos['shares']}주")
    print(f"   투자 금액: {pos['amount']:,}원")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
