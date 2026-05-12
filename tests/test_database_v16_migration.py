"""test_database_v16_migration.py — DB v16 마이그레이션 + supply 헬퍼 테스트.

검증 항목:
1. v15 → v16 마이그레이션 (신규 테이블 3개 + 컬럼 추가 16개)
2. 멱등성: 재실행 시 에러 없음
3. save_supply_snapshot: 정상 저장 + 1일 추정 + 5일 평균 거래대금 산출
4. save_supply_snapshot_null: NULL 행 저장
5. save_foreign_top_ranking: 멱등 (재실행 시 행 수 동일)
6. save_supply_score_observation: Shadow Run 관측 모드 저장
7. get_supply_snapshot / get_latest_supply_snapshot_date / count

파일 직접 실행: python tests/test_database_v16_migration.py
"""
import sys
import tempfile
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


def _temp_db() -> Database:
    """임시 파일 기반 Database 인스턴스 (테스트 격리).

    Database 생성자에 db_path를 직접 주입하여 기본 경로(data/trading.db) 디렉토리
    사이드이펙트(mkdir)를 피한다.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="test_supply_v16_"))
    db = Database(db_path=str(tmpdir / "trading.db"))
    db.connect()
    db.init_tables()
    return db


def test_v16_migration_creates_tables():
    """v16 마이그레이션 시 신규 테이블 3개 생성 확인."""
    db = _temp_db()
    try:
        assert db._get_schema_version() == 16, "schema_version != 16"

        with db.get_cursor() as cursor:
            for tbl in ["daily_supply_snapshot", "foreign_top_ranking", "supply_score_observation"]:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
                assert cursor.fetchone() is not None, f"{tbl} 테이블 누락"

        print("  [PASS] v16_migration_creates_tables")
    finally:
        db.close()


def test_v16_migration_adds_portfolio_columns():
    """portfolio 7개 컬럼 추가 확인."""
    db = _temp_db()
    try:
        expected = [
            "foreign_net_at_buy", "institution_net_at_buy", "supply_strength_at_buy",
            "foreign_top_rank_at_buy", "institution_consec_days_at_buy",
            "theme_supply_score_at_buy", "ai_supply_signal_at_buy",
        ]
        for col in expected:
            assert db._has_column("portfolio", col), f"portfolio.{col} 누락"

        print("  [PASS] v16_migration_adds_portfolio_columns")
    finally:
        db.close()


def test_v16_migration_adds_trade_reviews_columns():
    """trade_reviews 9개 컬럼 추가 확인."""
    db = _temp_db()
    try:
        expected = [
            "foreign_net_5d_at_buy", "institution_net_5d_at_buy", "supply_strength_at_buy",
            "foreign_top_rank_at_buy", "institution_consec_days_at_buy",
            "theme_supply_score_at_buy", "ai_supply_signal_at_buy",
            "foreign_direction_match", "institution_direction_match",
        ]
        for col in expected:
            assert db._has_column("trade_reviews", col), f"trade_reviews.{col} 누락"

        print("  [PASS] v16_migration_adds_trade_reviews_columns")
    finally:
        db.close()


def test_v16_migration_idempotent():
    """재실행 시 에러 없음 (init_tables 2회 호출)."""
    db = _temp_db()
    try:
        v1 = db._get_schema_version()
        db.init_tables()  # 재실행
        v2 = db._get_schema_version()
        assert v1 == v2 == 16, f"멱등성 위반: {v1} != {v2}"

        print("  [PASS] v16_migration_idempotent")
    finally:
        db.close()


def test_save_supply_snapshot_basic():
    """기본 저장 + 1일 추정 + 5일 평균 거래대금 산출."""
    db = _temp_db()
    try:
        sample = {
            "stock_name": "삼성전자",
            "foreign_net": 5_000_000_000,
            "institution_net": 3_000_000_000,
            "individual_net": -8_000_000_000,
            "foreign_ratio": 55.2,
            "daily": [
                {"date": "20260508", "foreign": 100, "institution": 50,
                 "individual": -150, "close_price": 70000},
                {"date": "20260507", "foreign": 80, "institution": 30,
                 "individual": -110, "close_price": 69500},
            ],
        }
        db.save_supply_snapshot(date(2026, 5, 8), "005930", sample)
        snap = db.get_supply_snapshot("005930", date(2026, 5, 8))

        assert snap is not None
        assert snap["foreign_net_5d"] == 5_000_000_000
        assert snap["institution_net_5d"] == 3_000_000_000
        assert snap["foreign_net_1d"] == 100 * 70000  # 7,000,000
        assert snap["close_price"] == 70000
        assert snap["trade_value_5d_avg"] is not None
        assert snap["source"] == "FHKST01010900"

        print("  [PASS] save_supply_snapshot_basic")
    finally:
        db.close()


def test_save_supply_snapshot_idempotent():
    """같은 trade_date+stock_code 재호출 시 1행만 유지 (INSERT OR REPLACE)."""
    db = _temp_db()
    try:
        sample = {"foreign_net": 1000, "institution_net": 500, "individual_net": -1500,
                  "foreign_ratio": 50.0, "daily": []}
        td = date(2026, 5, 8)
        db.save_supply_snapshot(td, "005930", sample)
        db.save_supply_snapshot(td, "005930", sample)
        db.save_supply_snapshot(td, "005930", sample)

        cnt = db.count_supply_snapshots_for_date(td)
        assert cnt == 1, f"멱등성 위반: 행 수 = {cnt}"

        print("  [PASS] save_supply_snapshot_idempotent")
    finally:
        db.close()


def test_save_supply_snapshot_null():
    """NULL 행 저장 (graceful failure)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 8)
        db.save_supply_snapshot_null(td, "999999", stock_name="실패종목")
        snap = db.get_supply_snapshot("999999", td)

        assert snap is not None
        assert snap["foreign_net_5d"] is None
        assert snap["source"] == "FHKST01010900_FAILED"
        assert snap["stock_name"] == "실패종목"

        print("  [PASS] save_supply_snapshot_null")
    finally:
        db.close()


