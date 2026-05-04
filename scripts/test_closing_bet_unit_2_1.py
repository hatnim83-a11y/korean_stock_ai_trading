"""단위 2-1 검증: kis_orderbook_collector.

시나리오:
    OB-1: 정상 호가 → Snapshot 생성, ask1/bid1/spread_pct 정확
    OB-2: KIS API success=False → is_valid=False, error_msg 채움
    OB-3: KIS 예외 → is_valid=False
    OB-4: spread_pct 계산 ((ask1-bid1)/((ask1+bid1)/2)) 정확
    OB-5: ask1=0 또는 bid1=0 → spread_pct None, 가격 None
    OB-6: 무효 ticker → is_valid=False
    OB-7: collect_for_universe 5종목 → 5 Snapshot
    OB-8: 일부 종목 실패 격리 → 다른 종목 정상
    OB-9: DB v2 마이그레이션 — orderbook_snapshots 테이블 생성
    OB-10: insert_snapshots → 정상 INSERT, REPLACE on PK 충돌
    OB-11: orderbook_collector=None default → 자동 생성
    OB-12: main_orchestrator orderbook_collector property — KisOrderbookCollector 인스턴스
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.collectors.kis_orderbook_collector import (
    KisOrderbookCollector,
    OrderbookSnapshot,
    insert_snapshots,
    _safe_int,
    _compute_spread_pct,
)


KST = timezone(timedelta(hours=9))


def _mock_order_api(success_payload: dict | None = None, side_effect=None):
    fake = MagicMock()
    if side_effect is not None:
        fake.inquire_asking_price.side_effect = side_effect
    else:
        fake.inquire_asking_price.return_value = success_payload
    return fake


def test_OB_1_정상():
    payload = {
        "success": True,
        "ask1": 50100, "bid1": 50000,
        "ask_volume1": 1000, "bid_volume1": 800,
        "current_price": 50050, "message": "",
    }
    c = KisOrderbookCollector(order_api=_mock_order_api(payload))
    snap = c.collect_snapshot("005930")
    assert snap.is_valid is True
    assert snap.ask1 == 50100 and snap.bid1 == 50000
    assert snap.ask_volume1 == 1000 and snap.bid_volume1 == 800
    assert snap.current_price == 50050
    # spread = (50100-50000) / ((50100+50000)/2) = 100/50050 ≈ 0.001998
    assert snap.spread_pct is not None
    assert abs(snap.spread_pct - 0.001998) < 1e-5
    assert snap.error_msg is None
    print(f"[PASS] OB-1: 정상 — ask=50100, bid=50000, spread={snap.spread_pct:.6f}")


def test_OB_2_API_success_False():
    payload = {"success": False, "ask1": 0, "bid1": 0, "current_price": 0, "message": "API 응답 오류"}
    c = KisOrderbookCollector(order_api=_mock_order_api(payload))
    snap = c.collect_snapshot("005930")
    assert snap.is_valid is False
    assert snap.error_msg == "API 응답 오류"
    print("[PASS] OB-2: API success=False → is_valid=False")


def test_OB_3_API_예외():
    c = KisOrderbookCollector(order_api=_mock_order_api(side_effect=RuntimeError("KIS 장애")))
    snap = c.collect_snapshot("005930")
    assert snap.is_valid is False
    assert "api_exception" in (snap.error_msg or "")
    print("[PASS] OB-3: API 예외 → is_valid=False")


def test_OB_4_spread_pct_계산():
    # 분리 함수 직접 호출
    s = _compute_spread_pct(10100, 10000)
    # (10100-10000) / ((10100+10000)/2) = 100/10050 ≈ 0.00995
    assert s is not None and abs(s - 0.009950) < 1e-5
    s2 = _compute_spread_pct(5050, 5000)
    # 50 / 5025 ≈ 0.009950
    assert s2 is not None and abs(s2 - 0.009950) < 1e-5
    print(f"[PASS] OB-4: spread_pct 계산 정확 ({s:.6f})")


def test_OB_5_ask1_0_또는_bid1_0():
    payload = {
        "success": True,
        "ask1": 0, "bid1": 5000,
        "ask_volume1": 0, "bid_volume1": 100,
        "current_price": 0,
    }
    c = KisOrderbookCollector(order_api=_mock_order_api(payload))
    snap = c.collect_snapshot("005930")
    assert snap.is_valid is True  # API 성공
    assert snap.ask1 is None      # _safe_int 가 0 → None
    assert snap.bid1 == 5000
    assert snap.spread_pct is None  # ask1 None 이라 계산 불가
    assert snap.current_price is None
    print("[PASS] OB-5: 0 가격 → None 처리, spread_pct None")


def test_OB_6_무효_ticker():
    c = KisOrderbookCollector(order_api=_mock_order_api({"success": True}))
    for invalid in ["abc", "12345", "1234567", "", None]:
        snap = c.collect_snapshot(invalid)  # type: ignore
        assert snap.is_valid is False
        assert snap.error_msg == "invalid_ticker"
    print("[PASS] OB-6: 무효 ticker 5건 → is_valid=False")


def test_OB_7_collect_for_universe_5종목():
    payload = {
        "success": True, "ask1": 100, "bid1": 99,
        "ask_volume1": 10, "bid_volume1": 10, "current_price": 100,
    }
    c = KisOrderbookCollector(order_api=_mock_order_api(payload))
    snaps = c.collect_for_universe(["100001", "100002", "100003", "100004", "100005"])
    assert len(snaps) == 5
    assert all(s.is_valid for s in snaps)
    print("[PASS] OB-7: collect_for_universe 5종목 → 5 Snapshot 정상")


def test_OB_8_일부_실패_격리():
    fake = MagicMock()
    def _flaky(ticker):
        if ticker == "100002":
            raise RuntimeError("API 차단")
        return {"success": True, "ask1": 100, "bid1": 99, "ask_volume1": 5,
                "bid_volume1": 5, "current_price": 100}
    fake.inquire_asking_price.side_effect = _flaky
    c = KisOrderbookCollector(order_api=fake)
    snaps = c.collect_for_universe(["100001", "100002", "100003"])
    assert len(snaps) == 3
    assert snaps[0].is_valid is True
    assert snaps[1].is_valid is False  # 격리됨
    assert snaps[2].is_valid is True
    print("[PASS] OB-8: 일부 실패 격리 → 다른 종목 계속")


def test_OB_9_DB_v2_마이그레이션():
    """closing_bet.db에 orderbook_snapshots 테이블 + 인덱스 생성."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        from closing_bet_system.storage.db import ClosingBetDatabase
        db = ClosingBetDatabase(db_path)
        db.connect()
        db.init_tables()
        with db.get_cursor() as cur:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orderbook_snapshots'")
            assert cur.fetchone() is not None
            cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_orderbook_ticker_time'")
            assert cur.fetchone() is not None
            cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_orderbook_time'")
            assert cur.fetchone() is not None
            # schema_version v2
            cur.execute("SELECT MAX(version) FROM schema_version")
            assert cur.fetchone()[0] >= 2
        db.close()
    print("[PASS] OB-9: DB v2 — orderbook_snapshots 테이블 + 2 인덱스 + schema v2")


