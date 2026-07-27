"""종가베팅 누적 실현손익 어댑터 + 전역 자산 정합성 회귀 테스트.

작업: docs/work-plans/active/closing-bet-cumulative-realized-pnl/

계산 정책 (financial-correctness follow-up, 2026-07-10):
- **Tier 1 (primary, "direct persisted-cost arithmetic")**:
      (exit_price - entry_price) * exit_shares
      - (buy_commission + sell_commission + transaction_tax + estimated_slippage)
  저장된 net_pnl_pct 를 신뢰하지 않는다(엔진 net_pnl_pct 가 실현 원화와 불일치하는 행 존재).
  실 DB candidate 251 = -4,440 / 414 = +16,777 을 원 단위로 재현한다.
- **Tier 2 (fallback, exit_price 부재 legacy)**: net_pnl_pct × (entry_cost_basis + 매수수수료).
  entry_cost_basis = entry_amount(>0) 우선, 없으면 entry_price × 진입체결수량(p1+p2).
  **exit_shares 를 진입수량 대용으로 쓰지 않는다**.
- **Tier 3 (skip-safe)**: 진입원가/손익 미확정 legacy 행은 조작 없이 skip → skipped_incomplete 집계.

검증 포인트:
1. Tier 1 직접계산 (비용 레그 차감, net_pnl_pct 무시)
2. 실 DB 251/414 두 행이 direct persisted-cost arithmetic 으로 rounded KRW 재현
3. 완전청산(final_exit_time NOT NULL AND exit_shares>0)만 집계, 부분/비청산 제외
4. 오늘 vs 누적 분리 (KST 청산일 = final_exit_time 날짜 접두)
5. exit_shares != 진입수량 시 Tier 2 폴백이 진입 체결수량을 사용(exit_shares 아님)
6. Tier 2 폴백: entry_amount 우선 / legacy NULL buy_commission → 설정율 / 율 0 → 진입원가
7. Tier 3 skip-safe: 불완전 legacy 행은 손익 미조작 skip
8. reconciliation: 전역 current_total 은 KIS 잔액/종가베팅 PnL 이 아니라
   settings.TOTAL_CAPITAL + trading.db realized 기반 모델값이다.
"""

import asyncio
import sqlite3

import pytest

from closing_bet_system.dashboard import data_adapter


# ===== 임시 closing_bet.db 헬퍼 =====

_SCHEMA = """
CREATE TABLE candidates (
    candidate_id INTEGER PRIMARY KEY,
    trade_date TEXT,
    ticker TEXT,
    name TEXT,
    candidate_status TEXT,
    entry_price REAL,
    entry_amount REAL,
    exit_price REAL,
    exit_shares INTEGER,
    buy_commission REAL,
    sell_commission REAL,
    transaction_tax REAL,
    estimated_slippage REAL,
    net_pnl_pct REAL,
    exit_time TEXT,
    final_exit_time TEXT,
    entry_phase1_executed_shares INTEGER,
    entry_phase2_executed_shares INTEGER
);
"""

_COLUMNS = [
    "candidate_id", "trade_date", "ticker", "name", "candidate_status",
    "entry_price", "entry_amount", "exit_price", "exit_shares",
    "buy_commission", "sell_commission", "transaction_tax", "estimated_slippage",
    "net_pnl_pct", "exit_time", "final_exit_time",
    "entry_phase1_executed_shares", "entry_phase2_executed_shares",
]

_DEFAULTS = {
    "trade_date": "2026-07-09", "ticker": "TST", "name": "종목",
    "candidate_status": "entered",
    "entry_price": None, "entry_amount": None, "exit_price": None, "exit_shares": 0,
    "buy_commission": 0.0, "sell_commission": 0.0, "transaction_tax": 0.0,
    "estimated_slippage": 0.0, "net_pnl_pct": None,
    "exit_time": None, "final_exit_time": None,
    "entry_phase1_executed_shares": None, "entry_phase2_executed_shares": None,
}


