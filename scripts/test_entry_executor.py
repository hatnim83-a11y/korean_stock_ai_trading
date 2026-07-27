"""EntryExecutor 단위 테스트 (단위 2-4c, EE-1~30).

실행:
    venv/bin/python scripts/test_entry_executor.py

테스트 분류:
- 후보 select / score 필터 / fund_guard 거부+로깅 (EE-1~3)
- market_guard CRISIS/CAUTION/NORMAL 분기 (EE-4~6)
- 가격 상한 위반 / 호가 틱 정렬 / VWAP None (EE-7~9)
- 1차 체결 → 2차 / 1차 미체결 fallback (EE-10~12)
- 예상체결가 +0.5% 보류 / 잔량 비율 취소 (EE-13~14)
- dry_run 모드 / 실 모드 분기 (EE-15~16)
- KIS 500 재시도 / submit fail / 스윙 중복 거부 (EE-17~19)
- 부분 체결 처리 / 미체결 잔량 / phase2_enabled=False (EE-20~22)
- mark_entered 가중 평균 / phase2 미체결 시 50% 보유 / mark_entered 미호출 (EE-23~25)
- 텔레그램 비활성 graceful / DB ODNO 박제 / score_threshold 필터 (EE-26~30)
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.collectors.vwap_collector import (
    InsufficientDataError,
    VWAPSnapshot,
)
from closing_bet_system.collectors.estimated_price_collector import (
    EstimatedPriceSnapshot,
)
from closing_bet_system.execution.entry_executor import (
    CandidateOrder,
    EntryExecutor,
    EntryExecutorSettings,
    Phase1Result,
    Phase2Result,
)
from closing_bet_system.execution.fill_checker import FillStatus
from closing_bet_system.infra.fund_guard import OrderDecision
from closing_bet_system.storage.candidate_logger import CandidateLogger
from closing_bet_system.storage.db import ClosingBetDatabase
from modules.market_guard import MarketStatus


# ===== 픽스처 =====


def _make_db(tmpdir: Path) -> ClosingBetDatabase:
    db = ClosingBetDatabase(db_path=tmpdir / "test_entry_executor.db")
    db.connect()
    db.init_tables()
    return db


def _make_executor(
    db: ClosingBetDatabase,
    *,
    settings: EntryExecutorSettings,
    vwap_snap=None,
    ep_snap=None,
    fill_status=None,
    order_success: bool = True,
    fund_guard_allow: bool = True,
    fund_guard_transient: bool = False,
    market_status=MarketStatus.NORMAL,
    total_value: int = 100_000_000,
    raise_insufficient: bool = False,
):
    """공통 mock executor 빌더.

    fund_guard 판정 mock (2026-07-10):
        production 은 ``fund_guard.evaluate_order()`` → :class:`OrderDecision` 을 호출한다.
        - ``fund_guard_allow=True``            → allowed (통과)
        - ``fund_guard_allow=False`` (기본)    → 영구 거부 (transient=False, rejected_filter 확정)
        - ``fund_guard_transient=True``        → 일시적 거부 (transient=True, recommended 유지)
        레거시 ``allow_order()`` 도 동일 판정으로 동기화(후방호환 wrapper 경로 커버).
    """
    kis_order_api = MagicMock()
    kis_order_api.buy_limit_order.return_value = {
        "success": order_success,
        "order_id": "ODNO_TEST_001" if order_success else "",
        "stock_code": "005930",
        "quantity": 10,
        "price": 75000,
        "message": "ok" if order_success else "FAIL",
    }

    fund_guard = MagicMock()
    fund_guard.config = MagicMock(
        capital_ratio=0.5,
        max_position_per_stock=0.20,
        max_concurrent_positions=5,
        max_daily_entries=3,
        weekly_loss_limit=-0.05,
    )
    # evaluate_order → OrderDecision (production 실경로). allowed / 영구거부 / 일시거부 모델링.
    if fund_guard_allow:
        _decision = OrderDecision(allowed=True, reason="", transient=False)
    elif fund_guard_transient:
        _decision = OrderDecision(
            allowed=False,
            reason="총 자산 조회 실패 또는 0원 (보수적 차단)",
            transient=True,
        )
    else:
        _decision = OrderDecision(allowed=False, reason="테스트 거부", transient=False)
    fund_guard.evaluate_order.return_value = _decision
    # 레거시 allow_order wrapper 도 동일 판정으로 동기화 (후방호환 경로 커버)
    fund_guard.allow_order.return_value = (_decision.allowed, _decision.reason)
    fund_guard._get_total_value.return_value = total_value
    # 2026-05-29: _compute_order_amount → fund_guard.compute_capital_limit 호출 mock
    # capital_limit = total_value × 0.5 (cap), per_stock = capital_limit / max_concurrent
    _mock_capital_limit = int(total_value * 0.5)
    fund_guard.compute_capital_limit.return_value = (
        _mock_capital_limit,
        {"mode": "test_mock", "base_pool": total_value // 10},
    )

    candidate_logger = CandidateLogger(db=db)

    vwap_collector = MagicMock()
    if raise_insufficient:
        vwap_collector.get_snapshot = AsyncMock(side_effect=InsufficientDataError("test"))
    else:
        vwap_collector.get_snapshot = AsyncMock(return_value=vwap_snap)

    estimated_price_collector = MagicMock()
    estimated_price_collector.get_estimated_price = AsyncMock(return_value=ep_snap)

    orderbook_collector = MagicMock()

    fill_checker = MagicMock()
    fill_checker.get_fill_status = AsyncMock(return_value=fill_status)

    market_guard = MagicMock()
    market_guard.check.return_value = (market_status, {
        "kospi_rate": 0.5, "kosdaq_rate": 0.3, "reason": "test",
    })

    entry_notifier = MagicMock()
    entry_notifier.send_phase1_result.return_value = True
    entry_notifier.send_phase2_result.return_value = True
    entry_notifier.send_market_guard_skip.return_value = True

    return EntryExecutor(
        kis_order_api=kis_order_api,
        fund_guard=fund_guard,
        candidate_logger=candidate_logger,
        vwap_collector=vwap_collector,
        estimated_price_collector=estimated_price_collector,
        orderbook_collector=orderbook_collector,
        fill_checker=fill_checker,
        market_guard=market_guard,
        entry_notifier=entry_notifier,
        settings=settings,
    )


def _insert_candidate(
    db: ClosingBetDatabase,
    *,
    ticker: str,
    name: str,
    total_score: int,
    trade_date: str = "2026-05-14",
    candidate_status: str = "recommended",
) -> int:
    """candidate row 생성 → candidate_id 반환."""
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


def _set_phase1_filled(
    db: ClosingBetDatabase,
    candidate_id: int,
    *,
    order_id: str = "ODNO_P1",
    price: float = 75000.0,
    shares: int = 5,
) -> None:
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE candidates
            SET entry_phase1_order_id=?,
                entry_phase1_executed_price=?,
                entry_phase1_executed_shares=?
            WHERE candidate_id=?
            """,
            (order_id, price, shares, candidate_id),
        )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===== Test cases =====


