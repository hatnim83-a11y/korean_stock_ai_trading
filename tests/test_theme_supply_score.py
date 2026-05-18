"""test_theme_supply_score.py — Phase 1-B½ Shadow Run 단위 테스트.

검증 대상:
1. calculate_theme_supply_score_v2: 빈 리스트, 강한 케이스, 경계값, 음수 평균
2. measure_universe_top_supply_signal: 빈 DB, 정상 데이터, top_n 한도
3. SUPPLY_SCORE_OBSERVE_ONLY 토글 동작 (점수 계산 vs 총점 반영)

파일 직접 실행: python tests/test_theme_supply_score.py
"""
import sys
import tempfile
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from modules.theme_analyzer.scorer import (
    calculate_theme_supply_score_v2,
    measure_universe_top_supply_signal,
)


def _temp_db() -> Database:
    """임시 DB."""
    tmpdir = Path(tempfile.mkdtemp(prefix="test_theme_supply_v2_"))
    db = Database(db_path=str(tmpdir / "trading.db"))
    db.connect()
    db.init_tables()
    return db


def _insert_snapshots(db: Database, trade_date: date, samples: list[tuple]) -> None:
    """samples: [(code, name, foreign_net_5d, institution_net_5d, close), ...]"""
    for code, name, fnet, inet, close in samples:
        db.save_supply_snapshot(trade_date, code, {
            "stock_name": name,
            "foreign_net": fnet,
            "institution_net": inet,
            "individual_net": -(fnet + inet),
            "foreign_ratio": 50.0,
            "daily": [
                {
                    "date": "20260518",
                    "foreign": int(fnet / close) if close else 0,
                    "institution": int(inet / close) if close else 0,
                    "individual": 0,
                    "close_price": close,
                }
            ],
        })


def test_empty_stock_codes():
    """빈 리스트 → score=0.0, top_codes=[]."""
    db = _temp_db()
    try:
        result = calculate_theme_supply_score_v2([], db, top_n=5, ref_bil=30.0, max_score=5.0)
        assert result["score"] == 0.0
        assert result["top_codes"] == []
        assert result["foreign_pos_ratio"] == 0.0
        assert result["avg_net_bil"] == 0.0
        print("✅ test_empty_stock_codes PASS")
    finally:
        db.close()


def test_no_snapshots_in_db():
    """DB에 데이터 없으면 score=0.0."""
    db = _temp_db()
    try:
        result = calculate_theme_supply_score_v2(["005930", "000660"], db,
                                                  top_n=5, ref_bil=30.0, max_score=5.0)
        assert result["score"] == 0.0
        assert result["top_codes"] == []
        print("✅ test_no_snapshots_in_db PASS")
    finally:
        db.close()


def test_strong_positive_case():
    """평균 외인 5일 net이 ref_bil보다 훨씬 큰 경우 → score = max_score."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 5종목 평균 외인 +60억 (ref_bil 30억의 2배 → cap 1.0 → max_score)
        samples = [
            ("005930", "삼성전자", 8e9, 3e9, 50000),
            ("000660", "SK하이닉스", 6e9, 2e9, 150000),
            ("035420", "NAVER", 7e9, 5e8, 200000),
            ("373220", "LG에너지솔루션", 5e9, -2e9, 300000),
            ("066970", "엘앤에프", 4e9, 1e9, 400000),
        ]
        _insert_snapshots(db, td, samples)
        codes = [c for c, *_ in samples]
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0)
        assert result["score"] == 5.0, f"expected 5.0, got {result['score']}"
        assert result["foreign_pos_ratio"] == 1.0
        assert len(result["top_codes"]) == 5
        print(f"✅ test_strong_positive_case PASS (score={result['score']}, avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_negative_avg_returns_zero():
    """평균이 음수면 score=0.0 (양의 흐름만 점수 부여)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", -5e9, 3e9, 50000),
            ("000660", "SK하이닉스", -4e9, 2e9, 150000),
            ("035420", "NAVER", -3e9, 5e8, 200000),
        ]
        _insert_snapshots(db, td, samples)
        codes = [c for c, *_ in samples]
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0)
        assert result["score"] == 0.0
        assert result["foreign_pos_ratio"] == 0.0
        print(f"✅ test_negative_avg_returns_zero PASS (avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_boundary_exact_ref_bil():
    """평균 외인 = ref_bil (30억) → score = max_score."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 평균 정확히 30억 (3e9 원)
        samples = [
            ("005930", "삼성전자", 3e9, 0, 50000),
            ("000660", "SK하이닉스", 3e9, 0, 150000),
            ("035420", "NAVER", 3e9, 0, 200000),
        ]
        _insert_snapshots(db, td, samples)
        codes = [c for c, *_ in samples]
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0)
        assert abs(result["score"] - 5.0) < 0.01, f"expected ~5.0, got {result['score']}"
        print(f"✅ test_boundary_exact_ref_bil PASS (score={result['score']})")
    finally:
        db.close()


def test_top_n_selection():
    """top_n=2면 절댓값 상위 2개만 선택, 평균 계산에 반영."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 10e9, 0, 50000),    # 100억 (top 1)
            ("000660", "SK하이닉스", 8e9, 0, 150000),  # 80억 (top 2)
            ("035420", "NAVER", 1e9, 0, 200000),       # 10억 (제외)
            ("373220", "LG", 0.5e9, 0, 300000),         # 5억 (제외)
        ]
        _insert_snapshots(db, td, samples)
        codes = [c for c, *_ in samples]
        result = calculate_theme_supply_score_v2(codes, db, top_n=2, ref_bil=30.0, max_score=5.0)
        # 평균 (100+80)/2 = 90억 → ratio=1.0 → score=5.0
        assert result["score"] == 5.0
        assert len(result["top_codes"]) == 2
        assert "005930" in result["top_codes"]
        assert "000660" in result["top_codes"]
        print(f"✅ test_top_n_selection PASS (selected={result['top_codes']})")
    finally:
        db.close()


