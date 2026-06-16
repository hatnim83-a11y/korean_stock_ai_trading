"""
test_trailing_atr_cap_be_floor.py — 트레일링 ATR 폭 상한(cap) + BE 바닥 보존 회귀 테스트

2026-06-16 피에스케이홀딩스(031980) 트레일링 L2 청산이 +16.9% 고점 → -9.4%로
26%p를 반납한 사건 대응. 두 결함 수정:
- 결함 A: effective_trailing_pct()에 ATR 폭 상한(cap) 도입 (TRAILING_ATR_CAP_PCT)
- 결함 B: _check_stop_loss()가 trailing_active면 무조건 양보 → 조건부(trailing_stop >= stop_loss 일 때만)

실행:
    pytest tests/test_trailing_atr_cap_be_floor.py -v
"""

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2, Position
from config import now_kst, settings


# ===== 공용 헬퍼 =====

def _make_pos(
    *,
    first_buy_price: float = 100_000,
    atr_at_buy: float = 0.0,
    trailing_active: bool = False,
    trailing_stop=None,
    stop_loss_price: float = 93_000,
    current_price: float = 100_000,
    max_profit_rate: float = 0.0,
    buy_date=None,
) -> Position:
    pos = Position(
        stock_code="031980",
        stock_name="테스트종목",
        shares=10,
        remaining_shares=10,
        buy_price=first_buy_price,
        stop_loss_price=stop_loss_price,
        theme="테스트",
        buy_date=buy_date or now_kst(),
        trailing_active=trailing_active,
        trailing_stop=trailing_stop,
        max_profit_rate=max_profit_rate,
        first_buy_price=first_buy_price,
        avg_buy_price=first_buy_price,
        atr_at_buy=atr_at_buy,
    )
    pos.current_price = current_price
    pos.highest_price = max(current_price, first_buy_price)
    return pos


def _stoploss_monitor(*, be_floor_enabled: bool = True, grace_days: int = 1) -> PortfolioMonitorV2:
    """_check_stop_loss 가 참조하는 속성만 갖춘 경량 monitor."""
    m = PortfolioMonitorV2.__new__(PortfolioMonitorV2)
    m.trail_be_floor_enabled = be_floor_enabled
    m.grace_period_days = grace_days
    m.grace_period_stop_loss = -0.08
    m.enable_be_stop = True
    m.trail_be_activate_pct = 0.05
    return m


# ===== 결함 A: effective_trailing_pct cap =====

def test_A1_atr_above_cap_clamped(monkeypatch):
    """ATR폭 > cap → cap으로 클램프 (피에스케이홀딩스 사건)."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)
    pos = _make_pos(first_buy_price=132_300, atr_at_buy=14_878.57)  # atr_pct = 22.5%
    # fixed L2=3% → max(0.03, min(0.225, 0.08)) = 0.08
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.08)


def test_A2_atr_equals_cap_boundary(monkeypatch):
    """ATR폭 == cap → cap 값 그대로 반환 (경계)."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)
    # atr_pct = 2.0 * 4000 / 100000 = 0.08 == cap
    pos = _make_pos(first_buy_price=100_000, atr_at_buy=4_000)
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.08)


def test_A3_atr_below_cap_unchanged(monkeypatch):
    """ATR폭 < cap → 클램프 미발동, 기존 max(fixed, atr) 동치."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)
    # atr_pct = 2.0 * 2000 / 100000 = 0.04 < cap, > L2(0.03)
    pos = _make_pos(first_buy_price=100_000, atr_at_buy=2_000)
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.04)


def test_A4_atr_missing_fallback(monkeypatch):
    """atr_at_buy=0 → fixed_pct 폴백 (cap 미관여)."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)
    pos = _make_pos(first_buy_price=100_000, atr_at_buy=0.0)
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.03)


def test_A5_use_atr_false_fallback(monkeypatch):
    """TRAILING_USE_ATR=False → fixed_pct (cap·큰 ATR 무관)."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", False)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)
    pos = _make_pos(first_buy_price=132_300, atr_at_buy=14_878.57)
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.03)


def test_A6_cap_zero_means_unlimited(monkeypatch):
    """cap=0 → 상한 없음(롤백). ATR이 0으로 억제되지 않고 기존 무제한 동작."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.0)
    pos = _make_pos(first_buy_price=132_300, atr_at_buy=14_878.57)  # 22.5%
    # cap=0 → min() 미적용 → max(0.03, 0.225) = 0.225 (기존 무제한)
    assert pos.effective_trailing_pct(0.03) == pytest.approx(0.225, abs=1e-3)


def test_A7_cap_below_fixed_keeps_fixed_floor(monkeypatch):
    """cap < fixed_pct(오설정) → max() 하한으로 고정값 보장."""
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.02)  # cap < L1(0.04)
    pos = _make_pos(first_buy_price=132_300, atr_at_buy=14_878.57)
    # min(0.225, 0.02)=0.02, max(0.04, 0.02)=0.04 → 고정값 하한 보장
    assert pos.effective_trailing_pct(0.04) == pytest.approx(0.04)


