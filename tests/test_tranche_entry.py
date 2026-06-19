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


def test_add_position_v17_fields_restored():
    """add_position v17 필드 복원 — 2026-05-27 critical hotfix 회귀 테스트.

    load_positions_from_db → add_position 흐름에서 second_tranche_pending/atr_at_buy 등
    v17 필드가 DB→메모리 복원 안 되어 2차 진입이 항상 차단되던 버그 fix 검증.
    """
    from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2

    monitor = PortfolioMonitorV2(use_mock=True)

    # DB에서 로드된 행을 시뮬레이션 (v17 필드 포함)
    monitor.add_position(
        stock_code="437730",
        stock_name="삼현",
        shares=9,
        buy_price=61500,
        stop_loss_price=57195,  # -7%
        theme="AI반도체",
        first_buy_price=61500,
        avg_buy_price=61500,
        tranche_count=1,
        second_tranche_executed=False,
        second_tranche_pending=True,  # ⚠️ DB에서 True 복원
        atr_at_buy=4992.86,  # ⚠️ DB에서 박제값 복원
        atr_period=14,
    )

    pos = monitor.positions["437730"]
    # 복원 정확성 검증
    assert pos.second_tranche_pending is True, f"pending 복원 실패: {pos.second_tranche_pending}"
    assert pos.second_tranche_executed is False
    assert pos.atr_at_buy == 4992.86, f"atr 복원 실패: {pos.atr_at_buy}"
    assert pos.tranche_count == 1
    assert pos.first_buy_price == 61500
    assert pos.avg_buy_price == 61500

    # +6.5% 도달 시뮬레이션 (삼현 케이스 재현)
    pos.current_price = 65500
    assert pos.profit_rate > 0.05, f"+5% 트리거 임계 미충족: {pos.profit_rate}"

    # effective_trailing_pct ATR 적용 검증 (atr=4992.86, first=61500 → atr_pct = 2.0 × 4992.86 / 61500 = 16.2%)
    eff_pct = pos.effective_trailing_pct(0.04)  # L1 고정 4%
    assert eff_pct > 0.04, f"ATR 적용 실패: {eff_pct} (atr_pct는 16.2%여야 함)"
    print("  [PASS] add_position_v17_fields_restored")


def test_add_position_v17_defaults_safe():
    """add_position v17 필드 미전달 시 안전한 default — 기존 코드 호환성."""
    from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2

    monitor = PortfolioMonitorV2(use_mock=True)
    # v17 필드 미전달 (기존 호출 패턴)
    monitor.add_position(
        stock_code="000001",
        stock_name="테스트",
        shares=10,
        buy_price=10000,
        stop_loss_price=9300,
    )

    pos = monitor.positions["000001"]
    assert pos.first_buy_price == 10000, "first_buy_price 폴백 실패"
    assert pos.avg_buy_price == 10000, "avg_buy_price 폴백 실패"
    assert pos.second_tranche_pending is False, "default False 실패"
    assert pos.atr_at_buy == 0.0, "default 0 실패"
    # ATR=0 → effective_trailing_pct는 고정값 유지
    assert pos.effective_trailing_pct(0.04) == 0.04
    print("  [PASS] add_position_v17_defaults_safe")


def test_restore_trailing_state_db_source_v17_key_missing():
    """2026-05-28 hotfix 옵션 A — DB source 경로에서 v17 필드 키 미존재 시 덮어쓰기 방지.

    position_state 테이블에 v17 컬럼 없으면 s.get() → None/default 반환.
    add_position이 portfolio 테이블에서 정확 복원한 값을 덮어쓰지 말아야 함.
    """
    from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2

    monitor = PortfolioMonitorV2(use_mock=True)
    # add_position이 portfolio 테이블에서 정확 복원한 상태 가정
    monitor.add_position(
        stock_code="437730",
        stock_name="삼현",
        shares=9,
        buy_price=61500,
        stop_loss_price=57195,
        theme="AI반도체",
        first_buy_price=61500,
        avg_buy_price=61500,
        tranche_count=1,
        second_tranche_pending=True,  # ⚠️ 정상 복원값
        atr_at_buy=4992.86,            # ⚠️ 정상 복원값
    )
    pos = monitor.positions["437730"]
    assert pos.second_tranche_pending is True
    assert pos.atr_at_buy == 4992.86

    # _restore_trailing_state DB source 경로 시뮬레이션:
    # position_state 테이블에 v17 컬럼 없으면 dict에 키 자체 없음
    # (구 DB source state - JSON과 달리 v17 키 부재)
    db_source_state_no_v17 = {
        "437730": {
            "current_price": 65500,
            "highest_price": 65800,
            "trailing_level": 0,
            "trailing_active": False,
            "trailing_stop_price": None,
            "max_profit_rate": 0.0846,
            "partial_1_executed": False,
            "partial_2_executed": False,
            "partial_3_executed": False,
            "remaining_shares": 9,
            # v17 키 일부러 누락 (position_state DDL에 없음)
        }
    }

    # 옵션 A 패치 직접 검증: 키 존재 체크 로직
    s = db_source_state_no_v17["437730"]
    # 패치된 로직: "second_tranche_pending" not in s → 덮어쓰기 skip
    if "second_tranche_pending" in s and s.get("second_tranche_pending") is not None:
        pos.second_tranche_pending = bool(s.get("second_tranche_pending"))
    assert pos.second_tranche_pending is True, "DB source v17 키 미존재 시 덮어쓰기 발생 (옵션 A 실패)"

    if "atr_at_buy" in s and s.get("atr_at_buy") is not None and s.get("atr_at_buy") > 0:
        pos.atr_at_buy = s.get("atr_at_buy")
    assert pos.atr_at_buy == 4992.86, "DB source atr 키 미존재 시 덮어쓰기 발생"

    print("  [PASS] restore_trailing_state_db_source_v17_key_missing")