def test_save_supply_snapshot_empty_daily():
    """daily=[] 빈 리스트 — 1일/거래대금 None, 5일은 그대로 저장."""
    db = _temp_db()
    try:
        sample = {"foreign_net": 1000, "institution_net": 500, "individual_net": -1500,
                  "foreign_ratio": 50.0, "daily": []}
        td = date(2026, 5, 8)
        db.save_supply_snapshot(td, "005930", sample)
        snap = db.get_supply_snapshot("005930", td)

        assert snap["foreign_net_5d"] == 1000
        assert snap["foreign_net_1d"] is None
        assert snap["trade_value_5d_avg"] is None

        print("  [PASS] save_supply_snapshot_empty_daily")
    finally:
        db.close()


def test_save_supply_snapshot_zero_close_price():
    """close_price=0 (유동성 낮음) — 거래대금 None 안전 처리."""
    db = _temp_db()
    try:
        sample = {
            "foreign_net": 1000, "institution_net": 500, "individual_net": -1500,
            "foreign_ratio": 50.0,
            "daily": [
                {"date": "20260508", "foreign": 100, "institution": 50,
                 "individual": -150, "close_price": 0},
            ],
        }
        td = date(2026, 5, 8)
        db.save_supply_snapshot(td, "005930", sample)
        snap = db.get_supply_snapshot("005930", td)

        # close_price 0 → foreign_1d None, trade_value None
        assert snap["foreign_net_1d"] is None
        assert snap["trade_value_5d_avg"] is None

        print("  [PASS] save_supply_snapshot_zero_close_price")
    finally:
        db.close()


