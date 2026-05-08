"""closing_bet_system.backtest.phase25_data_loader

Phase 2.5 백테스트용 통합 데이터 로더.

candidates + candidate_features + candidate_labels 3 테이블을 LEFT JOIN으로
로드하여 단위 2-7b 시뮬레이터 / 2-7c 분석 리포트가 사용할 단일 DataFrame을 반환한다.

본 모듈은 **데이터 접근 계층만** 책임진다:
- 시뮬레이션 / EV 계산은 단위 2-7b
- walk-forward 분석은 단위 2-7c
- 자동매매 진입 결정은 단위 2-4/2-5 (별도 100건 게이트 후)

PRD 11-1 candidates 스키마 + PRD 12-1 라벨 정의 참조.

실행 예시:
    >>> from closing_bet_system.backtest.phase25_data_loader import load_phase25_dataset
    >>> df = load_phase25_dataset("2026-05-04", "2026-05-08")
    >>> df.shape  # 5/8 시점: recommended/entered 49건 × 50컬럼
    (49, 50)
    >>> df, meta = load_phase25_dataset("2026-05-04", "2026-05-08", return_meta=True)
    >>> meta["labeled_rows"]  # 5/4 15 + 5/7 18 = 33 (5/8 후보는 5/11 라벨링 예정)
    33
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Union

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from config import now_kst
from logger import logger


# ===== 모듈 상수 =====

_DEFAULT_DB_PATH = "data/closing_bet.db"
_DEFAULT_STATUSES = ("recommended", "entered")

# candidate_features 18 컬럼 (PRD 11-1 + 6-1 Layer 1/2/3 + market regime)
_FEATURE_COLUMNS = (
    "inst_net_buy_estimated",
    "foreign_net_buy_3d",
    "program_net_buy_change",
    "closing_flow_concentration",
    "close_strength",
    "upper_shadow_atr",
    "last_30min_vwap_position",
    "closing_buy_sell_ratio",
    "volume_surprise",
    "atr_overheat",
    "days_from_52w_high",
    "relative_strength_5d",
    "theme_leadership_rank",
    "kospi_above_200ma",
    "vkospi",
    "foreign_5d_cumulative",
    "us_futures_change",
    "usd_krw_change",
)

# candidate_labels 7 컬럼 (PRD 9-2 + 12-1)
_LABEL_PCT_COLUMNS = ("next_open_pct", "next_morning_high_pct", "next_morning_low_pct")
_LABEL_BOOL_COLUMNS = (
    "label_gap_up",
    "label_morning_exit",
    "label_stop_risk",
    "label_net_ev_positive",
)
_LABEL_COLUMNS = _LABEL_PCT_COLUMNS + _LABEL_BOOL_COLUMNS + ("labeled_at",)


# ===== Public API =====


def load_phase25_dataset(
    start_date: Union[date, datetime, str],
    end_date: Union[date, datetime, str],
    *,
    db_path: Optional[str] = None,
    statuses: Optional[Iterable[str]] = None,
    only_labeled: bool = False,
    only_features: bool = False,
    return_meta: bool = False,
) -> Union[pd.DataFrame, tuple[pd.DataFrame, dict]]:
    """Phase 2.5 백테스트 데이터셋 로드.

    Args:
        start_date / end_date: ``date`` / ``datetime`` / ISO 8601 ``str`` (YYYY-MM-DD).
            inclusive 양 끝.
        db_path: SQLite DB 경로. 기본값 ``data/closing_bet.db`` (프로젝트 루트 기준).
        statuses: candidate_status 필터. 기본 ``("recommended", "entered")``.
            None 명시 → ``_DEFAULT_STATUSES`` 사용.
            빈 iterable → 모든 상태 (필터 없음).
        only_labeled: True 면 ``candidate_labels`` 가 있는 행만 반환.
        only_features: True 면 ``candidate_features`` 가 있는 행만 반환.
        return_meta: True 면 ``(df, meta_dict)`` 튜플 반환.

    Returns:
        DataFrame (return_meta=False) 또는 (DataFrame, meta) 튜플.

    Raises:
        FileNotFoundError: db_path 가 존재하지 않을 때.
        ValueError: 날짜 파싱 실패 시.
    """
    start_str = _parse_date(start_date, "start_date")
    end_str = _parse_date(end_date, "end_date")

    db_file = Path(db_path or _DEFAULT_DB_PATH)
    if not db_file.is_absolute():
        db_file = _PROJECT_ROOT / db_file
    if not db_file.exists():
        raise FileNotFoundError(f"closing_bet DB not found: {db_file}")

    if statuses is None:
        statuses_tuple = _DEFAULT_STATUSES
    else:
        statuses_tuple = tuple(statuses)

    query, params = _build_query(start_str, end_str, statuses_tuple, only_labeled, only_features)

    conn = sqlite3.connect(str(db_file))
    try:
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    df = _post_process(df)

    if return_meta:
        meta = _build_meta(df, start_str, end_str, statuses_tuple, str(db_file))
        return df, meta
    return df


# ===== 내부 헬퍼 =====


def _parse_date(value: Union[date, datetime, str], field: str) -> str:
    """date/datetime/str → ISO 8601 (YYYY-MM-DD) 문자열."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as e:
            raise ValueError(f"{field} 파싱 실패 (YYYY-MM-DD 필요): {value!r}") from e
    raise ValueError(f"{field} 타입 오류 (date/datetime/str): {type(value).__name__}")


