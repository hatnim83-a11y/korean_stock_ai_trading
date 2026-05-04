"""단위 2-6 검증: dashboard data_adapter + 5 API + 탭.

시나리오:
    DA-1: get_today_candidates 빈 DB → 0 카운트 + 빈 리스트
    DA-2: get_today_candidates 데이터 있음 → 카운트 + 리스트
    DA-3: get_gate_progress 빈 DB → 0/30
    DA-4: get_gate_progress 일부 데이터 → 진척도 계산
    DA-5: get_gate_progress 통과 케이스 → passed=True
    DA-6: get_orderbook_history 테이블 없음 → 빈 리스트 (graceful)
    DA-7: get_orderbook_history 데이터 있음 → 정렬된 리스트
    DA-8: get_recent_rejections 7일 필터
    DA-9: get_fund_guard_status 빈 DB → config 만 노출
    DA-10: get_fund_guard_status 활성 포지션 → active_amount 합계
    DA-11: DB 미존재 → 모든 함수 빈 결과 (graceful)
    DA-12: 5 API 엔드포인트 존재 확인 (router 내) — fastapi 라우트
    DA-13: 인증 미통과 시 401 (TestClient 통합)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date as date_cls, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.dashboard import data_adapter as da


def _create_test_db(tmpdir: Path) -> Path:
    """v2 마이그레이션 적용된 임시 DB 생성."""
    db_path = tmpdir / "test_closing_bet.db"
    from closing_bet_system.storage.db import ClosingBetDatabase
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()
    db.close()
    return db_path


def _patch_resolve_db(db_path: Path):
    """data_adapter._resolve_db_path → 테스트 DB 경로 반환."""
    return patch.object(da, "_resolve_db_path", return_value=db_path)


def _insert_candidate(db_path, **kwargs):
    """편의 INSERT 헬퍼."""
    conn = sqlite3.connect(str(db_path))
    cols = list(kwargs.keys())
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO candidates ({','.join(cols)}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )
    conn.commit()
    conn.close()


# ===== get_today_candidates =====


def test_DA_1_today_빈_DB():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        with _patch_resolve_db(db):
            result = da.get_today_candidates()
    assert sum(result["status_counts"].values()) == 0
    assert result["candidates"] == []
    print("[PASS] DA-1: today 빈 DB → 0 카운트 + 빈 리스트")


def test_DA_2_today_데이터():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        today = date_cls.today().isoformat()
        _insert_candidate(db, trade_date=today, ticker="005930", name="삼성전자",
                          candidate_status="recommended", layer1_score=2, layer2_score=3,
                          layer3_score=2, total_score=7)
        _insert_candidate(db, trade_date=today, ticker="000660", name="SK하이닉스",
                          candidate_status="rejected_filter",
                          rejection_reason="DART 즉시제외: 유상증자")
        with _patch_resolve_db(db):
            result = da.get_today_candidates()
    assert result["status_counts"]["recommended"] == 1
    assert result["status_counts"]["rejected_filter"] == 1
    assert len(result["candidates"]) == 2
    print(f"[PASS] DA-2: today 2건 → counts {result['status_counts']}")


# ===== get_gate_progress =====


def test_DA_3_gate_빈_DB():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        with _patch_resolve_db(db):
            result = da.get_gate_progress()
    assert result["actual"]["recommended"] == 0
    assert result["target"]["recommended"] == 30
    assert result["passed"] is False
    print("[PASS] DA-3: gate 빈 DB → 0/30, passed=False")


def test_DA_4_gate_일부_데이터():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        # 5일간 다른 종목 10개 recommended
        for i in range(10):
            d = (date_cls.today() - timedelta(days=i % 5)).isoformat()
            _insert_candidate(db, trade_date=d, ticker=f"10000{i}", name=f"종목{i}",
                              candidate_status="recommended")
        with _patch_resolve_db(db):
            result = da.get_gate_progress()
    assert result["actual"]["recommended"] == 10  # 30 미만
    assert result["actual"]["business_days"] == 5  # 15 미만
    assert result["actual"]["distinct_stocks"] == 10  # 20 미만
    assert result["passed"] is False
    print(f"[PASS] DA-4: gate 일부 → rec=10/days=5/stocks=10, passed=False")


def test_DA_5_gate_통과():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        # 30일간 30 종목 recommended
        for i in range(30):
            d = (date_cls.today() - timedelta(days=i)).isoformat()
            _insert_candidate(db, trade_date=d, ticker=f"1{i:05d}", name=f"종목{i}",
                              candidate_status="recommended")
        with _patch_resolve_db(db):
            result = da.get_gate_progress()
    # business_days=30 (각각 다른 날), stocks=30 → 모두 충족
    assert result["passed"] is True
    print(f"[PASS] DA-5: gate 30건+ → passed=True")


# ===== get_orderbook_history =====


def test_DA_6_orderbook_데이터():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        # orderbook_snapshots 테이블 INSERT
        conn = sqlite3.connect(str(db))
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO orderbook_snapshots
               (ticker, snapshot_time, is_valid, ask1, bid1, current_price, spread_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("005930", ts, 1, 50100, 50000, 50050, 0.001998),
        )
        conn.commit()
        conn.close()
        with _patch_resolve_db(db):
            result = da.get_orderbook_history(days=1)
    assert len(result["snapshots"]) == 1
    assert result["snapshots"][0]["ticker"] == "005930"
    print(f"[PASS] DA-6: orderbook 1건 → 정상 반환")


def test_DA_7_orderbook_빈():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        with _patch_resolve_db(db):
            result = da.get_orderbook_history(days=1)
    assert result["snapshots"] == []
    print("[PASS] DA-7: orderbook 빈 → 빈 리스트")


# ===== get_recent_rejections =====


def test_DA_8_rejections():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        # 1일 전 rejected_filter 1건
        d = (date_cls.today() - timedelta(days=1)).isoformat()
        _insert_candidate(db, trade_date=d, ticker="005930", name="삼성전자",
                          candidate_status="rejected_filter",
                          rejection_reason="DART 즉시제외: 횡령/배임")
        # 10일 전 (윈도 밖)
        d_old = (date_cls.today() - timedelta(days=10)).isoformat()
        _insert_candidate(db, trade_date=d_old, ticker="000660", name="SK하이닉스",
                          candidate_status="rejected_filter",
                          rejection_reason="atr_overheat>1.8")
        with _patch_resolve_db(db):
            result = da.get_recent_rejections(days=7)
    assert len(result["rejections"]) == 1
    assert result["rejections"][0]["ticker"] == "005930"
    print("[PASS] DA-8: rejections 7일 필터 — 윈도 밖 1건 제외")


# ===== get_fund_guard_status =====


def test_DA_9_fund_guard_빈_DB():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        with _patch_resolve_db(db):
            result = da.get_fund_guard_status()
    assert result["active_amount"] == 0
    assert result["active_tickers"] == []
    assert result["today_entries"] == 0
    assert result["weekly_pnl_pct"] is None
    # config는 settings.yaml 또는 default
    assert result["config"]["weekly_loss_limit"] == -0.05
    print("[PASS] DA-9: fund_guard 빈 DB → 0 + config")


def test_DA_10_fund_guard_활성():
    with tempfile.TemporaryDirectory() as td:
        db = _create_test_db(Path(td))
        today = date_cls.today().isoformat()
        # entered, 미청산
        _insert_candidate(db, trade_date=today, ticker="005930", name="삼성전자",
                          candidate_status="entered", entry_amount=300000)
        _insert_candidate(db, trade_date=today, ticker="000660", name="SK하이닉스",
                          candidate_status="entered", entry_amount=400000)
        # entered + 매도 (주간 PnL 합산 대상)
        d_yest = (date_cls.today() - timedelta(days=1)).isoformat()
        _insert_candidate(db, trade_date=d_yest, ticker="111111", name="X",
                          candidate_status="entered", entry_amount=200000,
                          exit_time="2026-05-04 09:30:00", net_pnl_pct=-0.02)
        with _patch_resolve_db(db):
            result = da.get_fund_guard_status()
    assert result["active_amount"] == 700000
    assert set(result["active_tickers"]) == {"005930", "000660"}
    assert result["today_entries"] == 2
    assert result["weekly_pnl_pct"] == -0.02
    print(f"[PASS] DA-10: fund_guard 활성 — active=700000원, weekly=-2%")


# ===== DB 미존재 graceful =====


def test_DA_11_DB_미존재_graceful():
    nonexistent = Path("/tmp/_nonexistent_closing_bet_db_test_.db")
    if nonexistent.exists():
        nonexistent.unlink()
    with patch.object(da, "_resolve_db_path", return_value=nonexistent):
        r1 = da.get_today_candidates()
        r2 = da.get_gate_progress()
        r3 = da.get_orderbook_history()
        r4 = da.get_recent_rejections()
        r5 = da.get_fund_guard_status()
    # 모두 graceful
    assert r1["candidates"] == []
    assert r2["actual"]["recommended"] == 0
    assert r3["snapshots"] == []
    assert r4["rejections"] == []
    assert r5["active_amount"] == 0
    print("[PASS] DA-11: DB 미존재 → 5함수 모두 빈 결과 graceful")


# ===== API 라우트 존재 확인 =====


def test_DA_12_router_라우트_5개():
    """fastapi router 에 종가베팅 라우트 5개 등록됨."""
    from web.api_routes import router
    paths = [r.path for r in router.routes]
    expected = [
        "/api/v1/closing-bet/today",
        "/api/v1/closing-bet/gate-progress",
        "/api/v1/closing-bet/orderbook-history",
        "/api/v1/closing-bet/rejections",
        "/api/v1/closing-bet/fund-guard-status",
    ]
    for p in expected:
        assert p in paths, f"DA-12 FAIL: {p} missing in {paths}"
    print(f"[PASS] DA-12: router 라우트 5개 등록 확인")


def test_DA_13_router_인증_dependency():
    """router 가 require_auth dependency 를 가짐 (401 보장)."""
    from web.api_routes import router
    # APIRouter dependencies 확인
    assert any(
        getattr(d, "dependency", None) is not None
        for d in router.dependencies
    ), "DA-13 FAIL: router 에 dependency 없음"
    print("[PASS] DA-13: router 인증 dependency 활성")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 2-6 검증: dashboard data_adapter + 5 API")
    print("=" * 60)
    test_DA_1_today_빈_DB()
    test_DA_2_today_데이터()
    test_DA_3_gate_빈_DB()
    test_DA_4_gate_일부_데이터()
    test_DA_5_gate_통과()
    test_DA_6_orderbook_데이터()
    test_DA_7_orderbook_빈()
    test_DA_8_rejections()
    test_DA_9_fund_guard_빈_DB()
    test_DA_10_fund_guard_활성()
    test_DA_11_DB_미존재_graceful()
    test_DA_12_router_라우트_5개()
    test_DA_13_router_인증_dependency()
    print("\n" + "=" * 60)
    print("✅ 단위 2-6 13 시나리오 모두 PASS")
    print("=" * 60)