def test_save_foreign_top_ranking_idempotent():
    """같은 trade_date+kind 재호출 시 행 수 동일 (DELETE+INSERT)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 8)
        items = [
            {"stock_code": "005930", "stock_name": "삼성전자", "net_amount": 1e11},
            {"stock_code": "000660", "stock_name": "SK하이닉스", "net_amount": 8e10},
            {"stock_code": "035420", "stock_name": "NAVER", "net_amount": 5e10},
        ]
        db.save_foreign_top_ranking(td, "foreign_buy", items)
        db.save_foreign_top_ranking(td, "foreign_buy", items)  # 재실행

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM foreign_top_ranking WHERE trade_date=? AND kind=?",
                (td.isoformat(), "foreign_buy")
            )
            cnt = cursor.fetchone()[0]
        assert cnt == 3, f"멱등성 위반: 행 수 = {cnt}"

        print("  [PASS] save_foreign_top_ranking_idempotent")
    finally:
        db.close()


def test_save_foreign_top_ranking_skips_invalid_code():
    """6자리 종목코드 아니면 stock_code 없는 항목 — skip 안 함 (저장은 됨, 검증은 호출 측).

    실제로 SupplyCollector에서 _is_valid_code로 사전 필터링하므로 DB 레벨은 관대.
    빈 stock_code만 skip 확인.
    """
    db = _temp_db()
    try:
        td = date(2026, 5, 8)
        items = [
            {"stock_code": "005930", "stock_name": "삼성전자"},
            {"stock_code": None, "stock_name": "X"},  # skip
            {"stock_code": "", "stock_name": "Y"},  # skip
            {"stock_code": "000660", "stock_name": "SK하이닉스"},
        ]
        db.save_foreign_top_ranking(td, "foreign_buy", items)
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM foreign_top_ranking WHERE trade_date=? AND kind=?",
                (td.isoformat(), "foreign_buy")
            )
            cnt = cursor.fetchone()[0]
        assert cnt == 2, f"빈 코드 skip 실패: 행 수 = {cnt}"

        print("  [PASS] save_foreign_top_ranking_skips_invalid_code")
    finally:
        db.close()


def test_save_supply_score_observation():
    """Shadow Run 관측 모드 저장."""
    db = _temp_db()
    try:
        db.save_supply_score_observation(
            obs_date=date(2026, 5, 8),
            theme_name="반도체",
            supply_score_v2=3.5,
            momentum_score=20.0,
            news_score=10.0,
            ai_score=8.0,
            theme_total_actual=51.0,
            breakdown_json='{"intensity_avg": 0.7}',
        )
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT supply_score_v2, theme_total_actual FROM supply_score_observation "
                "WHERE obs_date=? AND theme_name=?",
                ("2026-05-08", "반도체")
            )
            row = cursor.fetchone()
        assert row is not None
        assert row[0] == 3.5
        assert row[1] == 51.0

        print("  [PASS] save_supply_score_observation")
    finally:
        db.close()


def test_get_latest_supply_snapshot_date():
    """가장 최근 trade_date 반환."""
    db = _temp_db()
    try:
        sample = {"foreign_net": 0, "institution_net": 0, "individual_net": 0,
                  "foreign_ratio": 0, "daily": []}
        db.save_supply_snapshot(date(2026, 5, 7), "005930", sample)
        db.save_supply_snapshot(date(2026, 5, 8), "005930", sample)
        db.save_supply_snapshot(date(2026, 5, 6), "005930", sample)

        latest = db.get_latest_supply_snapshot_date()
        assert latest == "2026-05-08", f"latest = {latest}"

        # 빈 DB → None
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM daily_supply_snapshot")
        assert db.get_latest_supply_snapshot_date() is None

        print("  [PASS] get_latest_supply_snapshot_date")
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("DB v16 마이그레이션 + supply 헬퍼 테스트")
    print("=" * 60)

    test_v16_migration_creates_tables()
    test_v16_migration_adds_portfolio_columns()
    test_v16_migration_adds_trade_reviews_columns()
    test_v16_migration_idempotent()
    test_save_supply_snapshot_basic()
    test_save_supply_snapshot_idempotent()
    test_save_supply_snapshot_null()
    test_save_supply_snapshot_empty_daily()
    test_save_supply_snapshot_zero_close_price()
    test_save_foreign_top_ranking_idempotent()
    test_save_foreign_top_ranking_skips_invalid_code()
    test_save_supply_score_observation()
    test_get_latest_supply_snapshot_date()

    print("=" * 60)
    print("✅ 모든 테스트 통과")
    print("=" * 60)
