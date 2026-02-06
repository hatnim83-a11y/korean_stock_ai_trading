"""
rebalancer.py - 일일 리밸런싱 모듈

이 파일은 포트폴리오 리밸런싱 기능을 제공합니다.

주요 기능:
- 청산된 포지션 확인
- 새 후보 종목 탐색
- 리밸런싱 결정
- 매도/매수 주문 생성

리밸런싱 규칙:
1. 익절/손절로 청산된 종목 자리에 새 종목 편입
2. 수급 이탈 종목 매도 후 대체
3. 최대 보유 종목 수 유지

사용법:
    from modules.rebalancer.rebalancer import Rebalancer
    
    rebalancer = Rebalancer()
    result = rebalancer.daily_rebalancing()
"""

from datetime import datetime, date, timedelta
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings
from database import Database


# ===== 상수 정의 (settings에서 로드) =====
MAX_POSITIONS = settings.MAX_POSITIONS  # 최대 보유 종목 수
MIN_CASH_RATIO = 0.05  # 최소 현금 비율 (고정)
SUPPLY_EXIT_THRESHOLD = -30  # 수급 이탈 기준 (외국인+기관 순매도 30억, 고정)


class Rebalancer:
    """
    포트폴리오 리밸런서
    
    청산된 포지션을 새 종목으로 대체하고
    수급 이탈 종목을 관리합니다.
    
    Attributes:
        db: 데이터베이스 연결
        max_positions: 최대 보유 종목 수
        
    Example:
        >>> rebalancer = Rebalancer()
        >>> result = rebalancer.daily_rebalancing()
        >>> print(f"매도: {result['sell_count']}건, 매수: {result['buy_count']}건")
    """
    
    def __init__(
        self,
        max_positions: int = MAX_POSITIONS
    ):
        """
        리밸런서 초기화
        
        Args:
            max_positions: 최대 보유 종목 수
        """
        self.max_positions = max_positions
        self.db: Optional[Database] = None
        
        logger.info(f"리밸런서 초기화 (최대 {max_positions}종목)")
    
    # ===== 리밸런싱 실행 =====
    
    def daily_rebalancing(
        self,
        current_positions: Optional[list[dict]] = None,
        new_candidates: Optional[list[dict]] = None,
        cash_available: float = 0,
        use_mock: bool = False
    ) -> dict:
        """
        일일 리밸런싱 실행
        
        Args:
            current_positions: 현재 보유 포지션
            new_candidates: 새 후보 종목 (AI 검증 완료)
            cash_available: 가용 현금
            use_mock: 모의 모드
        
        Returns:
            {
                'date': '2025-02-05',
                'sell_orders': [...],
                'buy_orders': [...],
                'sell_count': 2,
                'buy_count': 2,
                'rebalanced': True
            }
        """
        logger.info("=" * 60)
        logger.info("📊 일일 리밸런싱 시작")
        logger.info(f"   현재 포지션: {len(current_positions or [])}개")
        logger.info(f"   새 후보: {len(new_candidates or [])}개")
        logger.info("=" * 60)
        
        # 현재 포지션 로드
        if current_positions is None:
            current_positions = self._load_current_positions()
        
        # 1. 청산 필요 종목 확인
        to_sell = self._find_positions_to_sell(current_positions)
        
        # 2. 빈 슬롯 계산
        empty_slots = self.max_positions - (len(current_positions) - len(to_sell))
        
        # 3. 새 매수 종목 선정
        to_buy = []
        if new_candidates and empty_slots > 0:
            to_buy = self._select_new_positions(
                new_candidates,
                current_positions,
                empty_slots,
                cash_available
            )
        
        # 4. 주문 생성
        sell_orders = self._generate_sell_orders(to_sell)
        buy_orders = self._generate_buy_orders(to_buy, cash_available)
        
        result = {
            "date": str(date.today()),
            "sell_orders": sell_orders,
            "buy_orders": buy_orders,
            "sell_count": len(sell_orders),
            "buy_count": len(buy_orders),
            "rebalanced": len(sell_orders) > 0 or len(buy_orders) > 0
        }
        
        # 리밸런싱 내역 로깅
        logger.info(f"\n📋 리밸런싱 결과:")
        logger.info(f"   매도: {len(sell_orders)}건")
        for order in sell_orders:
            logger.info(f"      - {order.get('stock_name')}: {order.get('reason')}")
        
        logger.info(f"   매수: {len(buy_orders)}건")
        for order in buy_orders:
            logger.info(f"      - {order.get('stock_name')}")
        
        return result
    
    # ===== 포지션 분석 =====
    
    def _load_current_positions(self) -> list[dict]:
        """DB에서 현재 포지션 로드"""
        try:
            db = Database()
            db.connect()
            positions = db.get_portfolio(status="holding")
            db.close()
            return positions
        except Exception as e:
            logger.error(f"포지션 로드 실패: {e}")
            return []
    
    def _find_positions_to_sell(
        self,
        positions: list[dict]
    ) -> list[dict]:
        """
        매도 대상 종목 찾기
        
        매도 조건:
        1. 이미 청산된 포지션 (status='closed')
        2. 수급 이탈 (외국인+기관 대량 순매도)
        3. 보유 기간 초과 (7일 이상)
        
        Args:
            positions: 현재 포지션
        
        Returns:
            매도 대상 리스트
        """
        to_sell = []
        today = date.today()
        
        for pos in positions:
            sell_reason = None
            
            # 1. 이미 청산된 포지션
            if pos.get("status") == "closed":
                sell_reason = "청산 완료"
            
            # 2. 수급 이탈 체크
            supply_score = pos.get("supply_score", 0)
            if supply_score < SUPPLY_EXIT_THRESHOLD:
                sell_reason = f"수급 이탈 ({supply_score:.0f}억)"
            
            # 3. 보유 기간 체크 (손실 시 최대 보유 기간 초과)
            buy_date = pos.get("date")
            if buy_date:
                try:
                    if isinstance(buy_date, str):
                        buy_date = datetime.strptime(buy_date, "%Y-%m-%d").date()
                    holding_days = (today - buy_date).days
                    if holding_days > settings.MAX_HOLD_DAYS_LOSS:
                        # 수익 중이면 유지, 손실 중이면 매도 고려
                        profit_rate = pos.get("profit_rate", 0)
                        if profit_rate < 0:
                            sell_reason = f"보유 기간 초과 ({holding_days}일, 손실 중)"
                except (ValueError, TypeError) as e:
                    logger.debug(f"보유 기간 계산 실패: {e}")
            
            if sell_reason:
                pos["sell_reason"] = sell_reason
                to_sell.append(pos)
                logger.info(f"매도 대상: {pos.get('stock_name')} - {sell_reason}")
        
        return to_sell
    
    def _select_new_positions(
        self,
        candidates: list[dict],
        current_positions: list[dict],
        max_count: int,
        cash_available: float
    ) -> list[dict]:
        """
        새 매수 종목 선정
        
        선정 기준:
        1. 현재 보유하지 않은 종목
        2. 최종 점수 높은 순
        3. 가용 현금 범위 내
        
        Args:
            candidates: 후보 종목
            current_positions: 현재 보유 종목
            max_count: 최대 선정 수
            cash_available: 가용 현금
        
        Returns:
            선정된 종목 리스트
        """
        # 현재 보유 종목 코드
        current_codes = {p.get("stock_code") for p in current_positions}
        
        # 필터링: 중복 제외
        filtered = [c for c in candidates if c.get("code") not in current_codes]
        
        # 점수순 정렬
        sorted_candidates = sorted(
            filtered,
            key=lambda x: x.get("final_score", 0),
            reverse=True
        )
        
        # 선정
        selected = []
        remaining_cash = cash_available
        
        for candidate in sorted_candidates:
            if len(selected) >= max_count:
                break
            
            # 투자 금액 계산
            price = candidate.get("price", 0)
            target_amount = remaining_cash / (max_count - len(selected))
            
            if price > 0 and target_amount >= price:
                shares = int(target_amount / price)
                if shares > 0:
                    candidate["shares"] = shares
                    candidate["amount"] = shares * price
                    selected.append(candidate)
                    remaining_cash -= candidate["amount"]
        
        logger.info(f"새 종목 {len(selected)}개 선정")
        return selected
    
    # ===== 주문 생성 =====
    
    def _generate_sell_orders(
        self,
        positions: list[dict]
    ) -> list[dict]:
        """매도 주문 생성"""
        orders = []
        
        for pos in positions:
            order = {
                "stock_code": pos.get("stock_code"),
                "stock_name": pos.get("stock_name"),
                "quantity": pos.get("shares", 0),
                "order_type": "market",
                "reason": pos.get("sell_reason", "리밸런싱")
            }
            orders.append(order)
        
        return orders
    
    def _generate_buy_orders(
        self,
        positions: list[dict],
        cash_available: float
    ) -> list[dict]:
        """매수 주문 생성"""
        orders = []
        
        for pos in positions:
            order = {
                "stock_code": pos.get("code"),
                "stock_name": pos.get("name"),
                "quantity": pos.get("shares", 0),
                "price": pos.get("price", 0),
                "amount": pos.get("amount", 0),
                "order_type": "market",
                "stop_loss": pos.get("stop_loss_price"),
                "take_profit": pos.get("take_profit_price"),
                "theme": pos.get("theme"),
                "final_score": pos.get("final_score")
            }
            orders.append(order)
        
        return orders
    
    # ===== 수급 체크 =====
    
    def check_supply_exit(
        self,
        position: dict,
        supply_data: dict
    ) -> bool:
        """
        수급 이탈 여부 체크
        
        Args:
            position: 포지션 정보
            supply_data: 수급 데이터 (외국인/기관 순매수)
        
        Returns:
            이탈 여부
        """
        foreign_net = supply_data.get("foreign_net", 0)
        institution_net = supply_data.get("institution_net", 0)
        
        # 억원 단위로 변환
        total_net = (foreign_net + institution_net) / 100_000_000
        
        if total_net < SUPPLY_EXIT_THRESHOLD:
            logger.warning(f"수급 이탈 감지: {position.get('stock_name')}")
            logger.warning(f"   외국인: {foreign_net/100_000_000:.1f}억, 기관: {institution_net/100_000_000:.1f}억")
            return True
        
        return False
    
    # ===== 리밸런싱 요약 =====
    
    def get_rebalancing_summary(
        self,
        result: dict
    ) -> str:
        """
        리밸런싱 요약 텍스트 생성
        
        Args:
            result: 리밸런싱 결과
        
        Returns:
            요약 문자열
        """
        lines = [
            f"📊 *리밸런싱 결과* ({result.get('date')})",
            ""
        ]
        
        # 매도
        sell_orders = result.get("sell_orders", [])
        if sell_orders:
            lines.append(f"📉 *매도* ({len(sell_orders)}건)")
            for order in sell_orders:
                lines.append(f"  - {order.get('stock_name')}: {order.get('reason')}")
            lines.append("")
        
        # 매수
        buy_orders = result.get("buy_orders", [])
        if buy_orders:
            lines.append(f"📈 *매수* ({len(buy_orders)}건)")
            for order in buy_orders:
                lines.append(f"  - {order.get('stock_name')}: {order.get('quantity')}주")
            lines.append("")
        
        if not sell_orders and not buy_orders:
            lines.append("✅ 리밸런싱 없음 (포트폴리오 유지)")
        
        return "\n".join(lines)