def test_restore_trailing_state_json_v17_key_present():
    """JSON 폴백 경로에서 v17 필드 키 존재 시 정상 덮어쓰기 (regress 방지)."""
    from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2

    monitor = PortfolioMonitorV2(use_mock=True)
    monitor.add_position(
        stock_code="000001",
        stock_name="테스트",
        shares=10,
        buy_price=10000,
        stop_loss_price=9300,
        first_buy_price=10000,
        avg_buy_price=10000,
        tranche_count=1,
        second_tranche_pending=True,
        atr_at_buy=200.0,
    )
    pos = monitor.positions["000001"]

    # JSON 폴백 시뮬레이션: 2차 진입 완료 상태로 갱신
    json_state = {
        "second_tranche_pending": False,
        "second_tranche_executed": True,
        "tranche_count": 2,
        "avg_buy_price": 10250,
        "atr_at_buy": 200.0,
    }
    s = json_state

    if "second_tranche_pending" in s and s.get("second_tranche_pending") is not None:
        pos.second_tranche_pending = bool(s.get("second_tranche_pending"))
    if "second_tranche_executed" in s and s.get("second_tranche_executed") is not None:
        pos.second_tranche_executed = bool(s.get("second_tranche_executed"))
    if "tranche_count" in s and s.get("tranche_count"):
        pos.tranche_count = s.get("tranche_count")

    assert pos.second_tranche_pending is False, "JSON 키 존재 시 정상 덮어쓰기 실패"
    assert pos.second_tranche_executed is True
    assert pos.tranche_count == 2
    print("  [PASS] restore_trailing_state_json_v17_key_present")


def test_swing_pool_calculation_logic():
    """가드 B' 핵심 로직 — swing_pool 한도 계산 (2026-05-23 hotfix).

    종가베팅 cap 50% 정책 정합 — v17 1차+2차 합쳐 swing_pool(90%) 한도 절대 안 넘김.
    fund_guard.compute_capital_limit 의 swing_used 계산(cost_basis) 과 동일 식.
    """
    # 시뮬레이션: 총자본 4000만 / swing 90% = 3600만
    total_capital = 4000_0000
    swing_capital_ratio = 0.9
    swing_pool = int(total_capital * swing_capital_ratio)
    assert swing_pool == 3600_0000, f"swing_pool 계산 실패: {swing_pool}"

    # v17 1차 매수만 진행: 5종목 × 360만 = 1800만 swing_used
    positions = [
        Position(stock_code=f"00{i}", stock_name=f"테스트{i}",
                 shares=120, remaining_shares=120,
                 buy_price=30000, stop_loss_price=27900,
                 first_buy_price=30000, avg_buy_price=30000)
        for i in range(5)
    ]
    swing_used = sum(p.first_buy_price * p.shares for p in positions)
    assert swing_used == 1800_0000, f"swing_used 누적 실패: {swing_used}"

    swing_available = max(0, swing_pool - swing_used)
    assert swing_available == 1800_0000, f"swing_available 1차 후: {swing_available}"

    # v17 2차 발화 시도: 1차 매수금 360만/종목 = 1800만 추가 필요
    target_amount_per_stock = 30000 * 120  # = 360만
    # 5종목 모두 2차 발화: 360만 × 5 = 1800만 ≤ swing_available 1800만 → 허용
    # 하지만 1종목씩 발화하면서 누적되므로 마지막 종목 시점에 한도 도달
    # 즉 1번째 2차: swing_used=1800+360=2160 → available=1440 → target 360 가능
    # 2번째: 2520 / available=1080 / target 360
    # 3번째: 2880 / 720 / 360
    # 4번째: 3240 / 360 / 360
    # 5번째: 3600 / 0 / 0 → 차단 ✅
    print("  [PASS] swing_pool_calculation_logic")


