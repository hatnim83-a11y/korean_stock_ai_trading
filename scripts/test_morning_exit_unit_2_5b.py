"""단위 2-5b 테스트 — morning_price_collector + exit_target_query + mark_entered_phase1_only.

테스트 분류:
- COL-1~4: MorningPriceCollector (정상 / 시가 0 / 예외 / 잘못된 ticker)
- QUERY-1~6: select_exit_targets (entered / phase1 only / 둘 다 / 빈 결과 / 이미 exit / 다른 날짜)
- P1ONLY-1~2: mark_entered_phase1_only (정상 / phase1 미체결 → LookupError)

실행:
    venv/bin/python scripts/test_morning_exit_unit_2_5b.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors.morning_price_collector import (
    MorningPriceCollector,
    MorningPriceSnapshot,
)
from closing_bet_system.execution.exit_target_query import (
    ExitTarget,
    select_exit_targets,
)
from closing_bet_system.storage.candidate_logger import CandidateLogger
from closing_bet_system.storage.db import ClosingBetDatabase


# ===== 픽스처 =====


def _make_db(tmpdir: Path) -> ClosingBetDatabase:
    db = ClosingBetDatabase(db_path=tmpdir / "test_unit_2_5b.db")
    db.connect()
    db.init_tables()
    return db


def _insert_candidate(
    db: ClosingBetDatabase,
    *,
    ticker: str,
    name: str = "테스트종목",
    total_score: int = 3,
    trade_date: str = "2026-05-15",
    candidate_status: str = "recommended",
) -> int:
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO candidates (trade_date, ticker, name, candidate_status,
                                    layer1_score, layer2_score, layer3_score,
                                    total_score)
            VALUES (?, ?, ?, ?, 1, 1, 0, ?)
            """,
            (trade_date, ticker, name, candidate_status, total_score),
        )
        return cursor.lastrowid


def _set_phase1(db, cid, *, price=75000.0, shares=5):
    with db.get_cursor() as cursor:
        cursor.execute(
            """UPDATE candidates SET entry_phase1_order_id='ODNO_P1',
               entry_phase1_executed_price=?, entry_phase1_executed_shares=?
               WHERE candidate_id=?""",
            (price, shares, cid),
        )


def _set_phase2(db, cid, *, price=75500.0, shares=5):
    with db.get_cursor() as cursor:
        cursor.execute(
            """UPDATE candidates SET entry_phase2_order_id='ODNO_P2',
               entry_phase2_executed_price=?, entry_phase2_executed_shares=?
               WHERE candidate_id=?""",
            (price, shares, cid),
        )


def _set_entered(db, cid, *, price=75250.0, amount=752500.0):
    with db.get_cursor() as cursor:
        cursor.execute(
            """UPDATE candidates SET candidate_status='entered',
               entry_price=?, entry_amount=?, entry_time='2026-05-15T15:25:00'
               WHERE candidate_id=?""",
            (price, amount, cid),
        )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===== COL 테스트 (4건) =====


def test_COL_1_snapshot_normal():
    """COL-1: get_current_price 정상 응답 → MorningPriceSnapshot 정상."""
    mock_kis = MagicMock()
    mock_kis.get_current_price.return_value = {
        "code": "005930", "name": "삼성전자", "price": 75000,
        "open": 74800, "high": 75500, "low": 74500, "change_rate": 0.67,
        "volume": 10_000_000,
    }
    collector = MorningPriceCollector(mock_kis)
    snap = _run(collector.get_snapshot("005930"))
    assert snap is not None
    assert snap.open_price == 74800
    assert snap.high == 75500
    assert snap.current_price == 75000
    assert snap.change_rate == 0.67
    print("✅ COL-1 정상 응답 → snapshot 정확 (open=74800)")


def test_COL_2_zero_open():
    """COL-2: 시가 0 → None graceful (시가 갭률 계산 불가)."""
    mock_kis = MagicMock()
    mock_kis.get_current_price.return_value = {
        "code": "005930", "open": 0, "high": 0, "low": 0, "price": 0,
        "change_rate": 0.0,
    }
    collector = MorningPriceCollector(mock_kis)
    snap = _run(collector.get_snapshot("005930"))
    assert snap is None
    print("✅ COL-2 시가 0 → None graceful")


def test_COL_3_exception():
    """COL-3: KIS 예외 → None graceful."""
    mock_kis = MagicMock()
    mock_kis.get_current_price.side_effect = RuntimeError("KIS 500")
    collector = MorningPriceCollector(mock_kis)
    snap = _run(collector.get_snapshot("005930"))
    assert snap is None
    print("✅ COL-3 예외 → None graceful")


def test_COL_4_invalid_ticker():
    """COL-4: 잘못된 ticker(6자리 X) → None + KIS 호출 X."""
    mock_kis = MagicMock()
    collector = MorningPriceCollector(mock_kis)
    for bad in ["12345", "0059AB", "", None, "1234567"]:
        snap = _run(collector.get_snapshot(bad))
        assert snap is None, f"bad={bad!r}"
    mock_kis.get_current_price.assert_not_called()
    print("✅ COL-4 잘못된 ticker → None graceful (KIS 미호출)")


# ===== QUERY 테스트 (6건) =====


