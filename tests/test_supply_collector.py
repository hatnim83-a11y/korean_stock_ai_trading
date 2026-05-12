"""test_supply_collector.py — SupplyCollector 단위 테스트.

검증 항목:
1. _is_valid_code: 6자리 숫자 검증
2. _build_universe: 테마/보유/시총 합집합 (mock DB)
3. _collect_one_stock: 정상/실패/retry (mock KIS)
4. _collect_top_ranking: market_provider 있을 때/없을 때
5. run_collection: 전체 흐름 (universe → 종목별 → ranking → 결과 dict)

파일 직접 실행: python tests/test_supply_collector.py
"""
import sys
import tempfile
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from modules.supply_collector.collector import SupplyCollector


class FakeKisApi:
    """KIS API mock.

    Args:
        responses: {stock_code: response_dict or None or Exception}
    """
    def __init__(self, responses: dict):
        self.responses = responses
        self.call_count = 0

    def get_investor_trading(self, stock_code: str, days: int = 5):
        self.call_count += 1
        r = self.responses.get(stock_code)
        if isinstance(r, Exception):
            raise r
        return r


class FakeMarketProvider:
    """KisMarketProvider mock."""
    def __init__(self, foreign_buy_codes=None, market_cap_codes=None):
        self._foreign_buy = foreign_buy_codes or []
        self._market_cap = {c: {} for c in (market_cap_codes or [])}

    def get_top_foreign_buy_codes(self, top_n=200):
        return self._foreign_buy[:top_n]

    def get_top_market_cap_data(self, top_n=200):
        return dict(list(self._market_cap.items())[:top_n])


