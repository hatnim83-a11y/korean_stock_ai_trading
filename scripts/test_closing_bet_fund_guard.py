"""scripts/test_closing_bet_fund_guard.py

종가베팅 fund_guard 5케이스 단위 테스트 (P0-1 검증).

- KIS API / 스윙 DB는 의존성 주입(provider)으로 mock
- closing_bet.db 는 임시 파일에 v1 마이그레이션 후 시나리오별 fixture 삽입
- 운영 DB(``data/closing_bet.db``)는 절대 건드리지 않음

실행:
    venv/bin/python scripts/test_closing_bet_fund_guard.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.infra.fund_guard import FundGuard, GuardConfig
from closing_bet_system.storage.db import ClosingBetDatabase
from config import now_kst


# ===== 헬퍼 =====


def _make_temp_db() -> Path:
    """v1 마이그레이션 적용된 임시 closing_bet.db 생성."""
    tmp = Path(tempfile.mkdtemp(prefix="closing_bet_test_"))
    db_path = tmp / "closing_bet.db"
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()
    db.close()
    return db_path


def _insert_entered(
    db_path: Path,
    ticker: str,
    name: str,
    amount: int,
    trade_date: str,
    has_exit: bool = False,
) -> None:
    """candidates 테이블에 entered 상태로 삽입."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO candidates (
            trade_date, ticker, name, candidate_status,
            entry_price, entry_amount, entry_time,
            exit_price, exit_time
        ) VALUES (?, ?, ?, 'entered', 10000, ?, ?, ?, ?)
        """,
        (
            trade_date,
            ticker,
            name,
            amount,
            f"{trade_date} 15:25:00",
            10500 if has_exit else None,
            f"{trade_date.replace('05-03', '05-04')} 09:30:00" if has_exit else None,
        ),
    )
    conn.commit()
    conn.close()


def _make_guard(
    db_path: Path,
    total_value: int = 100_000_000,         # 1억원
    swing_holdings: set[str] = frozenset(),
) -> FundGuard:
    """주입된 provider로 FundGuard 생성. KIS/스윙 DB 의존성 우회."""
    return FundGuard(
        config=GuardConfig(
            capital_ratio=0.10,             # 종가베팅 풀: 1천만원
            max_position_per_stock=0.25,    # 1종목 최대: 250만원
            max_concurrent_positions=4,
            max_daily_entries=2,
        ),
        db_path=db_path,
        total_value_provider=lambda: total_value,
        swing_holdings_provider=lambda: set(swing_holdings),
    )


# ===== 5 + α 케이스 =====


def test_normal_pass():
    """정상: 비어있는 DB, 한도 내 주문."""
    db_path = _make_temp_db()
    guard = _make_guard(db_path)
    allowed, reason = guard.allow_order("005930", 1_000_000)  # 1백만원
    assert allowed, f"정상 케이스 실패: {reason}"
    print(f"  [PASS] 정상 진입 통과")


def test_block_per_stock_limit():
    """1종목 비중 초과: 주문 > 250만원 (1억 × 10% × 25%)."""
    db_path = _make_temp_db()
    guard = _make_guard(db_path)
    allowed, reason = guard.allow_order("005930", 3_000_000)  # 3백만원
    assert not allowed, "1종목 비중 초과 차단 실패"
    assert "1종목 비중 초과" in reason, f"reason 메시지 불일치: {reason}"
    print(f"  [PASS] 1종목 비중 초과 차단 — {reason[:60]}...")


def test_block_capital_limit():
    """자금 한도 초과: 누적 900만원 + 신규 200만원 > 1천만원."""
    today = now_kst().date().isoformat()
    db_path = _make_temp_db()
    # 활성 4종목 미만이지만 누적 자금 한도에 가까운 상태
    _insert_entered(db_path, "111111", "A", 4_500_000, today)
    _insert_entered(db_path, "222222", "B", 4_500_000, today)
    guard = _make_guard(db_path)
    allowed, reason = guard.allow_order("333333", 2_000_000)
    assert not allowed, "자금 한도 차단 실패"
    assert "자금 한도 초과" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] 자금 한도 초과 차단 — {reason[:60]}...")


def test_block_concurrent_positions():
    """동시 보유 4종목 초과: 새 종목 진입 차단."""
    today = now_kst().date().isoformat()
    db_path = _make_temp_db()
    for i, code in enumerate(["111111", "222222", "333333", "444444"]):
        _insert_entered(db_path, code, f"S{i}", 1_000_000, today)
    guard = _make_guard(db_path)
    # 5번째 새 종목 — 200만원 (1종목 비중/자금 한도 OK)
    allowed, reason = guard.allow_order("555555", 2_000_000)
    assert not allowed, "동시 보유 차단 실패"
    assert "동시 보유 한도 초과" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] 동시 보유 4종목 초과 차단 — {reason[:60]}...")


def test_pass_existing_ticker_additional_buy():
    """기존 ticker 추가 매수: 동시 보유/일일 진입 한도 미적용."""
    today = now_kst().date().isoformat()
    db_path = _make_temp_db()
    for i, code in enumerate(["111111", "222222", "333333", "444444"]):
        _insert_entered(db_path, code, f"S{i}", 1_000_000, today)
    guard = _make_guard(db_path)
    # 기존 종목 추가 매수 — 200만원 (자금 한도 1000-400=600만원 가용)
    allowed, reason = guard.allow_order("111111", 1_500_000)
    assert allowed, f"기존 종목 추가 매수 차단됨: {reason}"
    print(f"  [PASS] 기존 ticker 추가 매수 통과 (동시 보유 한도 미적용)")


def test_block_daily_entry_limit():
    """1일 진입 2종목 초과: 3번째 새 종목 차단."""
    today = now_kst().date().isoformat()
    db_path = _make_temp_db()
    _insert_entered(db_path, "111111", "A", 1_000_000, today)
    _insert_entered(db_path, "222222", "B", 1_000_000, today)
    guard = _make_guard(db_path)
    allowed, reason = guard.allow_order("333333", 2_000_000)
    assert not allowed, "1일 진입 한도 차단 실패"
    assert "1일 진입 한도 초과" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] 1일 진입 2종목 초과 차단 — {reason[:60]}...")


def test_block_swing_duplicate():
    """스윙이 이미 보유한 종목: 차단."""
    db_path = _make_temp_db()
    guard = _make_guard(db_path, swing_holdings={"005930", "035720"})
    allowed, reason = guard.allow_order("005930", 1_000_000)
    assert not allowed, "스윙 중복 차단 실패"
    assert "스윙 시스템이 이미 보유" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] 스윙 중복 종목 차단 — {reason[:60]}...")


def test_block_total_value_zero():
    """총 자산 0원 (KIS API 실패): 보수적 차단."""
    db_path = _make_temp_db()
    guard = _make_guard(db_path, total_value=0)
    allowed, reason = guard.allow_order("005930", 1_000_000)
    assert not allowed, "총자산 0원 차단 실패"
    assert "총 자산 조회 실패" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] 총 자산 0원 보수적 차단 — {reason[:60]}...")


def test_invalid_inputs():
    """입력 검증: 잘못된 ticker / amount (float, bool 포함)."""
    db_path = _make_temp_db()
    guard = _make_guard(db_path)

    bad_cases = [
        ("12345", 1_000_000, "5자리 종목코드"),
        ("ABCDEF", 1_000_000, "영문 종목코드"),
        ("005930", 0, "0원 주문"),
        ("005930", -1_000, "음수 주문"),
        ("005930", 1_000_000.5, "float 주문"),     # float 차단
        ("005930", True, "bool 주문"),              # bool 차단 (int subclass 회피)
        (12345, 1_000_000, "non-str ticker"),       # 정수 ticker
    ]
    for ticker, amount, label in bad_cases:
        allowed, reason = guard.allow_order(ticker, amount)
        assert not allowed, f"{label} 차단 실패: {reason}"
    print(f"  [PASS] 잘못된 입력 7케이스 모두 차단 (float/bool/정수ticker 포함)")


def test_db_query_failure():
    """DB 조회 실패 시 보수적 차단."""
    # 존재하지 않는 DB 경로
    from pathlib import Path
    fake_db = Path("/tmp/nonexistent_closing_bet_xyz123.db")
    if fake_db.exists():
        fake_db.unlink()

    guard = FundGuard(
        config=GuardConfig(),
        db_path=fake_db,
        total_value_provider=lambda: 100_000_000,
        swing_holdings_provider=lambda: set(),
    )
    allowed, reason = guard.allow_order("005930", 1_000_000)
    assert not allowed, "DB 조회 실패 시 차단 실패"
    assert "DB 조회 실패" in reason, f"reason 불일치: {reason}"
    print(f"  [PASS] DB 조회 실패 보수적 차단 — {reason[:60]}...")


def main():
    print("=== fund_guard 단위 테스트 ===\n")
    tests = [
        test_normal_pass,
        test_block_per_stock_limit,
        test_block_capital_limit,
        test_block_concurrent_positions,
        test_pass_existing_ticker_additional_buy,
        test_block_daily_entry_limit,
        test_block_swing_duplicate,
        test_block_total_value_zero,
        test_invalid_inputs,
        test_db_query_failure,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()
    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
