"""test_supply_db_helpers.py — Phase 1-B 신규 헬퍼 5개 단위 테스트.

검증 대상:
1. get_supply_snapshots_bulk: 다수 종목 일괄 조회 (트레이드 데이트 자동/명시)
2. get_theme_supply_aggregate: 테마 집계 (평균 + 양수 비율)
3. is_in_foreign_top: 외인 TOP 순위 조회
4. update_portfolio_supply_context: 매수 후 컨텍스트 박제 (화이트리스트 SQL injection 방어 포함)
5. get_supply_attribution_data: 사후 분석용 trade_reviews 슬라이스
6. kis_api.get_stock_full_info skip_supply 옵션 (interface 레벨)

파일 직접 실행: python tests/test_supply_db_helpers.py
"""
import sys
import tempfile
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


def _temp_db() -> Database:
    """임시 DB (사이드이펙트 없음)."""
    tmpdir = Path(tempfile.mkdtemp(prefix="test_supply_helpers_"))
    db = Database(db_path=str(tmpdir / "trading.db"))
    db.connect()
    db.init_tables()
    return db


def _insert_sample_snapshots(db: Database, trade_date: date) -> None:
    """5종목 샘플 데이터 삽입."""
    samples = [
        ("005930", "삼성전자", 5e9, 3e9, 100.0),
        ("000660", "SK하이닉스", 4e9, 2e9, 200.0),
        ("035420", "NAVER", -1e9, 5e8, 50.0),
        ("373220", "LG에너지솔루션", 8e9, -2e9, 300.0),
        ("066970", "엘앤에프", -5e8, 1e9, 400.0),
    ]
    for code, name, fnet, inet, close in samples:
        db.save_supply_snapshot(trade_date, code, {
            "stock_name": name, "foreign_net": fnet, "institution_net": inet,
            "individual_net": -(fnet + inet), "foreign_ratio": 50.0,
            "daily": [
                {"date": "20260512", "foreign": int(fnet / close), "institution": int(inet / close),
                 "individual": 0, "close_price": close},
            ],
        })


# ===== get_supply_snapshots_bulk =====

def test_bulk_returns_only_existing():
    """존재하는 종목만 반환 (없는 종목은 키 누락)."""
    db = _temp_db()
    try:
        _insert_sample_snapshots(db, date(2026, 5, 12))
        result = db.get_supply_snapshots_bulk(
            ["005930", "000660", "999999"], date(2026, 5, 12)
        )
        assert len(result) == 2
        assert "005930" in result
        assert "000660" in result
        assert "999999" not in result
        print("  [PASS] bulk_returns_only_existing")
    finally:
        db.close()


def test_bulk_empty_codes_returns_empty():
    """빈 리스트 입력 → 빈 dict."""
    db = _temp_db()
    try:
        result = db.get_supply_snapshots_bulk([], date(2026, 5, 12))
        assert result == {}
        print("  [PASS] bulk_empty_codes_returns_empty")
    finally:
        db.close()


def test_bulk_trade_date_none_returns_latest():
    """trade_date=None이면 각 종목별 가장 최근 영업일."""
    db = _temp_db()
    try:
        # 005930: 5/10, 5/11, 5/12 (가장 최근=5/12)
        for d in [date(2026, 5, 10), date(2026, 5, 11), date(2026, 5, 12)]:
            db.save_supply_snapshot(d, "005930", {
                "foreign_net": int(d.day) * 1e9, "institution_net": 0,
                "individual_net": 0, "foreign_ratio": 50.0, "daily": [],
            })
        # 000660: 5/9만
        db.save_supply_snapshot(date(2026, 5, 9), "000660", {
            "foreign_net": 9e9, "institution_net": 0,
            "individual_net": 0, "foreign_ratio": 50.0, "daily": [],
        })

        result = db.get_supply_snapshots_bulk(["005930", "000660"])
        assert result["005930"]["foreign_net_5d"] == 12 * 1e9  # 5/12 데이터
        assert result["005930"]["trade_date"] == "2026-05-12"
        assert result["000660"]["foreign_net_5d"] == 9e9
        assert result["000660"]["trade_date"] == "2026-05-09"
        print("  [PASS] bulk_trade_date_none_returns_latest")
    finally:
        db.close()