def test_OB_10_insert_snapshots_REPLACE():
    """같은 (ticker, snapshot_time) PK 시 REPLACE."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        from closing_bet_system.storage.db import ClosingBetDatabase
        db = ClosingBetDatabase(db_path)
        db.connect()
        db.init_tables()
        ts = datetime(2026, 5, 4, 15, 10, 0, tzinfo=KST)
        s1 = OrderbookSnapshot(ticker="005930", snapshot_time=ts, is_valid=True,
                               ask1=100, bid1=99, current_price=100, spread_pct=0.01)
        s2 = OrderbookSnapshot(ticker="005930", snapshot_time=ts, is_valid=True,
                               ask1=200, bid1=199, current_price=200, spread_pct=0.005)
        n1 = insert_snapshots([s1], db=db)
        n2 = insert_snapshots([s2], db=db)
        assert n1 == 1 and n2 == 1
        with db.get_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orderbook_snapshots")
            assert cur.fetchone()[0] == 1  # REPLACE 됐으므로 1행
            cur.execute("SELECT ask1 FROM orderbook_snapshots WHERE ticker=?", ("005930",))
            assert cur.fetchone()[0] == 200  # 두 번째 값으로 교체
        db.close()
    print("[PASS] OB-10: insert_snapshots — INSERT OR REPLACE 정상")


def test_OB_11_default_order_api():
    """order_api=None default → property 호출 시 자동 생성."""
    c = KisOrderbookCollector()
    # property 호출은 실제 KIS 토큰을 만들 수 있으므로 mock으로 가로채기
    with patch("closing_bet_system.infra.kis_client.get_order_api") as m:
        m.return_value = MagicMock()
        api = c.order_api
        assert api is not None
        m.assert_called_once()
    print("[PASS] OB-11: order_api=None default → 자동 생성")


def test_OB_12_orchestrator_property():
    """MainOrchestrator orderbook_collector property — 자동 instantiate."""
    from closing_bet_system.main_orchestrator import MainOrchestrator
    orch = MainOrchestrator()
    # lazy 생성 확인
    with patch("closing_bet_system.collectors.kis_orderbook_collector.KisOrderbookCollector") as m:
        m.return_value = MagicMock(name="orderbook_instance")
        c = orch.orderbook_collector
        assert c is m.return_value
    print("[PASS] OB-12: MainOrchestrator.orderbook_collector lazy property OK")


def test_OB_13_safe_int_방어():
    assert _safe_int(None) is None
    assert _safe_int("") is None
    assert _safe_int("abc") is None
    assert _safe_int(0) is None  # 0 → None
    assert _safe_int("100") == 100
    assert _safe_int(50) == 50
    assert _safe_int(-1) == -1   # 음수도 통과 (호가는 발생 안 함)
    print("[PASS] OB-13: _safe_int 방어 — None/빈/문자/0 모두 None")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 2-1 검증: kis_orderbook_collector")
    print("=" * 60)
    test_OB_1_정상()
    test_OB_2_API_success_False()
    test_OB_3_API_예외()
    test_OB_4_spread_pct_계산()
    test_OB_5_ask1_0_또는_bid1_0()
    test_OB_6_무효_ticker()
    test_OB_7_collect_for_universe_5종목()
    test_OB_8_일부_실패_격리()
    test_OB_9_DB_v2_마이그레이션()
    test_OB_10_insert_snapshots_REPLACE()
    test_OB_11_default_order_api()
    test_OB_12_orchestrator_property()
    test_OB_13_safe_int_방어()
    print("\n" + "=" * 60)
    print("✅ 단위 2-1 13 시나리오 모두 PASS")
    print("=" * 60)