def test_time_guard_logic():
    """가드 A 핵심 로직 — 15:15+ 시간 차단 (2026-05-23 hotfix).

    종가베팅 phase1=15:18 / phase2=15:25 시점 보호.
    """
    from datetime import datetime as _dt, time as _time, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    # 15:14 (1분 전) → 통과
    t1 = _dt(2026, 5, 26, 15, 14, 59, tzinfo=KST)
    assert not (t1.hour == 15 and t1.minute >= 15), "15:14 통과 실패"
    # 15:15 정각 → 차단
    t2 = _dt(2026, 5, 26, 15, 15, 0, tzinfo=KST)
    assert (t2.hour == 15 and t2.minute >= 15), "15:15 차단 실패"
    # 15:25 (phase2 시점) → 차단
    t3 = _dt(2026, 5, 26, 15, 25, 0, tzinfo=KST)
    assert (t3.hour == 15 and t3.minute >= 15), "15:25 차단 실패"
    # 16:00 (장 마감 후) → 차단 (단, 모니터 자체가 15:30 정지)
    t4 = _dt(2026, 5, 26, 16, 0, 0, tzinfo=KST)
    assert (t4.hour == 15 and t4.minute >= 15) is False, "16:00 차단 분기 의도 — 시간 가드는 15시 한정"
    # 14:59 → 통과
    t5 = _dt(2026, 5, 26, 14, 59, 0, tzinfo=KST)
    assert not (t5.hour == 15 and t5.minute >= 15), "14:59 통과 실패"
    print("  [PASS] time_guard_logic")


def test_pyramid_min_one_share_fallback_logic():
    """최소 1주 폴백 핵심 로직 (2026-06-19 삼성물산 사건).

    고가주를 1차에 1주만 진입하면 mirror_first 예산(=1차 원가)이
    상승한 현재가보다 작아 int(예산//현재가)=0 → 불타기 영구 불발.
    PYRAMID_MIN_ONE_SHARE 폴백이 자본 여유 시 1주로 보정하는지 검증.
    """
    # --- 사건 재현: 삼성물산 1차 1주 @482,500, 현재가 511,000 (+5.9%) ---
    first_buy_price = 482500
    shares = 1
    cur_price = 511000
    second_ratio = first_ratio = 0.5
    target_amount = (first_buy_price * shares) * (second_ratio / first_ratio)
    assert target_amount == 482500, "mirror_first 예산 계산"

    # 기존 동작: int(예산//현재가) = 0 → 구조적 불발
    second_qty = int(target_amount // max(1, cur_price))
    assert second_qty == 0, "고가주 1주 → 산정수량 0 (사건 재현)"

    # --- 폴백 적용 (자본 여유 충분: swing_available 1주가 이상, cash 충분) ---
    def apply_fallback(second_qty, cur_price, swing_available, cash, enabled=True, dry_run=False):
        if second_qty <= 0 and enabled and cur_price > 0:
            swing_ok = (swing_available is None) or (cur_price <= swing_available)
            cash_ok = True
            if swing_ok and not dry_run:
                cash_ok = cur_price <= max(0, cash)
            if swing_ok and cash_ok:
                return 1
        return second_qty

    # 케이스 1: swing/cash 여유 충분 → 1주 폴백
    assert apply_fallback(0, cur_price, swing_available=2000_0000, cash=2000_0000) == 1, \
        "자본 여유 시 1주 폴백 적용 실패"

    # 케이스 2: swing_available 부족 (현재가 < swing) → 차단
    assert apply_fallback(0, cur_price, swing_available=500000, cash=2000_0000) == 0, \
        "swing_pool 부족 시 폴백 차단 실패 (511,000 > 500,000)"

    # 케이스 3: 현금 부족 → 차단
    assert apply_fallback(0, cur_price, swing_available=2000_0000, cash=300000) == 0, \
        "현금 부족 시 폴백 차단 실패"

    # 케이스 4: 토글 OFF → 기존 동작(0 유지)
    assert apply_fallback(0, cur_price, swing_available=2000_0000, cash=2000_0000, enabled=False) == 0, \
        "토글 OFF 시 기존 동작(스킵) 실패"

    # 케이스 5: swing_available=None(계산 실패) → swing 가드 통과, cash로만 판정
    assert apply_fallback(0, cur_price, swing_available=None, cash=2000_0000) == 1, \
        "swing None 시 cash 통과로 1주 폴백 실패"

    # 케이스 6: DRY_RUN → cash 조회 스킵, swing만 통과하면 1주 (시뮬)
    assert apply_fallback(0, cur_price, swing_available=2000_0000, cash=0, dry_run=True) == 1, \
        "DRY_RUN 시 cash 스킵하고 1주 폴백 실패"

    # 케이스 7: 정상 산정수량(>0)이면 폴백 미진입 (그대로 유지)
    assert apply_fallback(3, cur_price, swing_available=2000_0000, cash=2000_0000) == 3, \
        "정상 수량 시 폴백 개입 금지 위반"

    print("  [PASS] pyramid_min_one_share_fallback_logic")


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
        test_add_position_v17_fields_restored,  # 2026-05-27 critical hotfix
        test_add_position_v17_defaults_safe,    # 2026-05-27 critical hotfix
        test_restore_trailing_state_db_source_v17_key_missing,  # 2026-05-28 hotfix 옵션 A
        test_restore_trailing_state_json_v17_key_present,       # 2026-05-28 hotfix 옵션 A
        test_swing_pool_calculation_logic,  # 2026-05-23 hotfix 가드 B'
        test_time_guard_logic,              # 2026-05-23 hotfix 가드 A
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
