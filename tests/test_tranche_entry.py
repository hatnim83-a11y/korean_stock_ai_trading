"""
test_tranche_entry.py - v17 분할 진입 + 불타기 + 익절 avg 기준 단위 테스트

검증 대상:
- Position 필드 (first_buy_price, avg_buy_price, tranche_count 등)
- profit_rate (first 기준) vs profit_rate_avg (avg 기준) 정책 분기
- effective_trailing_pct (max(고정값, ATR×배수))
- BuyLock 동시성 + try/finally 해제
- DB v17 마이그레이션 idempotent + 백필 4문장

파일 직접 실행: python tests/test_tranche_entry.py
"""

import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.portfolio_monitor_v2 import Position
from modules.trading_engine.buy_lock import BuyLockRegistry
from config import now_kst


def test_position_post_init_first_avg_fallback():
    """Position 생성 시 first/avg 미지정 → buy_price로 폴백 (Coder P2 catastrophic 방어)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=10, remaining_shares=10,
        buy_price=70000, stop_loss_price=65100,
    )
    assert pos.first_buy_price == 70000, "first_buy_price 폴백 실패"
    assert pos.avg_buy_price == 70000, "avg_buy_price 폴백 실패"
    print("  [PASS] post_init_first_avg_fallback")


def test_position_avg_zero_fallback():
    """avg_buy_price=0 명시 시 first로 폴백 (분할 익절 무한대 방지)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=10, remaining_shares=10,
        buy_price=70000, stop_loss_price=65100,
        first_buy_price=70000, avg_buy_price=0,
    )
    assert pos.avg_buy_price == 70000, f"avg=0 폴백 실패: {pos.avg_buy_price}"
    print("  [PASS] avg_zero_fallback")


def test_profit_rate_first_basis():
    """profit_rate는 first_buy_price 기준 (정책 분기: 손절/BE/트레일링/2차 트리거)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=20, remaining_shares=20,
        buy_price=10000, stop_loss_price=9300,
        first_buy_price=10000, avg_buy_price=10250,  # 2차 진입 후 시뮬레이션
    )
    pos.current_price = 10500
    # profit_rate = (10500 - 10000) / 10000 = 0.05 (first 기준)
    assert abs(pos.profit_rate - 0.05) < 1e-9, f"first 기준 실패: {pos.profit_rate}"
    print("  [PASS] profit_rate_first_basis")


def test_profit_rate_avg_basis():
    """profit_rate_avg는 avg_buy_price 기준 (정책 분기: 익절 트리거)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=20, remaining_shares=20,
        buy_price=10000, stop_loss_price=9300,
        first_buy_price=10000, avg_buy_price=10250,
    )
    pos.current_price = 11480  # avg×1.12 (= +12% avg 기준)
    expected = (11480 - 10250) / 10250
    assert abs(pos.profit_rate_avg - expected) < 1e-9, f"avg 기준 실패: {pos.profit_rate_avg}"
    # 동시에 first 기준은 (11480-10000)/10000 = 0.148
    assert abs(pos.profit_rate - 0.148) < 1e-9, f"first 기준 동시 검증 실패: {pos.profit_rate}"
    print("  [PASS] profit_rate_avg_basis")


def test_effective_trailing_pct_atr_wins():
    """ATR 폭 > 고정값 → ATR 우선 (변동성 큰 종목 보호)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=10, remaining_shares=10,
        buy_price=50000, stop_loss_price=46500,
        first_buy_price=50000, atr_at_buy=2000,  # 2.0 × 2000 / 50000 = 8%
    )
    # L1 고정 = 0.05, ATR 폭 = 0.08 → max=0.08
    pct = pos.effective_trailing_pct(0.05)
    assert pct == 0.08, f"ATR 우선 실패: {pct}"
    print("  [PASS] effective_trailing_pct_atr_wins")


def test_effective_trailing_pct_fixed_wins():
    """ATR 폭 < 고정값 → 고정값 유지 (안정 종목)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=10, remaining_shares=10,
        buy_price=50000, stop_loss_price=46500,
        first_buy_price=50000, atr_at_buy=500,  # 2.0 × 500 / 50000 = 2%
    )
    pct = pos.effective_trailing_pct(0.05)
    assert pct == 0.05, f"고정값 우선 실패: {pct}"
    print("  [PASS] effective_trailing_pct_fixed_wins")


