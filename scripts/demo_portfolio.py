#!/usr/bin/env python3
"""
demo_portfolio.py - 포트폴리오 생성 데모

모의 데이터로 포트폴리오 생성 과정을 시연합니다.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.portfolio_optimizer import run_daily_optimization, display_portfolio


# 모의 검증 통과 종목
mock_verified_stocks = [
    {
        "stock_code": "373220",
        "stock_name": "LG에너지솔루션",
        "theme": "2차전지",
        "current_price": 420000,
        "score": 85.5,
        "ai_score": 8.5,
        "supply_score": 42.3,
        "technical_score": 35.2
    },
    {
        "stock_code": "006400",
        "stock_name": "삼성SDI",
        "theme": "2차전지",
        "current_price": 320000,
        "score": 83.2,
        "ai_score": 8.2,
        "supply_score": 40.1,
        "technical_score": 34.9
    },
    {
        "stock_code": "000660",
        "stock_name": "SK하이닉스",
        "theme": "AI 반도체",
        "current_price": 180000,
        "score": 82.1,
        "ai_score": 8.3,
        "supply_score": 38.5,
        "technical_score": 35.3
    },
    {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "theme": "AI 반도체",
        "current_price": 75000,
        "score": 79.5,
        "ai_score": 7.9,
        "supply_score": 36.2,
        "technical_score": 35.4
    },
    {
        "stock_code": "207940",
        "stock_name": "삼성바이오로직스",
        "theme": "바이오",
        "current_price": 920000,
        "score": 78.3,
        "ai_score": 7.8,
        "supply_score": 35.8,
        "technical_score": 34.7
    },
    {
        "stock_code": "068270",
        "stock_name": "셀트리온",
        "theme": "바이오",
        "current_price": 185000,
        "score": 76.8,
        "ai_score": 7.7,
        "supply_score": 34.5,
        "technical_score": 34.6
    },
    {
        "stock_code": "035720",
        "stock_name": "카카오",
        "theme": "플랫폼",
        "current_price": 42000,
        "score": 75.2,
        "ai_score": 7.5,
        "supply_score": 33.2,
        "technical_score": 34.5
    },
    {
        "stock_code": "035420",
        "stock_name": "NAVER",
        "theme": "플랫폼",
        "current_price": 220000,
        "score": 74.5,
        "ai_score": 7.4,
        "supply_score": 32.8,
        "technical_score": 34.3
    }
]


def main():
    print("=" * 70)
    print("💼 포트폴리오 생성 데모 (모의 데이터)")
    print("=" * 70)
    
    print(f"\n✅ AI 검증 통과 종목: {len(mock_verified_stocks)}개")
    for i, stock in enumerate(mock_verified_stocks, 1):
        print(f"   {i}. {stock['stock_name']} ({stock['theme']})")
        print(f"      현재가: {stock['current_price']:,}원, 점수: {stock['score']:.1f}")
    
    print("\n" + "=" * 70)
    print("💼 포트폴리오 최적화 시작...")
    print("=" * 70)
    
    # 포트폴리오 최적화 실행
    capital = 10_000_000  # 1000만원
    
    result = run_daily_optimization(
        verified_stocks=mock_verified_stocks,
        capital=capital,
        strategy="score_based",  # 점수 기반 가중치
        save_to_db=False,
        use_mock_data=True
    )
    
    portfolio = result["portfolio"]
    orders = result["orders"]
    
    print("\n" + "=" * 70)
    print("📊 최적화된 포트폴리오")
    print("=" * 70)
    
    print(f"\n총 자본금: {portfolio['total_capital']:,}원")
    print(f"투자 금액: {portfolio['total_invested']:,}원")
    print(f"종목 수: {len(portfolio['positions'])}개")
    
    print("\n" + "=" * 70)
    print("💰 매수 주문 목록")
    print("=" * 70)
    
    total_amount = 0
    for i, order in enumerate(orders, 1):
        amount = order["quantity"] * order["price"]
        total_amount += amount
        weight = (amount / capital) * 100
        
        print(f"\n{i}. {order['stock_name']} ({order['stock_code']})")
        print(f"   테마: {order['theme']}")
        print(f"   현재가: {order['price']:,}원")
        print(f"   수량: {order['quantity']:,}주")
        print(f"   금액: {amount:,}원 (비중: {weight:.1f}%)")
        print(f"   손절가: {order.get('stop_loss', 0):,}원 ({order.get('stop_loss_pct', -8):.1f}%)")
        print(f"   익절가: {order.get('take_profit', 0):,}원 (+{order.get('take_profit_pct', 15):.1f}%)")
    
    print("\n" + "=" * 70)
    print("📈 포트폴리오 요약")
    print("=" * 70)
    
    print(f"\n총 투자 자본: {capital:,}원")
    print(f"총 투자 금액: {total_amount:,}원")
    print(f"현금 잔액: {capital - total_amount:,}원")
    print(f"종목 수: {len(orders)}개")
    
    # 테마별 집계
    theme_amounts = {}
    for order in orders:
        theme = order["theme"]
        amount = order["quantity"] * order["price"]
        theme_amounts[theme] = theme_amounts.get(theme, 0) + amount
    
    print(f"\n📊 테마별 배분:")
    for theme, amount in sorted(theme_amounts.items(), key=lambda x: x[1], reverse=True):
        weight = (amount / capital) * 100
        print(f"   - {theme}: {amount:,}원 ({weight:.1f}%)")
    
    # 위험 지표
    print(f"\n⚠️  위험 관리:")
    max_loss = sum(order["quantity"] * (order["price"] - order.get("stop_loss", order["price"] * 0.92)) 
                   for order in orders)
    max_profit = sum(order["quantity"] * (order.get("take_profit", order["price"] * 1.15) - order["price"]) 
                     for order in orders)
    
    print(f"   최대 손실 (전체 손절 시): -{max_loss:,}원 ({-max_loss/capital*100:.1f}%)")
    print(f"   최대 수익 (전체 익절 시): +{max_profit:,}원 (+{max_profit/capital*100:.1f}%)")
    print(f"   손익비: 1 : {max_profit/max_loss:.2f}")
    
    print("\n" + "=" * 70)
    print("✅ 포트폴리오 생성 완료!")
    print("=" * 70)
    
    print("\n💡 다음 단계:")
    print("   - 실전투자: 09:00에 자동 매수 실행")
    print("   - 모의투자: --test 플래그로 실행")
    print("   - 수동 매수: execute_buy_orders() 함수 호출")


if __name__ == "__main__":
    main()