# ===== get_theme_supply_aggregate =====

def test_aggregate_basic():
    """평균 + 양수 비율 정확 계산."""
    db = _temp_db()
    try:
        _insert_sample_snapshots(db, date(2026, 5, 12))
        # 005930=+5e9, 000660=+4e9, 373220=+8e9, 035420=-1e9, 066970=-5e8
        agg = db.get_theme_supply_aggregate(
            ["005930", "000660", "035420", "373220", "066970"], date(2026, 5, 12)
        )
        assert agg["n"] == 5
        # 평균 = (5+4-1+8-0.5) / 5 = 3.1
        expected_foreign_avg = (5e9 + 4e9 - 1e9 + 8e9 - 0.5e9) / 5
        assert abs(agg["foreign_avg"] - expected_foreign_avg) < 1.0
        # 양수 비율: 005930+/000660+/373220+ = 3/5 = 60%
        assert abs(agg["foreign_pos_ratio"] - 60.0) < 0.1
        # 기관 양수: 3e9, 2e9, 5e8, 1e9 = 4건 / -2e9 = 1건 → 4/5 = 80%
        assert abs(agg["institution_pos_ratio"] - 80.0) < 0.1
        print("  [PASS] aggregate_basic")
    finally:
        db.close()


def test_aggregate_empty_codes():
    """빈 리스트 → 모두 0."""
    db = _temp_db()
    try:
        agg = db.get_theme_supply_aggregate([], date(2026, 5, 12))
        assert agg["n"] == 0
        assert agg["foreign_avg"] == 0.0
        assert agg["foreign_pos_ratio"] == 0.0
        print("  [PASS] aggregate_empty_codes")
    finally:
        db.close()


def test_aggregate_handles_nulls():
    """NULL foreign_net_5d 행은 평균에서 제외."""
    db = _temp_db()
    try:
        # 1개는 정상, 1개는 NULL 행
        db.save_supply_snapshot(date(2026, 5, 12), "005930", {
            "foreign_net": 5e9, "institution_net": 3e9, "individual_net": 0,
            "foreign_ratio": 50.0, "daily": [],
        })
        db.save_supply_snapshot_null(date(2026, 5, 12), "999999")

        agg = db.get_theme_supply_aggregate(["005930", "999999"], date(2026, 5, 12))
        assert agg["n"] == 2  # 2개 스냅샷
        assert agg["foreign_avg"] == 5e9  # NULL 제외하고 1개만
        assert agg["foreign_pos_ratio"] == 100.0
        print("  [PASS] aggregate_handles_nulls")
    finally:
        db.close()


# ===== is_in_foreign_top =====

def test_in_foreign_top_returns_rank():
    """외인 TOP 진입 종목은 rank 반환."""
    db = _temp_db()
    try:
        items = [
            {"stock_code": "005930", "stock_name": "삼성전자", "net_amount": 1e11},
            {"stock_code": "000660", "stock_name": "SK하이닉스", "net_amount": 8e10},
            {"stock_code": "035420", "stock_name": "NAVER", "net_amount": 5e10},
        ]
        db.save_foreign_top_ranking(date(2026, 5, 12), "foreign_buy", items)

        assert db.is_in_foreign_top("005930", "foreign_buy", date(2026, 5, 12)) == 1
        assert db.is_in_foreign_top("000660", "foreign_buy", date(2026, 5, 12)) == 2
        assert db.is_in_foreign_top("035420", "foreign_buy", date(2026, 5, 12)) == 3
        assert db.is_in_foreign_top("999999", "foreign_buy", date(2026, 5, 12)) is None
        print("  [PASS] in_foreign_top_returns_rank")
    finally:
        db.close()


def test_in_foreign_top_none_for_wrong_kind():
    """kind 불일치 시 None."""
    db = _temp_db()
    try:
        items = [{"stock_code": "005930", "net_amount": 1e11}]
        db.save_foreign_top_ranking(date(2026, 5, 12), "foreign_buy", items)
        # 다른 kind로 조회
        assert db.is_in_foreign_top("005930", "institution_buy", date(2026, 5, 12)) is None
        print("  [PASS] in_foreign_top_none_for_wrong_kind")
    finally:
        db.close()