def _today_exit():
    today = data_adapter.now_kst().date().isoformat()
    return f"{today}T09:05:00+09:00"


def _make_db(tmp_path, rows):
    """rows: dict 리스트(부분 지정, 나머지는 _DEFAULTS). closing_bet.db 생성 후 경로 반환."""
    db_path = tmp_path / "closing_bet.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    records = []
    for i, r in enumerate(rows, start=1):
        merged = dict(_DEFAULTS)
        merged.update(r)
        merged.setdefault("candidate_id", i)
        merged["candidate_id"] = r.get("candidate_id", i)
        records.append(tuple(merged[c] for c in _COLUMNS))
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.executemany(f"INSERT INTO candidates VALUES ({placeholders})", records)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def cb_db(tmp_path, monkeypatch):
    """오늘/과거/부분/비청산 혼합 후보 (Tier 1 직접계산 · 비용 레그 0).

    exit_price 를 net_pnl_pct 와 일관되게 두어 direct arithmetic 이 round-number 기대값을
    낸다. 비용 레그 차감·net_pnl_pct 무시·폴백은 별도 테스트에서 검증.
    """
    today_exit = _today_exit()
    past_exit = "2026-01-02T09:05:00+09:00"
    rows = [
        # A: 완전청산·오늘·양수 → (1100-1000)*10 - 0 = +1000
        dict(candidate_id=1, entry_price=1000.0, entry_amount=10000.0, exit_price=1100.0,
             exit_shares=10, net_pnl_pct=0.10, exit_time=today_exit,
             final_exit_time=today_exit, entry_phase1_executed_shares=10),
        # B: 완전청산·과거·음수 → (1900-2000)*5 - 0 = -500
        dict(candidate_id=2, trade_date="2026-01-01", entry_price=2000.0, entry_amount=10000.0,
             exit_price=1900.0, exit_shares=5, net_pnl_pct=-0.05, exit_time=past_exit,
             final_exit_time=past_exit, entry_phase1_executed_shares=5),
        # C: 부분청산(final_exit_time NULL) → 제외
        dict(candidate_id=3, entry_price=3000.0, entry_amount=9000.0, exit_price=3100.0,
             exit_shares=3, net_pnl_pct=0.03, exit_time=today_exit, final_exit_time=None,
             entry_phase1_executed_shares=3),
        # D: 비청산(recommended, exit_shares 0) → 제외
        dict(candidate_id=4, candidate_status="recommended", entry_price=None,
             entry_amount=None, exit_price=None, exit_shares=0),
        # E: 완전청산·오늘·음수 → (490-500)*4 - 0 = -40
        dict(candidate_id=5, entry_price=500.0, entry_amount=2000.0, exit_price=490.0,
             exit_shares=4, net_pnl_pct=-0.02, exit_time=today_exit,
             final_exit_time=today_exit, entry_phase1_executed_shares=4),
    ]
    db_path = _make_db(tmp_path, rows)
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    return db_path


# ===== 집계/필터/오늘분리 (Tier 1, 비용 0) =====


def test_cumulative_realized_pnl(cb_db):
    """완전청산 3건만 집계: +1000 + (-500) + (-40) = +460, count=3."""
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 3
    assert s["cumulative_realized_pnl"] == 460
    assert s["skipped_incomplete"] == 0


def test_today_vs_cumulative_split(cb_db):
    """오늘 청산 2건(A,E)만 오늘 집계: +1000 + (-40) = +960, count=2."""
    s = data_adapter.get_realized_pnl_summary()
    assert s["today_closed_count"] == 2
    assert s["today_realized_pnl"] == 960
    assert s["today_realized_pnl"] != s["cumulative_realized_pnl"]


def test_partial_and_non_exited_excluded(cb_db):
    """부분청산(C, final_exit_time NULL)·비청산(D)은 집계에서 제외."""
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 3
    assert s["cumulative_realized_pnl"] == 460  # C 의 +90 이 빠져야 함