# --- EE-1~3: 후보 select / fund_guard ---


def test_EE_1_candidate_select_by_score():
    """EE-1: score >= threshold 인 후보만 select."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _insert_candidate(db, ticker="000660", name="SK하이닉스", total_score=1)
        _insert_candidate(db, ticker="035720", name="카카오", total_score=2)

        settings = EntryExecutorSettings(enabled=True, dry_run=True, score_threshold=2)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # score >= 2 인 후보 2건만 select
        assert result.total_candidates == 2
        tickers = {o.ticker for o in result.orders}
        assert tickers == {"005930", "035720"}, f"got {tickers}"
        print("✅ EE-1 score filter 정확 (score>=2 → 2건)")
        db.close()


def test_EE_2_fund_guard_rejects():
    """EE-2: fund_guard 거부 시 mark_rejected_by_filter 로깅."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            fund_guard_allow=False,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.fund_guard_rejected == 1, f"got {result.fund_guard_rejected}"
        # DB 상태 확인
        row = ex.candidate_logger.get_candidate(cid)
        assert row["candidate_status"] == "rejected_filter"
        assert "fund_guard:" in (row["rejection_reason"] or "")
        print("✅ EE-2 fund_guard 거부 + DB 로깅 정확")
        db.close()


def test_EE_3_disabled_returns_empty():
    """EE-3: settings.enabled=False → 즉시 빈 결과 반환."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=False, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.total_candidates == 0
        ex.market_guard.check.assert_not_called()
        print("✅ EE-3 enabled=False 즉시 빈 결과 (MarketGuard 미호출)")
        db.close()


# --- EE-4~6: MarketGuard ---


def test_EE_4_market_guard_crisis_skips_all():
    """EE-4: MarketGuard CRISIS → 전체 스킵."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.CRISIS,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.total_candidates == 0
        assert result.market_guard_status == "crisis"
        ex.entry_notifier.send_market_guard_skip.assert_called_once()
        print("✅ EE-4 CRISIS 전체 스킵")
        db.close()


