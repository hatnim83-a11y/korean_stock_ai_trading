#!/usr/bin/env python3
"""analyze_supply_score_observations.py — supply_score_v2 Shadow Run 관측 분석.

Phase 1-C gating(활성화 여부) 판단을 재현 가능하게 만들기 위한 읽기 전용 분석 스크립트.
ad-hoc SQL 없이 동일 기준으로 커버리지·분포·상관계수를 산출한다.

동작 원칙:
- 읽기 전용 SQLite 접근 (mode=ro). 쓰기/네트워크/시크릿 미사용.
- breakdown_json의 stocks_source / stocks_available 메타데이터 집계.
- supply_score_v2 vs momentum_score Pearson 상관 (표본 2개 이상일 때).

사용법:
    ./venv/bin/python scripts/analyze_supply_score_observations.py --since 2026-06-22
    ./venv/bin/python scripts/analyze_supply_score_observations.py --since 2026-06-22 --stocks-available-only
    ./venv/bin/python scripts/analyze_supply_score_observations.py --since 2026-06-22 --db data/trading.db
"""
import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _date_str(v: str) -> str:
    """--since YYYY-MM-DD 형식 검증 (오분석 방지). SQLite 텍스트 비교라 잘못된 형식은 조용히 오답)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise argparse.ArgumentTypeError(f"날짜 형식 오류: {v} (YYYY-MM-DD 필요)")
    return v


def _resolve_db_path(explicit: str | None) -> str:
    """DB 경로 결정 (인자 > config.DATABASE_PATH > 기본값)."""
    if explicit:
        return explicit
    try:
        from config import settings
        return settings.DATABASE_PATH
    except Exception:
        return "data/trading.db"


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """읽기 전용 커넥션. 파일이 없으면 명확히 오류."""
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"DB 파일 없음: {db_path}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _pearson(xs: list, ys: list) -> float | None:
    """Pearson 상관계수 (표본 2개 미만이거나 분산 0이면 None)."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _bucket(score: float) -> str:
    """supply_score_v2 분포 버킷."""
    if score < 1.0:
        return "<1"
    if score < 2.0:
        return "1~2"
    if score < 3.5:
        return "2~3.5"
    if score < 4.5:
        return "3.5~4.5"
    return ">=4.5"


BUCKET_ORDER = ["<1", "1~2", "2~3.5", "3.5~4.5", ">=4.5"]


def analyze(db_path: str, since: str | None, stocks_available_only: bool) -> dict:
    """관측 데이터 집계 → 결과 dict 반환."""
    conn = _connect_readonly(db_path)
    try:
        sql = (
            "SELECT obs_date, theme_name, supply_score_v2, momentum_score, "
            "breakdown_json FROM supply_score_observation"
        )
        params: list = []
        if since:
            sql += " WHERE obs_date >= ?"
            params.append(since)
        sql += " ORDER BY obs_date ASC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    total_rows = len(rows)
    stocks_available_count = 0
    source_counts: dict = {}
    buckets: dict = {b: 0 for b in BUCKET_ORDER}
    supply_vals: list = []
    momentum_vals: list = []
    dates: list = []
    used_rows = 0

    for row in rows:
        breakdown = {}
        raw = row.get("breakdown_json")
        if raw:
            try:
                breakdown = json.loads(raw)
            except (ValueError, TypeError):
                breakdown = {}

        stocks_available = bool(breakdown.get("stocks_available", False))
        if stocks_available:
            stocks_available_count += 1

        # stocks_source 집계 (구 데이터는 키 없음 → 'unknown')
        source = breakdown.get("stocks_source", "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

        # --stocks-available-only: 커버리지 있는 행만 분포/상관에 반영
        if stocks_available_only and not stocks_available:
            continue

        used_rows += 1
        if row.get("obs_date"):
            dates.append(row["obs_date"])

        # 0.0은 유효값 — `or` 폴백 금지, None만 제외
        s = row.get("supply_score_v2")
        m = row.get("momentum_score")
        if s is not None:
            buckets[_bucket(float(s))] += 1
        if s is not None and m is not None:
            supply_vals.append(float(s))
            momentum_vals.append(float(m))

    date_range = (min(dates), max(dates)) if dates else (None, None)
    pearson = _pearson(supply_vals, momentum_vals)
    coverage_ratio = (stocks_available_count / total_rows) if total_rows else 0.0

    return {
        "db_path": db_path,
        "since": since,
        "stocks_available_only": stocks_available_only,
        "total_rows": total_rows,
        "used_rows": used_rows,
        "date_range": date_range,
        "stocks_available_count": stocks_available_count,
        "coverage_ratio": coverage_ratio,
        "buckets": buckets,
        "source_counts": source_counts,
        "pearson_supply_vs_momentum": pearson,
        "correlation_sample_size": len(supply_vals),
    }


def render(result: dict) -> str:
    """사람이 읽을 수 있는 리포트 문자열."""
    lines = []
    lines.append("=" * 60)
    lines.append("supply_score_v2 Shadow Run 관측 분석")
    lines.append("=" * 60)
    lines.append(f"DB: {result['db_path']}")
    lines.append(f"since: {result['since'] or '(전체)'}")
    lines.append(f"필터: stocks_available_only={result['stocks_available_only']}")
    lines.append("")
    lo, hi = result["date_range"]
    lines.append(f"전체 관측 행: {result['total_rows']}")
    lines.append(f"분석 대상 행: {result['used_rows']}")
    lines.append(f"관측 기간: {lo or '-'} ~ {hi or '-'}")
    lines.append(
        f"종목 커버리지(stocks_available=True): "
        f"{result['stocks_available_count']} / {result['total_rows']} "
        f"({result['coverage_ratio']:.1%})"
    )
    lines.append("")
    lines.append("supply_score_v2 분포:")
    total_b = sum(result["buckets"].values()) or 1
    for b in BUCKET_ORDER:
        cnt = result["buckets"][b]
        lines.append(f"  {b:>8}: {cnt:>5}  ({cnt / total_b:.1%})")
    lines.append("")
    lines.append("stocks_source 분포:")
    if result["source_counts"]:
        for src, cnt in sorted(result["source_counts"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {src:>22}: {cnt}")
    else:
        lines.append("  (데이터 없음)")
    lines.append("")
    p = result["pearson_supply_vs_momentum"]
    if p is None:
        lines.append(
            f"Pearson(supply_score_v2, momentum_score): 계산 불가 "
            f"(표본={result['correlation_sample_size']}, 분산 0 또는 표본<2)"
        )
    else:
        lines.append(
            f"Pearson(supply_score_v2, momentum_score): {p:+.4f} "
            f"(표본={result['correlation_sample_size']})"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="supply_score_v2 Shadow Run 관측 분석 (읽기 전용)"
    )
    parser.add_argument("--since", type=_date_str, default=None, help="관측일 하한 (YYYY-MM-DD)")
    parser.add_argument(
        "--stocks-available-only", action="store_true",
        help="stocks_available=True 행만 분포/상관에 반영",
    )
    parser.add_argument("--db", default=None, help="DB 경로 override (기본 config.DATABASE_PATH)")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    try:
        result = analyze(db_path, args.since, args.stocks_available_only)
    except FileNotFoundError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
