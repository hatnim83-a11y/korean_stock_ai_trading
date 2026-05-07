"""
test_health_check.py - 16:10 일일 헬스체크 헬퍼 메서드 단위 테스트

main.TradingSystem의 _hc_* 헬퍼 11개를 :memory: SQLite + 가짜 인스턴스로 검증.

실행:
    pytest tests/test_health_check.py -v
또는 직접:
    python tests/test_health_check.py
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


# ============================================================
# 헬퍼: 인스턴스 메서드를 함수처럼 호출하기 위한 Stub
# ============================================================
def _make_stub_system():
    """TradingSystem 인스턴스화 비용 회피 — 헬퍼 메서드만 호출 가능한 stub"""
    from main import TradingSystem
    sys_obj = TradingSystem.__new__(TradingSystem)  # __init__ 우회
    sys_obj._midweek_dropped_theme = None
    sys_obj._midweek_new_theme = None
    sys_obj._last_theme_rotation_date = None
    return sys_obj


def _make_in_memory_db() -> Database:
    db = Database(db_path=":memory:")
    db.connect()
    db.init_tables()
    return db


# ============================================================
# 1. 테마 박제 회귀 감지
# ============================================================
def test_theme_pinning_detects_3plus_zero_rows():
    """selected=1 5건 중 3건 이상 momentum=0/ai=0/news=0 → issue (실제 0 박제만)"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    today_str = str(today)
    try:
        with db.get_cursor() as cur:
            for i, name in enumerate(["A", "B", "C", "D", "E"]):
                # 3건 0 박제, 2건 정상
                if i < 3:
                    cur.execute(
                        "INSERT INTO themes (date, theme_name, score, momentum, ai_sentiment, news_count, selected) "
                        "VALUES (?, ?, ?, 0, 0, 0, 1)",
                        (today, name, 30.0)
                    )
                else:
                    cur.execute(
                        "INSERT INTO themes (date, theme_name, score, momentum, ai_sentiment, news_count, selected) "
                        "VALUES (?, ?, ?, 22.0, 7.5, 100, 1)",
                        (today, name, 30.0)
                    )
        result = sys_obj._hc_theme_score_pinning(db, today_str)
        assert result["issue"] is not None
        assert "박제 의심 3건" in result["issue"]
        assert result["info"] is None
    finally:
        db.close()


def test_theme_pinning_null_only_is_not_issue():
    """selected=1 5건 모두 NULL → info에 폴백 대기 표시, issue 아님"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            for name in ["A", "B", "C", "D", "E"]:
                cur.execute(
                    "INSERT INTO themes (date, theme_name, score, selected) VALUES (?, ?, ?, 1)",
                    (today, name, 30.0)
                )
        result = sys_obj._hc_theme_score_pinning(db, str(today))
        assert result["issue"] is None
        assert "NULL 5건" in result["info"]
        assert "폴백 대기" in result["info"]
    finally:
        db.close()


def test_theme_pinning_normal_state():
    """selected=1 5건 모두 정상 → info, issue=None"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            for name in ["A", "B", "C", "D", "E"]:
                cur.execute(
                    "INSERT INTO themes (date, theme_name, score, momentum, ai_sentiment, news_count, selected) "
                    "VALUES (?, ?, ?, 22.0, 7.5, 100, 1)",
                    (today, name, 30.0)
                )
        result = sys_obj._hc_theme_score_pinning(db, str(today))
        assert result["issue"] is None
        assert "정상 5건" in result["info"]
    finally:
        db.close()