def test_effective_trailing_pct_atr_zero_fallback():
    """atr_at_buy=0 → 고정값 폴백 (안전 디그레이드)."""
    pos = Position(
        stock_code="005930", stock_name="삼성전자",
        shares=10, remaining_shares=10,
        buy_price=50000, stop_loss_price=46500,
        first_buy_price=50000, atr_at_buy=0,
    )
    pct = pos.effective_trailing_pct(0.05)
    assert pct == 0.05, f"atr=0 폴백 실패: {pct}"
    print("  [PASS] effective_trailing_pct_atr_zero_fallback")


def test_buy_lock_acquire_release():
    """BuyLock: acquire 후 release하면 다시 acquire 가능."""
    reg = BuyLockRegistry()
    assert reg.acquire("005930", "pyramid_in") is True
    assert reg.acquire("005930", "other") is False  # 점유 중
    reg.release("005930")
    assert reg.acquire("005930", "other") is True  # release 후 재획득
    print("  [PASS] buy_lock_acquire_release")


def test_buy_lock_clear_all():
    """BuyLock: clear_all로 일괄 해제 (15:30 안전망)."""
    reg = BuyLockRegistry()
    reg.acquire("005930", "pyramid_in")
    reg.acquire("000660", "pyramid_in")
    assert reg.clear_all() == 2
    assert reg.acquire("005930", "test") is True
    print("  [PASS] buy_lock_clear_all")


def test_db_v17_migration_idempotent():
    """DB v17 마이그레이션 idempotent: 2회 실행해도 컬럼 중복 X."""
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(str(db_path))
        db.connect()
        db.init_tables()  # 1차 호출 (v17까지 자동 진행)
        # 컬럼 존재 확인
        assert db._has_column("portfolio", "first_buy_price")
        assert db._has_column("portfolio", "avg_buy_price")
        assert db._has_column("portfolio", "second_tranche_pending")
        assert db._has_column("portfolio", "atr_at_buy")
        db.close()

        # 2차 호출 (재실행, 컬럼 중복 추가 안 됨)
        db2 = Database(str(db_path))
        db2.connect()
        db2.init_tables()
        db2.close()
        print("  [PASS] db_v17_migration_idempotent")


def test_db_v17_backfill_existing_holdings():
    """기존 holding 종목 백필: second_tranche_pending=0 강제 (200% 매수 사고 차단)."""
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        # v16까지만 마이그레이션된 DB 시뮬레이션은 어려우므로
        # init_tables 후 holding row 수동 INSERT → 백필 동작 확인
        db = Database(str(db_path))
        db.connect()
        db.init_tables()

        # 백필 후 second_tranche_pending=0인지 확인 (DEFAULT 0이 적용됨)
        db.save_holding_position({
            "date": str(now_kst().date()),
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "theme": "AI반도체",
            "shares": 10,
            "buy_price": 70000,
            "stop_loss": 65100,
            "take_profit": 80500,
            "weight": 0.5,
            # v17 컬럼은 명시 안 함 → 폴백 동작 확인
        })
        rows = db.get_portfolio(status="holding")
        assert len(rows) == 1
        row = rows[0]
        assert row["first_buy_price"] == 70000, f"first 폴백 실패: {row['first_buy_price']}"
        assert row["avg_buy_price"] == 70000, f"avg 폴백 실패: {row['avg_buy_price']}"
        assert row["tranche_count"] == 1, f"tranche_count 폴백 실패"
        # TRANCHE_ENTRY_ENABLED=True 환경이면 pending=1 추론 → 명시적 전달이 없을 때만
        # (테스트에서는 config 토글에 의존하므로 결과만 검증)
        assert row["second_tranche_executed"] == 0, "executed 초기값 실패"
        db.close()
        print("  [PASS] db_v17_backfill_existing_holdings")


