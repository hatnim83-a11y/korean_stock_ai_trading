"""
formatter.py - 포트폴리오 출력 포맷팅 모듈

이 파일은 포트폴리오 정보를 보기 좋게 포맷팅합니다.

주요 기능:
- 콘솔 출력 포맷
- 텔레그램 메시지 포맷
- CSV/JSON 출력
- 리포트 생성

사용법:
    from modules.portfolio_optimizer.formatter import (
        display_portfolio,
        format_portfolio_for_telegram,
        generate_portfolio_summary
    )
    
    display_portfolio(portfolio)
    message = format_portfolio_for_telegram(portfolio)
"""

import json
from datetime import datetime, date
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst


# ===== 콘솔 출력 =====

def display_portfolio(
    portfolio: dict,
    show_details: bool = True
) -> None:
    """
    포트폴리오 콘솔 출력
    
    Args:
        portfolio: 최적화된 포트폴리오
        show_details: 상세 정보 표시 여부
    """
    positions = portfolio.get("positions", [])
    
    print()
    print("=" * 80)
    print("📊 최적화 포트폴리오")
    print("=" * 80)
    print(f"  날짜: {portfolio.get('date', str(now_kst().date()))}")
    print(f"  전략: {portfolio.get('strategy', 'score_based')}")
    print()
    
    # 요약 정보
    print("💰 자본 배분")
    print("-" * 40)
    print(f"  총 자본금:   {portfolio.get('capital', 0):>15,}원")
    print(f"  투자 가능:   {portfolio.get('investable', 0):>15,}원")
    print(f"  투자 금액:   {portfolio.get('total_invested', 0):>15,}원")
    print(f"  잔여 현금:   {portfolio.get('cash_remaining', 0):>15,}원")
    print()
    
    # 리스크 정보
    if "total_risk" in portfolio:
        risk_status = "✅ 적정" if portfolio.get("risk_ok") else "⚠️ 주의"
        print("⚠️ 리스크")
        print("-" * 40)
        print(f"  총 리스크:   {portfolio.get('total_risk', 0):>15,}원")
        print(f"  리스크 상태: {risk_status:>15}")
        print()
    
    # 포지션 목록
    if positions:
        print("📈 포지션 목록")
        print("-" * 80)
        
        # 헤더
        header = (
            f"{'No.':<4} {'종목명':<12} {'테마':<10} "
            f"{'비중':>7} {'수량':>6} {'금액':>12} "
            f"{'손절':>7} {'익절':>7} {'R/R':>5}"
        )
        print(header)
        print("-" * 80)
        
        # 각 포지션
        for i, pos in enumerate(positions, 1):
            name = pos.get('name', '')[:10]  # 10자 제한
            theme = pos.get('theme', '')[:8]  # 8자 제한
            
            row = (
                f"{i:<4} {name:<12} {theme:<10} "
                f"{pos.get('weight', 0):>6.1%} "
                f"{pos.get('shares', 0):>5}주 "
                f"{pos.get('amount', 0):>11,}원 "
                f"{pos.get('stop_loss_pct', 0):>6.1%} "
                f"{pos.get('take_profit_pct', 0):>6.1%} "
                f"1:{pos.get('risk_reward_ratio', 0):.1f}"
            )
            print(row)
        
        print("-" * 80)
        print(f"  총 {len(positions)}개 종목")
    
    # 상세 정보
    if show_details and positions:
        print()
        print("📋 포지션 상세")
        print("-" * 80)
        
        for i, pos in enumerate(positions, 1):
            print(f"\n[{i}] {pos.get('name')} ({pos.get('code')})")
            print(f"    테마: {pos.get('theme')}")
            print(f"    현재가: {pos.get('price', 0):,}원")
            print(f"    매수 수량: {pos.get('shares', 0)}주")
            print(f"    투자 금액: {pos.get('amount', 0):,}원 (비중: {pos.get('weight', 0):.1%})")
            print(f"    손절가: {pos.get('stop_loss_price', 0):,}원 ({pos.get('stop_loss_pct', 0):.1%})")
            print(f"    익절가: {pos.get('take_profit_price', 0):,}원 ({pos.get('take_profit_pct', 0):.1%})")
            print(f"    최종 점수: {pos.get('final_score', 0):.1f}")
            print(f"    AI 감성: {pos.get('ai_sentiment', 0):.1f}/10")
    
    print()
    print("=" * 80)


