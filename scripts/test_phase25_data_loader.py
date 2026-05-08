"""phase25_data_loader 단위 테스트.

테스트 전략: 임시 SQLite DB를 생성하여 fixture 데이터를 INSERT한 후 검증.
실제 data/closing_bet.db에 영향 없음.

실행:
    venv/bin/python scripts/test_phase25_data_loader.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from closing_bet_system.backtest.phase25_data_loader import (
    _FEATURE_COLUMNS,
    _LABEL_BOOL_COLUMNS,
    _LABEL_PCT_COLUMNS,
    load_phase25_dataset,
)


# ===== 픽스처 =====


def _create_test_db() -> str:
    """임시 SQLite DB 생성 + 스키마 + 픽스처 INSERT.

    데이터:
    - 5/4: 3건 (id 1=recommended/labeled/featured, id 2=recommended/labeled/featured, id 3=rejected_filter/featured)
    - 5/7: 2건 (id 4=recommended/labeled/featured, id 5=recommended/featured but no label)
    - 5/8: 1건 (id 6=entered/labeled/featured)
    - 5/11: 1건 (id 7=recommended only — features/labels 없음)

    합계: 7건
    - recommended/entered: 6건 (id 1,2,4,5,6,7)
    - rejected_filter: 1건 (id 3)
    - labeled: 4건 (id 1,2,4,6)
    - featured: 6건 (id 1,2,3,4,5,6)
    """
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_phase25_")
    import os
    os.close(fd)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 스키마 (실제 closing_bet.db와 동일)
    cur.execute("""
        CREATE TABLE candidates (
            candidate_id INTEGER PRIMARY KEY,
            trade_date DATE NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL,
            candidate_status TEXT NOT NULL,
            rejection_reason TEXT,
            layer1_score INTEGER,
            layer2_score INTEGER,
            layer3_score INTEGER,
            external_risk_score INTEGER,
            total_score INTEGER,
            entry_price REAL,
            entry_amount REAL,
            entry_time TIMESTAMP,
            exit_price REAL,
            exit_time TIMESTAMP,
            buy_commission REAL,
            sell_commission REAL,
            transaction_tax REAL,
            estimated_slippage REAL,
            net_pnl_pct REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    feature_cols_def = ", ".join(f"{c} REAL" for c in _FEATURE_COLUMNS)
    cur.execute(f"""
        CREATE TABLE candidate_features (
            candidate_id INTEGER PRIMARY KEY,
            {feature_cols_def}
        )
    """)
    cur.execute("""
        CREATE TABLE candidate_labels (
            candidate_id INTEGER PRIMARY KEY,
            next_open_pct REAL,
            next_morning_high_pct REAL,
            next_morning_low_pct REAL,
            label_gap_up BOOLEAN,
            label_morning_exit BOOLEAN,
            label_stop_risk BOOLEAN,
            label_net_ev_positive BOOLEAN,
            labeled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # candidates 7건
    cands = [
        (1, "2026-05-04", "005930", "삼성전자", "recommended", None, 1, 2, 0, 0, 3),
        (2, "2026-05-04", "000660", "SK하이닉스", "recommended", None, 0, 2, 0, 0, 2),
        (3, "2026-05-04", "012330", "현대모비스", "rejected_filter", "atr_overheat", 1, 2, 0, 0, 3),
        (4, "2026-05-07", "068270", "셀트리온", "recommended", None, 0, 1, 0, 0, 1),
        (5, "2026-05-07", "035420", "NAVER", "recommended", None, 0, 1, 0, 0, 1),
        (6, "2026-05-08", "028260", "삼성물산", "entered", None, 1, 2, 0, 0, 3),
        (7, "2026-05-11", "402340", "SK스퀘어", "recommended", None, 0, 2, 0, 0, 2),
    ]
    cur.executemany("""
        INSERT INTO candidates (
            candidate_id, trade_date, ticker, name, candidate_status, rejection_reason,
            layer1_score, layer2_score, layer3_score, external_risk_score, total_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, cands)

    # candidate_features (id 1,2,3,4,5,6 = 6건. id 7 없음)
    n_cols = len(_FEATURE_COLUMNS)
    feat_data = [(i,) + tuple(0.5 + i * 0.1 for _ in range(n_cols)) for i in [1, 2, 3, 4, 5, 6]]
    placeholders = ", ".join("?" for _ in range(n_cols + 1))
    feat_cols_sql = ", ".join(("candidate_id",) + _FEATURE_COLUMNS)
    cur.executemany(
        f"INSERT INTO candidate_features ({feat_cols_sql}) VALUES ({placeholders})",
        feat_data,
    )

    # candidate_labels (id 1,2,4,6 = 4건. id 3,5,7 없음)
    labels = [
        # (cid, open_pct, high_pct, low_pct, gap_up, morning_exit, stop_risk, ev_positive)
        (1, 0.012, 0.020, -0.005, 1, 1, 0, 1),  # 갭상승+익절 도달+EV+
        (2, -0.005, 0.003, -0.020, 0, 0, 1, 0),  # 손절 위험
        (4, 0.007, 0.007, -0.027, 1, 0, 1, 0),   # 셀트리온 케이스
        (6, None, None, None, None, None, None, None),  # NULL 라벨 (회귀)
    ]
    cur.executemany("""
        INSERT INTO candidate_labels (
            candidate_id, next_open_pct, next_morning_high_pct, next_morning_low_pct,
            label_gap_up, label_morning_exit, label_stop_risk, label_net_ev_positive
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, labels)

    conn.commit()
    conn.close()
    return db_path


_TEST_DB = _create_test_db()


def _cleanup():
    """테스트 종료 후 임시 DB 삭제."""
    import os
    try:
        os.unlink(_TEST_DB)
    except OSError:
        pass


# ===== 테스트 =====


def test_LD_1_normal_load():
    """LD-1: 정상 범위(5/4~5/11) 로드 → 6건 (rejected_filter 제외)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB)
    assert len(df) == 6, f"recommended/entered만 6건이어야 함 (실제: {len(df)})"
    assert "rejected_filter" not in df["candidate_status"].values, "rejected_filter는 default 제외"
    print("✅ LD-1 정상 로드 (6건)")


def test_LD_2_status_filter():
    """LD-2: statuses=('recommended',) → entered 제외."""
    df = load_phase25_dataset(
        "2026-05-04", "2026-05-11",
        db_path=_TEST_DB,
        statuses=("recommended",),
    )
    statuses = df["candidate_status"].unique().tolist()
    assert statuses == ["recommended"], f"recommended만 반환 (실제: {statuses})"
    assert len(df) == 5, "recommended 5건"
    print("✅ LD-2 status 필터")


def test_LD_3_only_labeled():
    """LD-3: only_labeled=True → 라벨링된 4건만."""
    df = load_phase25_dataset(
        "2026-05-04", "2026-05-11",
        db_path=_TEST_DB,
        only_labeled=True,
    )
    assert len(df) == 4, f"labeled 4건 (실제: {len(df)})"
    assert df["is_labeled"].all(), "모든 행 is_labeled=True"
    print("✅ LD-3 only_labeled")


def test_LD_4_only_features():
    """LD-4: only_features=True → features 있는 5건 (id 1,2,4,5,6 — 단 default status 필터로 6건 중 1건은 rejected_filter라 제외)."""
    df = load_phase25_dataset(
        "2026-05-04", "2026-05-11",
        db_path=_TEST_DB,
        only_features=True,
    )
    # default statuses=(recommended, entered) + features in 1,2,3,4,5,6 → id 1,2,4,5,6 = 5건
    assert len(df) == 5, f"recommended/entered ∩ featured = 5건 (실제: {len(df)})"
    assert df["is_featured"].all()
    print("✅ LD-4 only_features")


def test_LD_5_empty_result():
    """LD-5: 먼 미래 날짜 → 빈 DataFrame."""
    df, meta = load_phase25_dataset(
        "2030-01-01", "2030-01-31",
        db_path=_TEST_DB,
        return_meta=True,
    )
    assert len(df) == 0, "빈 결과"
    assert meta["rows"] == 0
    assert meta["labeled_rows"] == 0
    print("✅ LD-5 빈 결과")


def test_LD_6_invalid_db_path():
    """LD-6: 잘못된 db_path → FileNotFoundError 명확 메시지."""
    try:
        load_phase25_dataset("2026-05-04", "2026-05-08", db_path="/nonexistent/path.db")
        raise AssertionError("FileNotFoundError 발생해야 함")
    except FileNotFoundError as e:
        assert "/nonexistent/path.db" in str(e), "에러 메시지에 경로 포함"
    print("✅ LD-6 잘못된 db_path")


def test_LD_7_dtypes():
    """LD-7: dtype 검증 (trade_date Timestamp / 라벨 BooleanDtype)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB)
    assert pd.api.types.is_datetime64_any_dtype(df["trade_date"]), "trade_date datetime64"
    for col in _LABEL_BOOL_COLUMNS:
        assert df[col].dtype == "boolean", f"{col} BooleanDtype (실제: {df[col].dtype})"
    for col in _LABEL_PCT_COLUMNS:
        assert pd.api.types.is_float_dtype(df[col]), f"{col} float (실제: {df[col].dtype})"
    print("✅ LD-7 dtype 검증")


def test_LD_8_null_label_preserved():
    """LD-8: NULL 라벨 → pd.NA 보존 (False로 강제 변환 X)."""
    df = load_phase25_dataset(
        "2026-05-08", "2026-05-08",
        db_path=_TEST_DB,
        statuses=("entered",),
    )
    assert len(df) == 1, "5/8 entered 1건"
    # id=6은 라벨이 NULL로 INSERT됨
    row = df.iloc[0]
    assert row["is_labeled"] == True, "라벨 row는 존재 (LEFT JOIN 매치)"
    # NULL이 boolean 컬럼에서 pd.NA로 보존되는지
    assert pd.isna(row["label_gap_up"]), "NULL 라벨은 pd.NA 보존"
    print("✅ LD-8 NULL 라벨 pd.NA 보존")


def test_LD_9_ordering():
    """LD-9: 정렬 검증 (trade_date ASC, candidate_id ASC)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB)
    cids = df["candidate_id"].tolist()
    assert cids == sorted(cids), f"candidate_id ASC 정렬 (실제: {cids})"
    dates = pd.to_datetime(df["trade_date"]).tolist()
    assert dates == sorted(dates), "trade_date ASC 정렬"
    print("✅ LD-9 정렬")


def test_LD_10_return_meta():
    """LD-10: return_meta=True → 튜플 반환 + 키 검증."""
    result = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB, return_meta=True)
    assert isinstance(result, tuple), "튜플 반환"
    assert len(result) == 2, "(df, meta)"
    df, meta = result
    expected_keys = {"rows", "labeled_rows", "features_rows", "date_range", "statuses", "db_path", "generated_at"}
    assert set(meta.keys()) == expected_keys, f"메타 키 7종 (실제: {set(meta.keys())})"
    assert meta["rows"] == 6
    assert meta["labeled_rows"] == 4
    assert meta["features_rows"] == 5
    assert meta["date_range"] == ("2026-05-04", "2026-05-11")
    print("✅ LD-10 return_meta")