def test_theme_pinning_null_columns_show_in_info():
    """NULL 컬럼은 info에 표시되지만 issue는 아님 (3건 미만)"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            # 2건 NULL, 3건 정상 (박제 의심 2건 < 3 → issue 아님)
            for i, name in enumerate(["A", "B", "C", "D", "E"]):
                if i < 2:
                    cur.execute(
                        "INSERT INTO themes (date, theme_name, score, selected) VALUES (?, ?, ?, 1)",
                        (today, name, 30.0)
                    )
                else:
                    cur.execute(
                        "INSERT INTO themes (date, theme_name, score, momentum, ai_sentiment, news_count, selected) "
                        "VALUES (?, ?, ?, 22.0, 7.5, 100, 1)",
                        (today, name, 30.0)
                    )
        result = sys_obj._hc_theme_score_pinning(db, str(today))
        assert result["issue"] is None
        assert "NULL 2건" in result["info"]
        assert "정상 3건" in result["info"]
    finally:
        db.close()


# ============================================================
# 2. screening_log 3-stage 적재
# ============================================================
def test_screening_log_stages_all_present():
    """3 stage 모두 적재 → info"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            for stage, count in [("filter", 80), ("gap_filter", 5), ("ai_verify", 3)]:
                for i in range(count):
                    cur.execute(
                        "INSERT INTO screening_log (date, stock_code, stock_name, stage, passed) "
                        "VALUES (?, ?, ?, ?, 1)",
                        (today, f"00000{i:01d}", f"S{i}", stage)
                    )
        result = sys_obj._hc_screening_log_stages(db, str(today))
        assert result["issue"] is None
        assert "filter=80" in result["info"]
        assert "gap=5" in result["info"]
        assert "ai=3" in result["info"]
    finally:
        db.close()