# ===== 결함 B: _check_stop_loss 조건부 양보 =====

def test_B8_trailing_above_stoploss_yields(monkeypatch):
    """trailing_stop > stop_loss_price → 양보(return False). 정상 18건 회귀 동치."""
    m = _stoploss_monitor(be_floor_enabled=True)
    # 현재가가 손절가보다 낮아도, trailing이 BE보다 위면 양보 → 트레일링이 처리
    pos = _make_pos(
        trailing_active=True, trailing_stop=105_000, stop_loss_price=99_000,
        current_price=98_000,
    )
    assert m._check_stop_loss(pos) is False


def test_B9_trailing_equals_stoploss_yields():
    """trailing_stop == stop_loss_price → 양보(경계)."""
    m = _stoploss_monitor(be_floor_enabled=True)
    pos = _make_pos(
        trailing_active=True, trailing_stop=99_000, stop_loss_price=99_000,
        current_price=98_000,
    )
    assert m._check_stop_loss(pos) is False


def test_B10_trailing_below_stoploss_triggers_be(monkeypatch):
    """trailing_stop < stop_loss_price → 양보 해제, BE 바닥에서 손절 발동 (피에스케이 사건 방어)."""
    m = _stoploss_monitor(be_floor_enabled=True, grace_days=1)
    # hold_days > grace 위해 buy_date를 충분히 과거로 (line 1338 경로 직행)
    old = now_kst() - timedelta(days=10)
    # 피에스케이 재현: trailing 119,905 < BE 130,977, 현재가가 BE 아래
    pos = _make_pos(
        first_buy_price=132_300, trailing_active=True, trailing_stop=119_905,
        stop_loss_price=130_977, current_price=130_000, max_profit_rate=0.169,
        buy_date=old,
    )
    assert pos.hold_days > m.grace_period_days  # 전제 확인
    assert m._check_stop_loss(pos) is True  # BE 바닥에서 손절


def test_B11_trailing_stop_none_no_yield_branch():
    """trailing_active=True & trailing_stop=None → 양보 분기 미진입 (기존 동치)."""
    m = _stoploss_monitor(be_floor_enabled=True, grace_days=1)
    old = now_kst() - timedelta(days=10)
    pos = _make_pos(
        first_buy_price=100_000, trailing_active=True, trailing_stop=None,
        stop_loss_price=99_000, current_price=98_000, buy_date=old,
    )
    # 양보 안 함 → line 1338: 98,000 <= 99,000 → True
    assert m._check_stop_loss(pos) is True


def test_B12_be_floor_disabled_rolls_back(monkeypatch):
    """TRAIL_BE_FLOOR_ENABLED=False → 무조건 양보(롤백 동치). trailing<stoploss여도 손절 안 함."""
    m = _stoploss_monitor(be_floor_enabled=False, grace_days=1)
    old = now_kst() - timedelta(days=10)
    pos = _make_pos(
        first_buy_price=132_300, trailing_active=True, trailing_stop=119_905,
        stop_loss_price=130_977, current_price=130_000, buy_date=old,
    )
    assert m._check_stop_loss(pos) is False  # 기존 무조건 양보


def test_B13_grace_period_be_floor_interaction():
    """grace 기간 내 + trailing_stop < BE(활성) → grace 블록에서 BE 손절 발동(휩쏘 거동 명시)."""
    m = _stoploss_monitor(be_floor_enabled=True, grace_days=1)
    # buy_date=오늘 → hold_days <= grace → grace 블록 진입
    pos = _make_pos(
        first_buy_price=100_000, trailing_active=True, trailing_stop=90_000,
        stop_loss_price=99_000, current_price=98_500, max_profit_rate=0.10,
        buy_date=now_kst(),
    )
    assert pos.hold_days <= m.grace_period_days  # 전제 확인
    # be_active(max_profit 0.10>=0.05) → effective_stop=max(grace=92,000, BE 99,000)=99,000
    # 98,500 <= 99,000 → True (BE 바닥 손절)
    assert m._check_stop_loss(pos) is True


def test_B14_normal_trailing_in_grace_still_yields():
    """grace 기간 내라도 trailing_stop >= stop_loss면 양보 (정상 케이스 회귀)."""
    m = _stoploss_monitor(be_floor_enabled=True, grace_days=1)
    pos = _make_pos(
        first_buy_price=100_000, trailing_active=True, trailing_stop=108_000,
        stop_loss_price=100_000, current_price=99_000, max_profit_rate=0.10,
        buy_date=now_kst(),
    )
    assert m._check_stop_loss(pos) is False


# ===== cap 즉시 소급 (복원 경로) =====