def test_win_count(cb_db):
    """승(양수 PnL) 건수 = A 1건."""
    s = data_adapter.get_realized_pnl_summary()
    assert s["win_count"] == 1


def test_negative_pnl_reflected(cb_db):
    """음수 PnL 이 정확히 차감(누적이 오늘보다 작아야 함: 과거 -500 포함)."""
    s = data_adapter.get_realized_pnl_summary()
    assert s["cumulative_realized_pnl"] < s["today_realized_pnl"]


def test_empty_db_safe():
    """DB 미존재 시에도 0 카운트/0 PnL 안전 반환 (KeyError 금지)."""
    orig = data_adapter._resolve_db_path
    try:
        from pathlib import Path
        data_adapter._resolve_db_path = lambda: Path("/nonexistent/closing_bet.db")
        s = data_adapter.get_realized_pnl_summary()
        assert s["closed_count"] == 0
        assert s["cumulative_realized_pnl"] == 0
        assert s["today_realized_pnl"] == 0
        assert s["today_closed_count"] == 0
        assert s["skipped_incomplete"] == 0
    finally:
        data_adapter._resolve_db_path = orig


# ===== Tier 1: direct persisted-cost arithmetic =====


def test_tier1_subtracts_all_cost_legs_and_ignores_net_pnl_pct(tmp_path, monkeypatch):
    """Tier 1 은 4개 비용 레그를 차감하고 net_pnl_pct 를 무시한다.

    gross = (10500-10000)*10 = 5000; costs = 100+120+300+80 = 600 → 4400.
    net_pnl_pct 를 고의로 엉뚱하게(0.99) 넣어도 무시됨을 검증.
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=100000.0, exit_price=10500.0, exit_shares=10,
        buy_commission=100.0, sell_commission=120.0, transaction_tax=300.0,
        estimated_slippage=80.0, net_pnl_pct=0.99,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 4400  # not 0.99×... (net_pnl_pct 무시)


def test_real_historical_rows_reconcile_to_rounded_krw(tmp_path, monkeypatch):
    """실 DB candidate 251/414 의 저장값이 direct persisted-cost arithmetic 으로 재현된다.

    251(HPSP): (58000-60100)*2 - (9.015+8.7+104.4+118.1) = -4200 - 240.215 = -4440.215 → -4440
    414(LG엔솔): (394500-389500)*4 - (116.85+118.35+1420.2+1568.0) = 20000 - 3223.4 = 16776.6 → 16777
    저장된 net_pnl_pct(-0.03893.../0.008697...) 로는 -4680/+13552 가 나오므로,
    Tier 1(net_pnl_pct 무시)만이 사용자 검증 타깃을 원 단위로 만족한다.
    """
    exit_251 = "2026-05-27T09:02:04.515267+09:00"
    exit_414 = "2026-06-12T09:02:02.690716+09:00"
    rows = [
        dict(candidate_id=251, name="HPSP", entry_price=60100.0, entry_amount=120200.0,
             exit_price=58000.0, exit_shares=2, buy_commission=9.014999999999999,
             sell_commission=8.7, transaction_tax=104.39999999999999,
             estimated_slippage=118.10000000000001, net_pnl_pct=-0.03893284559728694,
             exit_time=exit_251, final_exit_time=exit_251,
             entry_phase1_executed_shares=1, entry_phase2_executed_shares=1),
        dict(candidate_id=414, name="LG에너지솔루션", entry_price=389500.0, entry_amount=1558000.0,
             exit_price=394500.0, exit_shares=4, buy_commission=116.85,
             sell_commission=118.35, transaction_tax=1420.2, estimated_slippage=1568.0,
             net_pnl_pct=0.008697796742555188, exit_time=exit_414, final_exit_time=exit_414,
             entry_phase1_executed_shares=4),
    ]
    # 개별 행 검증 (rounded KRW)
    for row, expected in [(rows[0], -4440), (rows[1], 16777)]:
        sub = tmp_path / f"c{row['candidate_id']}"
        sub.mkdir(exist_ok=True)
        db_path = _make_db(sub, [row])
        monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda p=db_path: p)
        s = data_adapter.get_realized_pnl_summary()
        assert s["closed_count"] == 1
        assert s["cumulative_realized_pnl"] == expected, (
            f"candidate {row['candidate_id']} expected {expected}, got "
            f"{s['cumulative_realized_pnl']}"
        )

    # 합산 검증: -4440.215 + 16776.6 = 12336.385 → 12336
    both_dir = tmp_path / "both"
    both_dir.mkdir(exist_ok=True)
    db_path = _make_db(both_dir, rows)
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 2
    assert s["cumulative_realized_pnl"] == 12336
    assert s["win_count"] == 1  # 414 만 양수


# ===== Tier 2: net_pnl_pct 폴백 (exit_price 부재 legacy) =====


def test_tier2_fallback_uses_entry_amount_when_exit_price_missing(tmp_path, monkeypatch):
    """exit_price NULL → net_pnl_pct × (entry_amount + buy_commission).

    0.05 × (100000 + 200) = 0.05 × 100200 = 5010.
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=100000.0, exit_price=None, exit_shares=10,
        buy_commission=200.0, net_pnl_pct=0.05,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 5010