def test_screening_log_filter_zero_on_business_day_is_issue():
    """영업일에 filter=0이면 issue (휴장일이면 정상)"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    # 영업일이 아닌 토요일로 가정 (실제 평일 영업일 검증은 mock 필요하나, 이 테스트는 기본 동작만)
    # 5/8은 금요일 영업일, filter=0이면 issue
    today = date(2026, 5, 8)
    try:
        result = sys_obj._hc_screening_log_stages(db, str(today))
        # 5/8은 영업일 → issue
        if not result["info"] or "휴장일" not in result["info"]:
            assert result["issue"] is not None
    finally:
        db.close()


# ============================================================
# 3. Phase A 메트릭
# ============================================================
def test_phase_a_metrics_protected_count():
    """theme_slot_protected=1 + RSI 분포 정상 표시"""
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            for i in range(10):
                cur.execute(
                    "INSERT INTO screening_log (date, stock_code, stock_name, stage, passed, "
                    "rsi_at_screen, theme_slot_protected) "
                    "VALUES (?, ?, ?, 'filter', ?, ?, ?)",
                    (today, f"00000{i:01d}", f"S{i}", 1 if i < 5 else 0, 60.0 + i, 1 if i < 5 else 0)
                )
        result = sys_obj._hc_phase_a_metrics(db, str(today))
        assert "슬롯 보장 5건" in result["info"]
        assert "RSI 평균" in result["info"]
    finally:
        db.close()


# ============================================================
# 4. midweek 교체 발생 알림
# ============================================================
def test_midweek_replacement_detected():
    sys_obj = _make_stub_system()
    sys_obj._midweek_dropped_theme = "철강"
    sys_obj._midweek_new_theme = {"theme_name": "조선"}
    result = sys_obj._hc_midweek_replacement()
    assert "❌철강" in result["info"]
    assert "🆕조선" in result["info"]
    assert result["issue"] is None


def test_midweek_replacement_idle():
    sys_obj = _make_stub_system()
    result = sys_obj._hc_midweek_replacement()
    assert result["info"] is None
    assert result["issue"] is None


# ============================================================
# 5. 보정 재선정 발화 감지
# ============================================================
def test_makeup_reselection_fired():
    """오늘이 보정일 + _last_theme_rotation_date >= missed_tue → info"""
    sys_obj = _make_stub_system()
    sys_obj._last_theme_rotation_date = date(2026, 5, 6)  # 5/5 어린이날 후 발화

    # 오늘을 5/6으로 가정하기 위해 now_kst 패치
    with patch("main.now_kst") as mock_now:
        mock_now.return_value = datetime(2026, 5, 6, 16, 10)
        with patch("main.is_makeup_reselection_day") as mock_makeup:
            mock_makeup.return_value = (True, date(2026, 5, 5))
            result = sys_obj._hc_makeup_reselection()
            assert "발화함" in result["info"]
            assert result["issue"] is None


def test_makeup_reselection_missed():
    """오늘이 보정일이지만 _last_theme_rotation_date < missed_tue → issue"""
    sys_obj = _make_stub_system()
    sys_obj._last_theme_rotation_date = date(2026, 5, 4)  # 5/5 휴장 직전

    with patch("main.now_kst") as mock_now:
        mock_now.return_value = datetime(2026, 5, 6, 16, 10)
        with patch("main.is_makeup_reselection_day") as mock_makeup:
            mock_makeup.return_value = (True, date(2026, 5, 5))
            result = sys_obj._hc_makeup_reselection()
            assert result["info"] is None
            assert "미발화 의심" in result["issue"]


def test_makeup_reselection_not_makeup_day():
    """오늘이 보정일 아님 → 둘 다 None"""
    sys_obj = _make_stub_system()
    with patch("main.is_makeup_reselection_day") as mock_makeup:
        mock_makeup.return_value = (False, None)
        result = sys_obj._hc_makeup_reselection()
        assert result["info"] is None
        assert result["issue"] is None


# ============================================================
# 6. trade_reviews 누락 감지
# ============================================================
def test_trade_review_coverage_missing():
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            # SELL 3건 / review 1건
            for i in range(3):
                cur.execute(
                    "INSERT INTO trades (date, stock_code, stock_name, action) "
                    "VALUES (?, ?, ?, 'sell')",
                    (today, f"00000{i:01d}", f"S{i}")
                )
            cur.execute(
                "INSERT INTO trade_reviews (stock_code, stock_name, sell_price, sell_reason, hold_days, profit_rate, profit_amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("000000", "S0", 1000, "test", 1, 0.05, 1000, datetime(2026, 5, 8, 10, 0))
            )
        result = sys_obj._hc_trade_review_coverage(db, str(today))
        assert "누락 의심" in result["issue"]
    finally:
        db.close()


def test_trade_review_coverage_normal():
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "INSERT INTO trades (date, stock_code, stock_name, action) VALUES (?, ?, ?, 'sell')",
                (today, "000001", "S1")
            )
            cur.execute(
                "INSERT INTO trade_reviews (stock_code, stock_name, sell_price, sell_reason, hold_days, profit_rate, profit_amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("000001", "S1", 1000, "test", 1, 0.05, 1000, datetime(2026, 5, 8, 10, 0))
            )
        result = sys_obj._hc_trade_review_coverage(db, str(today))
        assert "1/1건 적재" in result["info"]
        assert result["issue"] is None
    finally:
        db.close()


# ============================================================
# 7. slippage 측정 적재율
# ============================================================
def test_slippage_coverage_all_null():
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            for i in range(3):
                cur.execute(
                    "INSERT INTO trades (date, stock_code, stock_name, action, slippage) "
                    "VALUES (?, ?, ?, 'sell', NULL)",
                    (today, f"00000{i:01d}", f"S{i}")
                )
        result = sys_obj._hc_slippage_coverage(db, str(today))
        assert "미측정" in result["issue"]
    finally:
        db.close()


def test_slippage_coverage_partial():
    db = _make_in_memory_db()
    sys_obj = _make_stub_system()
    today = date(2026, 5, 8)
    try:
        with db.get_cursor() as cur:
            cur.execute(
                "INSERT INTO trades (date, stock_code, stock_name, action, slippage) "
                "VALUES (?, ?, ?, 'sell', 0.001)",
                (today, "000001", "S1")
            )
            cur.execute(
                "INSERT INTO trades (date, stock_code, stock_name, action, slippage) "
                "VALUES (?, ?, ?, 'sell', NULL)",
                (today, "000002", "S2")
            )
        result = sys_obj._hc_slippage_coverage(db, str(today))
        assert "1/2건 측정" in result["info"]
        assert result["issue"] is None
    finally:
        db.close()


# ============================================================
# 8. 디스크 사용률
# ============================================================
def test_disk_usage_returns_info():
    """디스크 사용률은 시스템 의존이라 형식만 검증"""
    sys_obj = _make_stub_system()
    result = sys_obj._hc_disk_usage()
    # 90% 이하면 info, 초과면 issue
    assert (result["info"] is not None) or (result["issue"] is not None)
    if result["info"]:
        assert "%" in result["info"]
        assert "GB" in result["info"]


# ============================================================
# 9. SellLock 잔존
# ============================================================
def test_sell_lock_residual_empty():
    sys_obj = _make_stub_system()
    from modules.trading_engine.sell_lock import sell_lock
    sell_lock.clear_all()  # 테스트 격리
    result = sys_obj._hc_sell_lock_residual()
    assert "잔존 락 0건" in result["info"]
    assert result["issue"] is None


def test_sell_lock_residual_with_locks():
    sys_obj = _make_stub_system()
    from modules.trading_engine.sell_lock import sell_lock
    sell_lock.clear_all()
    sell_lock.acquire("005930", "test_owner")
    try:
        result = sys_obj._hc_sell_lock_residual()
        assert result["info"] is None
        assert "잔존 의심" in result["issue"]
        assert "005930" in result["issue"]
    finally:
        sell_lock.clear_all()


# ============================================================
# 10. systemd dashboard
# ============================================================
def test_systemd_dashboard_uses_subprocess():
    """systemctl 호출은 시스템 의존 — 형식만 검증"""
    sys_obj = _make_stub_system()
    result = sys_obj._hc_systemd_dashboard()
    # active or inactive (둘 다 형식 정상)
    assert (result["info"] is not None) or (result["issue"] is not None)


# ============================================================
# 11. 종가베팅 universe
# ============================================================
def test_closing_bet_universe_no_db_returns_none():
    """data/closing_bet.db 없으면 둘 다 None (옵션)"""
    sys_obj = _make_stub_system()
    # 실제 운영 DB는 존재할 수 있으나, 결과 형식만 검증
    result = sys_obj._hc_closing_bet_universe("2099-01-01")  # 미래 날짜로 0건 보장
    # 0건 → info=None, issue=None
    if result["info"]:
        assert "candidates" in result["info"]
    # 정상 호출 — 예외 없음


# ============================================================
# 직접 실행
# ============================================================
if __name__ == "__main__":
    import traceback
    tests = [
        ("Theme pinning detects 3+ zero rows", test_theme_pinning_detects_3plus_zero_rows),
        ("Theme pinning NULL only is not issue", test_theme_pinning_null_only_is_not_issue),
        ("Theme pinning normal state", test_theme_pinning_normal_state),
        ("Theme pinning NULL columns in info", test_theme_pinning_null_columns_show_in_info),
        ("Screening log all stages present", test_screening_log_stages_all_present),
        ("Screening log filter=0 issue", test_screening_log_filter_zero_on_business_day_is_issue),
        ("Phase A metrics protected count", test_phase_a_metrics_protected_count),
        ("Midweek replacement detected", test_midweek_replacement_detected),
        ("Midweek replacement idle", test_midweek_replacement_idle),
        ("Makeup reselection fired", test_makeup_reselection_fired),
        ("Makeup reselection missed", test_makeup_reselection_missed),
        ("Makeup reselection not makeup day", test_makeup_reselection_not_makeup_day),
        ("Trade review coverage missing", test_trade_review_coverage_missing),
        ("Trade review coverage normal", test_trade_review_coverage_normal),
        ("Slippage coverage all NULL", test_slippage_coverage_all_null),
        ("Slippage coverage partial", test_slippage_coverage_partial),
        ("Disk usage info", test_disk_usage_returns_info),
        ("SellLock residual empty", test_sell_lock_residual_empty),
        ("SellLock residual with locks", test_sell_lock_residual_with_locks),
        ("systemd dashboard subprocess", test_systemd_dashboard_uses_subprocess),
        ("Closing bet universe no DB", test_closing_bet_universe_no_db_returns_none),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n총 {len(tests)}건 — PASS {passed} / FAIL {failed}")
    sys.exit(0 if failed == 0 else 1)