# ===== update_portfolio_supply_context =====

def test_update_portfolio_supply_context_basic():
    """보유 종목에 supply 컨텍스트 박제."""
    db = _temp_db()
    try:
        # 보유 종목 1건 추가
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO portfolio (date, stock_code, stock_name, shares, "
                "buy_price, current_price, status, take_profit, stop_loss, theme) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-12", "005930", "삼성전자", 10, 70000, 70000, "holding",
                 0.1, 0.05, "반도체")
            )

        rowcount = db.update_portfolio_supply_context("005930", {
            "foreign_net_at_buy": 5e9,
            "institution_net_at_buy": 3e9,
            "supply_strength_at_buy": 1.5,
            "foreign_top_rank_at_buy": 5,
            "institution_consec_days_at_buy": 3,
            "theme_supply_score_at_buy": 4.2,
            "ai_supply_signal_at_buy": "strong",
        })
        assert rowcount == 1

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT foreign_net_at_buy, supply_strength_at_buy, ai_supply_signal_at_buy "
                "FROM portfolio WHERE stock_code='005930'"
            )
            row = cursor.fetchone()
        assert row["foreign_net_at_buy"] == 5e9
        assert row["supply_strength_at_buy"] == 1.5
        assert row["ai_supply_signal_at_buy"] == "strong"
        print("  [PASS] update_portfolio_supply_context_basic")
    finally:
        db.close()


def test_update_portfolio_supply_context_sql_injection_defense():
    """화이트리스트 외 키는 무시 (SQL injection 방어)."""
    db = _temp_db()
    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO portfolio (date, stock_code, stock_name, shares, "
                "buy_price, current_price, status, take_profit, stop_loss, theme) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-12", "005930", "삼성전자", 10, 70000, 70000, "holding",
                 0.1, 0.05, "반도체")
            )

        rowcount = db.update_portfolio_supply_context("005930", {
            "foreign_net_at_buy": 1e9,
            "bad_key; DROP TABLE portfolio; --": "evil",  # 무시되어야 함
            "another_invalid": 999,  # 무시되어야 함
        })
        # foreign_net_at_buy만 화이트리스트 통과 → rowcount=1
        assert rowcount == 1
        # portfolio 테이블 여전히 존재
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM portfolio")
            assert cursor.fetchone()[0] == 1
        print("  [PASS] update_portfolio_supply_context_sql_injection_defense")
    finally:
        db.close()


def test_update_portfolio_supply_context_no_holding():
    """미보유 종목 → rowcount=0."""
    db = _temp_db()
    try:
        rowcount = db.update_portfolio_supply_context("999999", {
            "foreign_net_at_buy": 1e9,
        })
        assert rowcount == 0
        print("  [PASS] update_portfolio_supply_context_no_holding")
    finally:
        db.close()


def test_update_portfolio_supply_context_empty_ctx():
    """빈 ctx → rowcount=0."""
    db = _temp_db()
    try:
        rowcount = db.update_portfolio_supply_context("005930", {})
        assert rowcount == 0
        print("  [PASS] update_portfolio_supply_context_empty_ctx")
    finally:
        db.close()


# ===== get_supply_attribution_data =====

def test_attribution_data_empty():
    """trade_reviews 비어있을 때 빈 리스트."""
    db = _temp_db()
    try:
        result = db.get_supply_attribution_data(days=14)
        assert result == []
        print("  [PASS] attribution_data_empty")
    finally:
        db.close()


