"""
optimizer.py - 포트폴리오 최적화 모듈

이 파일은 검증된 종목들을 기반으로 최적 포트폴리오를 구성합니다.

주요 기능:
- 종목별 가중치 계산
- 포트폴리오 최적화 (동일가중, 점수기반, 리스크패리티)
- 자본 배분
- 최종 매수 주문 생성

최적화 전략:
1. 동일 가중 (Equal Weight) - 모든 종목 동일 비중
2. 점수 기반 (Score Based) - AI 점수에 따라 비중 조절
3. 리스크 패리티 (Risk Parity) - 리스크 동일 배분

사용법:
    from modules.portfolio_optimizer.optimizer import optimize_portfolio
    
    portfolio = optimize_portfolio(
        verified_stocks=verified,
        capital=10_000_000,
        strategy="score_based"
    )
"""

import math
from typing import Optional
from datetime import datetime, date

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst
from database import Database
from modules.portfolio_optimizer.calculators import (
    calculate_volatility,
    calculate_atr,
    calculate_stop_take_profit,
    calculate_position_size,
    calculate_daily_risk
)


# ===== 상수 정의 (settings에서 로드) =====
MAX_POSITIONS = settings.MAX_POSITIONS  # 최대 보유 종목 수
MIN_POSITION_WEIGHT = settings.MIN_POSITION_WEIGHT  # 최소 비중
MAX_POSITION_WEIGHT = settings.MAX_POSITION_WEIGHT  # 최대 비중
CASH_BUFFER = 0.05  # 현금 버퍼 5% (수수료 0.3% + 슬리피지 0.3~0.5% 대비 충분, 2026-04-16 공격적 충전)


# ===== 가중치 계산 =====

def calculate_equal_weights(
    stocks: list[dict],
    max_positions: int = MAX_POSITIONS
) -> list[dict]:
    """
    동일 가중치 계산
    
    모든 종목에 동일한 비중 배분
    
    Args:
        stocks: 종목 리스트
        max_positions: 최대 종목 수
    
    Returns:
        가중치가 추가된 종목 리스트
    """
    # 최대 종목 수 제한
    selected = stocks[:max_positions]
    n = len(selected)
    
    if n == 0:
        return []
    
    weight = 1.0 / n
    
    for stock in selected:
        stock["weight"] = round(weight, 4)
        stock["weight_method"] = "equal"
    
    logger.info(f"동일 가중치 계산: {n}개 종목, 각 {weight:.1%}")
    
    return selected


def calculate_score_based_weights(
    stocks: list[dict],
    score_key: str = "final_score",
    max_positions: int = MAX_POSITIONS,
    min_weight: float = MIN_POSITION_WEIGHT,
    max_weight: float = MAX_POSITION_WEIGHT
) -> list[dict]:
    """
    점수 기반 가중치 계산
    
    최종 점수에 비례하여 비중 배분
    점수가 높을수록 더 많은 비중
    
    Args:
        stocks: 종목 리스트
        score_key: 점수 필드명
        max_positions: 최대 종목 수
        min_weight: 최소 비중
        max_weight: 최대 비중
    
    Returns:
        가중치가 추가된 종목 리스트
    """
    # 최대 종목 수 제한
    selected = stocks[:max_positions]
    n = len(selected)
    
    if n == 0:
        return []
    
    # 점수 합계
    scores = [s.get(score_key, 50) for s in selected]
    total_score = sum(scores)
    
    if total_score == 0:
        return calculate_equal_weights(selected)
    
    # 점수 비율로 가중치 계산
    raw_weights = [s / total_score for s in scores]
    
    # 최소/최대 제한 적용
    adjusted_weights = []
    for w in raw_weights:
        w = max(min_weight, min(max_weight, w))
        adjusted_weights.append(w)
    
    # 정규화 (합계 = 1)
    total_weight = sum(adjusted_weights)
    normalized_weights = [w / total_weight for w in adjusted_weights]
    
    # 적용
    for i, stock in enumerate(selected):
        stock["weight"] = round(normalized_weights[i], 4)
        stock["weight_method"] = "score_based"
    
    logger.info(f"점수 기반 가중치 계산: {n}개 종목")
    for s in selected:
        logger.debug(f"  - {s.get('name')}: {s.get('weight'):.1%} (점수: {s.get(score_key, 0):.1f})")
    
    return selected