def _build_query(
    start_str: str,
    end_str: str,
    statuses: tuple,
    only_labeled: bool,
    only_features: bool,
) -> tuple[str, list]:
    """SQL JOIN 쿼리 + 파라미터 생성."""
    feature_cols_sql = ", ".join(f"cf.{c}" for c in _FEATURE_COLUMNS)
    label_cols_sql = ", ".join(f"cl.{c}" for c in _LABEL_COLUMNS)

    conditions = ["c.trade_date BETWEEN ? AND ?"]
    params: list = [start_str, end_str]

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        conditions.append(f"c.candidate_status IN ({placeholders})")
        params.extend(statuses)

    if only_labeled:
        conditions.append("cl.candidate_id IS NOT NULL")
    if only_features:
        conditions.append("cf.candidate_id IS NOT NULL")

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT
            c.candidate_id, c.trade_date, c.ticker, c.name,
            c.candidate_status, c.rejection_reason,
            c.layer1_score, c.layer2_score, c.layer3_score,
            c.external_risk_score, c.total_score,
            c.entry_price, c.entry_amount, c.entry_time,
            c.exit_price, c.exit_time,
            c.buy_commission, c.sell_commission, c.transaction_tax,
            c.estimated_slippage, c.net_pnl_pct, c.created_at,
            {feature_cols_sql},
            (cf.candidate_id IS NOT NULL) AS is_featured,
            {label_cols_sql},
            (cl.candidate_id IS NOT NULL) AS is_labeled
        FROM candidates c
        LEFT JOIN candidate_features cf ON c.candidate_id = cf.candidate_id
        LEFT JOIN candidate_labels cl ON c.candidate_id = cl.candidate_id
        WHERE {where_clause}
        ORDER BY c.trade_date ASC, c.candidate_id ASC
    """
    return query, params


def _post_process(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼 dtype 후처리.

    - trade_date → ``pd.Timestamp``
    - 라벨 4종 → ``BooleanDtype()`` (None / True / False 3-state 보존)
    - pct 3종 → float (NaN 허용)
    - is_labeled / is_featured → bool
    """
    if df.empty:
        # 빈 DataFrame이라도 컬럼 dtype 명시 (downstream 시뮬레이터 일관성 보장)
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        for col in _LABEL_PCT_COLUMNS:
            if col in df.columns:
                df[col] = df[col].astype("float64")
        for col in _LABEL_BOOL_COLUMNS:
            if col in df.columns:
                df[col] = df[col].astype("boolean")
        return df

    df = df.copy()

    # trade_date → Timestamp
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")

    # pct 컬럼 → float
    for col in _LABEL_PCT_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 라벨 bool 컬럼 → BooleanDtype (None 보존)
    for col in _LABEL_BOOL_COLUMNS:
        if col in df.columns:
            # SQLite 0/1 → 변환 시 NULL은 NaN → BooleanDtype은 NaN을 pd.NA로 보존
            df[col] = df[col].astype("boolean")

    # is_* 파생 컬럼 → bool
    for col in ("is_labeled", "is_featured"):
        if col in df.columns:
            df[col] = df[col].astype("boolean").fillna(False).astype(bool)

    return df


def _build_meta(
    df: pd.DataFrame,
    start_str: str,
    end_str: str,
    statuses: tuple,
    db_path: str,
) -> dict:
    """반환 메타 정보 dict 생성."""
    if df.empty:
        labeled_rows = 0
        features_rows = 0
    else:
        labeled_rows = int(df["is_labeled"].sum())
        features_rows = int(df["is_featured"].sum())

    return {
        "rows": len(df),
        "labeled_rows": labeled_rows,
        "features_rows": features_rows,
        "date_range": (start_str, end_str),
        "statuses": statuses,
        "db_path": db_path,
        "generated_at": now_kst().isoformat(timespec="seconds"),
    }


# ===== CLI 진입점 (선택) =====


def _main_cli() -> None:
    """단발 검증용 CLI: 5/4~5/8 로드 + meta 출력."""
    import argparse

    parser = argparse.ArgumentParser(description="Phase 2.5 데이터셋 단발 검증")
    parser.add_argument("--start", default="2026-05-04")
    parser.add_argument("--end", default="2026-05-08")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--only-labeled", action="store_true")
    args = parser.parse_args()

    df, meta = load_phase25_dataset(
        args.start,
        args.end,
        db_path=args.db_path,
        only_labeled=args.only_labeled,
        return_meta=True,
    )
    logger.info(f"[phase25_data_loader] shape={df.shape} meta={meta}")
    print(df.head(10))


if __name__ == "__main__":
    _main_cli()