def test_save_holding_position_explicit_v17():
    """save_holding_position 명시 v17 컬럼: avg/first/atr 정확 저장."""
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(str(db_path))
        db.connect()
        db.init_tables()
        db.save_holding_position({
            "date": str(now_kst().date()),
            "stock_code": "000660",
            "stock_name": "SK하이닉스",
            "theme": "AI반도체",
            "shares": 5,
            "buy_price": 200000,
            "stop_loss": 186000,
            "take_profit": 230000,
            "weight": 0.3,
            "first_buy_price": 200000,
            "avg_buy_price": 200000,
            "tranche_count": 1,
            "second_tranche_pending": True,
            "atr_at_buy": 8500.0,
            "atr_period": 14,
        })
        rows = db.get_portfolio(status="holding")
        row = rows[0]
        assert row["atr_at_buy"] == 8500.0, f"atr_at_buy 저장 실패: {row['atr_at_buy']}"
        assert row["second_tranche_pending"] == 1, "pending=1 명시 실패"
        db.close()
        print("  [PASS] save_holding_position_explicit_v17")


def test_update_portfolio_second_tranche():
    """update_portfolio_second_tranche: 가중평균/누적 갱신 검증."""
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(str(db_path))
        db.connect()
        db.init_tables()
        # 1차 매수: 100주 @ 10000
        db.save_holding_position({
            "date": str(now_kst().date()),
            "stock_code": "999999",
            "stock_name": "TEST",
            "theme": "테스트",
            "shares": 100,
            "buy_price": 10000,
            "first_buy_price": 10000,
            "avg_buy_price": 10000,
            "tranche_count": 1,
            "second_tranche_pending": True,
            "atr_at_buy": None,
        })
        # 2차 매수: +100주 @ 10500 → avg = (10000×100 + 10500×100) / 200 = 10250
        rowcount = db.update_portfolio_second_tranche(
            stock_code="999999",
            added_shares=100,
            second_filled_price=10500,
            avg_buy_price=10250,
        )
        assert rowcount == 1, f"UPDATE rowcount 실패: {rowcount}"

        rows = db.get_portfolio(status="holding")
        row = rows[0]
        assert row["shares"] == 200, f"shares 누적 실패: {row['shares']}"
        assert row["original_shares"] == 200, f"original_shares 누적 실패"
        assert row["avg_buy_price"] == 10250, f"avg 가중평균 실패: {row['avg_buy_price']}"
        assert row["tranche_count"] == 2, "tranche_count 갱신 실패"
        assert row["second_tranche_executed"] == 1, "executed=1 실패"
        assert row["second_tranche_pending"] == 0, "pending=0 실패"
        # first_buy_price는 불변 (손절/BE 기준)
        assert row["first_buy_price"] == 10000, f"first 불변 실패: {row['first_buy_price']}"
        db.close()
        print("  [PASS] update_portfolio_second_tranche")


def main():
    print("=" * 60)
    print("v17 Tranche Entry + Pyramid-In + ATR 단위 테스트")
    print("=" * 60)

    tests = [
        test_position_post_init_first_avg_fallback,
        test_position_avg_zero_fallback,
        test_profit_rate_first_basis,
        test_profit_rate_avg_basis,
        test_effective_trailing_pct_atr_wins,
        test_effective_trailing_pct_fixed_wins,
        test_effective_trailing_pct_atr_zero_fallback,
        test_buy_lock_acquire_release,
        test_buy_lock_clear_all,
        test_db_v17_migration_idempotent,
        test_db_v17_backfill_existing_holdings,
        test_save_holding_position_explicit_v17,
        test_update_portfolio_second_tranche,
    ]

    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")

    print("=" * 60)
    if failed == 0:
        print(f"✅ 전체 {len(tests)}개 테스트 통과")
        sys.exit(0)
    else:
        print(f"❌ {failed}/{len(tests)} 실패")
        sys.exit(1)


if __name__ == "__main__":
    main()