def calculate_risk_parity_weights(
    stocks: list[dict],
    max_positions: int = MAX_POSITIONS,
    min_weight: float = MIN_POSITION_WEIGHT,
    max_weight: float = MAX_POSITION_WEIGHT
) -> list[dict]:
    """
    리스크 패리티 가중치 계산
    
    변동성의 역수에 비례하여 비중 배분
    변동성이 낮을수록 더 많은 비중
    
    Args:
        stocks: 종목 리스트 (volatility 필드 필요)
        max_positions: 최대 종목 수
        min_weight: 최소 비중
        max_weight: 최대 비중
    
    Returns:
        가중치가 추가된 종목 리스트
    """
    # 최대 종목 수 제한
    selected = stocks[:max_positions]
    n = len(selected)
    
    if n == 0:
        return []
    
    # 변동성 가져오기 (없으면 기본값 0.3)
    volatilities = []
    for s in selected:
        vol = s.get("volatility", 0.3)
        if vol <= 0:
            vol = 0.3
        volatilities.append(vol)
    
    # 변동성의 역수
    inverse_vols = [1.0 / v for v in volatilities]
    total_inverse = sum(inverse_vols)
    
    # 가중치 계산
    raw_weights = [iv / total_inverse for iv in inverse_vols]
    
    # 최소/최대 제한
    adjusted_weights = []
    for w in raw_weights:
        w = max(min_weight, min(max_weight, w))
        adjusted_weights.append(w)
    
    # 정규화
    total_weight = sum(adjusted_weights)
    normalized_weights = [w / total_weight for w in adjusted_weights]
    
    # 적용
    for i, stock in enumerate(selected):
        stock["weight"] = round(normalized_weights[i], 4)
        stock["weight_method"] = "risk_parity"
    
    logger.info(f"리스크 패리티 가중치 계산: {n}개 종목")
    
    return selected


# ===== 포트폴리오 최적화 =====

def optimize_portfolio(
    verified_stocks: list[dict],
    capital: float,
    strategy: str = "score_based",
    max_positions: int = MAX_POSITIONS,
    cash_buffer: float = CASH_BUFFER,
    use_mock_data: bool = False
) -> dict:
    """
    포트폴리오 최적화
    
    검증된 종목들을 기반으로 최적 포트폴리오 구성
    
    Args:
        verified_stocks: AI 검증 완료된 종목 리스트
        capital: 총 투자 자본
        strategy: 최적화 전략
            - "equal": 동일 가중
            - "score_based": 점수 기반
            - "risk_parity": 리스크 패리티
        max_positions: 최대 종목 수
        cash_buffer: 현금 버퍼 비율
        use_mock_data: 모의 데이터 사용 여부
    
    Returns:
        {
            'date': '2025-02-05',
            'capital': 10000000,
            'investable': 9500000,
            'positions': [...],
            'total_invested': 9200000,
            'cash_remaining': 800000
        }
    """
    logger.info("=" * 60)
    logger.info("📊 포트폴리오 최적화 시작")
    logger.info(f"   전략: {strategy}")
    logger.info(f"   자본금: {capital:,}원")
    logger.info(f"   후보 종목: {len(verified_stocks)}개")
    logger.info("=" * 60)
    
    if not verified_stocks:
        logger.warning("최적화할 종목이 없습니다")
        return {
            "date": str(now_kst().date()),
            "capital": capital,
            "investable": 0,
            "positions": [],
            "total_invested": 0,
            "cash_remaining": capital
        }
    
    # 투자 가능 금액 (버퍼 제외)
    investable = capital * (1 - cash_buffer)
    
    # 1. 가중치 계산
    if strategy == "equal":
        weighted_stocks = calculate_equal_weights(verified_stocks, max_positions)
    elif strategy == "risk_parity":
        weighted_stocks = calculate_risk_parity_weights(verified_stocks, max_positions)
    else:  # score_based (기본)
        weighted_stocks = calculate_score_based_weights(verified_stocks, "final_score", max_positions)
    
    # 2. 각 종목 상세 계산
    positions = []
    total_invested = 0
    
    for stock in weighted_stocks:
        # ATR 및 변동성 계산 (모의 데이터 또는 실제)
        if use_mock_data:
            atr = stock.get("price", 0) * 0.025  # 가격의 2.5%
            volatility = 0.25  # 25%
        else:
            atr = stock.get("atr", stock.get("price", 0) * 0.025)
            volatility = stock.get("volatility", 0.25)
        
        price = stock.get("price", 0)
        
        # 손절/익절 계산
        ai_target = stock.get("ai_target_return", None)
        stop_take = calculate_stop_take_profit(
            price=price,
            atr=atr,
            atr_multiplier=2.0,
            risk_reward_ratio=2.0,
            ai_target_return=ai_target
        )
        
        # 포지션 사이즈 계산 (주문 유형별 증거금 배수 반영)
        pos_size = calculate_position_size(
            capital=investable,
            weight=stock.get("weight", 0.1),
            price=price,
            order_type=settings.ORDER_TYPE_DEFAULT,
        )
        
        # 포지션 정보 구성
        position = {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "theme": stock.get("theme"),
            "price": price,
            "shares": pos_size["shares"],
            "amount": pos_size["amount"],
            "weight": stock.get("weight"),
            "actual_weight": pos_size["actual_weight"],
            "stop_loss_price": stop_take["stop_loss_price"],
            "stop_loss_pct": stop_take["stop_loss_pct"],
            "take_profit_price": stop_take["take_profit_price"],
            "take_profit_pct": stop_take["take_profit_pct"],
            "risk_reward_ratio": stop_take["risk_reward_ratio"],
            "final_score": stock.get("final_score", 0),
            "ai_sentiment": stock.get("ai_sentiment", 0),
            "volatility": volatility
        }
        
        positions.append(position)
        total_invested += pos_size["amount"]
    
    # 3. 리스크 검증
    risk_info = calculate_daily_risk(positions)
    
    if not risk_info["risk_ok"]:
        logger.warning("⚠️ 포트폴리오 리스크가 높습니다!")
    
    # 결과 구성
    result = {
        "date": str(now_kst().date()),
        "capital": capital,
        "investable": investable,
        "positions": positions,
        "position_count": len(positions),
        "total_invested": total_invested,
        "cash_remaining": capital - total_invested,
        "strategy": strategy,
        "total_risk": risk_info["total_risk"],
        "risk_ok": risk_info["risk_ok"]
    }
    
    logger.info(f"\n✅ 포트폴리오 최적화 완료")
    logger.info(f"   종목 수: {len(positions)}개")
    logger.info(f"   투자 금액: {total_invested:,}원")
    logger.info(f"   잔여 현금: {capital - total_invested:,}원")
    
    return result


