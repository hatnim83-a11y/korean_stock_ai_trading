"""test_dashboard_broker_balance.py — 대시보드 실계좌 잔고 분리 표시 (Part A).

목표:
- 전략 추정자산(`strategy_current_total`)과 KIS 실계좌 잔고(`get_broker_balance`)를 분리.
- KIS 조회 실패 시 전략 계산값을 실잔고로 **위장 금지** → status="error", total_assets=None.
- 성공 시 source/status/fetched_at/total_assets/total_assets_field 메타 포함.

실 KIS 미의존 — order_api 싱글톤을 mock 주입.

실행: pytest tests/test_dashboard_broker_balance.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import web.dashboard_service as svc


def _reset_broker_cache():
    # 테스트 격리: 모듈 TTL 캐시 초기화
    if hasattr(svc, "_broker_balance_cache"):
        svc._broker_balance_cache = None


def test_broker_balance_ok(monkeypatch):
    _reset_broker_cache()
    order_api = MagicMock()
    order_api.get_balance.return_value = {
        "ok": True,
        "total_value": 9_091_759,
        "total_assets": 12_345_678,
        "total_assets_field": "tot_asst_amt",
        "cash": 500_000,
        "total_eval_amount": 8_591_759,
        "error": None,
    }
    monkeypatch.setattr(svc, "_get_order_api", lambda: order_api)

    r = svc.get_broker_balance()
    assert r["source"] == "KIS"
    assert r["status"] == "ok"
    assert r["total_assets"] == 12_345_678
    assert r["total_assets_field"] == "tot_asst_amt"
    assert r["fetched_at"]  # ISO 문자열
    assert r["error"] is None


def test_broker_balance_failure_does_not_fake_strategy_value(monkeypatch):
    """KIS 실패 → total_assets=None, status=error. 전략값 위장 금지."""
    _reset_broker_cache()
    order_api = MagicMock()
    order_api.get_balance.return_value = {"ok": False, "total_value": 0, "error": "rt_cd=1"}
    monkeypatch.setattr(svc, "_get_order_api", lambda: order_api)

    r = svc.get_broker_balance()
    assert r["status"] == "error"
    assert r["total_assets"] is None
    assert r["error"]


def test_broker_balance_exception_is_error(monkeypatch):
    """get_balance 예외 → status=error(민감정보 미노출), total_assets=None."""
    _reset_broker_cache()
    order_api = MagicMock()
    order_api.get_balance.side_effect = RuntimeError("HTTP 500")
    monkeypatch.setattr(svc, "_get_order_api", lambda: order_api)

    r = svc.get_broker_balance()
    assert r["status"] == "error"
    assert r["total_assets"] is None


def test_portfolio_data_exposes_strategy_current_total(monkeypatch):
    """get_portfolio_data 는 전략 추정자산을 strategy_current_total 로 명시(후방호환 current_total 유지)."""
    _reset_broker_cache()

    # holdings 없음 경로 — DB/KIS 호출 최소화
    fake_db = MagicMock()
    fake_db.get_portfolio.return_value = []
    fake_db.get_all_sell_trades.return_value = []
    monkeypatch.setattr(svc, "_get_db", lambda: fake_db)

    import asyncio
    d = asyncio.run(svc.get_portfolio_data())
    assert "strategy_current_total" in d
    assert d["strategy_current_total"] == d["current_total"]