def test_LD_11_feature_columns_present():
    """LD-11: features 18 컬럼 모두 포함 (스키마 정합)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB)
    for col in _FEATURE_COLUMNS:
        assert col in df.columns, f"{col} 누락"
    assert len(_FEATURE_COLUMNS) == 18, "스키마 18 컬럼 명세 검증"
    print("✅ LD-11 features 컬럼 18 포함")


def test_LD_12_trade_date_counts():
    """LD-12: trade_date별 카운트 일치 (default status 필터)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB)
    counts = df.groupby(df["trade_date"].dt.date).size().to_dict()
    expected = {
        date(2026, 5, 4): 2,   # id 1, 2 (id 3 rejected_filter 제외)
        date(2026, 5, 7): 2,   # id 4, 5
        date(2026, 5, 8): 1,   # id 6 (entered)
        date(2026, 5, 11): 1,  # id 7
    }
    assert counts == expected, f"카운트 불일치 (실제: {counts}, 예상: {expected})"
    print("✅ LD-12 trade_date 카운트")


def test_LD_13_date_str_parsing():
    """LD-13: 날짜 str/date 모두 허용 (회귀)."""
    df1 = load_phase25_dataset("2026-05-04", "2026-05-04", db_path=_TEST_DB)
    df2 = load_phase25_dataset(date(2026, 5, 4), date(2026, 5, 4), db_path=_TEST_DB)
    assert len(df1) == len(df2), "str과 date 동일 결과"
    print("✅ LD-13 날짜 타입 호환")


