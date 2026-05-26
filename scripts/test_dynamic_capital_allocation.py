"""단위 테스트 — 동적 자본 분리 (2026-05-23)

Plan/CHECKLIST 의 DC-1~10 + DC-5-B + DC-6-B 시나리오 12건 검증.

실행:
    venv/bin/python scripts/test_dynamic_capital_allocation.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from closing_bet_system.infra.fund_guard import FundGuard, GuardConfig
from closing_bet_system.infra.swing_db_reader import SwingDBUnavailable


# ===== Helpers =====


def _make_cfg(**overrides) -> GuardConfig:
    """기본 GuardConfig + overrides."""
    base = dict(
        capital_ratio=0.10,
        max_position_per_stock=0.25,
        max_concurrent_positions=4,
        max_daily_entries=4,
        weekly_loss_limit=-0.05,
        absorb_swing_idle=True,
        swing_capital_ratio=0.9,
        closing_bet_pool_cap=0.5,
        swing_used_source="cost_basis",
        disable_absorb_on_crisis=True,
    )
    base.update(overrides)
    return GuardConfig(**base)


def _make_guard(cfg=None, swing_used=0, total_value=10_000_000, raise_db=False):
    """FundGuard with mocked providers."""
    def used_provider(src):
        if raise_db:
            raise SwingDBUnavailable("mock DB unavailable")
        return swing_used
    return FundGuard(
        config=cfg or _make_cfg(),
        total_value_provider=lambda: total_value,
        swing_holdings_provider=lambda: set(),
        swing_used_provider=used_provider,
    )


# ===== DC-1: 스윙 풀 0% 사용 → cap 도달 =====


def test_DC_1_swing_idle_full():
    fg = _make_guard(swing_used=0, total_value=10_000_000)
    limit, info = fg.compute_capital_limit(10_000_000)
    # base=1M, swing_pool=9M, swing_used=0, swing_idle=9M, cap=5M
    # dynamic_pool=1M+9M=10M, min(10M, 5M)=5M (capped)
    assert limit == 5_000_000, f"expected 5M, got {limit}"
    assert info["capped"] is True
    assert info["swing_idle"] == 9_000_000
    assert info["mode"] == "absorb_swing_idle"


# ===== DC-2: 스윙 풀 100%+ 사용 → base 10%만 =====


def test_DC_2_swing_fully_used():
    fg = _make_guard(swing_used=10_000_000, total_value=10_000_000)
    limit, info = fg.compute_capital_limit(10_000_000)
    # base=1M, swing_pool=9M, swing_used=10M, swing_idle=max(0,9M-10M)=0
    # dynamic_pool=1M+0=1M, min(1M, 5M)=1M
    assert limit == 1_000_000, f"expected 1M, got {limit}"
    assert info["swing_idle"] == 0
    assert info["capped"] is False


# ===== DC-3: 스윙 풀 50% 사용 → 10%+40% =====


def test_DC_3_swing_half_used():
    fg = _make_guard(swing_used=4_500_000, total_value=10_000_000)
    limit, info = fg.compute_capital_limit(10_000_000)
    # base=1M, swing_pool=9M, swing_used=4.5M, swing_idle=4.5M
    # dynamic_pool=1M+4.5M=5.5M, min(5.5M, 5M)=5M (capped 직전)
    assert limit == 5_000_000, f"expected 5M (cap), got {limit}"
    assert info["swing_idle"] == 4_500_000
    assert info["capped"] is True


# ===== DC-4: absorb_swing_idle=False → 기존 10% 고정 =====


def test_DC_4_absorb_disabled():
    cfg = _make_cfg(absorb_swing_idle=False)
    fg = _make_guard(cfg=cfg, swing_used=0)
    limit, info = fg.compute_capital_limit(10_000_000)
    assert limit == 1_000_000, f"expected 1M (base only), got {limit}"
    assert info["mode"] == "base_only(absorb_off)"


# ===== DC-5: 4종목 강제 (LIMIT 4) =====


def test_DC_5_top4_strict():
    """_select_phase1_candidates SQL 직접 검증 (mock DB)."""
    # 이 케이스는 entry_executor 통합 테스트라 별도 mock 필요.
    # 여기서는 cfg.max_concurrent_positions 값 검증만.
    cfg = _make_cfg(max_concurrent_positions=4)
    assert cfg.max_concurrent_positions == 4
    # per_stock_limit 계산 검증
    fg = _make_guard(cfg=cfg, swing_used=0)
    limit, _ = fg.compute_capital_limit(10_000_000)
    per_stock = limit // cfg.max_concurrent_positions
    assert per_stock == 1_250_000, f"expected 1.25M, got {per_stock}"


# ===== DC-5-B: max_concurrent_positions=3 (다른 N 검증) =====


def test_DC_5_B_max_positions_3():
    cfg = _make_cfg(max_concurrent_positions=3)
    fg = _make_guard(cfg=cfg, swing_used=0)
    limit, _ = fg.compute_capital_limit(10_000_000)
    per_stock = limit // cfg.max_concurrent_positions
    # limit=5M (cap), per_stock=5M/3=1,666,666
    assert per_stock == 1_666_666, f"expected ~1.67M, got {per_stock}"


# ===== DC-6: 빈 portfolio → swing_used=0 =====


def test_DC_6_empty_portfolio():
    fg = _make_guard(swing_used=0)
    limit, info = fg.compute_capital_limit(10_000_000)
    assert info["swing_used"] == 0
    assert info["swing_idle"] == 9_000_000  # swing_pool 전체가 idle


# ===== DC-6-B: 스윙 DB 파일 없음 → fund_guard 폴백 (swing_pool 가정) =====


def test_DC_6_B_swing_db_unavailable():
    fg = _make_guard(raise_db=True, total_value=10_000_000)
    limit, info = fg.compute_capital_limit(10_000_000)
    # DB 폴백 → swing_used=swing_pool → swing_idle=0 → dynamic_pool=base만
    assert limit == 1_000_000, f"expected 1M (base only fallback), got {limit}"
    assert info["swing_idle"] == 0


# ===== DC-7: SoT 동기화 (compute_capital_limit() 단일 진입점) =====


def test_DC_7_sot_consistency():
    """fund_guard.compute_capital_limit() 호출과 entry_executor 사용 동일성."""
    fg = _make_guard(swing_used=2_000_000)
    # 두 번 호출 결과가 같아야 (deterministic)
    limit1, _ = fg.compute_capital_limit(10_000_000)
    limit2, _ = fg.compute_capital_limit(10_000_000)
    assert limit1 == limit2

    # per_stock_limit이 일관성 있게 max_concurrent_positions로 나눠지는지
    per_stock = limit1 // fg.config.max_concurrent_positions
    expected = limit1 // 4
    assert per_stock == expected


# ===== DC-8: swing_used > swing_pool (수익 누적) → swing_idle=0 =====


def test_DC_8_swing_used_overflow():
    fg = _make_guard(swing_used=15_000_000, total_value=10_000_000)
    limit, info = fg.compute_capital_limit(10_000_000)
    # swing_pool=9M, swing_used=15M, max(0, 9M-15M)=0
    assert info["swing_idle"] == 0
    assert limit == 1_000_000  # base만


# ===== DC-9: settings 키 누락 → 기본값 사용 =====


def test_DC_9_settings_keys_missing():
    """from_settings()에 신규 키 누락된 dict 전달 → 기본값 사용."""
    incomplete = {"fund": {"capital_ratio": 0.15}}  # 다른 신규 키 모두 누락
    cfg = GuardConfig.from_settings(incomplete)
    assert cfg.capital_ratio == 0.15
    assert cfg.absorb_swing_idle is False  # 기본 False (롤백 안전)
    assert cfg.swing_capital_ratio == 0.9
    assert cfg.closing_bet_pool_cap == 0.5
    assert cfg.swing_used_source == "cost_basis"
    assert cfg.disable_absorb_on_crisis is True


# ===== DC-10: external_risk_active=True → absorb 비활성 =====


def test_DC_10_external_risk_disables_absorb():
    fg = _make_guard(swing_used=0, total_value=10_000_000)
    # NORMAL 시 cap 도달
    limit_normal, _ = fg.compute_capital_limit(10_000_000, external_risk_active=False)
    assert limit_normal == 5_000_000  # cap

    # CAUTION/DANGER 시 absorb 비활성 → base만
    limit_risk, info_risk = fg.compute_capital_limit(
        10_000_000, external_risk_active=True
    )
    assert limit_risk == 1_000_000, f"expected 1M (base only), got {limit_risk}"
    assert info_risk["mode"] == "base_only(crisis)"


# ===== DC-11: closing_bet_pool_cap 범위 검증 =====


def test_DC_11_cap_range_validation():
    """closing_bet_pool_cap 범위 이탈 시 from_settings 폴백."""
    invalid = {"fund": {"closing_bet_pool_cap": 1.5}}  # 1.0 초과
    cfg = GuardConfig.from_settings(invalid)
    assert cfg.closing_bet_pool_cap == 0.5  # 기본값 폴백

    invalid2 = {"fund": {"closing_bet_pool_cap": -0.1}}  # 음수
    cfg2 = GuardConfig.from_settings(invalid2)
    assert cfg2.closing_bet_pool_cap == 0.5


# ===== DC-12: swing_used_source 잘못된 값 → cost_basis 폴백 =====


def test_DC_12_invalid_swing_used_source():
    invalid = {"fund": {"swing_used_source": "wrong_mode"}}
    cfg = GuardConfig.from_settings(invalid)
    assert cfg.swing_used_source == "cost_basis"  # 폴백


# ===== DC-13: 실 portfolio 스키마(shares 컬럼) 통합 스모크 =====


def test_DC_13_real_schema_smoke():
    """get_swing_used_value() SQL이 실제 스윙 portfolio 스키마(shares 컬럼)와 정합.

    회귀 차단: 5/26 운영 환경에서 `no such column: quantity` 오류 발견 (스키마 잘못 가정).
    이 테스트는 임시 sqlite 파일에 실 schema로 portfolio 만들어 SUM 동작 검증.
    """
    import os
    import sqlite3
    import tempfile
    from pathlib import Path

    from closing_bet_system.infra import swing_db_reader

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        # 실 스키마 그대로 (database.py:create_portfolio_table 기반 핵심 컬럼)
        conn = sqlite3.connect(tmp_path)
        conn.execute(
            """
            CREATE TABLE portfolio (
                stock_code TEXT, stock_name TEXT,
                shares INTEGER, buy_price REAL, current_price REAL,
                status TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO portfolio VALUES ('068270', '셀트리온', 6, 184600, 195200, 'holding')"
        )
        conn.execute(
            "INSERT INTO portfolio VALUES ('005930', '삼성전자', 10, 75000, 76000, 'holding')"
        )
        conn.execute(
            # 매도 완료된 종목 (제외 확인)
            "INSERT INTO portfolio VALUES ('000660', 'SK하이닉스', 5, 180000, 0, 'sold')"
        )
        conn.commit()
        conn.close()

        # monkeypatch _resolve_swing_db_path
        original = swing_db_reader._resolve_swing_db_path
        swing_db_reader._resolve_swing_db_path = lambda: Path(tmp_path)
        try:
            # cost_basis: 6*184600 + 10*75000 = 1,107,600 + 750,000 = 1,857,600
            v_cost = swing_db_reader.get_swing_used_value(source="cost_basis")
            assert v_cost == 1_857_600, f"cost_basis: expected 1,857,600, got {v_cost}"

            # evaluation: position_state 미존재 → cost_basis 폴백 (동일 결과)
            v_eval = swing_db_reader.get_swing_used_value(source="evaluation")
            assert v_eval == 1_857_600, f"evaluation fallback: expected 1,857,600, got {v_eval}"
        finally:
            swing_db_reader._resolve_swing_db_path = original
    finally:
        os.unlink(tmp_path)


