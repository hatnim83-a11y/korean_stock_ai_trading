"""test_manual_buy.py — 텔레그램 /buy 수동 매수 기능 테스트.

검증 항목:
1. compute_per_slot_capital: 원본 공식 회귀 + slots<=0 ValueError
2. DB v18: portfolio.is_manual 컬럼 추가 + default 0
3. save_holding_position: is_manual=True→1 / 미전달→0 + v17 필드 보존
4. 로테이션 가드 계약: get_portfolio가 is_manual 노출 → 가드 predicate 동작
5. execute_buy: 슬롯 만석 / sell_lock 점유 / 중복 보유 거부, 성공 시 DB 저장 + v17 필드

실행: pytest tests/test_manual_buy.py -v
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from modules.trading_engine.capital_utils import compute_per_slot_capital


def _temp_db() -> Database:
    tmpdir = Path(tempfile.mkdtemp(prefix="test_manual_buy_"))
    db = Database(db_path=str(tmpdir / "trading.db"))
    db.connect()
    db.init_tables()
    return db


# ===== 1. compute_per_slot_capital 회귀 =====

def _original_sizing(tc, ac, sl, mx, r, tr, fr):
    pool = int(tc * r)
    mps = pool // mx
    psc = min(ac // sl, mps)
    full = psc
    if tr:
        psc = int(psc * fr)
    return pool, mps, psc, full


def test_compute_per_slot_capital_regression():
    cases = [
        (10_000_000, 9_000_000, 3, 5, 0.9, True, 0.5),
        (10_000_000, 9_000_000, 3, 5, 0.9, False, 0.5),
        (7_350_000, 5_000_000, 2, 5, 0.9, True, 0.5),
        (50_000_000, 12_345_678, 4, 5, 1.0, True, 0.5),
    ]
    for tc, ac, sl, mx, r, tr, fr in cases:
        pool, mps, psc, full = _original_sizing(tc, ac, sl, mx, r, tr, fr)
        h = compute_per_slot_capital(
            tc, ac, sl, max_positions=mx, swing_capital_ratio=r,
            tranche_enabled=tr, tranche_first_ratio=fr,
        )
        assert h["swing_capital_pool"] == pool
        assert h["max_per_stock"] == mps
        assert h["per_slot_capital"] == psc
        assert h["per_slot_capital_full"] == full
        assert h["tranche_applied"] is tr


def test_compute_per_slot_capital_zero_slots_raises():
    with pytest.raises(ValueError):
        compute_per_slot_capital(
            1e7, 1e6, 0, max_positions=5, swing_capital_ratio=0.9,
            tranche_enabled=False, tranche_first_ratio=0.5,
        )


# ===== 2. DB v18 마이그레이션 =====

def test_v18_adds_is_manual_column():
    db = _temp_db()
    try:
        assert db._get_schema_version() >= 18
        assert db._has_column("portfolio", "is_manual")
    finally:
        db.close()


def test_v18_is_manual_default_zero():
    db = _temp_db()
    try:
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": "000660",
            "stock_name": "SK하이닉스", "shares": 10, "buy_price": 100000,
        })  # is_manual 미전달
        rows = db.get_portfolio(status="holding")
        assert len(rows) == 1
        assert (rows[0].get("is_manual") or 0) == 0
    finally:
        db.close()


# ===== 3. save_holding_position is_manual + v17 필드 =====

def test_save_holding_position_manual_flag_and_v17():
    db = _temp_db()
    try:
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": "005930",
            "stock_name": "삼성전자", "theme": "", "shares": 5,
            "buy_price": 75000, "stop_loss": 71250,
            "first_buy_price": 75000, "avg_buy_price": 75000,
            "tranche_count": 1, "atr_at_buy": 1500.0, "atr_period": 14,
            "is_manual": True,
        })
        row = db.get_portfolio(status="holding")[0]
        assert row["is_manual"] == 1
        assert row["first_buy_price"] == 75000
        assert row["avg_buy_price"] == 75000
        assert row["tranche_count"] == 1
        assert row["atr_at_buy"] == 1500.0
        assert (row.get("theme") or "") == ""
    finally:
        db.close()


# ===== 4. 로테이션 가드 계약 =====

def test_rotation_guard_contract():
    """is_manual=1 종목은 midweek 교체 매도 가드(`if h.get('is_manual'): continue`)에
    의해 제외되고, is_manual=0 종목은 테마 매칭 시 매도 대상이 됨을 데이터 계약으로 검증."""
    db = _temp_db()
    try:
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": "111111",
            "stock_name": "수동종목", "theme": "", "shares": 1,
            "buy_price": 1000, "is_manual": True,
        })
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": "222222",
            "stock_name": "자동종목", "theme": "반도체", "shares": 1,
            "buy_price": 1000, "is_manual": False,
        })
        holdings = db.get_portfolio(status="holding")
        dropped_name = "반도체"
        sell_targets = []
        for h in holdings:
            if h.get("is_manual"):
                continue  # 수동 종목 제외 (main.py 가드와 동일)
            if h.get("theme", "") != dropped_name:
                continue
            sell_targets.append(h["stock_code"])
        assert "111111" not in sell_targets  # 수동 제외
        assert "222222" in sell_targets      # 자동 + 테마 매칭 → 대상
    finally:
        db.close()


# ===== 5. execute_buy (mock) =====

def _patch_dashboard(monkeypatch, db, *, holdings_count=0, current_price=10000,
                     buy_success=True, sell_locked=False):
    """dashboard_service 싱글톤/외부 의존성 mock."""
    import web.dashboard_service as ds
    import modules.atr_calculator as atr_mod

    # DB 싱글톤 교체
    monkeypatch.setattr(ds, "_get_db", lambda: db)

    # 보유 종목 N개 사전 적재 (슬롯 점유)
    for i in range(holdings_count):
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": f"9000{i:02d}",
            "stock_name": f"기보유{i}", "theme": "반도체", "shares": 1,
            "buy_price": 1000,
        })

    order_api = MagicMock()
    order_api.get_orderable_cash.return_value = 9_000_000
    order_api.get_balance.return_value = {"cash": 9_000_000, "total_value": 10_000_000}
    order_api.buy_market_order.return_value = {
        "success": buy_success, "order_id": "T123", "price": current_price,
        "message": "OK" if buy_success else "주문거부",
    }
    order_api.get_order_status.return_value = [{"filled_price": current_price}]

    kis = MagicMock()
    kis.get_current_price.return_value = {"price": current_price}
    kis.get_stock_name.return_value = "테스트종목"

    monkeypatch.setattr(ds, "_get_order_api", lambda: order_api)
    monkeypatch.setattr(ds, "_get_kis_api", lambda: kis)
    monkeypatch.setattr(atr_mod, "compute_atr", lambda code, period=14: 1234.0)
    # 가격 캐시 우회
    monkeypatch.setattr(ds, "get_cached_price", lambda k, c: {"price": current_price})

    if sell_locked:
        from modules.trading_engine.sell_lock import sell_lock
        sell_lock.acquire("005930", "test")
    return order_api


def test_execute_buy_success(monkeypatch):
    from modules.trading_engine.sell_lock import sell_lock
    from modules.trading_engine.buy_lock import buy_lock
    sell_lock.clear_all(); buy_lock.clear_all()
    import web.dashboard_service as ds
    db = _temp_db()
    try:
        _patch_dashboard(monkeypatch, db, holdings_count=0, current_price=10000)
        result = asyncio.run(ds.execute_buy("005930"))
        assert result["success"], result
        assert result["quantity"] > 0
        assert result["filled_price"] == 10000
        # DB 저장 확인 (is_manual=1, v17 필드)
        rows = [h for h in db.get_portfolio(status="holding") if h["stock_code"] == "005930"]
        assert len(rows) == 1
        r = rows[0]
        assert r["is_manual"] == 1
        assert r["first_buy_price"] == 10000
        assert r["avg_buy_price"] == 10000
        assert r["atr_at_buy"] == 1234.0
    finally:
        db.close()
        sell_lock.clear_all(); buy_lock.clear_all()


def test_execute_buy_slot_full(monkeypatch):
    from config import settings
    from modules.trading_engine.sell_lock import sell_lock
    from modules.trading_engine.buy_lock import buy_lock
    sell_lock.clear_all(); buy_lock.clear_all()
    import web.dashboard_service as ds
    db = _temp_db()
    try:
        _patch_dashboard(monkeypatch, db, holdings_count=settings.MAX_POSITIONS)
        result = asyncio.run(ds.execute_buy("005930"))
        assert result["success"] is False
        assert "슬롯" in result["message"]
    finally:
        db.close()
        sell_lock.clear_all(); buy_lock.clear_all()


def test_execute_buy_sell_locked(monkeypatch):
    from modules.trading_engine.sell_lock import sell_lock
    from modules.trading_engine.buy_lock import buy_lock
    sell_lock.clear_all(); buy_lock.clear_all()
    import web.dashboard_service as ds
    db = _temp_db()
    try:
        _patch_dashboard(monkeypatch, db, holdings_count=0, sell_locked=True)
        result = asyncio.run(ds.execute_buy("005930"))
        assert result["success"] is False
        assert "매도 처리 중" in result["message"]
    finally:
        db.close()
        sell_lock.clear_all(); buy_lock.clear_all()


def test_execute_buy_duplicate_holding(monkeypatch):
    from modules.trading_engine.sell_lock import sell_lock
    from modules.trading_engine.buy_lock import buy_lock
    sell_lock.clear_all(); buy_lock.clear_all()
    import web.dashboard_service as ds
    db = _temp_db()
    try:
        _patch_dashboard(monkeypatch, db, holdings_count=0)
        db.save_holding_position({
            "date": date(2026, 6, 1), "stock_code": "005930",
            "stock_name": "삼성전자", "theme": "반도체", "shares": 1, "buy_price": 1000,
        })
        result = asyncio.run(ds.execute_buy("005930"))
        assert result["success"] is False
        assert "이미 보유" in result["message"]
    finally:
        db.close()
        sell_lock.clear_all(); buy_lock.clear_all()


def test_execute_buy_order_failure_no_db_write(monkeypatch):
    from modules.trading_engine.sell_lock import sell_lock
    from modules.trading_engine.buy_lock import buy_lock
    sell_lock.clear_all(); buy_lock.clear_all()
    import web.dashboard_service as ds
    db = _temp_db()
    try:
        _patch_dashboard(monkeypatch, db, holdings_count=0, buy_success=False)
        result = asyncio.run(ds.execute_buy("005930"))
        assert result["success"] is False
        # KIS 실패 시 DB 미저장
        rows = [h for h in db.get_portfolio(status="holding") if h["stock_code"] == "005930"]
        assert len(rows) == 0
    finally:
        db.close()
        sell_lock.clear_all(); buy_lock.clear_all()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
