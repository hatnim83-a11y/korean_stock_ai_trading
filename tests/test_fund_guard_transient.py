"""test_fund_guard_transient.py — fund_guard 일시적 잔고오류 재시도/비영구화 (Part C+D).

2026-07-10 사건 재현: KIS inquire-balance transient 500 → `총 자산 조회 실패(보수적 차단)`
→ KB금융 후보 영구 rejected_filter. 수정 후:
- 잔고 조회 실패 시 제한된 재시도(주입 sleep, 실대기 없음) 후 성공하면 통과.
- 모든 재시도 실패면 여전히 안전 차단하되 `transient=True` (호출측이 영구거부 보류).
- **잔고 추정으로 주문 허용 금지** — 값 확보 실패 시 항상 차단.

실 KIS/DB 미의존 — provider 주입 + 임시 closing_bet.db.

실행: pytest tests/test_fund_guard_transient.py -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from closing_bet_system.infra.fund_guard import FundGuard, GuardConfig, OrderDecision
from closing_bet_system.storage.db import ClosingBetDatabase


def _make_temp_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="fg_transient_"))
    db_path = tmp / "closing_bet.db"
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()
    db.close()
    return db_path


def _guard(total_value_provider, *, retry_count=2, sleeps=None) -> FundGuard:
    cfg = GuardConfig(
        capital_ratio=0.5,
        max_concurrent_positions=4,
        max_daily_entries=4,
        balance_retry_count=retry_count,
        balance_retry_backoff_sec=0.5,
    )
    return FundGuard(
        config=cfg,
        db_path=_make_temp_db(),
        total_value_provider=total_value_provider,
        swing_holdings_provider=lambda: set(),
        sleep_func=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


# ===== D: 첫 실패 후 성공 → 통과 =====

def test_transient_then_success_allows_order():
    """첫 잔고 조회 0(500 실패) 다음 성공(9,091,759) → 후보 차단되지 않음(KB금융 재현)."""
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return 0 if calls["n"] == 1 else 9_091_759

    sleeps = []
    guard = _guard(provider, retry_count=2, sleeps=sleeps)
    decision = guard.evaluate_order("105560", 1_000_000)  # KB금융
    assert isinstance(decision, OrderDecision)
    assert decision.allowed, f"첫실패 후 성공인데 차단됨: {decision.reason}"
    assert decision.transient is False
    assert calls["n"] == 2, "재시도가 1회 발생해야 함"
    assert sleeps == [0.5], "backoff sleep 이 재시도 사이 1회 주입돼야 함"


# ===== D: 전 재시도 실패 → 안전 차단 + transient =====

def test_all_retries_fail_blocks_as_transient():
    """모든 잔고 조회 실패 → 차단하되 transient=True (영구 rejected_filter 금지 신호)."""
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return 0

    sleeps = []
    guard = _guard(provider, retry_count=2, sleeps=sleeps)
    decision = guard.evaluate_order("105560", 1_000_000)
    assert not decision.allowed, "잔고 미확보인데 허용됨(추정 금지 위반)"
    assert decision.transient is True
    assert "총 자산 조회 실패" in decision.reason
    assert calls["n"] == 3, "최초 1회 + 재시도 2회 = 3회 시도"
    assert sleeps == [0.5, 0.5]


def test_provider_exception_treated_as_transient():
    """provider 예외(500) → 0 취급 + transient. 추정 허용 절대 금지."""
    def provider():
        raise RuntimeError("HTTP 500 Server Error")

    guard = _guard(provider, retry_count=1)
    decision = guard.evaluate_order("105560", 1_000_000)
    assert not decision.allowed
    assert decision.transient is True


# ===== 후방호환: allow_order 는 (bool, str) 유지 =====

def test_allow_order_backward_compatible_tuple():
    guard = _guard(lambda: 100_000_000, retry_count=0)
    allowed, reason = guard.allow_order("105560", 1_000_000)
    assert allowed is True and reason == ""


def test_allow_order_zero_balance_still_blocks():
    """기존 회귀: 잔고 0 → 차단(reason 문자열 유지)."""
    guard = _guard(lambda: 0, retry_count=0)
    allowed, reason = guard.allow_order("105560", 1_000_000)
    assert not allowed
    assert "총 자산 조회 실패" in reason


# ===== 비-transient(진짜 한도 초과)는 transient=False 유지 =====

def test_real_limit_rejection_is_not_transient():
    """1종목 비중 초과(정상 한도 거부)는 transient=False → 영구 rejected_filter 정당."""
    guard = _guard(lambda: 10_000_000, retry_count=0)
    # capital_limit=5,000,000 / 4종목 = per_stock 1,250,000. 3,000,000 주문 → 비중 초과
    decision = guard.evaluate_order("105560", 3_000_000)
    assert not decision.allowed
    assert decision.transient is False
    assert "1종목 비중 초과" in decision.reason


def test_invalid_input_is_not_transient():
    guard = _guard(lambda: 10_000_000, retry_count=0)
    decision = guard.evaluate_order("BADCODE", 1_000_000)
    assert not decision.allowed
    assert decision.transient is False