def test_tier2_fallback_uses_entry_phase_qty_not_exit_shares(tmp_path, monkeypatch):
    """exit_shares != 진입수량: entry_amount 부재 시 진입 체결수량으로 재구성(exit_shares 금지).

    entry_amount NULL, entry_price=10000, 진입 체결수량 p1=6/p2=0 (=6주),
    exit_shares=4 (다름!), buy_commission=0, net_pnl_pct=0.05.
    entry_cost_basis = 10000 × 6 = 60000 → 0.05 × 60000 = 3000.
    (exit_shares 를 썼다면 10000×4=40000 → 2000 이 나옴 — 잘못된 값)
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=None, exit_price=None, exit_shares=4,
        buy_commission=0.0, net_pnl_pct=0.05,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=6, entry_phase2_executed_shares=0,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 3000  # not 2000 (exit_shares 대용 금지)


def test_tier2_entry_price_null_but_exit_price_present_uses_entry_amount(tmp_path, monkeypatch):
    """entry_price NULL(→0) 이면 exit_price>0 이라도 Tier 1 불가 → Tier 2 로 유도.

    entry_amount>0 이므로 Tier 2 는 entry_amount 로 정상 산출:
    0.05 × (100000 + 0) = 5000. (Tier 1 로 잘못 빠지면 (10500-0)*10 로 폭주)
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=None, entry_amount=100000.0, exit_price=10500.0, exit_shares=10,
        buy_commission=0.0, net_pnl_pct=0.05,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 5000


def test_tier2_zero_net_pnl_pct_counts_as_closed_not_win_not_skip(tmp_path, monkeypatch):
    """net_pnl_pct=0.0 (not None) 은 Tier 2 진입 → pnl 0.0 → 청산 집계·미승·미skip."""
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=100000.0, exit_price=None, exit_shares=10,
        buy_commission=0.0, net_pnl_pct=0.0,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 0
    assert s["win_count"] == 0
    assert s["skipped_incomplete"] == 0