def _seed_position_state(db_path, code: str, state: dict) -> None:
    """position_state 테이블 생성 + 트레일링 활성 상태 1건 시드 (db_source 복원 경로용)."""
    import sqlite3 as _sq
    conn = _sq.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS position_state (
            stock_code TEXT PRIMARY KEY, current_price REAL, highest_price REAL,
            trailing_active INTEGER, trailing_level INTEGER, trailing_stop_price REAL,
            max_profit_rate REAL, partial_1_executed INTEGER, partial_2_executed INTEGER,
            partial_3_executed INTEGER, remaining_shares INTEGER, last_updated TEXT)"""
    )
    conn.execute(
        """INSERT INTO position_state (stock_code,current_price,highest_price,trailing_active,
            trailing_level,trailing_stop_price,max_profit_rate,partial_1_executed,
            partial_2_executed,partial_3_executed,remaining_shares,last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            code, state["current_price"], state["highest_price"],
            int(state["trailing_active"]), state["trailing_level"],
            state["trailing_stop_price"], state["max_profit_rate"],
            0, 0, 0, state["remaining_shares"], "2026-06-16 06:00:00",
        ),
    )
    conn.commit()
    conn.close()


def _restore_monitor(db_path) -> PortfolioMonitorV2:
    """_restore_trailing_state 가 참조하는 속성을 갖춘 경량 monitor (cap 소급 포함)."""
    m = PortfolioMonitorV2.__new__(PortfolioMonitorV2)
    m.positions = {}
    m.enable_be_stop = True
    m.trail_be_activate_pct = 0.05
    m.trail_be_stop_pct = -0.01
    m.trail_be_floor_enabled = True
    # cap 소급 로직이 참조하는 레벨별 고정 폭
    m.trail_level1_pct = 0.04
    m.trail_level2_pct = 0.03
    m.trail_level3_pct = 0.02
    m.sell_failure_counts = {}
    m.on_sell_failed = None
    m._recovered_alerted = set()
    return m


def test_restore_applies_atr_cap_to_existing_position(tmp_path, monkeypatch):
    """복원 시 cap 미반영 옛 trailing_stop을 cap 적용값으로 즉시 소급 상향 (롯데쇼핑 케이스)."""
    db_path = tmp_path / "trading.db"
    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path), raising=False,
    )
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)

    # L2 활성 상태 시드 (옛 ATR폭 14.78% → trailing_stop 175,560)
    _seed_position_state(db_path, "023530", {
        "current_price": 196_400, "highest_price": 206_000,
        "trailing_active": True, "trailing_level": 2,
        "trailing_stop_price": 175_560, "max_profit_rate": 0.22,
        "remaining_shares": 8,
    })

    monitor = _restore_monitor(db_path)
    pos = _make_pos(first_buy_price=168_800, atr_at_buy=12_471, stop_loss_price=156_984)
    pos.stock_code = "023530"; pos.stock_name = "롯데쇼핑"
    monitor.positions["023530"] = pos

    monitor._restore_trailing_state()

    # cap 8% 적용 → 206,000 × 0.92 = 189,520 으로 상향 (옛값 175,560 대비)
    assert pos.trailing_active and pos.trailing_level == 2
    assert pos.trailing_stop == pytest.approx(206_000 * 0.92, abs=1)
    assert pos.trailing_stop > 175_560
    # stop_loss_price 도 동기화 상향 (올라가기만)
    assert pos.stop_loss_price == pytest.approx(206_000 * 0.92, abs=1)


def test_restore_no_downgrade_when_atr_below_cap(tmp_path, monkeypatch):
    """ATR폭이 cap 이하면 복원 trailing_stop을 낮추지 않음 (올라가기만 — 회귀 안전)."""
    db_path = tmp_path / "trading.db"
    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path), raising=False,
    )
    monkeypatch.setattr(settings, "TRAILING_USE_ATR", True)
    monkeypatch.setattr(settings, "ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(settings, "TRAILING_ATR_CAP_PCT", 0.08)

    # ATR폭 = 2*2000/100000 = 4% < cap. L2 고정 3%. effective=max(0.03,0.04)=0.04
    # 저장된 trailing_stop = 110,000×0.96 = 105,600 (정상값). 소급해도 동일 → 미변경
    _seed_position_state(db_path, "000001", {
        "current_price": 108_000, "highest_price": 110_000,
        "trailing_active": True, "trailing_level": 2,
        "trailing_stop_price": 105_600, "max_profit_rate": 0.10,
        "remaining_shares": 10,
    })

    monitor = _restore_monitor(db_path)
    pos = _make_pos(first_buy_price=100_000, atr_at_buy=2_000, stop_loss_price=93_000)
    pos.stock_code = "000001"; pos.stock_name = "테스트"
    monitor.positions["000001"] = pos

    monitor._restore_trailing_state()

    # effective 4% → 110,000×0.96 = 105,600. 저장값과 동일 → 하향/이상 변동 없음
    assert pos.trailing_stop == pytest.approx(105_600, abs=1)