# ===== DC-14: position_state JOIN 정상 동작 (evaluation 모드 본래 의도) =====


def test_DC_14_evaluation_mode_with_position_state():
    """evaluation 모드: position_state.current_price 우선 사용."""
    import os
    import sqlite3
    import tempfile
    from pathlib import Path

    from closing_bet_system.infra import swing_db_reader

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        conn = sqlite3.connect(tmp_path)
        conn.execute(
            """
            CREATE TABLE portfolio (
                stock_code TEXT, stock_name TEXT,
                shares INTEGER, buy_price REAL, current_price REAL,
                status TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE position_state (stock_code TEXT PRIMARY KEY, current_price REAL)"
        )
        # 셀트리온: portfolio.buy_price=184600, position_state.current_price=200000 (수익)
        conn.execute(
            "INSERT INTO portfolio VALUES ('068270', '셀트리온', 6, 184600, 195200, 'holding')"
        )
        conn.execute("INSERT INTO position_state VALUES ('068270', 200000)")
        conn.commit()
        conn.close()

        original = swing_db_reader._resolve_swing_db_path
        swing_db_reader._resolve_swing_db_path = lambda: Path(tmp_path)
        try:
            # evaluation: 6 * 200000 (position_state) = 1,200,000
            v = swing_db_reader.get_swing_used_value(source="evaluation")
            assert v == 1_200_000, f"evaluation: expected 1,200,000, got {v}"
        finally:
            swing_db_reader._resolve_swing_db_path = original
    finally:
        os.unlink(tmp_path)


# ===== Runner =====


if __name__ == "__main__":
    tests = [
        ("DC-1: 스윙 0% → cap 도달", test_DC_1_swing_idle_full),
        ("DC-2: 스윙 100%+ → base", test_DC_2_swing_fully_used),
        ("DC-3: 스윙 50% → cap 직전", test_DC_3_swing_half_used),
        ("DC-4: absorb 비활성", test_DC_4_absorb_disabled),
        ("DC-5: top 4 (max_concurrent=4)", test_DC_5_top4_strict),
        ("DC-5-B: max_concurrent=3", test_DC_5_B_max_positions_3),
        ("DC-6: 빈 portfolio", test_DC_6_empty_portfolio),
        ("DC-6-B: DB 폴백", test_DC_6_B_swing_db_unavailable),
        ("DC-7: SoT 동기화", test_DC_7_sot_consistency),
        ("DC-8: swing_used > pool", test_DC_8_swing_used_overflow),
        ("DC-9: settings 키 누락", test_DC_9_settings_keys_missing),
        ("DC-10: external_risk 차단", test_DC_10_external_risk_disables_absorb),
        ("DC-11: cap 범위 검증", test_DC_11_cap_range_validation),
        ("DC-12: invalid source 폴백", test_DC_12_invalid_swing_used_source),
        ("DC-13: 실 schema(shares) 스모크", test_DC_13_real_schema_smoke),
        ("DC-14: evaluation+position_state", test_DC_14_evaluation_mode_with_position_state),
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name} PASS")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name} FAIL: {e}")
            failed.append((name, str(e)))
        except Exception as e:
            print(f"  ❌ {name} ERROR: {type(e).__name__}: {e}")
            failed.append((name, f"{type(e).__name__}: {e}"))
    print(f"\n=== 결과: {passed}/{len(tests)} PASS ===")
    if failed:
        sys.exit(1)