def display_orders(orders: list[dict]) -> None:
    """
    매수 주문 리스트 콘솔 출력
    
    Args:
        orders: 매수 주문 리스트
    """
    print()
    print("=" * 60)
    print("🛒 매수 주문 리스트")
    print("=" * 60)
    
    if not orders:
        print("  주문 없음")
        return
    
    print(f"{'No.':<4} {'종목명':<12} {'유형':<6} {'수량':>6} {'금액':>12}")
    print("-" * 60)
    
    total_amount = 0
    
    for i, order in enumerate(orders, 1):
        name = order.get('stock_name', '')[:10]
        order_type = "시장가" if order.get('order_type') == 'market' else "지정가"
        
        row = (
            f"{i:<4} {name:<12} {order_type:<6} "
            f"{order.get('quantity', 0):>5}주 "
            f"{order.get('amount', 0):>11,}원"
        )
        print(row)
        total_amount += order.get('amount', 0)
    
    print("-" * 60)
    print(f"  총 {len(orders)}건, 합계: {total_amount:,}원")
    print("=" * 60)


# ===== 텔레그램 포맷 =====

def format_portfolio_for_telegram(
    portfolio: dict,
    include_details: bool = True
) -> str:
    """
    텔레그램 메시지용 포트폴리오 포맷
    
    Args:
        portfolio: 최적화된 포트폴리오
        include_details: 상세 정보 포함 여부
    
    Returns:
        마크다운 형식 문자열
    """
    positions = portfolio.get("positions", [])
    today = portfolio.get("date", str(now_kst().date()))
    
    # 헤더
    lines = [
        f"📊 *포트폴리오 최적화 완료*",
        f"📅 {today}",
        ""
    ]
    
    # 요약
    lines.extend([
        "💰 *자본 배분*",
        f"```",
        f"총 자본금: {portfolio.get('capital', 0):>12,}원",
        f"투자 금액: {portfolio.get('total_invested', 0):>12,}원",
        f"잔여 현금: {portfolio.get('cash_remaining', 0):>12,}원",
        f"```",
        ""
    ])
    
    # 포지션 목록
    if positions:
        lines.append(f"📈 *포지션 ({len(positions)}개)*")
        lines.append("```")
        
        for i, pos in enumerate(positions, 1):
            name = pos.get('name', '')[:8]
            lines.append(
                f"{i}. {name}: "
                f"{pos.get('shares', 0)}주 ({pos.get('weight', 0):.0%})"
            )
        
        lines.append("```")
        lines.append("")
    
    # 상세 정보
    if include_details and positions:
        lines.append("📋 *상세 정보*")
        
        for pos in positions[:5]:  # 상위 5개만
            lines.extend([
                f"",
                f"*{pos.get('name')}* ({pos.get('code')})",
                f"└ 💵 {pos.get('price', 0):,}원 × {pos.get('shares', 0)}주",
                f"└ 🔻 손절: {pos.get('stop_loss_pct', 0):.1%}",
                f"└ 🔺 익절: {pos.get('take_profit_pct', 0):.1%}",
                f"└ 🎯 점수: {pos.get('final_score', 0):.1f}"
            ])
        
        if len(positions) > 5:
            lines.append(f"\n... 외 {len(positions) - 5}개 종목")
    
    # 리스크 상태
    risk_status = "✅ 리스크 적정" if portfolio.get("risk_ok", True) else "⚠️ 리스크 주의"
    lines.extend(["", risk_status])
    
    return "\n".join(lines)


def format_orders_for_telegram(orders: list[dict]) -> str:
    """
    텔레그램 메시지용 주문 포맷
    
    Args:
        orders: 매수 주문 리스트
    
    Returns:
        마크다운 형식 문자열
    """
    if not orders:
        return "🛒 *매수 주문 없음*"
    
    lines = [
        f"🛒 *매수 주문 ({len(orders)}건)*",
        f"📅 {now_kst().date()}",
        "",
        "```"
    ]
    
    total_amount = 0
    
    for i, order in enumerate(orders, 1):
        name = order.get('stock_name', '')[:8]
        lines.append(
            f"{i}. {name}: "
            f"{order.get('quantity', 0)}주 / {order.get('amount', 0):,}원"
        )
        total_amount += order.get('amount', 0)
    
    lines.extend([
        "```",
        "",
        f"💰 *총 주문금액: {total_amount:,}원*"
    ])
    
    return "\n".join(lines)


