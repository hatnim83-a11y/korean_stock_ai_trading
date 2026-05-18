"""
test_monitor_state_residue.py - monitor_state.json 잔재 버그 회귀 방지 테스트

2026-05-12 한화오션(042660) BE 손절 오발동 사건 대응:
- remove_position()가 JSON 키를 동기 삭제하는지
- _execute_partial_sell 전량 익절 경로가 remove_position 호출하는지
- _restore_trailing_state의 buy_price sanity check 가드
- 대시보드 _load_monitor_state의 holding 필터링

실행:
    pytest tests/test_monitor_state_residue.py -v
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.trading_engine.portfolio_monitor_v2 import PortfolioMonitorV2, Position
from config import now_kst


# ===== 공용 헬퍼 =====

def _init_db(db_path: Path) -> None:
    """portfolio + position_state 최소 스키마 생성"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE portfolio (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            status TEXT,
            shares INTEGER,
            buy_price REAL
        );
        CREATE TABLE position_state (
            stock_code TEXT PRIMARY KEY,
            current_price REAL,
            highest_price REAL,
            trailing_active INTEGER,
            trailing_level INTEGER,
            trailing_stop_price REAL,
            max_profit_rate REAL,
            partial_1_executed INTEGER,
            partial_2_executed INTEGER,
            partial_3_executed INTEGER,
            remaining_shares INTEGER,
            last_updated TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _fresh_monitor(db_path: Path) -> PortfolioMonitorV2:
    """KIS/네트워크 초기화 우회한 PortfolioMonitorV2 인스턴스."""
    monitor = PortfolioMonitorV2.__new__(PortfolioMonitorV2)
    monitor.positions = {}
    # _restore_trailing_state 가 참조하는 BE 손절 관련 속성
    monitor.enable_be_stop = True
    monitor.trail_be_activate_pct = 0.05
    monitor.trail_be_stop_pct = -0.01
    # 매도 실패 카운터 (Phase 2 추가) — _record_sell_failure / remove_position 가 참조
    monitor.sell_failure_counts = {}
    monitor.on_sell_failed = None
    return monitor


def _make_position(code: str, name: str, buy_price: int, shares: int = 10) -> Position:
    pos = Position(
        stock_code=code,
        stock_name=name,
        shares=shares,
        remaining_shares=shares,
        buy_price=buy_price,
        stop_loss_price=int(buy_price * 0.93),
        theme="테스트",
        buy_date=now_kst(),
    )
    pos.current_price = buy_price
    pos.highest_price = buy_price
    return pos


# ===== Test 1: remove_position 이 JSON 키를 동기 삭제 =====

def test_remove_position_clears_json_key(tmp_path, monkeypatch):
    """매도 시 monitor_state.json의 해당 stock_code 키가 즉시 제거되어야 한다."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    json_path.write_text(json.dumps({
        "042660": {"highest_price": 136800, "max_profit_rate": 0.3, "trailing_active": False},
        "000270": {"highest_price": 180000, "max_profit_rate": 0.0, "trailing_active": False},
    }))

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    monitor.positions["042660"] = _make_position("042660", "한화오션", 126_950)
    monitor.remove_position("042660")

    state = json.loads(json_path.read_text())
    assert "042660" not in state, "remove_position 후 JSON 키 잔존"
    assert "000270" in state, "다른 종목 키까지 삭제되면 안됨"


def test_remove_position_no_json_file_does_not_crash(tmp_path, monkeypatch):
    """JSON 파일이 없어도 매도 자체는 차단되지 않아야 한다."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )
    monitor = _fresh_monitor(db_path)
    monitor.positions["042660"] = _make_position("042660", "한화오션", 126_950)
    monitor.remove_position("042660")  # 예외 없이 통과
    assert "042660" not in monitor.positions


# ===== Test 2: _execute_partial_sell 전량 익절 경로가 remove_position 호출 =====

def test_partial_sell_full_exit_clears_json(tmp_path, monkeypatch):
    """전량 익절(remaining_shares=0) 시 remove_position이 호출되어 JSON 정리 트리거."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    json_path.write_text(json.dumps({
        "042660": {"highest_price": 130000, "max_profit_rate": 0.1, "trailing_active": False},
    }))

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("042660", "한화오션", 126_950, shares=10)
    pos.current_price = 142_000  # 익절 시뮬레이션
    monitor.positions["042660"] = pos

    # trading_engine.execute_take_profit success 모킹
    monitor.trading_engine = MagicMock()
    monitor.trading_engine.execute_take_profit.return_value = {
        "success": True, "sell_price": 142_000, "slippage": 0.0
    }
    monitor._close_position_in_db = MagicMock()
    monitor.on_partial_profit = None
    monitor.on_sell_failed = None

    success = asyncio.run(
        monitor._execute_partial_sell(pos, sell_shares=10, stage=3, profit_rate=0.118)
    )

    assert success is True
    assert "042660" not in monitor.positions, "전량 익절 후 메모리에서 제거되어야 함"
    state = json.loads(json_path.read_text())
    assert "042660" not in state, "전량 익절 후 JSON 키도 제거되어야 함"


# ===== Test 3: _restore_trailing_state buy_price sanity check =====

def test_restore_skips_residue_by_buy_price(tmp_path, monkeypatch):
    """JSON highest_price > buy_price × 1.02 면 잔재로 판단하여 BE 손절가 복원 차단."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    # 5/12 시나리오 재현: buy_price 126,950 / 잔재 highest_price 136,800 (4월 사이클)
    json_path.write_text(json.dumps({
        "042660": {
            "highest_price": 136_800,
            "max_profit_rate": 0.3,
            "trailing_active": False,
            "current_price": 0,
            "remaining_shares": 14,
        },
    }))

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("042660", "한화오션", 126_950, shares=14)
    initial_stop = pos.stop_loss_price
    monitor.positions["042660"] = pos

    monitor._restore_trailing_state()

    # 잔재 데이터가 무시되어 highest_price/stop_loss_price 가 변경되지 않아야 함
    assert pos.highest_price == 126_950, (
        f"잔재 highest_price({136_800}) 가 복원되면 안됨, 실제: {pos.highest_price}"
    )
    assert pos.stop_loss_price == initial_stop, (
        f"BE 손절가 복원이 차단되어야 함, 실제: {pos.stop_loss_price}"
    )
    assert pos.max_profit_rate == 0, (
        f"잔재 max_profit_rate 가 복원되면 안됨, 실제: {pos.max_profit_rate}"
    )


def test_restore_accepts_legitimate_recent_cycle(tmp_path, monkeypatch):
    """정당한 직전 사이클 데이터(highest_price ~ buy_price 근처)는 정상 복원."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    # 매수 후 +1% 까지만 올랐던 정상 사이클 (1.02 임계 미달)
    json_path.write_text(json.dumps({
        "042660": {
            "highest_price": 128_000,  # buy_price 126,950 × 1.0083 → 통과
            "max_profit_rate": 0.008,
            "trailing_active": False,
            "current_price": 0,
            "remaining_shares": 14,
        },
    }))

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("042660", "한화오션", 126_950, shares=14)
    monitor.positions["042660"] = pos
    monitor._restore_trailing_state()

    # 정상 복원: highest_price 가 더 큰 값(128,000)으로 갱신되어야 함
    assert pos.highest_price == 128_000, f"정상 복원 실패: {pos.highest_price}"


# ===== Test 4: dashboard _load_monitor_state holding 필터링 =====

def test_dashboard_json_fallback_filters_residue(tmp_path, monkeypatch):
    """JSON 폴백 시 portfolio.status='holding' 외 키는 잔재로 제외."""
    from web import dashboard_service

    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    # holding=000270 만 등록
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO portfolio (stock_code, stock_name, status) VALUES (?, ?, ?)",
        ("000270", "기아", "holding"),
    )
    conn.commit()
    conn.close()

    json_path = tmp_path / "monitor_state.json"
    json_path.write_text(json.dumps({
        "000270": {"highest_price": 180000},
        "448900": {"highest_price": 136800},  # 잔재
    }))

    # _get_db() 가 임시 DB 반환하도록 모킹 + DB position_state 비어있게
    from database import Database
    fake_db = Database()
    fake_db.db_path = str(db_path)
    fake_db.connect()
    monkeypatch.setattr(dashboard_service, "_get_db", lambda: fake_db)
    monkeypatch.setattr(
        dashboard_service.settings, "DATABASE_PATH", str(db_path), raising=False
    )

    try:
        result = dashboard_service._load_monitor_state()
    finally:
        fake_db.close()

    assert "000270" in result, "holding 종목은 통과해야 함"
    assert "448900" not in result, "잔재 키는 필터링되어야 함"


# ===== Test 5 (Phase 2): JSON 폴백 holding 화이트리스트 가드 =====

def test_restore_skips_residue_by_holding_whitelist(tmp_path, monkeypatch):
    """portfolio.status='closed' 종목의 JSON 잔재가 ×1.02 sanity 우회해도
    holding 화이트리스트로 차단되어야 한다 (2026-05-18 기아 사건 회귀 방지).
    """
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    # 기아 시나리오: buy_price 175,700, JSON highest 184,200
    # ×1.02 임계 = 179,214 → 184,200 통과 (Phase 1 sanity 우회)
    # 그러나 portfolio.status='closed' 이므로 화이트리스트가 즉시 차단해야 함
    json_path.write_text(json.dumps({
        "000270": {
            "highest_price": 184_200,
            "max_profit_rate": 0.048,
            "trailing_active": False,
            "current_price": 163_200,
            "remaining_shares": 11,
        },
    }))

    # DB에 closed 상태로 기록
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO portfolio (stock_code, stock_name, status, shares, buy_price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("000270", "기아", "closed", 11, 175_700),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    # 모니터 메모리에 잔존한다고 가정 (실사건과 동일)
    pos = _make_position("000270", "기아", 175_700, shares=11)
    initial_stop = pos.stop_loss_price
    monitor.positions["000270"] = pos

    monitor._restore_trailing_state()

    # 화이트리스트로 state 자체가 비워졌으므로 어느 필드도 갱신되면 안 됨
    assert pos.highest_price == 175_700, (
        f"화이트리스트 차단 실패 — highest_price={pos.highest_price}"
    )
    assert pos.stop_loss_price == initial_stop, (
        f"BE 손절 복원 차단 실패 — stop={pos.stop_loss_price}"
    )
    assert pos.max_profit_rate == 0


def test_restore_keeps_holding_when_whitelist_passes(tmp_path, monkeypatch):
    """holding 종목은 화이트리스트 통과 후 정상 복원되어야 한다 (회귀 방지)."""
    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    json_path = tmp_path / "monitor_state.json"
    json_path.write_text(json.dumps({
        "000270": {
            "highest_price": 178_000,  # ×1.02=179,214 미만 → sanity 통과
            "max_profit_rate": 0.013,
            "trailing_active": False,
            "current_price": 177_000,
            "remaining_shares": 11,
        },
    }))
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO portfolio (stock_code, stock_name, status, shares, buy_price) "
        "VALUES (?, ?, ?, ?, ?)",
        ("000270", "기아", "holding", 11, 175_700),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("000270", "기아", 175_700, shares=11)
    monitor.positions["000270"] = pos
    monitor._restore_trailing_state()

    assert pos.highest_price == 178_000, (
        f"holding 종목 정상 복원 실패 — highest={pos.highest_price}"
    )


# ===== Test 6 (Phase 2): 매도 실패 카운터 무한 루프 차단 =====

def test_max_sell_failures_force_remove(tmp_path, monkeypatch):
    """매도가 임계(MAX_SELL_FAILURES) 연속 실패 시 강제 remove_position +
    텔레그램 1회 알림 후 도배가 멈춰야 한다.
    """
    from config import settings as cfg
    threshold = getattr(cfg, "MAX_SELL_FAILURES", 3)

    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("000270", "기아", 175_700, shares=11)
    monitor.positions["000270"] = pos

    alerts: list[tuple[str, str]] = []
    monitor.on_sell_failed = lambda p, t, m: alerts.append((t, m))

    # 임계까지 연속 실패 시뮬레이션
    for i in range(threshold):
        monitor._record_sell_failure(pos, "보유기간 초과", "주문 가능한 수량을 초과했습니다.")

    # 임계 도달 → 강제 제거
    assert "000270" not in monitor.positions, "임계 도달 시 메모리에서 제거되어야 함"
    assert monitor.sell_failure_counts.get("000270") is None, "카운터 리셋되어야 함"
    # 알림: 매 실패마다 1회씩 발사 (마지막은 "연속 N회 실패" 메시지)
    assert len(alerts) == threshold, f"알림 발사 횟수 불일치: {len(alerts)} vs {threshold}"
    assert "연속" in alerts[-1][1], f"최종 알림 메시지에 '연속' 포함 안됨: {alerts[-1]}"

    # 강제 제거 후 추가 호출은 카운터 0부터 다시 시작 (도배 봉쇄)
    monitor.positions["000270"] = pos  # 다시 잔존 시뮬
    monitor._record_sell_failure(pos, "보유기간 초과", "재진입 테스트")
    assert monitor.sell_failure_counts["000270"] == 1, "리셋 후 1부터 시작해야 함"


def test_sell_failure_below_threshold_keeps_position(tmp_path, monkeypatch):
    """임계 미만 실패는 메모리 유지 + 통상 알림만 발사되어야 한다."""
    from config import settings as cfg
    threshold = getattr(cfg, "MAX_SELL_FAILURES", 3)
    assert threshold >= 2, "본 테스트는 threshold>=2 가정"

    db_path = tmp_path / "trading.db"
    _init_db(db_path)
    monkeypatch.setattr(
        "modules.trading_engine.portfolio_monitor_v2.settings.DATABASE_PATH",
        str(db_path),
        raising=False,
    )

    monitor = _fresh_monitor(db_path)
    pos = _make_position("000270", "기아", 175_700, shares=11)
    monitor.positions["000270"] = pos
    alerts: list = []
    monitor.on_sell_failed = lambda p, t, m: alerts.append((t, m))

    monitor._record_sell_failure(pos, "손절", "일시 네트워크 오류")
    assert "000270" in monitor.positions, "임계 미만은 메모리 유지"
    assert monitor.sell_failure_counts["000270"] == 1
    assert len(alerts) == 1


if __name__ == "__main__":
    # pytest 미설치 환경 폴백 (수동 검증용)
    import subprocess
    sys.exit(subprocess.call(["pytest", __file__, "-v"]))