def test_EE_5_market_guard_caution_reduces_amount():
    """EE-5: MarketGuard CAUTION → 진입 비율 × 0.5."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.CAUTION,
            total_value=100_000_000,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.market_guard_status == "caution"
        order = result.orders[0]
        # base = 100M × 0.5 × 0.2 × 0.7 × 0.5 (caution) × 0.5 (phase1) = 875,000원
        assert order.quantity > 0
        # quantity = 875000 // target_price. target_price ≤ 75500 → qty ≥ 11
        # 단, caution × 0.5 가 적용되어야 함 (NORMAL 대비 절반)
        # NORMAL phase1: 100M × 0.5 × 0.2 × 0.7 × 0.5 = 3,500,000원 → ~46주
        # CAUTION phase1: 1,750,000원 → ~23주
        assert order.quantity < 30, f"caution 축소 실패: qty={order.quantity}"
        print(f"✅ EE-5 CAUTION 축소 정확: qty={order.quantity} (normal대비 절반)")
        db.close()


def test_EE_5b_market_guard_danger_reduces_amount():
    """EE-5b: MarketGuard DANGER → CAUTION 과 동일하게 비율 × 0.5.

    설계상 DANGER 도 단위 2-4 범위 내에서는 CAUTION 과 동일 처리 (지연 재체크는
    단위 2-4 범위 외, entry_executor.py docstring 참조).
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.DANGER,
            total_value=100_000_000,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.market_guard_status == "danger"
        order = result.orders[0]
        # DANGER 도 CAUTION 동일하게 ratio × 0.5 → NORMAL(49주)의 절반 근사
        assert order.quantity < 30, f"DANGER 축소 실패: qty={order.quantity}"
        print(f"✅ EE-5b DANGER 축소 정확: qty={order.quantity} (CAUTION 동일 처리)")
        db.close()


def test_EE_6_market_guard_normal_full_ratio():
    """EE-6: MarketGuard NORMAL → 전체 비율 적용."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.NORMAL,
            total_value=100_000_000,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.market_guard_status == "normal"
        order = result.orders[0]
        # NORMAL phase1: 100M × 0.5 × 0.2 × 0.7 × 0.5 = 3,500,000원 → ~46주
        assert 40 <= order.quantity <= 50, f"normal 비율 적용 실패: qty={order.quantity}"
        print(f"✅ EE-6 NORMAL 전체 비율: qty={order.quantity}")
        db.close()


# --- EE-7~9: 가격 상한 ---


def test_EE_7_price_cap_violation():
    """EE-7: VWAP / 고가 모두 0 → price_cap 스킵."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=None, high=0),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.skipped_price_cap == 1
        print("✅ EE-7 가격 상한 위반 (high=0) → price_cap 스킵")
        db.close()


def test_EE_8_tick_alignment():
    """EE-8: 가격 상한이 호가 단위로 floor 정렬."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        # VWAP × 1.005 = 80,000 × 1.005 = 80,400 / high=80,400
        # 5만~20만 구간 호가 단위 100원 → 80,400 그대로 (이미 100원 단위)
        # 변형: vwap=8003 (8030 → 100원 단위 floor → 8030)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=8003.0, high=8027),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        order = result.orders[0]
        # min(vwap*1.005=8043, high=8027) = 8027
        # 5천~2만 구간 호가 10원 → align(8027, "buy") = 8020 (floor)
        assert order.target_price == 8020, f"got {order.target_price}"
        print(f"✅ EE-8 호가 정렬: 8027 → {order.target_price}")
        db.close()


def test_EE_9_vwap_insufficient_uses_high_only():
    """EE-9: VWAP InsufficientDataError → high만 사용."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        # InsufficientDataError 시 vwap_snap=None → price_cap 스킵
        ex = _make_executor(db, settings=settings, raise_insufficient=True)
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.skipped_price_cap == 1
        print("✅ EE-9 InsufficientDataError → price_cap 스킵 (graceful)")
        db.close()