# ===== 요약 생성 =====

def generate_portfolio_summary(portfolio: dict) -> dict:
    """
    포트폴리오 요약 정보 생성
    
    Args:
        portfolio: 최적화된 포트폴리오
    
    Returns:
        요약 딕셔너리
    """
    positions = portfolio.get("positions", [])
    
    # 테마별 분류
    themes = {}
    for pos in positions:
        theme = pos.get("theme", "기타")
        if theme not in themes:
            themes[theme] = {"count": 0, "amount": 0}
        themes[theme]["count"] += 1
        themes[theme]["amount"] += pos.get("amount", 0)
    
    # 평균 점수
    scores = [p.get("final_score", 0) for p in positions if p.get("final_score")]
    avg_score = sum(scores) / len(scores) if scores else 0
    
    # 평균 손절/익절
    stop_losses = [p.get("stop_loss_pct", 0) for p in positions]
    take_profits = [p.get("take_profit_pct", 0) for p in positions]
    
    avg_stop_loss = sum(stop_losses) / len(stop_losses) if stop_losses else 0
    avg_take_profit = sum(take_profits) / len(take_profits) if take_profits else 0
    
    return {
        "date": portfolio.get("date"),
        "capital": portfolio.get("capital", 0),
        "total_invested": portfolio.get("total_invested", 0),
        "cash_remaining": portfolio.get("cash_remaining", 0),
        "position_count": len(positions),
        "themes": themes,
        "avg_final_score": round(avg_score, 1),
        "avg_stop_loss_pct": round(avg_stop_loss, 4),
        "avg_take_profit_pct": round(avg_take_profit, 4),
        "strategy": portfolio.get("strategy"),
        "risk_ok": portfolio.get("risk_ok", True)
    }


# ===== JSON/CSV 출력 =====

def export_portfolio_to_json(
    portfolio: dict,
    filepath: Optional[str] = None
) -> str:
    """
    포트폴리오를 JSON으로 내보내기
    
    Args:
        portfolio: 포트폴리오 데이터
        filepath: 저장 경로 (없으면 문자열 반환)
    
    Returns:
        JSON 문자열
    """
    json_str = json.dumps(portfolio, ensure_ascii=False, indent=2)
    
    if filepath:
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(json_str)
            logger.info(f"포트폴리오 JSON 저장: {filepath}")
        except Exception as e:
            logger.error(f"JSON 저장 실패: {e}")
    
    return json_str


def export_positions_to_csv(
    positions: list[dict],
    filepath: str
) -> bool:
    """
    포지션 리스트를 CSV로 내보내기
    
    Args:
        positions: 포지션 리스트
        filepath: 저장 경로
    
    Returns:
        저장 성공 여부
    """
    if not positions:
        logger.warning("내보낼 포지션이 없습니다")
        return False
    
    try:
        # 헤더
        headers = [
            "code", "name", "theme", "price", "shares", "amount",
            "weight", "stop_loss_price", "stop_loss_pct",
            "take_profit_price", "take_profit_pct",
            "final_score", "ai_sentiment"
        ]
        
        with open(filepath, 'w', encoding='utf-8-sig') as f:
            # 헤더 작성
            f.write(','.join(headers) + '\n')
            
            # 데이터 작성
            for pos in positions:
                row = [str(pos.get(h, '')) for h in headers]
                f.write(','.join(row) + '\n')
        
        logger.info(f"포지션 CSV 저장: {filepath}")
        return True
        
    except Exception as e:
        logger.error(f"CSV 저장 실패: {e}")
        return False


# ===== 리포트 생성 =====

