"""단위 E 검증: weekly_loss_limit (fund_guard).

시나리오:
    WL-1: 빈 DB (entered+exit 0건) → weekly_pnl None → 통과
    WL-2: 누적 -0.03 (-3%) > 한도 -0.05 → 통과
    WL-3: 누적 -0.05 (한도 정확) → 차단 (<=)
    WL-4: 누적 -0.07 (-7%) → 차단
    WL-5: 누적 +0.02 (+2%) → 통과
    WL-6: 7일 외 거래는 합계 제외 (8일 전 -10% → 통과)
    WL-7: net_pnl_pct=NULL 인 entered (정상 케이스: 매도 안 됨) → 합계 제외
    WL-8: GuardConfig.from_settings — settings.yaml 의 weekly_loss_limit=-0.05 로드
    WL-9: TOCTOU — _fetch_db_state 반환 dict 에 weekly_pnl 키 포함
    WL-10: 다른 검사가 먼저 차단되면 weekly_loss_limit 검사 안 함 (검사 순서 8번째)
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from closing_bet_system.infra.fund_guard import FundGuard, GuardConfig


def _create_fresh_db(tmpdir: Path) -> Path:
    """schema_v1 candidates 테이블만 가진 임시 DB."""
    db_path = tmpdir / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE candidates (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date DATE NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL,
            candidate_status TEXT NOT NULL,
            entry_amount REAL,
            exit_time TIMESTAMP,
            net_pnl_pct REAL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert(conn, *, trade_date, ticker, status="entered", entry_amount=100000,
            exit_time="2026-05-04 09:30:00", net_pnl_pct=None):
    conn.execute(
        "INSERT INTO candidates (trade_date, ticker, name, candidate_status, "
        "entry_amount, exit_time, net_pnl_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trade_date, ticker, f"종목{ticker}", status, entry_amount, exit_time, net_pnl_pct),
    )


def _make_guard(db_path: Path, total_value: int = 100_000_000):
    cfg = GuardConfig(
        capital_ratio=0.10, max_position_per_stock=0.25,
        max_concurrent_positions=4, max_daily_entries=2,
        weekly_loss_limit=-0.05,
    )
    return FundGuard(
        config=cfg,
        db_path=db_path,
        total_value_provider=lambda: total_value,
        swing_holdings_provider=lambda: set(),
    )


def test_WL_1_빈_DB_통과():
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert ok, f"WL-1 FAIL: {reason}"
    print("[PASS] WL-1: 빈 DB → 통과")


def test_WL_2_누적_3퍼_통과():
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        # 어제 -3% 매도 1건
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=-0.03)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert ok, f"WL-2 FAIL: {reason}"
    print("[PASS] WL-2: 누적 -3% > 한도 -5% → 통과")


def test_WL_3_정확히_한도_차단():
    """누적 == 한도 시 차단 (조건 <=)."""
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=-0.05)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert not ok, "WL-3 FAIL: 한도 정확치에서 통과됨"
    assert "주간 손실" in reason
    print(f"[PASS] WL-3: 누적 -5% (한도 정확) → 차단 ({reason[:30]}...)")


def test_WL_4_누적_7퍼_차단():
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=-0.04)
        _insert(conn, trade_date=str(date.today() - timedelta(days=2)),
                ticker="222222", net_pnl_pct=-0.03)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert not ok, f"WL-4 FAIL: 누적 -7% 통과 ({reason})"
    assert "주간 손실" in reason
    print(f"[PASS] WL-4: 누적 -7% → 차단")


def test_WL_5_플러스_통과():
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=+0.02)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert ok, f"WL-5 FAIL: {reason}"
    print("[PASS] WL-5: 누적 +2% → 통과")


def test_WL_6_7일_외_제외():
    """8일 전 -10% 거래는 7일 윈도 밖이라 합계 제외 → 통과."""
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=8)),
                ticker="111111", net_pnl_pct=-0.10)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert ok, f"WL-6 FAIL: 8일 전 거래 포함됨 ({reason})"
    print("[PASS] WL-6: 8일 전 거래는 7일 윈도 밖 → 통과")


def test_WL_7_NULL_제외():
    """net_pnl_pct=NULL (entered 후 미매도) 는 합계 제외."""
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        # entered 이지만 exit_time 없음 → weekly_pnl 쿼리 제외
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", exit_time=None, net_pnl_pct=None)
        # 같은 윈도 -3% 1건만 있음
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="222222", net_pnl_pct=-0.03)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert ok, f"WL-7 FAIL: {reason}"
    print("[PASS] WL-7: NULL net_pnl_pct (미매도) 합계 제외 → -3% 만 → 통과")


def test_WL_8_settings_load():
    """GuardConfig.from_settings 가 weekly_loss_limit 로드 확인."""
    cfg = GuardConfig.from_settings({
        "fund": {
            "capital_ratio": 0.10,
            "max_position_per_stock": 0.25,
            "max_concurrent_positions": 4,
            "max_daily_entries": 2,
            "weekly_loss_limit": -0.07,  # 커스텀
        }
    })
    assert cfg.weekly_loss_limit == -0.07, f"WL-8 FAIL: {cfg.weekly_loss_limit}"
    # default
    cfg_default = GuardConfig.from_settings({"fund": {}})
    assert cfg_default.weekly_loss_limit == -0.05, (
        f"WL-8 FAIL default: {cfg_default.weekly_loss_limit}"
    )
    print("[PASS] WL-8: GuardConfig.from_settings — weekly_loss_limit -0.07 / default -0.05")


def test_WL_9_db_state_키():
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        g = _make_guard(db)
        state = g._fetch_db_state()
    assert "weekly_pnl" in state, f"WL-9 FAIL: state keys = {list(state.keys())}"
    assert state["weekly_pnl"] is None  # 빈 DB
    assert state["active_amount"] == 0
    assert state["today_entries"] == 0
    print("[PASS] WL-9: _fetch_db_state 에 weekly_pnl 키 포함 (빈 DB → None)")


def test_WL_10_검사_순서_입력_먼저():
    """다른 검사가 먼저 차단되면 weekly_loss 검사 안 함 (입력 검증 1번)."""
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=-0.10)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        # 무효 ticker → 1번 검사에서 차단 (weekly_loss 도달 X)
        ok, reason = g.allow_order("abc", 100_000)
    assert not ok
    assert "유효하지 않은 종목코드" in reason, f"WL-10 FAIL: {reason}"
    print("[PASS] WL-10: 입력 검증(1번) 우선 → weekly_loss 검사 도달 X")


def test_WL_11_차단_메시지_형식():
    """차단 메시지에 누적 % 와 한도 % 가 포함."""
    with tempfile.TemporaryDirectory() as td:
        db = _create_fresh_db(Path(td))
        conn = sqlite3.connect(str(db))
        _insert(conn, trade_date=str(date.today() - timedelta(days=1)),
                ticker="111111", net_pnl_pct=-0.06)
        conn.commit()
        conn.close()
        g = _make_guard(db)
        ok, reason = g.allow_order("005930", 100_000)
    assert not ok
    assert "-6.00%" in reason and "-5.00%" in reason, f"WL-11 FAIL: {reason}"
    print(f"[PASS] WL-11: 차단 메시지 형식 — '{reason}'")


if __name__ == "__main__":
    print("=" * 60)
    print("단위 E 검증: weekly_loss_limit (fund_guard)")
    print("=" * 60)
    test_WL_1_빈_DB_통과()
    test_WL_2_누적_3퍼_통과()
    test_WL_3_정확히_한도_차단()
    test_WL_4_누적_7퍼_차단()
    test_WL_5_플러스_통과()
    test_WL_6_7일_외_제외()
    test_WL_7_NULL_제외()
    test_WL_8_settings_load()
    test_WL_9_db_state_키()
    test_WL_10_검사_순서_입력_먼저()
    test_WL_11_차단_메시지_형식()
    print("\n" + "=" * 60)
    print("✅ 단위 E 11 시나리오 모두 PASS")
    print("=" * 60)