def _temp_db() -> Database:
    """임시 DB.

    Database 생성자에 db_path를 직접 주입하여 기본 경로(data/trading.db) 디렉토리
    사이드이펙트(mkdir)를 피한다.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="test_collector_"))
    db = Database(db_path=str(tmpdir / "trading.db"))
    db.connect()
    db.init_tables()
    return db


def test_is_valid_code():
    """6자리 숫자만 통과."""
    assert SupplyCollector._is_valid_code("005930") is True
    assert SupplyCollector._is_valid_code("000660") is True
    # 실패 케이스
    assert SupplyCollector._is_valid_code(None) is False
    assert SupplyCollector._is_valid_code("") is False
    assert SupplyCollector._is_valid_code("5930") is False  # 4자리
    assert SupplyCollector._is_valid_code("0015G0") is False  # 알파벳 포함
    assert SupplyCollector._is_valid_code(5930) is False  # int 타입

    print("  [PASS] is_valid_code")


def test_build_universe_holdings_only():
    """테마 없고 보유만 있을 때 — 보유 종목만 반환."""
    db = _temp_db()
    try:
        # 보유 종목 1개 저장
        from datetime import datetime
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO portfolio (date, stock_code, stock_name, shares, "
                "buy_price, current_price, status, take_profit, stop_loss, theme) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-08", "005930", "삼성전자", 10, 70000, 70000, "holding",
                 0.1, 0.05, "반도체")
            )

        collector = SupplyCollector(db, kis_api=None, market_provider=None)
        codes = collector._build_universe(date(2026, 5, 8))
        assert "005930" in codes, f"보유 종목 누락: {codes}"

        print("  [PASS] build_universe_holdings_only")
    finally:
        db.close()


def test_collect_one_stock_success():
    """정상 응답 → True 반환 + DB 저장."""
    db = _temp_db()
    try:
        kis = FakeKisApi({
            "005930": {
                "foreign_net": 5e9, "institution_net": 3e9, "individual_net": -8e9,
                "foreign_ratio": 55.0, "daily": [
                    {"date": "20260508", "foreign": 100, "institution": 50,
                     "individual": -150, "close_price": 70000},
                ],
            }
        })
        collector = SupplyCollector(db, kis_api=kis, market_provider=None)
        ok = collector._collect_one_stock("005930", date(2026, 5, 8))
        assert ok is True
        assert kis.call_count == 1

        snap = db.get_supply_snapshot("005930", date(2026, 5, 8))
        assert snap["foreign_net_5d"] == 5e9

        print("  [PASS] collect_one_stock_success")
    finally:
        db.close()


def test_collect_one_stock_retry_on_none():
    """response=None 3회 → NULL 행 저장."""
    db = _temp_db()
    try:
        # 모든 호출이 None 반환
        kis = FakeKisApi({"005930": None})
        collector = SupplyCollector(db, kis_api=kis, market_provider=None)
        # 백오프 테스트를 위해 sleep을 짧게
        collector.backoff_seconds = [0.01, 0.01, 0.01]

        ok = collector._collect_one_stock("005930", date(2026, 5, 8))
        assert ok is False, "None 반환 시 False 기대"
        assert kis.call_count == 3, f"retry 3회 기대, 실제 {kis.call_count}회"

        # NULL 행 저장 확인
        snap = db.get_supply_snapshot("005930", date(2026, 5, 8))
        assert snap is not None
        assert snap["source"] == "FHKST01010900_FAILED"
        assert snap["foreign_net_5d"] is None

        print("  [PASS] collect_one_stock_retry_on_none")
    finally:
        db.close()


def test_collect_one_stock_retry_on_exception():
    """Exception 3회 → NULL 행 저장."""
    db = _temp_db()
    try:
        kis = FakeKisApi({"005930": RuntimeError("API timeout")})
        collector = SupplyCollector(db, kis_api=kis, market_provider=None)
        collector.backoff_seconds = [0.01, 0.01, 0.01]

        ok = collector._collect_one_stock("005930", date(2026, 5, 8))
        assert ok is False
        assert kis.call_count == 3

        snap = db.get_supply_snapshot("005930", date(2026, 5, 8))
        assert snap is not None
        assert snap["source"] == "FHKST01010900_FAILED"

        print("  [PASS] collect_one_stock_retry_on_exception")
    finally:
        db.close()


def test_collect_one_stock_retry_recovery():
    """1회차 실패 후 2회차 성공 → True 반환."""
    db = _temp_db()
    try:
        # call_count 기준으로 응답을 바꾸려면 Fake를 확장해야 함
        class FlakyKis:
            def __init__(self):
                self.call_count = 0
            def get_investor_trading(self, code, days=5):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("Transient error")
                return {"foreign_net": 5e9, "institution_net": 3e9, "individual_net": -8e9,
                        "foreign_ratio": 55.0, "daily": []}

        kis = FlakyKis()
        collector = SupplyCollector(db, kis_api=kis, market_provider=None)
        collector.backoff_seconds = [0.01, 0.01, 0.01]

        ok = collector._collect_one_stock("005930", date(2026, 5, 8))
        assert ok is True
        assert kis.call_count == 2

        print("  [PASS] collect_one_stock_retry_recovery")
    finally:
        db.close()


def test_collect_top_ranking_with_provider():
    """market_provider 있을 때 외인 TOP 저장."""
    db = _temp_db()
    try:
        mp = FakeMarketProvider(foreign_buy_codes=["005930", "000660", "035420"])
        collector = SupplyCollector(db, kis_api=None, market_provider=mp)

        count = collector._collect_top_ranking(date(2026, 5, 8), "foreign_buy", "1")
        assert count == 3, f"foreign_buy count={count}"

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM foreign_top_ranking WHERE kind='foreign_buy'"
            )
            assert cursor.fetchone()[0] == 3

        print("  [PASS] collect_top_ranking_with_provider")
    finally:
        db.close()


def test_collect_top_ranking_without_provider():
    """market_provider 없으면 0 반환."""
    db = _temp_db()
    try:
        collector = SupplyCollector(db, kis_api=None, market_provider=None)
        count = collector._collect_top_ranking(date(2026, 5, 8), "foreign_buy", "1")
        assert count == 0

        print("  [PASS] collect_top_ranking_without_provider")
    finally:
        db.close()


def test_collect_top_ranking_institution_skipped():
    """기관 TOP은 Phase 1-A에서 스킵 (etc_cls='2')."""
    db = _temp_db()
    try:
        mp = FakeMarketProvider(foreign_buy_codes=["005930"])
        collector = SupplyCollector(db, kis_api=None, market_provider=mp)

        count = collector._collect_top_ranking(date(2026, 5, 8), "institution_buy", "2")
        assert count == 0, "기관 TOP은 Phase 1-A 스킵"

        print("  [PASS] collect_top_ranking_institution_skipped")
    finally:
        db.close()


def test_run_collection_empty_universe():
    """universe 비어있을 때 — 안전한 0 반환."""
    db = _temp_db()
    try:
        collector = SupplyCollector(db, kis_api=None, market_provider=None)
        # 빈 portfolio + 빈 themes
        result = collector.run_collection(date(2026, 5, 8))
        assert result["codes"] == 0
        assert result["success"] == 0
        assert result["failure"] == 0

        print("  [PASS] run_collection_empty_universe")
    finally:
        db.close()


def test_run_collection_full_flow():
    """전체 흐름: 보유 → 종목별 수급 → 외인 TOP → 결과."""
    db = _temp_db()
    try:
        # 1) 보유 종목 1개 저장
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO portfolio (date, stock_code, stock_name, shares, "
                "buy_price, current_price, status, take_profit, stop_loss, theme) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-08", "005930", "삼성전자", 10, 70000, 70000, "holding",
                 0.1, 0.05, "반도체")
            )

        # 2) KIS mock: 005930 → 정상
        kis = FakeKisApi({
            "005930": {"foreign_net": 5e9, "institution_net": 3e9, "individual_net": -8e9,
                       "foreign_ratio": 55.0, "daily": []},
        })

        # 3) market_provider mock
        mp = FakeMarketProvider(foreign_buy_codes=["005930", "000660"])

        collector = SupplyCollector(db, kis_api=kis, market_provider=mp)
        result = collector.run_collection(date(2026, 5, 8))

        assert result["codes"] >= 1, f"universe={result['codes']}"
        assert result["success"] >= 1, f"success={result['success']}"
        assert result["foreign_ranking_count"] == 2
        assert result["institution_ranking_count"] == 0  # Phase 1-A 스킵
        assert "duration_sec" in result

        print("  [PASS] run_collection_full_flow")
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("SupplyCollector 단위 테스트")
    print("=" * 60)

    test_is_valid_code()
    test_build_universe_holdings_only()
    test_collect_one_stock_success()
    test_collect_one_stock_retry_on_none()
    test_collect_one_stock_retry_on_exception()
    test_collect_one_stock_retry_recovery()
    test_collect_top_ranking_with_provider()
    test_collect_top_ranking_without_provider()
    test_collect_top_ranking_institution_skipped()
    test_run_collection_empty_universe()
    test_run_collection_full_flow()

    print("=" * 60)
    print("✅ 모든 테스트 통과")
    print("=" * 60)
