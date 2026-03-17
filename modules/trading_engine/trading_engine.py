"""
trading_engine.py - 자동 매매 실행 엔진

이 파일은 포트폴리오 매수/매도 주문을 자동 실행합니다.

주요 기능:
- 포트폴리오 매수 실행
- 주문 상태 확인
- 체결 완료 대기
- 실패 주문 재시도
- 매매 기록 저장

사용법:
    from modules.trading_engine.trading_engine import TradingEngine
    
    engine = TradingEngine()
    result = engine.execute_portfolio(orders)
"""

import time
from datetime import datetime, date
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst
from database import Database
from modules.trading_engine.kis_order_api import KISOrderApi, MockOrderApi


# ===== 상수 정의 =====
MAX_RETRY = 3  # 최대 재시도 횟수
ORDER_CHECK_INTERVAL = 2  # 주문 확인 간격 (초)
ORDER_TIMEOUT = 60  # 주문 타임아웃 (초)
UPPER_LIMIT_RATIO = 1.3  # 시장가 주문 상한가 증거금 배수 (+30%)
RETRY_QTY_REDUCTION = 0.9  # 재시도 시 수량 감소 비율 (10% 감소)


class TradingEngine:
    """
    자동 매매 실행 엔진
    
    포트폴리오 매수/매도를 자동으로 실행합니다.
    
    Attributes:
        is_mock: 모의투자 여부
        order_api: 주문 API 클라이언트
        db: 데이터베이스 연결
        
    Example:
        >>> engine = TradingEngine()
        >>> result = engine.execute_portfolio(orders)
        >>> print(f"체결: {result['filled_count']}건")
    """
    
    def __init__(
        self,
        is_mock: Optional[bool] = None,
        use_mock_api: bool = False
    ):
        """
        매매 엔진 초기화
        
        Args:
            is_mock: 모의투자 여부 (KIS API)
            use_mock_api: 모의 API 사용 (테스트용)
        """
        self.is_mock = is_mock if is_mock is not None else settings.IS_MOCK
        self.use_mock_api = use_mock_api
        
        # 주문 API 초기화
        if use_mock_api:
            self.order_api = MockOrderApi()
            logger.info("매매 엔진 초기화 (모의 API)")
        else:
            self.order_api = KISOrderApi(is_mock=self.is_mock)
            mode = "모의투자" if self.is_mock else "실전투자"
            logger.info(f"매매 엔진 초기화 ({mode})")
        
        # 데이터베이스
        self.db: Optional[Database] = None
        
        # 실행 결과
        self.execution_results: list[dict] = []
    
    # ===== 포트폴리오 실행 =====
    
    def execute_portfolio(
        self,
        orders: list[dict],
        save_to_db: bool = True,
        wait_for_fill: bool = True
    ) -> dict:
        """
        포트폴리오 매수 실행
        
        Args:
            orders: 주문 리스트
                [
                    {
                        'stock_code': '005930',
                        'stock_name': '삼성전자',
                        'quantity': 10,
                        'order_type': 'market',
                        'price': 0
                    },
                    ...
                ]
            save_to_db: DB 저장 여부
            wait_for_fill: 체결 대기 여부
        
        Returns:
            {
                'success': True,
                'total_orders': 5,
                'filled_count': 5,
                'failed_count': 0,
                'total_amount': 9200000,
                'orders': [...],
                'execution_time': 3.5
            }
        """
        logger.info("=" * 60)
        logger.info("📊 포트폴리오 매수 실행")
        logger.info(f"   주문 수: {len(orders)}건")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        if not orders:
            logger.warning("실행할 주문이 없습니다")
            return {
                "success": False,
                "total_orders": 0,
                "filled_count": 0,
                "failed_count": 0,
                "message": "주문 없음"
            }
        
        # 1. 주문 실행
        executed_orders = self._execute_buy_orders(orders)
        
        # 2. 체결 확인
        if wait_for_fill and not self.use_mock_api:
            self._wait_for_fills(executed_orders)
        
        # 3. 결과 집계
        filled_count = sum(1 for o in executed_orders if o.get("filled", False) or o.get("success", False))
        failed_count = len(executed_orders) - filled_count
        total_amount = sum(o.get("amount", 0) for o in executed_orders if o.get("success"))
        
        execution_time = time.time() - start_time
        
        # 4. DB 저장
        if save_to_db:
            self._save_trades(executed_orders)
            self._save_positions(executed_orders)
        
        result = {
            "success": failed_count == 0,
            "total_orders": len(orders),
            "filled_count": filled_count,
            "failed_count": failed_count,
            "total_amount": total_amount,
            "orders": executed_orders,
            "execution_time": round(execution_time, 2)
        }
        
        logger.info(f"\n✅ 포트폴리오 실행 완료")
        logger.info(f"   체결: {filled_count}건, 실패: {failed_count}건")
        logger.info(f"   총 금액: {total_amount:,}원")
        logger.info(f"   소요 시간: {execution_time:.1f}초")
        
        return result
    
    def _execute_buy_orders(self, orders: list[dict]) -> list[dict]:
        """매수 주문 실행"""
        results = []

        # 주문 직전 실제 주문가능현금 재확인
        orderable_cash = 0
        if hasattr(self.order_api, 'get_orderable_cash'):
            orderable_cash = self.order_api.get_orderable_cash()
            logger.info(f"💰 주문 직전 가용현금 재확인: {orderable_cash:,}원")

        for i, order in enumerate(orders, 1):
            stock_code = order.get("stock_code", "")
            stock_name = order.get("stock_name", stock_code)
            quantity = order.get("quantity", 0)
            order_type = order.get("order_type", "market")
            price = order.get("price", 0)
            # 시장가 주문은 price=0이므로 expected_price(스크리닝 시점 가격) 사용
            expected_price = order.get("expected_price", price) or price

            logger.info(f"\n[{i}/{len(orders)}] 매수: {stock_name} ({stock_code})")
            logger.info(f"   수량: {quantity}주")

            # 시장가 주문 시 상한가 기준 사전 검증
            if (order_type == "market" or price == 0) and orderable_cash > 0 and expected_price > 0:
                margin_required = quantity * expected_price * UPPER_LIMIT_RATIO  # 상한가 기준 증거금
                if margin_required > orderable_cash:
                    safe_qty = int(orderable_cash / (expected_price * UPPER_LIMIT_RATIO))
                    if safe_qty < 1:
                        logger.warning(f"   ⚠️ 주문가능금액 부족 (필요: {margin_required:,}원, 가용: {orderable_cash:,}원) → 매수 건너뜀")
                        results.append({
                            "success": False,
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "message": "주문가능금액 부족 (상한가 기준)"
                        })
                        continue
                    logger.warning(f"   ⚠️ 상한가 증거금 초과 → 수량 조정: {quantity}주 → {safe_qty}주")
                    quantity = safe_qty

            # 재시도 로직 (실패 시 수량 5%씩 감소)
            for attempt in range(MAX_RETRY):
                try:
                    if order_type == "market" or price == 0:
                        result = self.order_api.buy_market_order(stock_code, quantity)
                    else:
                        result = self.order_api.buy_limit_order(stock_code, quantity, price)

                    if result.get("success"):
                        result.update({
                            "stock_name": stock_name,
                            "amount": order.get("amount", quantity * price),
                            "theme": order.get("theme"),
                            "stop_loss": order.get("stop_loss"),
                            "take_profit": order.get("take_profit")
                        })
                        # 성공 시 가용현금 차감 (다음 종목 계산용)
                        if orderable_cash > 0 and expected_price > 0:
                            orderable_cash = max(0, orderable_cash - quantity * expected_price * UPPER_LIMIT_RATIO)
                        results.append(result)
                        break
                    else:
                        msg = result.get("message", "")
                        # 주문가능금액 초과 → 수량 10% 감소 후 재시도
                        if "주문가능금액" in msg or "초과" in msg:
                            reduced = max(1, int(quantity * RETRY_QTY_REDUCTION))
                            if reduced < quantity:
                                logger.warning(f"   ⚠️ 주문가능금액 초과 → 수량 감소: {quantity}주 → {reduced}주 (재시도 {attempt + 1}/{MAX_RETRY})")
                                quantity = reduced
                                time.sleep(1)
                            else:
                                # 더 이상 줄일 수 없음
                                result["stock_name"] = stock_name
                                results.append(result)
                                break
                        elif attempt < MAX_RETRY - 1:
                            logger.warning(f"   재시도 {attempt + 1}/{MAX_RETRY}")
                            time.sleep(1)
                        else:
                            result["stock_name"] = stock_name
                            results.append(result)

                except Exception as e:
                    logger.error(f"   주문 오류: {e}")
                    if attempt == MAX_RETRY - 1:
                        results.append({
                            "success": False,
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "message": str(e)
                        })
            
            # 주문 간 딜레이
            if i < len(orders):
                time.sleep(0.5)
        
        return results
    
    def _wait_for_fills(
        self,
        orders: list[dict],
        timeout: int = ORDER_TIMEOUT
    ) -> None:
        """체결 완료 대기"""
        if not orders:
            return
        
        pending_orders = [o for o in orders if o.get("success") and not o.get("filled")]
        
        if not pending_orders:
            return
        
        logger.info(f"\n⏳ 체결 대기 중... ({len(pending_orders)}건)")
        
        start_time = time.time()
        
        while pending_orders and (time.time() - start_time) < timeout:
            time.sleep(ORDER_CHECK_INTERVAL)
            
            # 주문 상태 조회
            statuses = self.order_api.get_order_status()
            
            for order in pending_orders[:]:
                order_id = order.get("order_id")
                
                for status in statuses:
                    if status.get("order_id") == order_id:
                        if status.get("filled_qty", 0) >= order.get("quantity", 0):
                            order["filled"] = True
                            order["filled_price"] = status.get("filled_price")
                            pending_orders.remove(order)
                            logger.info(f"   ✅ 체결: {order.get('stock_name')}")
                            break
            
            if pending_orders:
                elapsed = int(time.time() - start_time)
                logger.debug(f"   대기 중... ({elapsed}s)")
        
        if pending_orders:
            logger.warning(f"   ⚠️ 미체결: {len(pending_orders)}건")
    
    # ===== 매도 실행 =====
    
    def execute_sell_orders(
        self,
        orders: list[dict],
        save_to_db: bool = True
    ) -> dict:
        """
        매도 주문 실행

        Args:
            orders: 매도 주문 리스트 (buy_price 포함 시 손익 계산)
            save_to_db: DB 저장 여부

        Returns:
            실행 결과
        """
        logger.info("=" * 60)
        logger.info("📉 매도 주문 실행")
        logger.info(f"   주문 수: {len(orders)}건")
        logger.info("=" * 60)

        results = []

        for i, order in enumerate(orders, 1):
            stock_code = order.get("stock_code", "")
            stock_name = order.get("stock_name", stock_code)
            quantity = order.get("quantity", 0)
            reason = order.get("reason", "")
            buy_price = order.get("buy_price", 0)

            logger.info(f"\n[{i}/{len(orders)}] 매도: {stock_name}")
            logger.info(f"   사유: {reason}")

            result = self.order_api.sell_market_order(stock_code, quantity)
            result["stock_name"] = stock_name
            result["reason"] = reason
            result["buy_price"] = buy_price
            results.append(result)

            if i < len(orders):
                time.sleep(0.5)

        # 체결 대기 (체결가 확인)
        if not self.use_mock_api:
            self._wait_for_fills(results)

        # 손익 계산 + 금액 업데이트
        for result in results:
            if not result.get("success"):
                continue
            filled_price = result.get("filled_price", 0)
            buy_price = result.get("buy_price", 0)
            quantity = result.get("quantity", 0)
            if filled_price and quantity:
                result["price"] = filled_price
                result["amount"] = filled_price * quantity
            if filled_price and buy_price:
                result["profit_rate"] = (filled_price - buy_price) / buy_price
                result["profit_amount"] = (filled_price - buy_price) * quantity
                logger.info(f"   📊 {result.get('stock_name')}: {buy_price:,}→{filled_price:,}원 ({result['profit_rate']:+.2%})")

        # 결과 집계
        success_count = sum(1 for r in results if r.get("success"))

        # DB 저장
        if save_to_db:
            self._save_trades(results, is_sell=True)

        logger.info(f"\n✅ 매도 완료: {success_count}/{len(orders)}건")

        return {
            "success": success_count == len(orders),
            "total_orders": len(orders),
            "success_count": success_count,
            "orders": results
        }
    
    # ===== 손절/익절 실행 =====
    
    def execute_stop_loss(
        self,
        position: dict,
        current_price: int
    ) -> dict:
        """
        손절 실행
        
        Args:
            position: 포지션 정보
            current_price: 현재가
        
        Returns:
            실행 결과
        """
        stock_code = position.get("stock_code")
        stock_name = position.get("stock_name", stock_code)
        quantity = position.get("shares", 0)
        buy_price = position.get("buy_price", 0)
        
        loss_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        
        logger.warning(f"🔻 손절 실행: {stock_name}")
        logger.warning(f"   매수가: {buy_price:,}원 → 현재가: {current_price:,}원 ({loss_pct:+.2f}%)")
        
        result = self.order_api.sell_market_order(stock_code, quantity)

        # 시장가 체결 후 실제 체결가 조회 (1초 대기 후)
        actual_price = current_price
        if result.get("success") and result.get("order_id"):
            time.sleep(1)
            try:
                orders = self.order_api.get_order_status(order_id=result["order_id"])
                if orders and orders[0].get("filled_price", 0) > 0:
                    actual_price = orders[0]["filled_price"]
                    logger.info(f"   실제 체결가: {actual_price:,}원")
            except Exception as e:
                logger.debug(f"체결가 조회 실패 (현재가 사용): {e}")

        actual_loss_pct = (actual_price - buy_price) / buy_price * 100 if buy_price > 0 else 0

        result.update({
            "stock_name": stock_name,
            "reason": "손절",
            "buy_price": buy_price,
            "sell_price": actual_price,
            "profit_rate": actual_loss_pct
        })

        return result
    
    def execute_take_profit(
        self,
        position: dict,
        current_price: int
    ) -> dict:
        """
        익절 실행
        
        Args:
            position: 포지션 정보
            current_price: 현재가
        
        Returns:
            실행 결과
        """
        stock_code = position.get("stock_code")
        stock_name = position.get("stock_name", stock_code)
        quantity = position.get("shares", 0)
        buy_price = position.get("buy_price", 0)
        
        profit_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        
        logger.info(f"🔺 익절 실행: {stock_name}")
        logger.info(f"   매수가: {buy_price:,}원 → 현재가: {current_price:,}원 ({profit_pct:+.2f}%)")
        
        result = self.order_api.sell_market_order(stock_code, quantity)

        # 시장가 체결 후 실제 체결가 조회 (1초 대기 후)
        actual_price = current_price
        if result.get("success") and result.get("order_id"):
            time.sleep(1)
            try:
                orders = self.order_api.get_order_status(order_id=result["order_id"])
                if orders and orders[0].get("filled_price", 0) > 0:
                    actual_price = orders[0]["filled_price"]
                    logger.info(f"   실제 체결가: {actual_price:,}원")
            except Exception as e:
                logger.debug(f"체결가 조회 실패 (현재가 사용): {e}")

        actual_profit_pct = (actual_price - buy_price) / buy_price * 100 if buy_price > 0 else 0

        result.update({
            "stock_name": stock_name,
            "reason": "익절",
            "buy_price": buy_price,
            "sell_price": actual_price,
            "profit_rate": actual_profit_pct
        })

        return result
    
    # ===== 잔고 조회 =====
    
    def get_balance(self) -> dict:
        """현재 잔고 조회"""
        return self.order_api.get_balance()

    def get_orderable_cash(self) -> int:
        """주문가능현금 조회"""
        return self.order_api.get_orderable_cash()

    def get_positions(self) -> list[dict]:
        """보유 종목 조회"""
        balance = self.get_balance()
        return balance.get("positions", [])
    
    # ===== DB 저장 =====
    
    def _save_trades(
        self,
        orders: list[dict],
        is_sell: bool = False
    ) -> None:
        """매매 기록 저장"""
        try:
            db = Database()
            db.connect()

            today = now_kst().date()

            for order in orders:
                if not order.get("success"):
                    continue

                filled_price = order.get("filled_price", 0)
                order_price = order.get("price", 0)
                buy_price = order.get("buy_price", 0)

                # 슬리피지: 매도 시 (체결가 - 주문 시 current_price) / current_price
                slippage = None
                if is_sell and filled_price and order_price:
                    slippage = round((filled_price - order_price) / order_price * 100, 4)

                trade = {
                    "date": str(today),
                    "stock_code": order.get("stock_code"),
                    "stock_name": order.get("stock_name"),
                    "action": "sell" if is_sell else "buy",
                    "shares": order.get("quantity", 0),
                    "price": order_price or filled_price,
                    "amount": order.get("amount", 0),
                    "reason": order.get("reason"),
                    "profit_rate": order.get("profit_rate"),
                    "profit_amount": order.get("profit_amount"),
                    "buy_price": buy_price if is_sell else (filled_price or order_price),
                    "filled_price": filled_price,
                    "slippage": slippage,
                    "remaining_shares": order.get("remaining_shares"),
                }

                db.save_trade(trade)

            db.close()

        except Exception as e:
            logger.error(f"매매 기록 저장 실패: {e}")
    
    def _save_positions(self, orders: list[dict]) -> None:
        """체결된 주문을 portfolio 테이블에 holding 상태로 저장"""
        try:
            db = Database()
            db.connect()
            today = str(now_kst().date())

            for order in orders:
                if not order.get("success"):
                    continue

                filled_price = order.get("filled_price") or order.get("price", 0)
                quantity = order.get("quantity", 0)
                db.save_holding_position({
                    "date": today,
                    "stock_code": order.get("stock_code"),
                    "stock_name": order.get("stock_name"),
                    "theme": order.get("theme"),
                    "shares": quantity,
                    "buy_price": filled_price,
                    "stop_loss": order.get("stop_loss"),
                    "take_profit": order.get("take_profit"),
                    "weight": order.get("weight"),
                })

            db.close()
        except Exception as e:
            logger.error(f"포지션 저장 실패: {e}")

    # ===== 유틸리티 =====
    
    def cancel_all_pending(self) -> int:
        """미체결 주문 전체 취소"""
        orders = self.order_api.get_order_status()
        pending = [o for o in orders if o.get("status") != "체결"]
        
        cancelled = 0
        for order in pending:
            result = self.order_api.cancel_order(
                order["order_id"],
                order["stock_code"],
                order["order_qty"]
            )
            if result.get("success"):
                cancelled += 1
        
        logger.info(f"미체결 취소: {cancelled}/{len(pending)}건")
        return cancelled


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📊 매매 엔진 테스트 (모의)")
    print("=" * 60)
    
    # 모의 API로 테스트
    engine = TradingEngine(use_mock_api=True)
    
    # 테스트 주문
    test_orders = [
        {"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "order_type": "market", "amount": 750000},
        {"stock_code": "000660", "stock_name": "SK하이닉스", "quantity": 5, "order_type": "market", "amount": 975000},
        {"stock_code": "373220", "stock_name": "LG에너지솔루션", "quantity": 2, "order_type": "market", "amount": 840000}
    ]
    
    print(f"\n테스트 주문: {len(test_orders)}건")
    
    # 포트폴리오 실행
    result = engine.execute_portfolio(test_orders, save_to_db=False)
    
    print(f"\n실행 결과:")
    print(f"  - 총 주문: {result['total_orders']}건")
    print(f"  - 체결: {result['filled_count']}건")
    print(f"  - 실패: {result['failed_count']}건")
    print(f"  - 소요 시간: {result['execution_time']}초")
    
    # 잔고 확인
    balance = engine.get_balance()
    print(f"\n잔고:")
    print(f"  - 총 평가액: {balance['total_value']:,}원")
    print(f"  - 보유 현금: {balance['cash']:,}원")
    print(f"  - 보유 종목: {balance['position_count']}개")
    
    print("\n" + "=" * 60)
    print("✅ 매매 엔진 테스트 완료!")
    print("=" * 60)