def test_max_score_zero_observe_mode():
    """max_score=0.0 (Phase 1-B½ 관측 모드) → 항상 score=0.0."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 10e9, 0, 50000),
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(["005930"], db, top_n=5, ref_bil=30.0, max_score=0.0)
        assert result["score"] == 0.0
        # 다만 진단 데이터는 그대로 노출됨 (avg_net_bil, pos_ratio)
        assert result["avg_net_bil"] > 0
        print(f"✅ test_max_score_zero_observe_mode PASS (score=0.0, avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_universe_top_signal_empty_db():
    """빈 DB → score=0.0, measured_date=None."""
    db = _temp_db()
    try:
        result = measure_universe_top_supply_signal(db, top_n=30, ref_bil=30.0, max_score=5.0)
        assert result["score"] == 0.0
        assert result["measured_date"] is None
        assert result["top_codes"] == []
        assert result["universe_size"] == 0
        print("✅ test_universe_top_signal_empty_db PASS")
    finally:
        db.close()


def test_universe_top_signal_normal():
    """정상 데이터 → score 계산 + top_codes 반환."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 8e9, 3e9, 50000),
            ("000660", "SK하이닉스", 6e9, 2e9, 150000),
            ("035420", "NAVER", 7e9, 5e8, 200000),
            ("373220", "LG에너지솔루션", 5e9, -2e9, 300000),
            ("066970", "엘앤에프", 4e9, 1e9, 400000),
        ]
        _insert_snapshots(db, td, samples)
        result = measure_universe_top_supply_signal(db, top_n=3, ref_bil=30.0, max_score=5.0)
        assert result["measured_date"] == "2026-05-18"
        assert result["universe_size"] == 5
        assert len(result["top_codes"]) == 3
        # top 3은 외인 큰 순: 005930(80억), 035420(70억), 000660(60억)
        assert result["top_codes"][0] == "005930"
        # 평균 (80+70+60)/3 = 70억 → 30억 기준 cap → 5.0
        assert result["score"] == 5.0
        assert result["pos_ratio"] == 1.0
        print(f"✅ test_universe_top_signal_normal PASS (top={result['top_codes']}, score={result['score']})")
    finally:
        db.close()


def test_universe_top_signal_top_n_cap():
    """top_n이 universe_size보다 크면 universe 전체 사용."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 8e9, 0, 50000),
            ("000660", "SK하이닉스", 6e9, 0, 150000),
        ]
        _insert_snapshots(db, td, samples)
        result = measure_universe_top_supply_signal(db, top_n=30, ref_bil=30.0, max_score=5.0)
        assert result["universe_size"] == 2
        assert len(result["top_codes"]) == 2  # universe보다 많이 요청해도 universe 만큼만
        print(f"✅ test_universe_top_signal_top_n_cap PASS (size={result['universe_size']})")
    finally:
        db.close()


def run_all():
    tests = [
        test_empty_stock_codes,
        test_no_snapshots_in_db,
        test_strong_positive_case,
        test_negative_avg_returns_zero,
        test_boundary_exact_ref_bil,
        test_top_n_selection,
        test_max_score_zero_observe_mode,
        test_universe_top_signal_empty_db,
        test_universe_top_signal_normal,
        test_universe_top_signal_top_n_cap,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"❌ {t.__name__} FAIL: {e}")
        except Exception as e:
            failures += 1
            print(f"❌ {t.__name__} ERROR: {e!r}")
    print()
    print(f"=== 결과: {len(tests) - failures}/{len(tests)} PASS ===")
    return failures == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