def test_attribution_data_days_filter_kst():
    """days 파라미터가 KST 기준으로 정확히 작동하는지 (UTC 버그 회귀 차단).

    code-tester 심각 #1: SQLite DATE('now')는 UTC라 KST 15:00~24:00 사이 호출 시
    오늘 매도 데이터 누락. now_kst() 기반 컷오프로 수정됨.
    """
    db = _temp_db()
    try:
        from datetime import date, timedelta
        from config import now_kst
        today = now_kst().date()
        # 5일 전 (포함되어야 함, days=14)
        old_sell = (today - timedelta(days=5)).isoformat()
        # 30일 전 (제외되어야 함, days=14)
        very_old_sell = (today - timedelta(days=30)).isoformat()

        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO trade_reviews (stock_code, stock_name, sell_date, profit_rate) "
                "VALUES (?, ?, ?, ?)",
                ("005930", "삼성전자", old_sell, 5.2)
            )
            cursor.execute(
                "INSERT INTO trade_reviews (stock_code, stock_name, sell_date, profit_rate) "
                "VALUES (?, ?, ?, ?)",
                ("000660", "SK하이닉스", very_old_sell, -3.1)
            )

        result = db.get_supply_attribution_data(days=14)
        assert len(result) == 1, f"days=14 필터 실패: {len(result)}건 (1 기대)"
        assert result[0]["stock_code"] == "005930"
        print("  [PASS] attribution_data_days_filter_kst")
    finally:
        db.close()


# ===== kis_api skip_supply 옵션 =====

def test_kis_api_skip_supply_signature():
    """get_stock_full_info(skip_supply=False) 기본값 호환성 검증."""
    import inspect
    from modules.stock_screener.kis_api import KISApi

    sig = inspect.signature(KISApi.get_stock_full_info)
    params = list(sig.parameters.keys())
    assert "skip_supply" in params, "skip_supply 파라미터 누락"
    # 기본값 False
    assert sig.parameters["skip_supply"].default is False, "skip_supply 기본값이 False가 아님"
    print("  [PASS] kis_api_skip_supply_signature")


def test_kis_api_skip_supply_skips_investor_call():
    """skip_supply=True 시 get_investor_trading이 실제로 호출되지 않음 (mock 검증)."""
    from unittest.mock import patch, MagicMock
    from modules.stock_screener.kis_api import KISApi

    kis = KISApi()
    # get_current_price만 응답 (수급/기술/재무는 mock으로 호출 횟수 추적)
    with patch.object(kis, "get_current_price", return_value={"price": 70000, "trade_value": 1e10}), \
         patch.object(kis, "get_investor_trading", return_value={"foreign_net": 1, "institution_net": 1, "foreign_ratio": 50}) as mock_inv, \
         patch.object(kis, "get_technical_indicators", return_value=None), \
         patch.object(kis, "get_financial_info", return_value=None):

        # skip_supply=True → get_investor_trading 호출 없음
        result = kis.get_stock_full_info("005930", skip_supply=True)
        assert result is not None
        assert mock_inv.call_count == 0, f"skip_supply=True인데 호출됨 (call_count={mock_inv.call_count})"
        assert "foreign_net" not in result, "skip_supply=True인데 foreign_net 채워짐"

        # skip_supply=False → 호출됨
        kis2 = KISApi()
        with patch.object(kis2, "get_current_price", return_value={"price": 70000}), \
             patch.object(kis2, "get_investor_trading", return_value={"foreign_net": 100, "institution_net": 50, "foreign_ratio": 55}) as mock_inv2, \
             patch.object(kis2, "get_technical_indicators", return_value=None), \
             patch.object(kis2, "get_financial_info", return_value=None):
            result2 = kis2.get_stock_full_info("005930", skip_supply=False)
            assert mock_inv2.call_count == 1
            assert result2["foreign_net"] == 100

    print("  [PASS] kis_api_skip_supply_skips_investor_call")


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 1-B 헬퍼 5개 단위 테스트")
    print("=" * 60)

    test_bulk_returns_only_existing()
    test_bulk_empty_codes_returns_empty()
    test_bulk_trade_date_none_returns_latest()

    test_aggregate_basic()
    test_aggregate_empty_codes()
    test_aggregate_handles_nulls()

    test_in_foreign_top_returns_rank()
    test_in_foreign_top_none_for_wrong_kind()

    test_update_portfolio_supply_context_basic()
    test_update_portfolio_supply_context_sql_injection_defense()
    test_update_portfolio_supply_context_no_holding()
    test_update_portfolio_supply_context_empty_ctx()

    test_attribution_data_empty()
    test_attribution_data_days_filter_kst()

    test_kis_api_skip_supply_signature()
    test_kis_api_skip_supply_skips_investor_call()

    print("=" * 60)
    print("✅ 모든 테스트 통과")
    print("=" * 60)