def generate_optimization_report(
    portfolio: dict,
    orders: list[dict]
) -> str:
    """
    최적화 결과 전체 리포트 생성
    
    Args:
        portfolio: 최적화된 포트폴리오
        orders: 매수 주문 리스트
    
    Returns:
        리포트 문자열
    """
    summary = generate_portfolio_summary(portfolio)
    positions = portfolio.get("positions", [])
    
    report = []
    
    # 헤더
    report.append("=" * 70)
    report.append("📊 포트폴리오 최적화 리포트")
    report.append("=" * 70)
    report.append(f"생성 일시: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"최적화 전략: {summary.get('strategy', 'score_based')}")
    report.append("")
    
    # 1. 자본 배분
    report.append("┌─────────────────────────────────────────┐")
    report.append("│ 1. 자본 배분                            │")
    report.append("├─────────────────────────────────────────┤")
    report.append(f"│ 총 자본금:     {summary['capital']:>15,}원 │")
    report.append(f"│ 투자 금액:     {summary['total_invested']:>15,}원 │")
    report.append(f"│ 잔여 현금:     {summary['cash_remaining']:>15,}원 │")
    report.append(f"│ 종목 수:       {summary['position_count']:>15}개 │")
    report.append("└─────────────────────────────────────────┘")
    report.append("")
    
    # 2. 테마별 분류
    report.append("┌─────────────────────────────────────────┐")
    report.append("│ 2. 테마별 배분                          │")
    report.append("├─────────────────────────────────────────┤")
    for theme, info in summary.get("themes", {}).items():
        pct = info['amount'] / summary['total_invested'] * 100 if summary['total_invested'] > 0 else 0
        report.append(f"│ {theme:<12} {info['count']}개 / {info['amount']:>10,}원 ({pct:.0f}%) │")
    report.append("└─────────────────────────────────────────┘")
    report.append("")
    
    # 3. 포지션 목록
    report.append("┌" + "─" * 78 + "┐")
    report.append("│ 3. 포지션 상세" + " " * 62 + "│")
    report.append("├" + "─" * 78 + "┤")
    report.append(f"│ {'No.':<3} {'종목':<10} {'가격':>10} {'수량':>6} {'금액':>12} {'손절':>7} {'익절':>7} {'점수':>6} │")
    report.append("├" + "─" * 78 + "┤")
    
    for i, pos in enumerate(positions, 1):
        name = pos.get('name', '')[:8]
        report.append(
            f"│ {i:<3} {name:<10} "
            f"{pos.get('price', 0):>9,} "
            f"{pos.get('shares', 0):>5}주 "
            f"{pos.get('amount', 0):>11,} "
            f"{pos.get('stop_loss_pct', 0):>6.1%} "
            f"{pos.get('take_profit_pct', 0):>6.1%} "
            f"{pos.get('final_score', 0):>5.1f} │"
        )
    
    report.append("└" + "─" * 78 + "┘")
    report.append("")
    
    # 4. 통계
    report.append("┌─────────────────────────────────────────┐")
    report.append("│ 4. 통계                                 │")
    report.append("├─────────────────────────────────────────┤")
    report.append(f"│ 평균 최종 점수:  {summary['avg_final_score']:>18.1f} │")
    report.append(f"│ 평균 손절률:     {summary['avg_stop_loss_pct']:>17.1%} │")
    report.append(f"│ 평균 익절률:     {summary['avg_take_profit_pct']:>17.1%} │")
    risk_status = "✅ 적정" if summary.get("risk_ok") else "⚠️ 주의"
    report.append(f"│ 리스크 상태:     {risk_status:>18} │")
    report.append("└─────────────────────────────────────────┘")
    report.append("")
    
    # 5. 매수 주문
    report.append("┌─────────────────────────────────────────┐")
    report.append("│ 5. 매수 주문                            │")
    report.append("├─────────────────────────────────────────┤")
    for i, order in enumerate(orders, 1):
        name = order.get('stock_name', '')[:10]
        report.append(f"│ {i}. {name:<12} {order.get('quantity', 0):>5}주 / {order.get('amount', 0):>10,}원 │")
    report.append("└─────────────────────────────────────────┘")
    report.append("")
    
    report.append("=" * 70)
    report.append("리포트 끝")
    report.append("=" * 70)
    
    return "\n".join(report)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    # 테스트 포트폴리오
    test_portfolio = {
        "date": str(date.today()),
        "capital": 10_000_000,
        "investable": 9_500_000,
        "total_invested": 9_200_000,
        "cash_remaining": 800_000,
        "position_count": 5,
        "strategy": "score_based",
        "risk_ok": True,
        "total_risk": 450_000,
        "positions": [
            {
                "code": "005930", "name": "삼성전자", "theme": "AI반도체",
                "price": 75000, "shares": 26, "amount": 1_950_000,
                "weight": 0.205, "actual_weight": 0.205,
                "stop_loss_price": 69000, "stop_loss_pct": -0.08,
                "take_profit_price": 87000, "take_profit_pct": 0.16,
                "risk_reward_ratio": 2.0,
                "final_score": 87.5, "ai_sentiment": 7.8
            },
            {
                "code": "373220", "name": "LG에너지솔루션", "theme": "2차전지",
                "price": 420000, "shares": 4, "amount": 1_680_000,
                "weight": 0.177, "actual_weight": 0.177,
                "stop_loss_price": 386000, "stop_loss_pct": -0.08,
                "take_profit_price": 487000, "take_profit_pct": 0.16,
                "risk_reward_ratio": 2.0,
                "final_score": 82.0, "ai_sentiment": 7.2
            },
            {
                "code": "000660", "name": "SK하이닉스", "theme": "AI반도체",
                "price": 195000, "shares": 10, "amount": 1_950_000,
                "weight": 0.205, "actual_weight": 0.205,
                "stop_loss_price": 179000, "stop_loss_pct": -0.08,
                "take_profit_price": 226000, "take_profit_pct": 0.16,
                "risk_reward_ratio": 2.0,
                "final_score": 85.0, "ai_sentiment": 7.5
            },
            {
                "code": "006400", "name": "삼성SDI", "theme": "2차전지",
                "price": 350000, "shares": 5, "amount": 1_750_000,
                "weight": 0.184, "actual_weight": 0.184,
                "stop_loss_price": 322000, "stop_loss_pct": -0.08,
                "take_profit_price": 406000, "take_profit_pct": 0.16,
                "risk_reward_ratio": 2.0,
                "final_score": 78.0, "ai_sentiment": 6.8
            },
            {
                "code": "051910", "name": "LG화학", "theme": "2차전지",
                "price": 310000, "shares": 6, "amount": 1_860_000,
                "weight": 0.196, "actual_weight": 0.196,
                "stop_loss_price": 285000, "stop_loss_pct": -0.08,
                "take_profit_price": 360000, "take_profit_pct": 0.16,
                "risk_reward_ratio": 2.0,
                "final_score": 75.5, "ai_sentiment": 6.5
            }
        ]
    }
    
    # 테스트 주문
    test_orders = [
        {"stock_code": "005930", "stock_name": "삼성전자", "order_type": "market", "quantity": 26, "amount": 1_950_000},
        {"stock_code": "373220", "stock_name": "LG에너지솔루션", "order_type": "market", "quantity": 4, "amount": 1_680_000},
        {"stock_code": "000660", "stock_name": "SK하이닉스", "order_type": "market", "quantity": 10, "amount": 1_950_000},
        {"stock_code": "006400", "stock_name": "삼성SDI", "order_type": "market", "quantity": 5, "amount": 1_750_000},
        {"stock_code": "051910", "stock_name": "LG화학", "order_type": "market", "quantity": 6, "amount": 1_860_000}
    ]
    
    # 콘솔 출력 테스트
    print("\n" + "=" * 80)
    print("1️⃣ 콘솔 출력 테스트")
    print("=" * 80)
    display_portfolio(test_portfolio, show_details=False)
    
    # 주문 출력 테스트
    display_orders(test_orders)
    
    # 텔레그램 포맷 테스트
    print("\n" + "=" * 80)
    print("2️⃣ 텔레그램 포맷 테스트")
    print("=" * 80)
    telegram_msg = format_portfolio_for_telegram(test_portfolio, include_details=False)
    print(telegram_msg)
    
    # 요약 테스트
    print("\n" + "=" * 80)
    print("3️⃣ 요약 정보 테스트")
    print("=" * 80)
    summary = generate_portfolio_summary(test_portfolio)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    # 리포트 테스트
    print("\n" + "=" * 80)
    print("4️⃣ 전체 리포트 테스트")
    print("=" * 80)
    report = generate_optimization_report(test_portfolio, test_orders)
    print(report)
    
    print("\n✅ 포맷터 테스트 완료!")