def test_tier2_legacy_null_buy_commission_uses_rate(tmp_path, monkeypatch):
    """Tier 2 에서 legacy NULL buy_commission 은 설정 매수수수료율로 재구성.

    폴백율 0.001 → buy_comm = 100000×0.001 = 100. 0.05 × (100000+100) = 5005.
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=100000.0, exit_price=None, exit_shares=10,
        buy_commission=None, net_pnl_pct=0.05,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    monkeypatch.setattr(data_adapter, "_fallback_buy_commission_rate", lambda: 0.001)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1
    assert s["cumulative_realized_pnl"] == 5005


def test_tier2_null_buy_commission_rate_zero_degrades_to_cost_basis(tmp_path, monkeypatch):
    """폴백율 0.0 시 legacy NULL buy_commission 행은 진입원가 기준으로 안전 복귀.

    0.05 × (100000 + 0) = 5000.
    """
    db_path = _make_db(tmp_path, [dict(
        entry_price=10000.0, entry_amount=100000.0, exit_price=None, exit_shares=10,
        buy_commission=None, net_pnl_pct=0.05,
        exit_time=_today_exit(), final_exit_time=_today_exit(),
        entry_phase1_executed_shares=10,
    )])
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    monkeypatch.setattr(data_adapter, "_fallback_buy_commission_rate", lambda: 0.0)
    s = data_adapter.get_realized_pnl_summary()
    assert s["cumulative_realized_pnl"] == 5000


# ===== Tier 3: skip-safe (불완전 legacy) =====


def test_tier3_incomplete_row_skipped_not_fabricated(tmp_path, monkeypatch):
    """진입원가/손익 미확정 legacy 행은 손익 조작 없이 skip → skipped_incomplete 집계.

    행 X: exit_price NULL, entry_amount NULL, entry_price NULL, 진입 체결수량 없음,
          net_pnl_pct 있음 → entry_cost_basis 산출 불가 → skip.
    행 Y: exit_price NULL, net_pnl_pct NULL → skip.
    행 Z: 정상 Tier 2 → 5000 (집계 정상 유지 확인).
    """
    rows = [
        dict(candidate_id=1, entry_price=None, entry_amount=None, exit_price=None,
             exit_shares=3, net_pnl_pct=0.05,
             exit_time=_today_exit(), final_exit_time=_today_exit()),
        dict(candidate_id=2, entry_price=10000.0, entry_amount=100000.0, exit_price=None,
             exit_shares=10, net_pnl_pct=None,
             exit_time=_today_exit(), final_exit_time=_today_exit(),
             entry_phase1_executed_shares=10),
        dict(candidate_id=3, entry_price=10000.0, entry_amount=100000.0, exit_price=None,
             exit_shares=10, buy_commission=0.0, net_pnl_pct=0.05,
             exit_time=_today_exit(), final_exit_time=_today_exit(),
             entry_phase1_executed_shares=10),
    ]
    db_path = _make_db(tmp_path, rows)
    monkeypatch.setattr(data_adapter, "_resolve_db_path", lambda: db_path)
    s = data_adapter.get_realized_pnl_summary()
    assert s["closed_count"] == 1          # Z 만 집계
    assert s["cumulative_realized_pnl"] == 5000
    assert s["skipped_incomplete"] == 2    # X, Y skip


def test_fallback_rate_helper_never_raises(monkeypatch):
    """_fallback_buy_commission_rate 는 엔진 로드 실패 시에도 예외 없이 0.0 반환."""
    import closing_bet_system.engines.cost_slippage_engine as eng

    def _boom():
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(eng, "get_engine", _boom)
    assert data_adapter._fallback_buy_commission_rate() == 0.0


# ===== reconciliation 회귀: 전역 current_total 은 모델값(KIS/종가베팅 미포함) =====


def test_global_current_total_is_model_not_kis(monkeypatch):
    """web.dashboard_service.get_portfolio_data 의 current_total 은
    settings.TOTAL_CAPITAL + trading.db realized 기반 모델값이며,
    KIS 잔액·closing_bet.db PnL 을 포함하지 않는다 (설계 사실 고정)."""
    from web import dashboard_service as svc

    class _FakeDB:
        def get_portfolio(self, status="holding"):
            return []  # 보유 없음 → KIS 호출 경로 미진입 (early return)

        def get_all_sell_trades(self):
            return [{"profit_amount": 12345}]  # trading.db 실현손익

    monkeypatch.setattr(svc, "_get_db", lambda: _FakeDB())

    data = asyncio.run(svc.get_portfolio_data())

    assert data["realized_pnl"] == 12345
    assert data["current_total"] == svc.settings.TOTAL_CAPITAL + 12345