def test_LD_14_invalid_date_raises():
    """LD-14: 잘못된 날짜 형식 → ValueError."""
    try:
        load_phase25_dataset("invalid-date", "2026-05-08", db_path=_TEST_DB)
        raise AssertionError("ValueError 발생해야 함")
    except ValueError as e:
        assert "start_date" in str(e), "필드명 명시"
    print("✅ LD-14 잘못된 날짜 형식")


def test_LD_15_empty_statuses_returns_all():
    """LD-15: statuses=() → 모든 상태 반환 (rejected_filter 포함)."""
    df = load_phase25_dataset("2026-05-04", "2026-05-11", db_path=_TEST_DB, statuses=())
    assert len(df) == 7, f"모든 상태 7건 (실제: {len(df)})"
    assert "rejected_filter" in df["candidate_status"].values
    print("✅ LD-15 statuses=() 전체 반환")


# ===== Runner =====


def main():
    tests = [
        test_LD_1_normal_load,
        test_LD_2_status_filter,
        test_LD_3_only_labeled,
        test_LD_4_only_features,
        test_LD_5_empty_result,
        test_LD_6_invalid_db_path,
        test_LD_7_dtypes,
        test_LD_8_null_label_preserved,
        test_LD_9_ordering,
        test_LD_10_return_meta,
        test_LD_11_feature_columns_present,
        test_LD_12_trade_date_counts,
        test_LD_13_date_str_parsing,
        test_LD_14_invalid_date_raises,
        test_LD_15_empty_statuses_returns_all,
    ]
    print(f"\n=== phase25_data_loader 단위 테스트 — {len(tests)}건 실행 ===\n")
    failed = 0
    try:
        for t in tests:
            try:
                t()
            except AssertionError as e:
                failed += 1
                print(f"❌ {t.__name__}: {e}")
            except Exception as e:
                failed += 1
                import traceback
                print(f"💥 {t.__name__} 예외: {type(e).__name__}: {e}")
                traceback.print_exc()
    finally:
        _cleanup()

    print(f"\n=== 결과: {len(tests) - failed}/{len(tests)} PASS ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
