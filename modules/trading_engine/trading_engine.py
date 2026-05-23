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
from enum import Enum
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


# ===== 공격적 지정가(limit_aggressive) 주문 지원 =====

class OrderErrorCategory(Enum):
    """KIS 주문 에러 분류 — 재시도 루프에서 분기 처리용"""
    FATAL = "fatal"            # 거래정지, 종목코드 오류 등 — 즉시 중단
    RECOVERABLE = "recoverable"  # 일시적 장애, 호가 변동 등 — 재시도
    SIZE_ERROR = "size_error"  # 주문가능금액/수량 초과 — 수량 감축 재시도
    PRICE_ERROR = "price_error"  # 호가단위 불일치 — 1회 보정 재시도


def _classify_order_error(message: str) -> OrderErrorCategory:
    """KIS 주문 실패 메시지를 분류

    실측 수집된 메시지에 맞춰 분류표를 확장해나감 (CONTEXT.md 참고).
    """
    msg = (message or "").strip()

    # SIZE_ERROR — 수량 감축으로 회복 가능
    if "주문가능금액" in msg or "증거금" in msg:
        return OrderErrorCategory.SIZE_ERROR
    # PRICE_ERROR — 호가 단위 문제
    if "호가" in msg and ("단위" in msg or "불일치" in msg):
        return OrderErrorCategory.PRICE_ERROR
    # FATAL — 재시도해도 무의미
    for token in ("거래정지", "상장폐지", "종목코드", "VI 발동", "매매정지", "정리매매"):
        if token in msg:
            return OrderErrorCategory.FATAL
    # 기본 — 일시적 오류로 간주하여 재시도
    return OrderErrorCategory.RECOVERABLE