# ===== 매수 주문 생성 =====

def _default_order_type() -> str:
    """settings.ORDER_TYPE_DEFAULT 지연 로드 (순환 참조 방지)"""
    return settings.ORDER_TYPE_DEFAULT


def generate_buy_orders(
    portfolio: dict,
    order_type: Optional[str] = None,
) -> list[dict]:
    """
    매수 주문 리스트 생성

    Args:
        portfolio: 최적화된 포트폴리오
        order_type: 주문 유형. None이면 settings.ORDER_TYPE_DEFAULT 사용
                    ("market" / "limit_aggressive" / "limit")

    Returns:
        [
            {
                'stock_code': '005930',
                'stock_name': '삼성전자',
                'order_type': 'market',
                'quantity': 45,
                'price': 0,  # 시장가 / limit_aggressive는 0
                'expected_price': 75000,  # 슬리피지 계산 기준
                'stop_loss': 69500,
                'take_profit': 86000
            },
            ...
        ]
    """
    if order_type is None:
        order_type = _default_order_type()

    orders = []

    for pos in portfolio.get("positions", []):
        # 주문 가격 결정:
        # - market: 0 (시장가는 KIS가 체결가 결정)
        # - limit_aggressive: 0 (실시간 매도 1호가를 재시도 루프에서 재조회하여 사용)
        # - limit: pos["price"] (현재가)
        if order_type == "market" or order_type == "limit_aggressive":
            order_price = 0
        else:
            order_price = pos["price"]

        order = {
            "stock_code": pos["code"],
            "stock_name": pos["name"],
            "order_type": order_type,
            "quantity": pos["shares"],
            "price": order_price,
            "expected_price": pos["price"],  # 슬리피지 계산 기준값, 모든 주문 유형에서 보존
            "amount": pos["amount"],
            "stop_loss": pos["stop_loss_price"],
            "take_profit": pos["take_profit_price"],
            "theme": pos.get("theme"),
            "final_score": pos.get("final_score")
        }
        orders.append(order)
    
    logger.info(f"매수 주문 {len(orders)}개 생성 완료")
    
    return orders


# ===== 포트폴리오 저장 =====

def save_portfolio_to_db(
    portfolio: dict,
    db: Optional[Database] = None
) -> bool:
    """
    포트폴리오를 데이터베이스에 저장
    
    Args:
        portfolio: 최적화된 포트폴리오
        db: 데이터베이스 연결 (없으면 새로 생성)
    
    Returns:
        저장 성공 여부
    """
    close_db = False
    
    try:
        if db is None:
            db = Database()
            db.connect()
            close_db = True
        
        today = now_kst().date()
        
        for pos in portfolio.get("positions", []):
            db.save_portfolio([{
                "stock_code": pos["code"],
                "stock_name": pos["name"],
                "theme": pos.get("theme"),
                "weight": pos.get("weight", 0),
                "shares": pos["shares"],
                "buy_price": pos["price"],
                "stop_loss": pos["stop_loss_price"],
                "take_profit": pos["take_profit_price"],
                "status": "pending",
            }], today)
        logger.info(f"포트폴리오 저장 완료: {len(portfolio.get('positions', []))}개 종목")
        
        return True
        
    except Exception as e:
        logger.error(f"포트폴리오 저장 실패: {e}")
        return False
    
    finally:
        if close_db and db:
            db.close()