# ===== __init__.py 용 =====

def run_daily_rebalancing(
    current_positions: Optional[list[dict]] = None,
    new_candidates: Optional[list[dict]] = None,
    cash_available: float = 0
) -> dict:
    """
    일일 리밸런싱 실행 (편의 함수)
    
    Args:
        current_positions: 현재 포지션
        new_candidates: 새 후보 종목
        cash_available: 가용 현금
    
    Returns:
        리밸런싱 결과
    """
    rebalancer = Rebalancer()
    return rebalancer.daily_rebalancing(
        current_positions=current_positions,
        new_candidates=new_candidates,
        cash_available=cash_available
    )


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 리밸런서 테스트")
    print("=" * 60)
    
    rebalancer = Rebalancer()
    
    # 테스트 현재 포지션
    current_positions = [
        {
            "stock_code": "005930", "stock_name": "삼성전자",
            "shares": 10, "buy_price": 75000, "status": "holding",
            "date": "2025-01-25", "profit_rate": -3.0, "supply_score": -35
        },
        {
            "stock_code": "000660", "stock_name": "SK하이닉스",
            "shares": 5, "buy_price": 195000, "status": "holding",
            "date": "2025-02-03", "profit_rate": 2.5, "supply_score": 20
        },
    ]
    
    # 테스트 후보 종목
    new_candidates = [
        {
            "code": "373220", "name": "LG에너지솔루션", "theme": "2차전지",
            "price": 420000, "final_score": 85
        },
        {
            "code": "006400", "name": "삼성SDI", "theme": "2차전지",
            "price": 350000, "final_score": 78
        },
    ]
    
    print(f"현재 포지션: {len(current_positions)}개")
    print(f"후보 종목: {len(new_candidates)}개")
    
    # 리밸런싱 실행
    result = rebalancer.daily_rebalancing(
        current_positions=current_positions,
        new_candidates=new_candidates,
        cash_available=2_000_000
    )
    
    print(f"\n결과:")
    print(f"  - 매도: {result['sell_count']}건")
    print(f"  - 매수: {result['buy_count']}건")
    
    # 요약 출력
    print("\n" + rebalancer.get_rebalancing_summary(result))
    
    print("\n" + "=" * 60)
    print("✅ 리밸런서 테스트 완료!")
    print("=" * 60)