def _compute_weighted_avg_price(fills: list[tuple[int, int]]) -> int:
    """부분체결 가중평균가 계산

    Args:
        fills: [(filled_qty, filled_price), ...]

    Returns:
        가중평균가 (원 단위 반올림). fills 비어있거나 총수량 0이면 0
    """
    total_qty = 0
    total_cost = 0
    for qty, price in fills:
        if qty <= 0 or price <= 0:
            continue
        total_qty += qty
        total_cost += qty * price
    if total_qty <= 0:
        return 0
    return int(round(total_cost / total_qty))


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
        """매수 주문 실행 — order_type별 3갈래 분기

        - market: 시장가 (기존 로직, 상한가 1.3배 증거금)
        - limit_aggressive: 매도 1호가 + 재시도 루프 (_place_aggressive_limit_with_retry)
        - limit: 일반 지정가 (order["price"] 그대로 전달)
        """
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
            logger.info(f"   수량: {quantity}주  주문유형: {order_type}")

            # 증거금 사전 검증 — 주문 유형별 배수 분기
            # limit과 limit_aggressive 모두 지정가 계열로 1.04배 적용 (실측 기준)
            margin_ratio_map = {
                "market": UPPER_LIMIT_RATIO,
                "limit_aggressive": settings.LIMIT_AGGRESSIVE_MARGIN_RATIO,
                "limit": settings.LIMIT_AGGRESSIVE_MARGIN_RATIO,
            }
            margin_ratio = margin_ratio_map.get(order_type, UPPER_LIMIT_RATIO)

            if orderable_cash > 0 and expected_price > 0 and quantity > 0:
                margin_required = quantity * expected_price * margin_ratio
                if margin_required > orderable_cash:
                    safe_qty = int(orderable_cash / (expected_price * margin_ratio))
                    if safe_qty < 1:
                        logger.warning(
                            f"   ⚠️ 주문가능금액 부족 (필요: {int(margin_required):,}원, "
                            f"가용: {orderable_cash:,}원, 배수 ×{margin_ratio}) → 매수 건너뜀"
                        )
                        results.append({
                            "success": False,
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "order_type": order_type,
                            "requested_quantity": quantity,
                            "message": f"주문가능금액 부족 (×{margin_ratio} 기준)",
                        })
                        continue
                    logger.warning(
                        f"   ⚠️ 증거금 초과 (×{margin_ratio}) → 수량 조정: {quantity}주 → {safe_qty}주"
                    )
                    quantity = safe_qty

            # 공격적 지정가 경로: 재시도 루프로 위임 (기존 MAX_RETRY 루프 미사용)
            if order_type == "limit_aggressive":
                try:
                    bl_result = self._place_aggressive_limit_with_retry(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        requested_qty=quantity,
                        expected_price=expected_price,
                    )
                except Exception as e:
                    logger.error(f"   [BL] 루프 예외: {e}")
                    bl_result = {
                        "success": False,
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "order_type": "limit_aggressive",
                        "requested_quantity": quantity,
                        "quantity": 0,
                        "remaining_shares": quantity,
                        "message": str(e),
                    }

                if bl_result.get("success"):
                    bl_result.update({
                        "theme": order.get("theme"),
                        "stop_loss": order.get("stop_loss"),
                        "take_profit": order.get("take_profit"),
                    })
                    # 다음 종목 계산용 가용현금 차감
                    # limit_aggressive는 이미 체결 확정된 실체결금액(amount) 기준 차감 — margin_ratio 중복 곱셈 금지
                    amt = bl_result.get("amount", 0)
                    if orderable_cash > 0 and amt > 0:
                        orderable_cash = max(0, orderable_cash - int(amt))

                results.append(bl_result)

                # 주문 간 딜레이
                if i < len(orders):
                    time.sleep(0.5)
                continue

            # 시장가/일반 지정가 경로: 기존 재시도 로직
            for attempt in range(MAX_RETRY):
                try:
                    if order_type == "market":
                        result = self.order_api.buy_market_order(stock_code, quantity)
                    else:  # limit
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
                        # order_type별 증거금 배수 적용 (market=1.3, limit=1.04)
                        if orderable_cash > 0 and expected_price > 0:
                            orderable_cash = max(0, orderable_cash - quantity * expected_price * margin_ratio)
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

    # ===== 공격적 지정가(limit_aggressive) 주문 실행 =====

    def _compute_aggressive_limit_price(
        self,
        stock_code: str,
        fallback_price: int
    ) -> tuple[int, str]:
        """매도 1호가 조회 후 지정가 가격 결정

        Returns:
            (price, source): 가격과 산출 출처("ask1" | "fallback")
        """
        try:
            ask_info = self.order_api.inquire_asking_price(stock_code)
            if ask_info.get("success") and ask_info.get("ask1", 0) > 0:
                return int(ask_info["ask1"]), "ask1"
        except Exception as e:
            logger.warning(f"   호가 조회 예외 ({stock_code}): {e}")

        # 폴백: fallback_price × LIMIT_AGGRESSIVE_FALLBACK_PREMIUM을 정수화
        # 호가 단위는 KIS 서버가 지정가 전송 시 정규화하므로 정수로만 맞춰줌
        if fallback_price > 0:
            premium = settings.LIMIT_AGGRESSIVE_FALLBACK_PREMIUM
            return int(round(fallback_price * premium)), "fallback"
        return 0, "fallback"

    def _capture_sell_reference_price(
        self,
        stock_code: str,
        fallback_price: int = 0
    ) -> tuple[int, str]:
        """매도 슬리피지 측정용 reference price 캡처

        시장가 매도는 매수 1호가(bid1)에서 체결되는 것이 정상이므로
        bid1을 기준 가격으로 사용한다. 실패 시 단계별 폴백.

        Args:
            stock_code: 종목코드 (6자리)
            fallback_price: 호가/현재가 모두 실패 시 최후 폴백 (예: buy_price)

        Returns:
            (price, source): 가격(int)과 출처 라벨
                source ∈ {"bid1", "current_price", "fallback", "none"}
        """
        try:
            ask_info = self.order_api.inquire_asking_price(stock_code)
            if ask_info.get("success"):
                bid1 = ask_info.get("bid1", 0) or 0
                if bid1 > 0:
                    return int(bid1), "bid1"
                cur = ask_info.get("current_price", 0) or 0
                if cur > 0:
                    return int(cur), "current_price"
        except Exception as e:
            logger.warning(f"   매도 reference price 조회 예외 ({stock_code}): {e}")

        if fallback_price and fallback_price > 0:
            return int(fallback_price), "fallback"
        return 0, "none"

    @staticmethod
    def _compute_sell_slippage(
        filled_price: float,
        reference_price: float
    ) -> Optional[float]:
        """시장가 매도 슬리피지 계산

        slippage = (filled_price - reference_price) / reference_price × 100
        - 음수: 불리한 체결 (매수 1호가 아래로 내려감, 시장가 매도의 정상)
        - 양수: 유리한 체결 (호가 흔들림 시 드물게)

        Returns:
            슬리피지 (%) 또는 reference_price/filled_price 무효 시 None
        """
        if not filled_price or not reference_price or reference_price <= 0:
            return None
        return round((filled_price - reference_price) / reference_price * 100, 4)

    def _place_aggressive_limit_with_retry(
        self,
        stock_code: str,
        stock_name: str,
        requested_qty: int,
        expected_price: int
    ) -> dict:
        """공격적 지정가 재시도 루프 — 매도 1호가 기반 즉시체결 에뮬레이션

        Flow (종목당):
            1) 매도 1호가 조회 → buy_limit_order
            2) POLL_INTERVAL 간격으로 체결 확인 (RETRY_TIMEOUT까지)
            3) 미체결 수량 취소 → CANCEL_DELAY 대기 → 재조회로 최종 체결량 확정
            4) 남은 수량 있으면 새 호가로 재주문 (MAX_RETRIES 또는 TOTAL_TIMEOUT까지)
            5) 에러 분류: FATAL=중단 / SIZE_ERROR=수량감축 / RECOVERABLE=재시도

        Returns:
            통합 결과 dict — _save_trades / _save_positions가 기대하는 형식
        """
        timeout = settings.LIMIT_AGGRESSIVE_RETRY_TIMEOUT
        poll_interval = settings.LIMIT_AGGRESSIVE_POLL_INTERVAL
        max_retries = settings.LIMIT_AGGRESSIVE_MAX_RETRIES
        total_timeout = settings.LIMIT_AGGRESSIVE_TOTAL_TIMEOUT
        cancel_delay = settings.LIMIT_AGGRESSIVE_CANCEL_DELAY
        warn_threshold = settings.ABNORMAL_RETRY_WARN_THRESHOLD

        fills: list[tuple[int, int]] = []  # (filled_qty, filled_price) 누적
        sub_order_ids: list[str] = []
        first_order_id: Optional[str] = None
        remaining = requested_qty
        start_ts = time.time()
        last_error_msg = ""

        logger.info(f"   [BL] 재시도 루프 시작: {stock_name} ({stock_code}) {requested_qty}주")

        for attempt in range(max_retries):
            elapsed = time.time() - start_ts
            if elapsed >= total_timeout:
                logger.warning(
                    f"   [BL] 총 타임아웃 도달 ({elapsed:.1f}s ≥ {total_timeout}s) → 포기"
                )
                break
            if remaining <= 0:
                break

            # 1) 매도 1호가 조회
            bid_price, src = self._compute_aggressive_limit_price(stock_code, expected_price)
            if bid_price <= 0:
                logger.warning(f"   [BL] 가격 산출 실패 → 루프 중단")
                last_error_msg = "가격 산출 실패 (호가/폴백 모두 실패)"
                break

            # 2) 지정가 주문
            try:
                result = self.order_api.buy_limit_order(stock_code, remaining, bid_price)
            except Exception as e:
                logger.error(f"   [BL] 주문 예외 ({attempt+1}/{max_retries}): {e}")
                last_error_msg = str(e)
                time.sleep(1)
                continue

            if not result.get("success"):
                last_error_msg = result.get("message", "")
                category = _classify_order_error(last_error_msg)
                logger.warning(
                    f"   [BL] 주문 실패 [{category.value}] ({attempt+1}/{max_retries}): {last_error_msg}"
                )
                if category == OrderErrorCategory.FATAL:
                    break
                if category == OrderErrorCategory.SIZE_ERROR:
                    new_qty = max(1, int(remaining * RETRY_QTY_REDUCTION))
                    if new_qty >= remaining:
                        break  # 더 줄일 수 없음
                    remaining = new_qty
                    continue
                # RECOVERABLE / PRICE_ERROR → 1초 후 재시도
                time.sleep(1)
                continue

            order_id = result.get("order_id", "")
            if order_id:
                sub_order_ids.append(order_id)
                if first_order_id is None:
                    first_order_id = order_id

            # 3) 폴링으로 체결 확인 (timeout까지)
            poll_start = time.time()
            attempt_filled_qty = 0
            attempt_filled_price = 0
            while (time.time() - poll_start) < timeout:
                time.sleep(poll_interval)
                statuses = self.order_api.get_order_status(order_id=order_id)
                if statuses:
                    st = statuses[0]
                    attempt_filled_qty = int(st.get("filled_qty", 0) or 0)
                    attempt_filled_price = int(st.get("filled_price", 0) or 0)
                    if attempt_filled_qty >= remaining:
                        # 전량 체결
                        break

            # 4) 미체결 취소
            if attempt_filled_qty < remaining:
                unfilled = remaining - attempt_filled_qty
                try:
                    cancel_result = self.order_api.cancel_order(order_id, stock_code, unfilled)
                except Exception as e:
                    logger.warning(f"   [BL] 취소 예외: {e}")
                    cancel_result = {"success": False, "message": str(e)}

                time.sleep(cancel_delay)  # 취소 반영 대기 — 필수

                # 취소 직전 추가 체결 반영 (race condition)
                final_statuses = self.order_api.get_order_status(order_id=order_id)
                if final_statuses:
                    fs = final_statuses[0]
                    final_filled_qty = int(fs.get("filled_qty", 0) or 0)
                    final_filled_price = int(fs.get("filled_price", 0) or 0)
                    if final_filled_qty > attempt_filled_qty:
                        logger.info(
                            f"   [BL] 취소 직전 추가 체결: {attempt_filled_qty} → {final_filled_qty}"
                        )
                    attempt_filled_qty = final_filled_qty
                    attempt_filled_price = final_filled_price or attempt_filled_price

                # "이미 전량 체결" 취소 실패는 정상 흐름
                if not cancel_result.get("success"):
                    cmsg = cancel_result.get("message", "")
                    if "취소가능수량" in cmsg or "이미" in cmsg:
                        logger.info(f"   [BL] 취소 전 전량 체결 확인: {order_id}")
                    else:
                        logger.warning(f"   [BL] 취소 실패: {cmsg}")

            # 5) 이번 라운드 체결분 누적
            if attempt_filled_qty > 0 and attempt_filled_price > 0:
                fills.append((attempt_filled_qty, attempt_filled_price))
                remaining -= attempt_filled_qty
                logger.info(
                    f"   [BL] 라운드 {attempt+1}: {attempt_filled_qty}주 @ {attempt_filled_price:,}원 "
                    f"(누적 {requested_qty - remaining}/{requested_qty})"
                )

            # warn_threshold 정확히 도달 시에만 1회 경고 (중복 발화 방지)
            if attempt + 1 == warn_threshold:
                logger.warning(
                    f"   [BL] 재시도 과다 ({attempt+1}회): {stock_name} 체결 {requested_qty - remaining}/{requested_qty}"
                )

            if remaining <= 0:
                break

        # 6) 결과 집계
        total_filled_qty = sum(q for q, _ in fills)
        avg_price = _compute_weighted_avg_price(fills)
        elapsed_total = time.time() - start_ts

        success = total_filled_qty > 0
        remaining_shares = requested_qty - total_filled_qty

        if success:
            logger.info(
                f"   [BL] 완료: {stock_name} {total_filled_qty}/{requested_qty}주 "
                f"@ 평균 {avg_price:,}원 ({elapsed_total:.1f}s, 재시도 {len(sub_order_ids)}회)"
            )
        else:
            logger.warning(
                f"   [BL] 실패: {stock_name} 체결 0/{requested_qty}주 "
                f"({elapsed_total:.1f}s, 재시도 {len(sub_order_ids)}회) — {last_error_msg}"
            )

        return {
            "success": success,
            "filled": success,  # _wait_for_fills 이중 폴링 방지 (이미 내부에서 체결 확정)
            "order_id": first_order_id or "",
            "sub_order_ids": sub_order_ids,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "quantity": total_filled_qty,
            "requested_quantity": requested_qty,
            "remaining_shares": remaining_shares,
            "price": avg_price,
            "filled_price": avg_price,
            "amount": avg_price * total_filled_qty,
            "expected_price": expected_price,
            "order_type": "limit_aggressive",
            "action": "매수",
            "retry_count": len(sub_order_ids),
            "elapsed_seconds": round(elapsed_total, 2),
            "message": "체결 완료" if success else (last_error_msg or "미체결"),
        }

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

            # 매도 직전 매수 1호가 캡처 (slippage 측정용)
            ref_price, ref_source = self._capture_sell_reference_price(
                stock_code, fallback_price=buy_price
            )

            result = self.order_api.sell_market_order(stock_code, quantity)
            result["stock_name"] = stock_name
            result["reason"] = reason
            result["buy_price"] = buy_price
            result["reference_price"] = ref_price
            result["reference_source"] = ref_source
            results.append(result)

            if i < len(orders):
                time.sleep(0.5)

        # 체결 대기 (체결가 확인)
        if not self.use_mock_api:
            self._wait_for_fills(results)

        # 손익 계산 + 금액 업데이트 + 슬리피지 계산
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

            # 매도 슬리피지 계산
            ref_price = result.get("reference_price", 0)
            slippage = self._compute_sell_slippage(filled_price, ref_price)
            result["slippage"] = slippage
            if slippage is not None:
                threshold = settings.SELL_SLIPPAGE_WARN_THRESHOLD
                log_fn = logger.warning if abs(slippage) > threshold else logger.info
                log_fn(
                    f"   📐 매도 slippage: {slippage:+.4f}% "
                    f"(filled {filled_price:,} vs ref {ref_price:,} [{result.get('reference_source')}])"
                )

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

        # 매도 직전 매수 1호가 캡처 (slippage 측정용)
        ref_price, ref_source = self._capture_sell_reference_price(
            stock_code, fallback_price=current_price or buy_price
        )

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
        slippage = self._compute_sell_slippage(actual_price, ref_price)
        if slippage is not None:
            threshold = settings.SELL_SLIPPAGE_WARN_THRESHOLD
            log_fn = logger.warning if abs(slippage) > threshold else logger.info
            log_fn(
                f"   📐 손절 slippage: {slippage:+.4f}% "
                f"(filled {actual_price:,} vs ref {ref_price:,} [{ref_source}])"
            )

        result.update({
            "stock_name": stock_name,
            "reason": "손절",
            "buy_price": buy_price,
            "sell_price": actual_price,
            "profit_rate": actual_loss_pct,
            "reference_price": ref_price,
            "reference_source": ref_source,
            "slippage": slippage,
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

        # 매도 직전 매수 1호가 캡처 (slippage 측정용)
        ref_price, ref_source = self._capture_sell_reference_price(
            stock_code, fallback_price=current_price or buy_price
        )

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
        slippage = self._compute_sell_slippage(actual_price, ref_price)
        if slippage is not None:
            threshold = settings.SELL_SLIPPAGE_WARN_THRESHOLD
            log_fn = logger.warning if abs(slippage) > threshold else logger.info
            log_fn(
                f"   📐 익절 slippage: {slippage:+.4f}% "
                f"(filled {actual_price:,} vs ref {ref_price:,} [{ref_source}])"
            )

        result.update({
            "stock_name": stock_name,
            "reason": "익절",
            "buy_price": buy_price,
            "sell_price": actual_price,
            "profit_rate": actual_profit_pct,
            "reference_price": ref_price,
            "reference_source": ref_source,
            "slippage": slippage,
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
        db = None
        try:
            db = Database()
            db.connect()

            today = now_kst().date()

            for order in orders:
                if not order.get("success"):
                    continue

                # 체결량 0은 DB 저장 skip (limit_aggressive 실패 시 0원 매수 방지)
                quantity = order.get("quantity", 0)
                if not is_sell and quantity <= 0:
                    logger.warning(
                        f"   체결량 0 → trades 저장 skip: {order.get('stock_name')}"
                    )
                    continue

                filled_price = order.get("filled_price", 0)
                order_price = order.get("price", 0)
                buy_price = order.get("buy_price", 0)
                expected_price = order.get("expected_price", 0)
                reference_price = order.get("reference_price", 0)

                # 슬리피지 계산
                # - 매도: order에 이미 계산된 slippage가 있으면 우선 사용 (execute_sell_orders에서 계산)
                #         없으면 (filled - reference) / reference 또는 (filled - order) / order로 폴백
                # - 매수(limit_aggressive): (가중평균 체결가 - expected_price) / expected_price
                slippage = order.get("slippage") if is_sell else None
                if is_sell and slippage is None:
                    if filled_price and reference_price > 0:
                        slippage = round((filled_price - reference_price) / reference_price * 100, 4)
                    elif filled_price and order_price:
                        slippage = round((filled_price - order_price) / order_price * 100, 4)
                elif not is_sell and filled_price and expected_price:
                    slippage = round((filled_price - expected_price) / expected_price * 100, 4)

                trade = {
                    "date": str(today),
                    "stock_code": order.get("stock_code"),
                    "stock_name": order.get("stock_name"),
                    "action": "sell" if is_sell else "buy",
                    "shares": quantity,
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

        except Exception as e:
            logger.error(f"매매 기록 저장 실패: {e}")
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _save_positions(self, orders: list[dict]) -> None:
        """체결된 주문을 portfolio 테이블에 holding 상태로 저장 (v17: 분할 진입 컬럼 포함)."""
        db = None
        try:
            db = Database()
            db.connect()
            today = str(now_kst().date())

            # v17: 분할 진입 활성화 여부 + ATR 박제
            tranche_enabled = False
            try:
                from config import settings as _settings
                tranche_enabled = getattr(_settings, "TRANCHE_ENTRY_ENABLED", False)
            except Exception:
                pass

            for order in orders:
                if not order.get("success"):
                    continue

                quantity = order.get("quantity", 0)
                filled_price = order.get("filled_price") or order.get("price", 0)
                stock_code = order.get("stock_code")

                # 체결량 0 또는 체결가 0은 portfolio 저장 skip
                if quantity <= 0 or filled_price <= 0:
                    logger.warning(
                        f"   체결량/체결가 0 → portfolio 저장 skip: {order.get('stock_name')}"
                    )
                    continue

                # v17: ATR 박제 (캐시 hit이면 즉시, miss면 동기 호출 — 09:20 prefetch로 캐시 채움)
                atr_value = 0.0
                if tranche_enabled and stock_code:
                    try:
                        from modules.atr_calculator import compute_atr
                        atr_value = compute_atr(stock_code, period=14)
                    except Exception as e:
                        logger.debug(f"[v17] ATR 박제 실패 {stock_code}: {e}")

                db.save_holding_position({
                    "date": today,
                    "stock_code": stock_code,
                    "stock_name": order.get("stock_name"),
                    "theme": order.get("theme"),
                    "shares": quantity,
                    "buy_price": filled_price,
                    "stop_loss": order.get("stop_loss"),
                    "take_profit": order.get("take_profit"),
                    "weight": order.get("weight"),
                    # v17: 분할 진입 컬럼 (1차 매수 시점 — first/avg 모두 체결가, pending=True)
                    "first_buy_price": filled_price,
                    "avg_buy_price": filled_price,
                    "tranche_count": 1,
                    "second_tranche_pending": tranche_enabled,
                    "atr_at_buy": atr_value if atr_value > 0 else None,
                    "atr_period": 14,
                })

        except Exception as e:
            logger.error(f"포지션 저장 실패: {e}")
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

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