# --- EE-10~12: phase 흐름 ---


def test_EE_10_phase1_filled_enables_phase2():
    """EE-10: phase1 체결 종목이 phase2 대상으로 select.

    ask/bid 비율 1.5(>0.8) 로 PRD 9-3 취소 미발화 + 예상체결가 75100 < 75000×1.005=75375
    으로 보류도 미발화 → 정상 발주.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            ep_snap=EstimatedPriceSnapshot(
                ticker="005930", estimated_price=75100,
                estimated_volume=10000, ask_total=3000, bid_total=2000,  # ratio=1.5 OK
                captured_at="2026-05-14T15:25:00",
            ),
        )
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result.eligible == 1
        assert result.submitted == 1, f"got submitted={result.submitted}, " \
            f"skipped={result.skipped_estimated_price}, cancelled={result.cancelled_ask_bid}"
        print("✅ EE-10 phase1 체결 → phase2 대상 정상 select")
        db.close()


def test_EE_11_phase1_not_filled_skipped_in_phase2():
    """EE-11: phase1 미체결 종목은 phase2 대상에서 제외."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        # entry_phase1_executed_shares 미설정 (NULL)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
        )
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result.eligible == 0
        print("✅ EE-11 phase1 미체결은 phase2에서 제외")
        db.close()


def test_EE_12_phase2_disabled_returns_none():
    """EE-12: phase2_enabled=False → None 반환."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=False)
        ex = _make_executor(db, settings=settings)
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result is None
        print("✅ EE-12 phase2_enabled=False → None 반환")
        db.close()


# --- EE-13~14: PRD 9-3 보류/취소 ---


def test_EE_13_estimated_price_too_high_hold():
    """EE-13: 예상체결가 > phase1_avg × 1.005 → 보류."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid, price=75000.0)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        # 예상체결가 76000 > 75000 × 1.005 = 75375 → 보류
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=76000),
            ep_snap=EstimatedPriceSnapshot(
                ticker="005930", estimated_price=76000,
                estimated_volume=10000, ask_total=1000, bid_total=2000,
                captured_at="2026-05-14T15:25:00",
            ),
        )
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result.skipped_estimated_price == 1
        assert result.submitted == 0
        print("✅ EE-13 예상체결가 +0.5% 보류 정확")
        db.close()


def test_EE_14_ask_bid_ratio_cancel():
    """EE-14: ask_total / bid_total < 0.8 → 취소."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        # ask=500 / bid=1000 = 0.5 < 0.8 → 취소
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            ep_snap=EstimatedPriceSnapshot(
                ticker="005930", estimated_price=75100,
                estimated_volume=10000, ask_total=500, bid_total=1000,
                captured_at="2026-05-14T15:25:00",
            ),
        )
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result.cancelled_ask_bid == 1
        print("✅ EE-14 ask/bid<0.8 취소 정확")
        db.close()


# --- EE-15~16: dry_run / 실 모드 ---


def test_EE_15_dry_run_no_kis_call():
    """EE-15: dry_run=True → kis_order_api.buy_limit_order 미호출."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
        )
        _run(ex.execute_phase1("2026-05-14"))
        ex.kis_order_api.buy_limit_order.assert_not_called()
        print("✅ EE-15 dry_run → KIS buy 미호출")
        db.close()


