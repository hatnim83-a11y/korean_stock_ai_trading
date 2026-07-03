#!/usr/bin/env python3
"""Read-only performance review for closing_bet_system.

This script never imports KIS/broker clients and opens SQLite with mode=ro.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


def pct(x):
    return None if x is None else round(float(x), 4)


def avg(xs):
    xs = [float(x) for x in xs if x is not None]
    return round(mean(xs), 4) if xs else None


def med(xs):
    xs = [float(x) for x in xs if x is not None]
    return round(median(xs), 4) if xs else None


def q(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def summarize_returns(rows):
    rets = [r.get("net_pnl_pct") for r in rows if r.get("net_pnl_pct") is not None]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    return {
        "trades": len(rows),
        "closed_with_pnl": len(rets),
        "win_rate_pct": round(len(wins) / len(rets) * 100, 2) if rets else None,
        "avg_net_pnl_pct": avg(rets),
        "median_net_pnl_pct": med(rets),
        "best_net_pnl_pct": round(max(rets), 4) if rets else None,
        "worst_net_pnl_pct": round(min(rets), 4) if rets else None,
        "avg_win_pct": avg(wins),
        "avg_loss_pct": avg(losses),
        "profit_factor_proxy": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else None,
    }


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/closing_bet.db")
    ap.add_argument("--since", default="2026-05-25")
    ap.add_argument("--json-out")
    ap.add_argument("--md-out")
    args = ap.parse_args()

    db = Path(args.db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    schema = {}
    for t in ["candidates", "candidate_features", "flow_reliability", "orderbook_snapshots"]:
        schema[t] = [dict(r) for r in con.execute(f"pragma table_info({t})")]

    candidates = q(
        con,
        """
        SELECT * FROM candidates
        WHERE trade_date >= ?
        ORDER BY trade_date, candidate_id
        """,
        (args.since,),
    )

    entered = []
    closed = []
    open_positions = []
    phase1_only = []
    for r in candidates:
        p1 = int(r.get("entry_phase1_executed_shares") or 0)
        p2 = int(r.get("entry_phase2_executed_shares") or 0)
        has_entry = bool(r.get("entry_time") or p1 > 0 or p2 > 0 or r.get("candidate_status") in ("entered", "exited"))
        if has_entry:
            entered.append(r)
        if r.get("exit_time") or r.get("final_exit_time") or r.get("net_pnl_pct") is not None:
            closed.append(r)
        elif has_entry:
            open_positions.append(r)
        if p1 > 0 and p2 == 0:
            phase1_only.append(r)

    by_week = defaultdict(list)
    for r in closed:
        try:
            d = datetime.fromisoformat(str(r["trade_date"]))
            week = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        except Exception:
            week = "unknown"
        by_week[week].append(r)

    by_score = defaultdict(list)
    for r in closed:
        s = r.get("total_score")
        if s is None:
            key = "unknown"
        elif s >= 9:
            key = "9+"
        elif s >= 8:
            key = "8"
        elif s >= 6:
            key = "6-7"
        else:
            key = "<=5"
        by_score[key].append(r)

    status = Counter(r.get("candidate_status") for r in candidates)
    total_by_day = Counter(r.get("trade_date") for r in candidates)
    entered_by_day = Counter(r.get("trade_date") for r in entered)

    flow_cols = schema.get("flow_reliability", [])
    flow_count = None
    if flow_cols:
        try:
            flow_count = con.execute("select count(*) from flow_reliability").fetchone()[0]
        except Exception:
            flow_count = None

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(db),
        "since": args.since,
        "schema_columns": {k: [c["name"] for c in v] for k, v in schema.items()},
        "candidate_count": len(candidates),
        "trading_days": len(total_by_day),
        "status_counts": dict(status),
        "entered_count": len(entered),
        "closed_count": len(closed),
        "open_entry_count": len(open_positions),
        "phase1_only_entry_count": len(phase1_only),
        "entry_rate_pct": round(len(entered) / len(candidates) * 100, 2) if candidates else None,
        "overall_closed": summarize_returns(closed),
        "weekly": {k: summarize_returns(v) for k, v in sorted(by_week.items())},
        "score_buckets": {k: summarize_returns(v) for k, v in sorted(by_score.items())},
        "recent_days": [
            {"trade_date": d, "candidates": total_by_day[d], "entries": entered_by_day.get(d, 0)}
            for d in sorted(total_by_day.keys())[-15:]
        ],
        "top_closed": [
            {"date": r["trade_date"], "ticker": r["ticker"], "name": r["name"], "score": r.get("total_score"), "net_pnl_pct": pct(r.get("net_pnl_pct"))}
            for r in sorted(closed, key=lambda x: (x.get("net_pnl_pct") is not None, x.get("net_pnl_pct") or -999), reverse=True)[:10]
        ],
        "bottom_closed": [
            {"date": r["trade_date"], "ticker": r["ticker"], "name": r["name"], "score": r.get("total_score"), "net_pnl_pct": pct(r.get("net_pnl_pct"))}
            for r in sorted([r for r in closed if r.get("net_pnl_pct") is not None], key=lambda x: x.get("net_pnl_pct"))[:10]
        ],
        "flow_reliability": {"columns": [c["name"] for c in flow_cols], "row_count": flow_count},
        "caveats": [
            "SQLite read-only analysis only; no broker/API/order calls.",
            "net_pnl_pct exists only for rows that have been logged as exited/closed.",
            "If phase1-only rows remain candidate_status=recommended, entry detection uses executed share columns as well as status.",
        ],
    }

    lines = []
    lines.append("# 종가베팅 현재 성과 분석 리포트")
    lines.append("")
    lines.append(f"- 생성 UTC: `{result['generated_at_utc']}`")
    lines.append(f"- DB: `{db}`")
    lines.append(f"- 분석 시작일: `{args.since}`")
    lines.append("- 안전: SQLite read-only 연결, KIS/주문 API 미사용")
    lines.append("")
    lines.append("## 1. 핵심 요약")
    lines.append("")
    rows = [
        ["후보 수", result["candidate_count"]],
        ["거래일 수", result["trading_days"]],
        ["실제 진입 감지", result["entered_count"]],
        ["청산/PnL 기록", result["closed_count"]],
        ["미청산 진입", result["open_entry_count"]],
        ["Phase1-only 진입", result["phase1_only_entry_count"]],
        ["진입률", f"{result['entry_rate_pct']}%"],
    ]
    lines.append(md_table(["지표", "값"], rows))
    lines.append("")
    s = result["overall_closed"]
    lines.append("## 2. 청산 성과")
    lines.append("")
    lines.append(md_table(["지표", "값"], [[k, v] for k, v in s.items()]))
    lines.append("")
    lines.append("## 3. 주별 성과")
    lines.append("")
    lines.append(md_table(["주", "청산", "승률", "평균%", "중앙%", "최악%", "최고%"], [
        [w, v["closed_with_pnl"], v["win_rate_pct"], v["avg_net_pnl_pct"], v["median_net_pnl_pct"], v["worst_net_pnl_pct"], v["best_net_pnl_pct"]]
        for w, v in result["weekly"].items()
    ] or [["데이터 없음", "-", "-", "-", "-", "-", "-"]]))
    lines.append("")
    lines.append("## 4. 점수 구간별 성과")
    lines.append("")
    lines.append(md_table(["점수 구간", "청산", "승률", "평균%", "중앙%", "최악%", "최고%"], [
        [w, v["closed_with_pnl"], v["win_rate_pct"], v["avg_net_pnl_pct"], v["median_net_pnl_pct"], v["worst_net_pnl_pct"], v["best_net_pnl_pct"]]
        for w, v in result["score_buckets"].items()
    ] or [["데이터 없음", "-", "-", "-", "-", "-", "-"]]))
    lines.append("")
    lines.append("## 5. 최근 거래일 Funnel")
    lines.append("")
    lines.append(md_table(["일자", "후보", "진입"], [[r["trade_date"], r["candidates"], r["entries"]] for r in result["recent_days"]]))
    lines.append("")
    lines.append("## 6. 상위/하위 청산")
    lines.append("")
    lines.append("### 상위")
    lines.append(md_table(["일자", "종목", "코드", "점수", "순손익%"], [[r["date"], r["name"], r["ticker"], r["score"], r["net_pnl_pct"]] for r in result["top_closed"]] or [["-", "-", "-", "-", "-"]]))
    lines.append("")
    lines.append("### 하위")
    lines.append(md_table(["일자", "종목", "코드", "점수", "순손익%"], [[r["date"], r["name"], r["ticker"], r["score"], r["net_pnl_pct"]] for r in result["bottom_closed"]] or [["-", "-", "-", "-", "-"]]))
    lines.append("")
    lines.append("## 7. Flow reliability 상태")
    lines.append("")
    lines.append(f"- 컬럼 수: `{len(result['flow_reliability']['columns'])}`")
    lines.append(f"- 행 수: `{result['flow_reliability']['row_count']}`")
    lines.append("")
    lines.append("## 8. 1차 판단")
    lines.append("")
    if s["closed_with_pnl"] < 20:
        lines.append("- 청산 표본이 20건 미만이면 통계적 확신은 낮습니다. 설정 고도화보다 관측 연장이 우선입니다.")
    if result["flow_reliability"]["row_count"] in (None, 0):
        lines.append("- flow_reliability 테이블이 비어 있거나 스키마가 없어 `layer1_weight` 활성화 판단 근거가 부족합니다.")
    if result["open_entry_count"]:
        lines.append("- 미청산 진입이 있어 최종 성과는 추후 달라질 수 있습니다.")
    lines.append("- Phase2 재활성화는 Phase1 단발 성과가 충분한 표본에서 안정적으로 확인된 뒤 검토해야 합니다.")
    lines.append("")

    md = "\n".join(lines)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_out).write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