def test_QUERY_1_entered_only():
    """QUERY-1: entered 상태만 select."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자")
        _set_phase1(db, cid)
        _set_phase2(db, cid)
        _set_entered(db, cid)

        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 1
        assert targets[0].candidate_status == "entered"
        assert targets[0].is_phase1_only is False
        assert targets[0].total_shares == 10
        print("✅ QUERY-1 entered 1건 정확")
        db.close()


def test_QUERY_2_phase1_only():
    """QUERY-2: phase1 only 보유 (status='recommended' + phase2 NULL)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930")
        _set_phase1(db, cid)
        # phase2 미체결 (NULL)

        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 1
        assert targets[0].candidate_status == "recommended"
        assert targets[0].is_phase1_only is True
        assert targets[0].total_shares == 5
        print("✅ QUERY-2 phase1 only 1건 정확 (is_phase1_only=True)")
        db.close()


def test_QUERY_3_both_cases():
    """QUERY-3: entered + phase1 only 동시 select."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid1 = _insert_candidate(db, ticker="005930", name="A")
        cid2 = _insert_candidate(db, ticker="000660", name="B")
        _set_phase1(db, cid1); _set_phase2(db, cid1); _set_entered(db, cid1)
        _set_phase1(db, cid2)

        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 2
        statuses = {t.candidate_status for t in targets}
        assert statuses == {"entered", "recommended"}
        phase1_only_count = sum(1 for t in targets if t.is_phase1_only)
        assert phase1_only_count == 1
        print(f"✅ QUERY-3 entered 1 + phase1_only 1 동시 select")
        db.close()


def test_QUERY_4_empty():
    """QUERY-4: 매도 대상 0건 (recommended만 + phase1 미체결) → 빈 결과."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        _insert_candidate(db, ticker="005930")
        # phase1 / phase2 모두 미체결 → 매도 대상 아님
        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 0
        print("✅ QUERY-4 phase1 미체결 → 매도 대상 0건")
        db.close()


def test_QUERY_5_already_exited():
    """QUERY-5: exit_time 채워진 종목 제외."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930")
        _set_phase1(db, cid); _set_phase2(db, cid); _set_entered(db, cid)
        # exit_time 박제
        with db.get_cursor() as cur:
            cur.execute(
                "UPDATE candidates SET exit_time='2026-05-16T09:30:00' WHERE candidate_id=?",
                (cid,),
            )
        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 0
        print("✅ QUERY-5 이미 매도 → 매도 대상 제외")
        db.close()


def test_QUERY_6_different_date():
    """QUERY-6: trade_date 다른 종목 제외."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930", trade_date="2026-05-14")
        _set_phase1(db, cid); _set_phase2(db, cid); _set_entered(db, cid)

        # 5/15 매도 대상 select → 5/14 candidate 제외
        targets = select_exit_targets(cl, "2026-05-15")
        assert len(targets) == 0
        # 5/14 매도 대상 → 1건
        targets14 = select_exit_targets(cl, "2026-05-14")
        assert len(targets14) == 1
        print("✅ QUERY-6 trade_date 필터 정확")
        db.close()


# ===== P1ONLY 테스트 (2건) =====


def test_P1ONLY_1_normal():
    """P1ONLY-1: phase1 체결 정보 정상 → mark_entered_phase1_only 성공.

    log_exit 호출 가능 상태로 전환 검증.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930")
        _set_phase1(db, cid, price=75000.0, shares=5)

        # 전환 전 status='recommended' + entry_price NULL
        row_before = cl.get_candidate(cid)
        assert row_before["candidate_status"] == "recommended"
        assert row_before["entry_price"] is None

        cl.mark_entered_phase1_only(cid)

        row_after = cl.get_candidate(cid)
        assert row_after["candidate_status"] == "entered"
        assert row_after["entry_price"] == 75000.0
        assert row_after["entry_amount"] == 375000.0  # 75000 × 5
        assert row_after["entry_time"] is not None
        print("✅ P1ONLY-1 phase1 only → entered 전환 정상 (entry_price=75000 amount=375000)")
        db.close()


def test_P1ONLY_2_no_phase1():
    """P1ONLY-2: phase1 미체결 → LookupError raise (log_exit 호출 불가)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cl = CandidateLogger(db=db)
        cid = _insert_candidate(db, ticker="005930")
        # phase1 미체결 (NULL)

        try:
            cl.mark_entered_phase1_only(cid)
            assert False, "LookupError 기대"
        except LookupError as e:
            assert "phase1 체결 정보 부재" in str(e)
            print(f"✅ P1ONLY-2 phase1 미체결 → LookupError 정상 raise")
        db.close()


# ===== Runner =====


def main():
    tests = [
        test_COL_1_snapshot_normal,
        test_COL_2_zero_open,
        test_COL_3_exception,
        test_COL_4_invalid_ticker,
        test_QUERY_1_entered_only,
        test_QUERY_2_phase1_only,
        test_QUERY_3_both_cases,
        test_QUERY_4_empty,
        test_QUERY_5_already_exited,
        test_QUERY_6_different_date,
        test_P1ONLY_1_normal,
        test_P1ONLY_2_no_phase1,
    ]
    print(f"\n=== 단위 2-5b 테스트 — {len(tests)}건 실행 ===\n")
    fails = []
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            fails.append((fn.__name__, type(exc).__name__, str(exc)))
            print(f"❌ {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n=== 결과: {len(tests) - len(fails)}/{len(tests)} PASS ===")
    if fails:
        for name, exc_type, msg in fails:
            print(f"  ❌ {name} — {exc_type}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
