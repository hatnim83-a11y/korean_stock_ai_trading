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
    _valid_stock_codes,
    _resolve_theme_stock_codes_for_supply,
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
        result = calculate_theme_supply_score_v2([], db, top_n=5, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
                                                  top_n=5, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = calculate_theme_supply_score_v2(codes, db, top_n=5, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = calculate_theme_supply_score_v2(codes, db, top_n=2, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = measure_universe_top_supply_signal(db, top_n=30, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = measure_universe_top_supply_signal(db, top_n=3, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
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
        result = measure_universe_top_supply_signal(db, top_n=30, ref_bil=30.0, max_score=5.0, signed=False, use_ratio=False)
        assert result["universe_size"] == 2
        assert len(result["top_codes"]) == 2  # universe보다 많이 요청해도 universe 만큼만
        print(f"✅ test_universe_top_signal_top_n_cap PASS (size={result['universe_size']})")
    finally:
        db.close()


# ===== 2026-06-06 신규: signed 양선형 매핑 + outlier_cap =====

def test_signed_avg_zero_returns_half_max():
    """signed=True, avg=0 → score = max_score/2 (중간값)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 평균 정확히 0 (3억-3억=0)
        samples = [
            ("005930", "삼성전자", 3e8, 0, 50000),
            ("000660", "SK하이닉스", -3e8, 0, 150000),
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930", "000660"], db,
            top_n=5, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False
        )
        assert abs(result["score"] - 2.5) < 0.01, f"expected ~2.5, got {result['score']}"
        print(f"✅ test_signed_avg_zero_returns_half_max PASS (score={result['score']})")
    finally:
        db.close()


def test_signed_negative_returns_low_score():
    """signed=True, avg=-5억(중간) → score = max/4 ≈ 1.25."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 평균 -5억 (-5e8)
        samples = [
            ("005930", "삼성전자", -5e8, 0, 50000),
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False
        )
        # ratio = (-5+10)/(2*10) = 0.25 → score = 1.25
        assert abs(result["score"] - 1.25) < 0.01, f"expected 1.25, got {result['score']}"
        print(f"✅ test_signed_negative_returns_low_score PASS (score={result['score']})")
    finally:
        db.close()


def test_signed_positive_returns_high_score():
    """signed=True, avg=+5억 → score = 3*max/4 ≈ 3.75."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 5e8, 0, 50000),
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False
        )
        # ratio = (5+10)/(2*10) = 0.75 → score = 3.75
        assert abs(result["score"] - 3.75) < 0.01, f"expected 3.75, got {result['score']}"
        print(f"✅ test_signed_positive_returns_high_score PASS (score={result['score']})")
    finally:
        db.close()


def test_signed_below_neg_ref_clamps_to_zero():
    """signed=True, avg <= -ref_bil → score = 0 (clamp)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", -15e8, 0, 50000),  # -15억, ref=10
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False
        )
        assert result["score"] == 0.0
        print(f"✅ test_signed_below_neg_ref_clamps_to_zero PASS (avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_signed_above_ref_clamps_to_max():
    """signed=True, avg >= ref_bil → score = max (clamp)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 50e8, 0, 50000),  # +50억, ref=10
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False
        )
        assert result["score"] == 5.0
        print(f"✅ test_signed_above_ref_clamps_to_max PASS (avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_outlier_cap_applied():
    """outlier_cap_bil=100, avg=-50000억(실측 outlier) → cap=-100 → signed=True score=0."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # avg ≈ -55341억 같은 outlier (5/19~6/1 발견된 반도체 케이스)
        samples = [
            ("005930", "삼성전자", -50000e8, 0, 50000),  # -5조억(원)
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0,
            signed=True, outlier_cap_bil=100.0, use_ratio=False,
        )
        # cap=-100억 → signed clamp max(-ref=-10, -100)=-10 → (-10+10)/(2×10)=0 → 0.0
        assert result["score"] == 0.0
        # avg_net_bil은 cap 적용 전 raw 값 노출
        assert result["avg_net_bil"] < -1000
        print(f"✅ test_outlier_cap_applied PASS (score={result['score']}, raw_avg={result['avg_net_bil']}억)")
    finally:
        db.close()


def test_outlier_cap_disabled_zero():
    """outlier_cap_bil=0 → cap 미적용."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", -50000e8, 0, 50000),
        ]
        _insert_snapshots(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, ref_bil=10.0, max_score=5.0,
            signed=True, outlier_cap_bil=0.0, use_ratio=False,
        )
        # cap 미적용이지만 ref=10 clamp는 작동 → score=0 (clamp -10)
        assert result["score"] == 0.0
        print(f"✅ test_outlier_cap_disabled_zero PASS (score={result['score']})")
    finally:
        db.close()


def test_universe_signal_signed_mapping():
    """universe top signal도 signed 매핑 적용."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 3종목 평균 -5억 (외인 약세 시장)
        samples = [
            ("005930", "삼성전자", -3e8, 0, 50000),
            ("000660", "SK하이닉스", -5e8, 0, 150000),
            ("035420", "NAVER", -7e8, 0, 200000),
        ]
        _insert_snapshots(db, td, samples)
        result = measure_universe_top_supply_signal(
            db, top_n=3, ref_bil=10.0, max_score=5.0, signed=True, use_ratio=False,
        )
        # 평균 -5억 → signed (-5+10)/(20)=0.25 → 1.25
        assert abs(result["score"] - 1.25) < 0.01
        print(f"✅ test_universe_signal_signed_mapping PASS (score={result['score']})")
    finally:
        db.close()


# ===== 2026-06-15 신규: ratio 모드 (옵션 C, 거래대금 대비 비율 정규화) =====


def _insert_snapshots_with_value(db, trade_date, samples):
    """samples: [(code, name, foreign_net_5d, inst_net_5d, close, trade_value_5d_avg), ...]

    save_supply_snapshot()이 trade_value를 daily에서 자동 계산하므로,
    테스트는 INSERT OR REPLACE로 직접 컬럼값 박제.
    """
    td_iso = trade_date.isoformat() if hasattr(trade_date, "isoformat") else trade_date
    with db.get_cursor() as cursor:
        for code, name, fnet, inet, close, trade_val in samples:
            cursor.execute("""
                INSERT OR REPLACE INTO daily_supply_snapshot (
                    trade_date, stock_code, stock_name,
                    foreign_net_5d, institution_net_5d, individual_net_5d,
                    foreign_ratio, foreign_net_1d, institution_net_1d,
                    close_price, trade_value_5d_avg, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TEST')
            """, (
                td_iso, code, name,
                fnet, inet, -(fnet + inet),
                50.0, None, None,
                close, trade_val,
            ))


def test_ratio_avg_zero_returns_half_max():
    """ratio 모드, avg_ratio=0 → score = max/2 (signed)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # ratio = 0 (외인 net = 0)
        samples = [
            ("005930", "삼성전자", 0, 0, 50000, 1e10),
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        assert abs(result["score"] - 2.5) < 0.01, f"expected ~2.5, got {result['score']}"
        assert result["mode"] == "ratio"
        print(f"✅ test_ratio_avg_zero_returns_half_max PASS (score={result['score']})")
    finally:
        db.close()


def test_ratio_positive_max():
    """ratio 모드, ratio >= ref_ratio → score = max."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # foreign_net = 5e9, trade_value_5d_avg = 6e9 → ratio = 5e9/(6e9*5) = 0.167 > 0.15
        samples = [
            ("005930", "삼성전자", 5e9, 0, 50000, 6e9),
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        assert result["score"] == 5.0
        assert result["avg_ratio"] > 0.15
        print(f"✅ test_ratio_positive_max PASS (avg_ratio={result['avg_ratio']:.4f})")
    finally:
        db.close()


def test_ratio_negative_zero():
    """ratio 모드, ratio <= -ref_ratio → score = 0 (clamp)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # ratio = -5e9/(6e9*5) = -0.167 < -0.15
        samples = [
            ("005930", "삼성전자", -5e9, 0, 50000, 6e9),
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        assert result["score"] == 0.0
        print(f"✅ test_ratio_negative_zero PASS (avg_ratio={result['avg_ratio']:.4f})")
    finally:
        db.close()


def test_ratio_trade_value_zero_guard():
    """ratio 모드, trade_value_5d_avg=0 → ratio=0 (division-by-zero 가드)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 5e9, 0, 50000, 0),  # trade_value_5d_avg=0
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930"], db,
            top_n=5, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        # ratio=0 → score = max/2 = 2.5 (signed 양선형 중간)
        assert abs(result["score"] - 2.5) < 0.01
        assert result["avg_ratio"] == 0.0
        print(f"✅ test_ratio_trade_value_zero_guard PASS (score={result['score']})")
    finally:
        db.close()


def test_ratio_top_n_uses_ratio_not_net():
    """ratio 모드 top_n 선정은 net 절댓값이 아닌 ratio 절댓값 기준 (대형주 편향 제거)."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        # 005930: 큰 net(-7e9)이지만 거래대금도 큼(8e10) → ratio = -0.0175
        # 100000(가상): 작은 net(+5e8)이지만 거래대금도 작음(5e8) → ratio = +0.20 (강함)
        samples = [
            ("005930", "삼성전자", -7e9, 0, 50000, 8e10),
            ("123456", "중소형주", 5e8, 0, 5000, 5e8),
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = calculate_theme_supply_score_v2(
            ["005930", "123456"], db,
            top_n=1, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        # top_n=1 선정 시 ratio 절댓값 큰 123456 선정 (0.20 vs 0.0175)
        assert "123456" in result["top_codes"]
        assert result["avg_ratio"] > 0
        print(f"✅ test_ratio_top_n_uses_ratio_not_net PASS (top={result['top_codes']}, ratio={result['avg_ratio']:.4f})")
    finally:
        db.close()


def test_ratio_universe_signal_normal():
    """universe top signal ratio 모드 정상 동작."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 3e9, 0, 50000, 4e9),  # ratio = 0.15
            ("000660", "SK하이닉스", 6e9, 0, 150000, 4e9),  # ratio = 0.30
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = measure_universe_top_supply_signal(
            db, top_n=2, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        assert result["mode"] == "ratio"
        # 평균 ratio = (0.30 + 0.15) / 2 = 0.225 → clamped 0.15 → ratio = 1.0 → score = 5.0
        assert result["score"] == 5.0
        assert result["top_avg_ratio"] > 0.15
        print(f"✅ test_ratio_universe_signal_normal PASS (score={result['score']}, avg_ratio={result['top_avg_ratio']:.4f})")
    finally:
        db.close()


def test_ratio_universe_signal_skips_zero_trade_value():
    """universe ratio 모드, trade_value_5d_avg=0 종목은 SQL에서 제외."""
    db = _temp_db()
    try:
        td = date(2026, 5, 18)
        samples = [
            ("005930", "삼성전자", 5e9, 0, 50000, 4e9),  # 정상
            ("000660", "SK하이닉스", 5e9, 0, 150000, 0),  # trade_value=0 → 제외
        ]
        _insert_snapshots_with_value(db, td, samples)
        result = measure_universe_top_supply_signal(
            db, top_n=10, max_score=5.0, signed=True,
            use_ratio=True, ref_ratio=0.15,
        )
        # 005930만 선정 (000660은 trade_value_5d_avg=0이라 제외)
        assert "000660" not in result["top_codes"]
        assert "005930" in result["top_codes"]
        # universe_size는 전체 개수(2) — 측정 대상이 1개여도 universe 자체는 2
        assert result["universe_size"] == 2
        print(f"✅ test_ratio_universe_signal_skips_zero_trade_value PASS (top={result['top_codes']})")
    finally:
        db.close()


# ===== 2026-07-03 신규: 종목코드 fallback 해석 (Phase 1-B½ 커버리지 개선) =====


def test_valid_stock_codes_filters():
    """6자리 숫자만 통과, 나머지 제외."""
    assert _valid_stock_codes(["005930", "0015G0", "12345", "1234567", None, 5930]) == ["005930"]
    assert _valid_stock_codes([]) == []
    assert _valid_stock_codes(None) == []
    print("✅ test_valid_stock_codes_filters PASS")


def test_resolve_prefers_theme_stocks():
    """유효한 theme['stocks']가 DB fallback보다 우선."""
    db = _temp_db()
    try:
        # DB에 fallback 후보를 넣어도 theme_stocks가 이기는지 확인
        today = _kst_today_iso()
        db.save_screening_log({
            "date": today, "stock_code": "000660", "stock_name": "SK하이닉스",
            "theme": "반도체", "stage": "filter", "passed": 1,
        })
        theme = {"name": "반도체", "stocks": ["005930", "0015G0"]}
        codes, source = _resolve_theme_stock_codes_for_supply(theme, "반도체", supply_db=db)
        assert codes == ["005930"], f"got {codes}"  # 무효코드 필터 + theme 우선
        assert source == "theme_stocks"
        print("✅ test_resolve_prefers_theme_stocks PASS")
    finally:
        db.close()


def test_resolve_uses_db_fallback_when_empty():
    """theme['stocks']가 비면 DB fallback 사용."""
    db = _temp_db()
    try:
        today = _kst_today_iso()
        db.save_screening_log({
            "date": today, "stock_code": "000660", "stock_name": "SK하이닉스",
            "theme": "반도체", "stage": "filter", "passed": 1,
        })
        theme = {"name": "반도체", "stocks": []}
        codes, source = _resolve_theme_stock_codes_for_supply(theme, "반도체", supply_db=db)
        assert codes == ["000660"], f"got {codes}"
        assert source == "screening_log_recent"
        print("✅ test_resolve_uses_db_fallback_when_empty PASS")
    finally:
        db.close()


def test_resolve_none_when_no_data():
    """theme_stocks 없고 fallback도 없으면 source='none'."""
    db = _temp_db()
    try:
        theme = {"name": "무데이터테마", "stocks": []}
        codes, source = _resolve_theme_stock_codes_for_supply(theme, "무데이터테마", supply_db=db)
        assert codes == []
        assert source == "none"
        print("✅ test_resolve_none_when_no_data PASS")
    finally:
        db.close()


def test_resolve_no_db_falls_back_to_theme_only():
    """supply_db=None이면 theme_stocks만 사용 (네트워크/DB 무접근)."""
    theme = {"name": "반도체", "stocks": ["005930"]}
    codes, source = _resolve_theme_stock_codes_for_supply(theme, "반도체", supply_db=None)
    assert codes == ["005930"]
    assert source == "theme_stocks"
    # 빈 theme_stocks + db None → none
    codes2, source2 = _resolve_theme_stock_codes_for_supply({"name": "X", "stocks": []}, "X", supply_db=None)
    assert codes2 == []
    assert source2 == "none"
    print("✅ test_resolve_no_db_falls_back_to_theme_only PASS")


def test_resolve_fallback_disabled():
    """SUPPLY_THEME_STOCK_FALLBACK_ENABLED=False면 DB fallback 미사용."""
    db = _temp_db()
    try:
        today = _kst_today_iso()
        db.save_screening_log({
            "date": today, "stock_code": "000660", "stock_name": "SK하이닉스",
            "theme": "반도체", "stage": "filter", "passed": 1,
        })
        from config import settings
        orig = settings.SUPPLY_THEME_STOCK_FALLBACK_ENABLED
        try:
            settings.SUPPLY_THEME_STOCK_FALLBACK_ENABLED = False
            codes, source = _resolve_theme_stock_codes_for_supply(
                {"name": "반도체", "stocks": []}, "반도체", supply_db=db
            )
            assert codes == []
            assert source == "none"
        finally:
            settings.SUPPLY_THEME_STOCK_FALLBACK_ENABLED = orig
        print("✅ test_resolve_fallback_disabled PASS")
    finally:
        db.close()


def _kst_today_iso():
    from config import now_kst
    return now_kst().date().isoformat()


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
        # 2026-06-06 신규 — signed 양선형 매핑 + outlier_cap
        test_signed_avg_zero_returns_half_max,
        test_signed_negative_returns_low_score,
        test_signed_positive_returns_high_score,
        test_signed_below_neg_ref_clamps_to_zero,
        test_signed_above_ref_clamps_to_max,
        test_outlier_cap_applied,
        test_outlier_cap_disabled_zero,
        test_universe_signal_signed_mapping,
        # 2026-06-15 신규 — ratio 모드 (옵션 C)
        test_ratio_avg_zero_returns_half_max,
        test_ratio_positive_max,
        test_ratio_negative_zero,
        test_ratio_trade_value_zero_guard,
        test_ratio_top_n_uses_ratio_not_net,
        test_ratio_universe_signal_normal,
        test_ratio_universe_signal_skips_zero_trade_value,
        # 2026-07-03 신규 — 종목코드 fallback 해석
        test_valid_stock_codes_filters,
        test_resolve_prefers_theme_stocks,
        test_resolve_uses_db_fallback_when_empty,
        test_resolve_none_when_no_data,
        test_resolve_no_db_falls_back_to_theme_only,
        test_resolve_fallback_disabled,
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