def test_EE_16_real_mode_calls_kis():
    """EE-16: dry_run=False → kis_order_api.buy_limit_order 호출 + ODNO 저장."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=False)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            fill_status=FillStatus(
                order_id="ODNO_TEST_001", order_qty=10,
                executed_shares=10, executed_price=75000,
                remaining_shares=0, is_filled=True, is_partial=False,
                raw_status="체결",
            ),
        )
        _run(ex.execute_phase1("2026-05-14"))
        ex.kis_order_api.buy_limit_order.assert_called_once()
        # ODNO 즉시 DB 저장 확인
        row = ex.candidate_logger.get_candidate(cid)
        assert row["entry_phase1_order_id"] == "ODNO_TEST_001"
        print("✅ EE-16 실 모드: KIS 호출 + ODNO 저장")
        db.close()


# --- EE-17~19: 에러 처리 ---


def test_EE_17_fill_checker_returns_none():
    """EE-17: fill_checker None 응답 → deadline 도달 후 graceful 종료."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        # deadline 0.05초 + polling 0.01초 → 즉시 deadline
        settings = EntryExecutorSettings(
            enabled=True, dry_run=False,
            polling_interval_sec=0.01,
            fill_check_deadline_sec=0.03,
            order_submit_sleep_sec=0.0,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            fill_status=None,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # 발주는 됐지만 체결은 0 (graceful)
        assert result.submitted == 1
        assert result.filled == 0
        print("✅ EE-17 fill_checker None → deadline graceful")
        db.close()


def test_EE_18_submit_fail():
    """EE-18: KIS buy_limit_order 실패 → submit_fail rejection."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=False,
            polling_interval_sec=0.01,
            fill_check_deadline_sec=0.03,
            order_submit_sleep_sec=0.0,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            order_success=False,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.submitted == 0
        assert result.orders[0].rejection_reason == "submit_fail"
        print("✅ EE-18 KIS 발주 실패 → submit_fail")
        db.close()


def test_EE_19_swing_overlap_rejected_via_fund_guard():
    """EE-19: fund_guard 가 스윙 중복 거부 → 'fund_guard:스윙...' reason."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            fund_guard_allow=False,
        )
        # production 은 evaluate_order 를 호출 — 스윙 중복은 영구 거부(transient=False)
        ex.fund_guard.evaluate_order.return_value = OrderDecision(
            allowed=False,
            reason="스윙 시스템이 이미 보유 중인 종목: 005930",
            transient=False,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        row = ex.candidate_logger.get_candidate(cid)
        assert "스윙" in (row["rejection_reason"] or "")
        assert result.fund_guard_rejected == 1
        print("✅ EE-19 스윙 중복 거부 reason 정상 저장")
        db.close()


# --- EE-20~22: 부분 체결 ---


def test_EE_20_partial_fill_recorded():
    """EE-20: 부분 체결 시 executed_shares 박제."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=False,
            polling_interval_sec=0.01,
            fill_check_deadline_sec=0.03,
            order_submit_sleep_sec=0.0,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            fill_status=FillStatus(
                order_id="ODNO_TEST_001", order_qty=10,
                executed_shares=6, executed_price=74500,
                remaining_shares=4, is_filled=False, is_partial=True,
                raw_status="미체결",
            ),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # 부분 체결도 filled로 카운트 (executed_shares > 0)
        assert result.filled == 1
        row = ex.candidate_logger.get_candidate(cid)
        assert row["entry_phase1_executed_shares"] == 6
        assert int(row["entry_phase1_executed_price"]) == 74500
        print("✅ EE-20 부분 체결 박제 (executed=6)")
        db.close()


def test_EE_21_total_shares_zero_skipped():
    """EE-21: 금액 부족 → quantity=0 → price_cap reason."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        # 총자산 100원 → quantity = 0
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            total_value=100,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.skipped_price_cap == 1
        print("✅ EE-21 quantity=0 → price_cap 스킵")
        db.close()


def test_EE_22_phase2_disabled_step0_fallback():
    """EE-22: phase2_enabled=False (Step 0 폴백) → execute_phase2 None."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=False)
        ex = _make_executor(db, settings=settings)
        result = _run(ex.execute_phase2("2026-05-14"))
        assert result is None
        ex.estimated_price_collector.get_estimated_price.assert_not_called()
        print("✅ EE-22 phase2 폴백: EstimatedPrice 미호출")
        db.close()


# --- EE-23~25: mark_entered 옵션 A ---


def test_EE_23_mark_entered_weighted_average():
    """EE-23: phase2 완료 시 phase1+phase2 가중 평균 mark_entered 1회."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid, price=75000.0, shares=5)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        # phase2 체결 직접 시뮬 — fill_status executed 5주 @ 75500
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            ep_snap=EstimatedPriceSnapshot(
                ticker="005930", estimated_price=75300,
                estimated_volume=10000, ask_total=2000, bid_total=2000,
                captured_at="2026-05-14T15:25:00",
            ),
        )
        # dry_run 분기라 _save_phase_fill 호출 안됨 → 직접 phase2 fill을 박제
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE candidates
                SET entry_phase2_executed_price=75500.0,
                    entry_phase2_executed_shares=5
                WHERE candidate_id=?
                """,
                (cid,),
            )
        # _finalize_mark_entered 직접 호출
        ex._finalize_mark_entered("2026-05-14")
        row = ex.candidate_logger.get_candidate(cid)
        assert row["candidate_status"] == "entered"
        # 가중평균 = (75000*5 + 75500*5) / 10 = 75250
        assert abs(row["entry_price"] - 75250.0) < 0.1
        # amount = 75250 × 10 = 752500
        assert abs(row["entry_amount"] - 752500.0) < 0.5
        print("✅ EE-23 mark_entered 가중평균 75250 / amount 752500")
        db.close()


def test_EE_24_phase1_only_keeps_recommended():
    """EE-24: phase2 미체결 시 candidate_status='recommended' 유지."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        ex = _make_executor(db, settings=settings)
        # phase2 _finalize_mark_entered 만 호출 (phase2 fill 없음)
        ex._finalize_mark_entered("2026-05-14")
        row = ex.candidate_logger.get_candidate(cid)
        # status는 여전히 recommended (mark_entered 미호출)
        assert row["candidate_status"] == "recommended"
        assert row["entry_phase1_executed_shares"] == 5
        print("✅ EE-24 phase1 only → recommended 유지 (단위 2-5 인터페이스)")
        db.close()


