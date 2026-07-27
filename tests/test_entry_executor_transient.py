"""test_entry_executor_transient.py — 종가베팅 진입 시 일시적 잔고오류 비영구화 (Part C+D).

2026-07-10 KB금융(candidate 666) 재현: fund_guard 가 일시적 KIS 500 으로 차단하면
기존 코드는 영구 `rejected_filter` 처리 → 다음 관측/재시도 불가. 수정 후:
- fund_guard decision.transient=True → **후보는 recommended 유지**(영구거부 금지),
  rejection_reason="fund_guard_transient", Phase1Result.fund_guard_transient 집계.
- decision.transient=False(진짜 한도 초과) → 기존대로 rejected_filter.
- **잔고 추정으로 주문 허용 없음** — transient 여도 order.submitted=False.

실 KIS/DB 미의존 — collaborators mock + 임시 closing_bet.db.

실행: pytest tests/test_entry_executor_transient.py -v
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from closing_bet_system.collectors.vwap_collector import VWAPSnapshot
from closing_bet_system.execution.entry_executor import (
    EntryExecutor,
    EntryExecutorSettings,
)
from closing_bet_system.infra.fund_guard import OrderDecision
from closing_bet_system.storage.candidate_logger import CandidateLogger
from closing_bet_system.storage.db import ClosingBetDatabase
from modules.market_guard import MarketStatus

TRADE_DATE = "2026-07-10"


def _make_db(tmpdir: Path) -> ClosingBetDatabase:
    db = ClosingBetDatabase(db_path=tmpdir / "cb.db")
    db.connect()
    db.init_tables()
    return db


def _insert_candidate(db: ClosingBetDatabase, ticker: str, name: str) -> int:
    with db.get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO candidates (trade_date, ticker, name, candidate_status,
                                    layer1_score, layer2_score, layer3_score, total_score)
            VALUES (?, ?, ?, 'recommended', 1, 1, 0, 2)
            """,
            (TRADE_DATE, ticker, name),
        )
        return cur.lastrowid


def _make_executor(db, *, decision: OrderDecision):
    settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=False)

    fund_guard = MagicMock()
    fund_guard.config = MagicMock(
        capital_ratio=0.5, max_position_per_stock=0.25,
        max_concurrent_positions=4, max_daily_entries=4, weekly_loss_limit=-0.05,
    )
    fund_guard._get_total_value.return_value = 9_091_759
    fund_guard.compute_capital_limit.return_value = (
        int(9_091_759 * 0.5), {"mode": "test"},
    )
    fund_guard.evaluate_order.return_value = decision
    # allow_order wrapper 도 정합(방어)
    fund_guard.allow_order.return_value = (decision.allowed, decision.reason)

    candidate_logger = CandidateLogger(db=db)

    vwap_collector = MagicMock()
    vwap_collector.get_snapshot = AsyncMock(
        return_value=VWAPSnapshot(ticker="105560", vwap=10000.0, high=10000)
    )
    fill_checker = MagicMock()
    fill_checker.get_fill_status = AsyncMock(return_value=None)
    market_guard = MagicMock()
    market_guard.check.return_value = (MarketStatus.NORMAL, {"reason": "t"})
    entry_notifier = MagicMock()

    return EntryExecutor(
        kis_order_api=MagicMock(),
        fund_guard=fund_guard,
        candidate_logger=candidate_logger,
        vwap_collector=vwap_collector,
        estimated_price_collector=MagicMock(),
        orderbook_collector=MagicMock(),
        fill_checker=fill_checker,
        market_guard=market_guard,
        entry_notifier=entry_notifier,
        settings=settings,
    )


def test_transient_balance_failure_keeps_candidate_recommended():
    """일시적 잔고오류 → 후보 recommended 유지(영구 rejected_filter 금지)."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ee_transient_"))
    db = _make_db(tmpdir)
    cid = _insert_candidate(db, "105560", "KB금융")

    decision = OrderDecision(
        allowed=False, reason="총 자산 조회 실패 또는 0원 (보수적 차단)", transient=True
    )
    ex = _make_executor(db, decision=decision)
    result = asyncio.run(ex.execute_phase1(TRADE_DATE))

    row = CandidateLogger(db=db).get_candidate(cid)
    assert row["candidate_status"] == "recommended", (
        f"일시적 오류인데 영구 처리됨: {row['candidate_status']} / {row['rejection_reason']}"
    )
    assert result.fund_guard_transient == 1
    assert result.fund_guard_rejected == 0
    assert result.submitted == 0
    assert result.orders and result.orders[0].rejection_reason == "fund_guard_transient"
    assert result.orders[0].submitted is False  # 추정 허용 금지
    db.close()


def test_permanent_rejection_marks_rejected_filter():
    """진짜 한도 초과(transient=False) → 기존대로 rejected_filter."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ee_perm_"))
    db = _make_db(tmpdir)
    cid = _insert_candidate(db, "105560", "KB금융")

    decision = OrderDecision(
        allowed=False, reason="1종목 비중 초과: ...", transient=False
    )
    ex = _make_executor(db, decision=decision)
    result = asyncio.run(ex.execute_phase1(TRADE_DATE))

    row = CandidateLogger(db=db).get_candidate(cid)
    assert row["candidate_status"] == "rejected_filter"
    assert "fund_guard:" in (row["rejection_reason"] or "")
    assert result.fund_guard_rejected == 1
    assert result.fund_guard_transient == 0
    db.close()
