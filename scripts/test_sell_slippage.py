"""
test_sell_slippage.py — 매도 슬리피지 측정 시스템 단위 테스트

PLAN의 5개 시나리오(A~E)를 trading_engine 진입점 + monitor 통합 경로에서 검증한다.

사용법:
    source venv/bin/activate
    python scripts/test_sell_slippage.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import now_kst
from modules.trading_engine.trading_engine import TradingEngine
from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2, Position
import database as _db_mod


# ===== Phase 1: trading_engine 진입점 (시나리오 A/B/C/E + 분할 D 일부) =====

def test_execute_sell_orders_normal():
    """[시나리오 A·E] execute_sell_orders 다종목 호출 시 종목별 reference_price/source/slippage가 채워지는지"""
    engine = TradingEngine(use_mock_api=True)
    engine.order_api.positions = {
        "005930": {"quantity": 30, "avg_price": 70000, "name": "삼성전자", "buy_price": 70000},
        "000660": {"quantity": 20, "avg_price": 120000, "name": "SK하이닉스", "buy_price": 120000},
    }
    engine.order_api.cash = 1_000_000

    result = engine.execute_sell_orders(
        [
            {"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "reason": "테스트", "buy_price": 70000},
            {"stock_code": "000660", "stock_name": "SK하이닉스", "quantity": 5, "reason": "테스트", "buy_price": 120000},
        ],
        save_to_db=False,
    )
    assert len(result["orders"]) == 2, f"orders 수 불일치: {len(result['orders'])}"
    for order in result["orders"]:
        assert order.get("success"), f"매도 실패: {order}"
        assert order.get("reference_price", 0) > 0, f"reference_price 누락: {order}"
        assert order.get("reference_source") == "bid1", f"source 예상 bid1, 실제 {order.get('reference_source')}"
        assert order.get("filled_price", 0) > 0, "filled_price 누락"
        assert order.get("slippage") is not None, "slippage None"
    print(
        f"  S-A·E PASS — 다종목 매도 2건 모두 ref/source/slippage 정상 캡처 "
        f"(slippages={[round(o['slippage'], 4) for o in result['orders']]})"
    )


def test_execute_stop_loss():
    """[시나리오 A 변형] execute_stop_loss 진입점에서 reference/slippage 캡처"""
    engine = TradingEngine(use_mock_api=True)
    engine.order_api.positions = {
        "005930": {"quantity": 30, "avg_price": 70000, "name": "삼성전자", "buy_price": 70000},
    }
    engine.order_api.cash = 1_000_000

    result = engine.execute_stop_loss(
        {"stock_code": "005930", "stock_name": "삼성전자", "shares": 5, "buy_price": 70000},
        current_price=65000,
    )
    assert result.get("success"), "손절 매도 실패"
    assert result.get("reference_price", 0) > 0
    assert result.get("reference_source") == "bid1"
    assert result.get("slippage") is not None
    # 시장가 매도 손절은 일반적으로 음수 슬리피지가 정상 → 단 mock은 fixed price라 양수 가능
    print(
        f"  S-A(stop) PASS — ref={result['reference_price']}[{result['reference_source']}], "
        f"sell={result['sell_price']}, slippage={result['slippage']:+.4f}%"
    )


def test_execute_take_profit():
    """[시나리오 A 변형] execute_take_profit 진입점에서 reference/slippage 캡처"""
    engine = TradingEngine(use_mock_api=True)
    engine.order_api.positions = {
        "005930": {"quantity": 30, "avg_price": 70000, "name": "삼성전자", "buy_price": 70000},
    }
    engine.order_api.cash = 1_000_000

    result = engine.execute_take_profit(
        {"stock_code": "005930", "stock_name": "삼성전자", "shares": 3, "buy_price": 70000},
        current_price=80000,
    )
    assert result.get("success")
    assert result.get("reference_price", 0) > 0
    assert result.get("reference_source") == "bid1"
    assert result.get("slippage") is not None
    print(
        f"  S-A(profit) PASS — ref={result['reference_price']}[{result['reference_source']}], "
        f"sell={result['sell_price']}, slippage={result['slippage']:+.4f}%"
    )


def test_capture_bid1_to_current_price_fallback():
    """[시나리오 B] bid1=0 (성공 응답이지만 호가 비정상) → current_price 폴백 → source=current_price"""
    engine = TradingEngine(use_mock_api=True)

    def _bid1_zero(_code):
        return {
            "success": True,
            "ask1": 0,
            "bid1": 0,
            "ask_volume1": 0,
            "bid_volume1": 0,
            "current_price": 70500,  # 살아있음
            "message": "",
        }
    engine.order_api.inquire_asking_price = _bid1_zero

    ref, src = engine._capture_sell_reference_price("005930", fallback_price=70000)
    assert ref == 70500, f"current_price 폴백 실패: ref={ref}"
    assert src == "current_price", f"source 예상 current_price, 실제 {src}"
    print(f"  S-B PASS — bid1=0 → current_price={ref}, src={src}")


def test_capture_full_failure_to_fallback_price():
    """[시나리오 C] 호가 조회 success=False + 모든 가격 0 → fallback_price 사용"""
    engine = TradingEngine(use_mock_api=True)

    def _fail(_code):
        return {
            "success": False,
            "ask1": 0,
            "bid1": 0,
            "ask_volume1": 0,
            "bid_volume1": 0,
            "current_price": 0,
            "message": "강제 실패",
        }
    engine.order_api.inquire_asking_price = _fail

    ref, src = engine._capture_sell_reference_price("005930", fallback_price=70000)
    assert ref == 70000, f"fallback price 잘못: {ref}"
    assert src == "fallback", f"source 예상 fallback, 실제 {src}"

    # fallback_price=0이면 (0, "none")
    ref2, src2 = engine._capture_sell_reference_price("005930", fallback_price=0)
    assert ref2 == 0 and src2 == "none", f"none 폴백 실패: ({ref2}, {src2})"
    print(f"  S-C PASS — 전부 실패 → fallback={ref}, none 케이스 src={src2}")


def test_capture_exception_resilience():
    """[시나리오 C 보강] inquire_asking_price 예외 발생 시에도 fallback으로 안전하게 폴백"""
    engine = TradingEngine(use_mock_api=True)

    def _raise(_code):
        raise RuntimeError("API 통신 오류 시뮬레이션")
    engine.order_api.inquire_asking_price = _raise

    ref, src = engine._capture_sell_reference_price("005930", fallback_price=70000)
    assert ref == 70000 and src == "fallback", f"예외 폴백 실패: ({ref}, {src})"
    print(f"  S-C(exc) PASS — 예외 발생 → fallback={ref}, src={src}")


def test_compute_sell_slippage_invalid_inputs():
    """[시나리오 C 검증] _compute_sell_slippage가 무효 입력에 None 반환"""
    assert TradingEngine._compute_sell_slippage(70000, 0) is None
    assert TradingEngine._compute_sell_slippage(0, 70000) is None
    assert TradingEngine._compute_sell_slippage(70000, -100) is None
    # 정상 입력
    slip = TradingEngine._compute_sell_slippage(70500, 70000)
    assert slip is not None and abs(slip - 0.7143) < 0.001, f"정상 계산 실패: {slip}"
    print("  S-Compute PASS — 무효 입력 None / 정상 입력 0.7143%")


# ===== Phase 2: monitor 경로 통합 (시나리오 D — 분할 매도) =====

def _setup_db_mock():
    """Database 메서드를 in-memory 캡처용 mock으로 교체"""
    captured = {"trades": [], "reviews": []}
    _db_mod.Database.save_trade = lambda self, t: (captured["trades"].append(dict(t)), 1)[1]
    _db_mod.Database.save_trade_review = lambda self, r: captured["reviews"].append(dict(r))
    _db_mod.Database.close_position = lambda self, c, r: None
    _db_mod.Database.update_portfolio_shares = lambda self, *a, **kw: None
    _db_mod.Database.update_portfolio_partial_state = lambda self, *a, **kw: None
    _db_mod.Database.connect = lambda self: None
    _db_mod.Database.close = lambda self: None
    return captured


def test_monitor_paths():
    """[시나리오 D] monitor 3경로 (손절/분할 부분/분할 전량) — slippage 비-None"""
    captured = _setup_db_mock()
    monitor = PortfolioMonitorV2(use_mock=True)
    engine = monitor.trading_engine
    engine.order_api.cash = 1_000_000

    today = now_kst().date()

    def mkpos(remaining=10, current=72000):
        return Position(
            stock_code="005930", stock_name="삼성전자", shares=10,
            remaining_shares=remaining, buy_price=70000,
            stop_loss_price=65000, current_price=current,
            theme="반도체", buy_date=today,
        )

    # D-1: 손절 → _close_position_in_db
    engine.order_api.positions = {"005930": {"quantity": 30, "avg_price": 70000, "name": "삼전", "buy_price": 70000}}
    captured["trades"].clear()
    asyncio.run(monitor._execute_stop_loss(mkpos()))
    assert captured["trades"], "손절 trades 누락"
    slip = captured["trades"][0].get("slippage")
    assert slip is not None, "손절 slippage None"
    print(f"  S-D1 PASS — 손절 slippage={slip:+.4f}%")

    # D-2: 분할 익절 부분 → _save_partial_sell_to_db
    engine.order_api.positions = {"005930": {"quantity": 30, "avg_price": 70000, "name": "삼전", "buy_price": 70000}}
    captured["trades"].clear()
    asyncio.run(monitor._execute_partial_sell(mkpos(remaining=10, current=80000), 3, 1, 0.1))
    assert captured["trades"], "분할 부분 trades 누락"
    slip = captured["trades"][0].get("slippage")
    assert slip is not None, "분할 부분 slippage None"
    print(f"  S-D2 PASS — 분할(부분) slippage={slip:+.4f}%")

    # D-3: 분할 전량 → _close_position_in_db
    engine.order_api.positions = {"005930": {"quantity": 30, "avg_price": 70000, "name": "삼전", "buy_price": 70000}}
    captured["trades"].clear()
    asyncio.run(monitor._execute_partial_sell(mkpos(remaining=2, current=80000), 2, 1, 0.1))
    assert captured["trades"], "분할 전량 trades 누락"
    slip = captured["trades"][0].get("slippage")
    assert slip is not None, "분할 전량 slippage None"
    print(f"  S-D3 PASS — 분할(전량) slippage={slip:+.4f}%")


# ===== 회귀 검증: slippage 미지정 호출처(default=None)도 안전 =====

def test_default_slippage_backward_compat():
    """[회귀] _close_position_in_db / _save_partial_sell_to_db default 인자(slippage=None) 호환"""
    captured = _setup_db_mock()
    monitor = PortfolioMonitorV2(use_mock=True)
    today = now_kst().date()
    pos = Position(
        stock_code="005930", stock_name="삼성전자", shares=10,
        remaining_shares=10, buy_price=70000,
        stop_loss_price=65000, current_price=72000,
        theme="반도체", buy_date=today,
    )

    # slippage 인자 생략 (default=None) — _close_position_in_db는 sync 함수
    captured["trades"].clear()
    monitor._close_position_in_db(pos, "회귀테스트", 72000)
    assert captured["trades"], "회귀 trades 누락"
    assert captured["trades"][0].get("slippage") is None, "default None 미준수"
    # _save_partial_sell_to_db도 동일
    captured["trades"].clear()
    monitor._save_partial_sell_to_db(pos, 3, 1, 72000)
    assert captured["trades"], "회귀 partial trades 누락"
    assert captured["trades"][0].get("slippage") is None, "partial default None 미준수"
    print("  S-Compat PASS — slippage 인자 생략 시 None 저장 (역호환, 2함수)")


# ===== 메인 =====

def main():
    print("=" * 60)
    print("매도 슬리피지 측정 시스템 단위 테스트 (Phase 3)")
    print("=" * 60)

    print("\n[Phase 1] trading_engine 진입점 — 시나리오 A/B/C/E")
    test_execute_sell_orders_normal()
    test_execute_stop_loss()
    test_execute_take_profit()
    test_capture_bid1_to_current_price_fallback()
    test_capture_full_failure_to_fallback_price()
    test_capture_exception_resilience()
    test_compute_sell_slippage_invalid_inputs()

    print("\n[Phase 2] monitor 경로 통합 — 시나리오 D")
    test_monitor_paths()

    print("\n[회귀] 역호환")
    test_default_slippage_backward_compat()

    print("\n" + "=" * 60)
    print("✅ 모든 시나리오 PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