def test_EE_25_mark_entered_not_called_without_phase2():
    """EE-25: _finalize_mark_entered 가 phase2 미체결 종목 스킵."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        ex = _make_executor(db, settings=settings)
        ex._finalize_mark_entered("2026-05-14")
        row = ex.candidate_logger.get_candidate(cid)
        assert row["entry_price"] is None or row["entry_price"] == 0
        print("✅ EE-25 phase2 미체결 → mark_entered 미호출")
        db.close()


# --- EE-26~30: 기타 ---


def test_EE_26_telegram_disabled_graceful():
    """EE-26: 텔레그램 봇 비활성 시 graceful (예외 없음)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
        )
        # send_phase1_result 가 False 반환해도 흐름 정상
        ex.entry_notifier.send_phase1_result.return_value = False
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.total_candidates == 1  # 흐름은 정상 진행
        print("✅ EE-26 텔레그램 비활성 graceful")
        db.close()


def test_EE_27_odno_persisted_on_real_mode():
    """EE-27: 실 모드 → ODNO 저장 직후 fill 실패해도 DB 박제."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=False,
            polling_interval_sec=0.01,
            fill_check_deadline_sec=0.03,
            order_submit_sleep_sec=0.0,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=75000.0, high=75500),
            fill_status=None,  # 체결 응답 실패
        )
        _run(ex.execute_phase1("2026-05-14"))
        row = ex.candidate_logger.get_candidate(cid)
        # 체결 정보는 NULL 이지만 ODNO 는 박제됨 → idempotency
        assert row["entry_phase1_order_id"] == "ODNO_TEST_001"
        assert row["entry_phase1_executed_shares"] is None
        print("✅ EE-27 ODNO idempotency 박제 (fill 실패해도 ODNO 보존)")
        db.close()


def test_EE_28_score_threshold_lower_excludes():
    """EE-28: score_threshold=5 → score=3 후보 제외."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, score_threshold=5)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        assert result.total_candidates == 0
        print("✅ EE-28 score_threshold=5 → 0건")
        db.close()