# ===== 일일 최적화 실행 =====

def run_daily_optimization(
    verified_stocks: list[dict],
    capital: Optional[float] = None,
    strategy: str = "score_based",
    save_to_db: bool = True,
    use_mock_data: bool = False
) -> dict:
    """
    일일 포트폴리오 최적화 실행
    
    Args:
        verified_stocks: AI 검증 완료된 종목 리스트
        capital: 총 투자 자본 (없으면 설정값 사용)
        strategy: 최적화 전략
        save_to_db: DB 저장 여부
        use_mock_data: 모의 데이터 사용 여부
    
    Returns:
        {
            'portfolio': {...},
            'orders': [...],
            'saved': True
        }
    """
    # 자본금 설정
    if capital is None:
        capital = settings.TOTAL_CAPITAL
    
    # 1. 포트폴리오 최적화
    portfolio = optimize_portfolio(
        verified_stocks=verified_stocks,
        capital=capital,
        strategy=strategy,
        use_mock_data=use_mock_data
    )
    
    # 2. 매수 주문 생성 (order_type은 settings.ORDER_TYPE_DEFAULT에서 결정)
    orders = generate_buy_orders(portfolio)
    
    # 3. DB 저장
    saved = False
    if save_to_db:
        saved = save_portfolio_to_db(portfolio)
    
    return {
        "portfolio": portfolio,
        "orders": orders,
        "saved": saved
    }


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 70)
    print("📊 포트폴리오 최적화 테스트")
    print("=" * 70)
    
    # 테스트 종목 (AI 검증 완료 가정)
    test_stocks = [
        {
            "code": "005930", "name": "삼성전자", "theme": "AI반도체",
            "price": 75000, "final_score": 87.5, "ai_sentiment": 7.8
        },
        {
            "code": "373220", "name": "LG에너지솔루션", "theme": "2차전지",
            "price": 420000, "final_score": 82.0, "ai_sentiment": 7.2
        },
        {
            "code": "000660", "name": "SK하이닉스", "theme": "AI반도체",
            "price": 195000, "final_score": 85.0, "ai_sentiment": 7.5
        },
        {
            "code": "006400", "name": "삼성SDI", "theme": "2차전지",
            "price": 350000, "final_score": 78.0, "ai_sentiment": 6.8
        },
        {
            "code": "051910", "name": "LG화학", "theme": "2차전지",
            "price": 310000, "final_score": 75.5, "ai_sentiment": 6.5
        }
    ]
    
    print(f"\n테스트 종목: {len(test_stocks)}개")
    
    # 포트폴리오 최적화
    result = run_daily_optimization(
        verified_stocks=test_stocks,
        capital=10_000_000,
        strategy="score_based",
        save_to_db=False,
        use_mock_data=True
    )
    
    portfolio = result["portfolio"]
    orders = result["orders"]
    
    # 결과 출력
    print(f"\n{'='*70}")
    print("📋 최적화 결과")
    print(f"{'='*70}")
    print(f"  총 자본금: {portfolio['capital']:,}원")
    print(f"  투자 금액: {portfolio['total_invested']:,}원")
    print(f"  잔여 현금: {portfolio['cash_remaining']:,}원")
    print(f"  종목 수: {portfolio['position_count']}개")
    
    print(f"\n{'='*70}")
    print("📈 포지션 상세")
    print(f"{'='*70}")
    print(f"{'종목':<15} {'비중':>7} {'수량':>6} {'금액':>12} {'손절':>8} {'익절':>8}")
    print("-" * 70)
    
    for pos in portfolio["positions"]:
        print(f"{pos['name']:<12} {pos['weight']:>7.1%} {pos['shares']:>6}주 "
              f"{pos['amount']:>11,}원 {pos['stop_loss_pct']:>7.1%} {pos['take_profit_pct']:>7.1%}")
    
    print(f"\n{'='*70}")
    print("🛒 매수 주문")
    print(f"{'='*70}")
    for i, order in enumerate(orders, 1):
        print(f"  {i}. {order['stock_name']} ({order['stock_code']}): "
              f"{order['quantity']}주 / {order['amount']:,}원")
    
    print(f"\n{'='*70}")
    print("✅ 포트폴리오 최적화 테스트 완료!")
    print(f"{'='*70}")