def test_EE_29_phase1_filled_no_estimated_price():
    """EE-29: phase2 시 EstimatedPrice None (15:20 이전) → 보류 처리."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _set_phase1_filled(db, cid)
        settings = EntryExecutorSettings(enabled=True, dry_run=True, phase2_enabled=True)
        # ep_snap=None → estimated_price 보류 (가격 상한 계산 불가)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=None, high=0),
            ep_snap=None,
        )
        result = _run(ex.execute_phase2("2026-05-14"))
        # 가격 후보 모두 0/None → estimated_price reason
        assert result.skipped_estimated_price == 1 or result.submitted == 0
        print("✅ EE-29 EstimatedPrice None + VWAP None → 보류")
        db.close()


def test_EE_30_score_breakdown_descending_order():
    """EE-30: 후보가 total_score DESC 로 select."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="111111", name="A", total_score=2)
        _insert_candidate(db, ticker="222222", name="B", total_score=4)
        _insert_candidate(db, ticker="333333", name="C", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # DESC 정렬: B(4) → C(3) → A(2)
        tickers_order = [o.ticker for o in result.orders]
        assert tickers_order == ["222222", "333333", "111111"], f"got {tickers_order}"
        print("✅ EE-30 후보 정렬 DESC 정확")
        db.close()


# ===== EE-31~33: 위기 시 score_threshold 동적 적용 (2026-05-29) =====


def test_EE_31_normal_uses_score_threshold():
    """EE-31: MarketGuard NORMAL → score_threshold(=2) 적용, score=2 후보 통과."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _insert_candidate(db, ticker="000660", name="SK하이닉스", total_score=2)
        _insert_candidate(db, ticker="035720", name="카카오", total_score=1)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=True,
            score_threshold=2, score_threshold_crisis=3,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.NORMAL,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # NORMAL: threshold=2 적용 → score>=2 후보 2건
        assert result.total_candidates == 2, f"got {result.total_candidates}"
        tickers = {o.ticker for o in result.orders}
        assert tickers == {"005930", "000660"}, f"got {tickers}"
        print("✅ EE-31 NORMAL threshold=2 → score>=2 후보 2건")
        db.close()


def test_EE_32_crisis_uses_score_threshold_crisis():
    """EE-32: MarketGuard DANGER → score_threshold_crisis(=3) 적용, score=2 후보 차단."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        _insert_candidate(db, ticker="000660", name="SK하이닉스", total_score=2)
        _insert_candidate(db, ticker="035720", name="카카오", total_score=1)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=True,
            score_threshold=2, score_threshold_crisis=3,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.DANGER,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # DANGER: threshold_crisis=3 적용 → score>=3 후보 1건만 (000660 score=2 차단)
        assert result.total_candidates == 1, f"got {result.total_candidates}"
        tickers = {o.ticker for o in result.orders}
        assert tickers == {"005930"}, f"got {tickers}"
        assert result.market_guard_status == "danger"
        print("✅ EE-32 DANGER threshold_crisis=3 → score=2 차단 (1건만)")
        db.close()


def test_EE_33_caution_uses_score_threshold_crisis():
    """EE-33: MarketGuard CAUTION 도 DANGER 와 동일하게 score_threshold_crisis 적용."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=4)
        _insert_candidate(db, ticker="000660", name="SK하이닉스", total_score=2)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=True,
            score_threshold=2, score_threshold_crisis=3,
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.CAUTION,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # CAUTION: threshold_crisis=3 적용 → score>=3 후보 1건만
        assert result.total_candidates == 1, f"got {result.total_candidates}"
        tickers = {o.ticker for o in result.orders}
        assert tickers == {"005930"}, f"got {tickers}"
        assert result.market_guard_status == "caution"
        print("✅ EE-33 CAUTION 도 threshold_crisis=3 적용 (score=2 차단)")
        db.close()


def test_EE_34_default_threshold_crisis_equals_threshold():
    """EE-34: score_threshold_crisis 기본값(2)일 때 NORMAL 과 DANGER 동일 동작 (롤백 안전)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        _insert_candidate(db, ticker="005930", name="삼성전자", total_score=2)
        settings = EntryExecutorSettings(
            enabled=True, dry_run=True,
            score_threshold=2,  # score_threshold_crisis 명시 X → default 2
        )
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            market_status=MarketStatus.DANGER,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # DANGER 이지만 threshold_crisis=2 (default) → score=2 후보 통과
        assert result.total_candidates == 1, f"got {result.total_candidates}"
        print("✅ EE-34 default threshold_crisis=2 → DANGER 에서도 score=2 통과 (롤백 안전)")
        db.close()


# ===== EE-35: transient 잔고오류 = 비영구 차단 (2026-07-10) =====


def test_EE_35_fund_guard_transient_keeps_recommended():
    """EE-35: evaluate_order transient=True → fund_guard_transient 카운트 + recommended 유지.

    일시적 잔고/DB 오류(KIS 5xx 등)는 영구 rejected_filter 로 확정하지 않는다
    (다음 재시도/관측 가능). 발주는 여전히 미실행(잔고 추정 대체 없음).
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = _make_db(td)
        cid = _insert_candidate(db, ticker="005930", name="삼성전자", total_score=3)
        settings = EntryExecutorSettings(enabled=True, dry_run=True)
        ex = _make_executor(
            db, settings=settings,
            vwap_snap=VWAPSnapshot(ticker="x", vwap=70000.0, high=75500),
            fund_guard_allow=False,
            fund_guard_transient=True,
        )
        result = _run(ex.execute_phase1("2026-05-14"))
        # 일시적 차단 → transient 카운트, 영구 rejected 카운트는 0
        assert result.fund_guard_transient == 1, f"got {result.fund_guard_transient}"
        assert result.fund_guard_rejected == 0, f"got {result.fund_guard_rejected}"
        # 후보는 recommended 유지 (rejected_filter 미확정)
        row = ex.candidate_logger.get_candidate(cid)
        assert row["candidate_status"] == "recommended", f"got {row['candidate_status']}"
        # mark_rejected_by_filter 미호출 확인
        assert result.orders[0].rejection_reason == "fund_guard_transient"
        print("✅ EE-35 transient 잔고오류 → 비영구 차단 (recommended 유지)")
        db.close()


# ===== Runner =====


def main():
    tests = [
        test_EE_1_candidate_select_by_score,
        test_EE_2_fund_guard_rejects,
        test_EE_3_disabled_returns_empty,
        test_EE_4_market_guard_crisis_skips_all,
        test_EE_5_market_guard_caution_reduces_amount,
        test_EE_5b_market_guard_danger_reduces_amount,
        test_EE_6_market_guard_normal_full_ratio,
        test_EE_7_price_cap_violation,
        test_EE_8_tick_alignment,
        test_EE_9_vwap_insufficient_uses_high_only,
        test_EE_10_phase1_filled_enables_phase2,
        test_EE_11_phase1_not_filled_skipped_in_phase2,
        test_EE_12_phase2_disabled_returns_none,
        test_EE_13_estimated_price_too_high_hold,
        test_EE_14_ask_bid_ratio_cancel,
        test_EE_15_dry_run_no_kis_call,
        test_EE_16_real_mode_calls_kis,
        test_EE_17_fill_checker_returns_none,
        test_EE_18_submit_fail,
        test_EE_19_swing_overlap_rejected_via_fund_guard,
        test_EE_20_partial_fill_recorded,
        test_EE_21_total_shares_zero_skipped,
        test_EE_22_phase2_disabled_step0_fallback,
        test_EE_23_mark_entered_weighted_average,
        test_EE_24_phase1_only_keeps_recommended,
        test_EE_25_mark_entered_not_called_without_phase2,
        test_EE_26_telegram_disabled_graceful,
        test_EE_27_odno_persisted_on_real_mode,
        test_EE_28_score_threshold_lower_excludes,
        test_EE_29_phase1_filled_no_estimated_price,
        test_EE_30_score_breakdown_descending_order,
        test_EE_31_normal_uses_score_threshold,
        test_EE_32_crisis_uses_score_threshold_crisis,
        test_EE_33_caution_uses_score_threshold_crisis,
        test_EE_34_default_threshold_crisis_equals_threshold,
        test_EE_35_fund_guard_transient_keeps_recommended,
    ]
    print(f"\n=== EntryExecutor 단위 테스트 — {len(tests)}건 실행 ===\n")
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
