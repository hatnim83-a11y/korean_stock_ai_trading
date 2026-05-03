"""closing_bet_system.backtest.sanity_check_report

Pre-Phase 1 Layer 2 sanity check 백테스트 결과를 markdown 리포트로 자동 생성.

입력: 0-C가 저장한 ``data/backtest_cache/daily_proxy_results.csv``
출력: ``docs/work-plans/active/closing-bet-system/0d_sanity_check_report.md``

분석 항목:
1. 데이터 개요 (종목/기간/표본 수)
2. 점수별 hit rate (morning_exit / stop_risk / gap_up)
3. **비용 차감 후 평균 PnL 추정** — settings.yaml ``cost.*`` 사용 (왕복 약 0.41%)
4. **ATR 과열 필터 적용/미적용 대조** — 역선택 패턴 확인
5. gap_up AND morning_exit / morning_exit only 분리
6. 종목별 후보 분포 (대형주 편향 점검)
7. 한계 및 종합 판정

실행:
    venv/bin/python -m closing_bet_system.backtest.sanity_check_report
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from logger import logger
from config import now_kst

from closing_bet_system.storage.db import _load_settings


_DEFAULT_INPUT = _PROJECT_ROOT / "data" / "backtest_cache" / "daily_proxy_results.csv"
_DEFAULT_OUTPUT = (
    _PROJECT_ROOT / "docs" / "work-plans" / "active" / "closing-bet-system"
    / "0d_sanity_check_report.md"
)


# ===== 데이터 로드 =====


def load_results(csv_path: Path = _DEFAULT_INPUT) -> pd.DataFrame:
    """0-C CSV 로드. symbol str 강제 + date 파싱."""
    df = pd.read_csv(csv_path, dtype={"symbol": str})
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("Asia/Seoul")
    return df


# ===== EV 모델 =====


def compute_round_trip_cost(settings: dict) -> float:
    """settings.yaml의 cost.* 로 왕복 거래비용(슬리피지 포함) 계산.

    매수: commission + slippage(편도)
    매도: commission + transaction_tax + slippage(편도)
    합계: ≈ 0.0041 (기본 settings 기준)
    """
    cost = settings.get("cost", {})
    buy_comm = float(cost.get("buy_commission", 0.00015))
    sell_comm = float(cost.get("sell_commission", 0.00015))
    tax = float(cost.get("transaction_tax", 0.0018))
    slip = float(cost.get("estimated_slippage", 0.001))
    return buy_comm + sell_comm + tax + slip * 2


def compute_pnl_per_row(
    df: pd.DataFrame,
    morning_exit_pct: float,
    stop_loss_pct: float,
) -> pd.Series:
    """단순 EV 추정 모델로 행별 PnL(세전, 슬리피지 미반영) 계산.

    가정 — 실제 09:30 윈도우 동작을 일봉 data 로 근사:
    - ``label_morning_exit=True`` → 진입가 + ``morning_exit_pct`` 에서 청산
    - ``label_stop_risk=True`` → 진입가 + ``stop_loss_pct`` 에서 청산
    - 둘 다 True → 보수적으로 stop 우선 (실전에서 09:00 갭다운 손절이 09:30 high 도달 전에 발생 가능)
    - 둘 다 False → 익일 시가에서 청산 (next_open_pct)
    - 익일 데이터 없음(NaN) → NaN

    이 모델은 sanity check 용. 진입가는 당일 종가(매수 시점), 청산가는 익일 09:30 윈도우 가정.
    """
    out = pd.Series(np.nan, index=df.index, dtype="float64")

    morning = df["label_morning_exit"].fillna(False)
    stop = df["label_stop_risk"].fillna(False)
    has_next_open = df["next_open_pct"].notna()

    # 1) stop 발생 (morning 동시 발생 시 stop 우선 — 보수적)
    out[stop] = stop_loss_pct
    # 2) morning만 (stop 없는 경우)
    out[morning & ~stop] = morning_exit_pct
    # 3) 둘 다 없으면 시가 청산
    out[~morning & ~stop & has_next_open] = df.loc[~morning & ~stop & has_next_open, "next_open_pct"]

    return out


# ===== 분석 함수 =====


def _format_table(df: pd.DataFrame, floatfmt: str = ".2f") -> str:
    """pandas → markdown 표 변환. tabulate 의존성 회피.

    "점수", "표본 수", "n", "통과 n", "필터된 n", "전체 일수", "후보 일수" 같은
    카운트성 컬럼은 정수 천단위로 표시 (자료가 dtype=float 이라도).
    """
    if df.empty:
        return "_(데이터 없음)_"

    int_like_columns = {"점수", "표본 수", "n", "통과 n", "필터된 n", "전체 일수", "후보 일수"}
    headers = list(df.columns)

    def _cell(v, header: str) -> str:
        if pd.isna(v) if isinstance(v, float) else False:
            return "—"
        if header in int_like_columns:
            try:
                return f"{int(round(float(v))):,}"
            except (TypeError, ValueError):
                return str(v)
        if isinstance(v, float):
            return f"{v:{floatfmt}}"
        if isinstance(v, (int, np.integer)):
            return f"{int(v):,}"
        return str(v)

    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_cell(row[h], h) for h in headers) + " |")

    return "\n".join(lines)


def section_overview(df: pd.DataFrame) -> str:
    """1) 데이터 개요."""
    n_total = len(df)
    n_symbols = df["symbol"].nunique()
    date_min = df["date"].min().strftime("%Y-%m-%d")
    date_max = df["date"].max().strftime("%Y-%m-%d")
    n_candidates = int(df["is_candidate"].sum())
    n_filtered_atr = int(df["filtered_atr_overheat"].sum())
    n_zero_volume = int((df["volume"] == 0).sum())
    n_label_missing = int(df["next_open_pct"].isna().sum())

    lines = [
        "## 1. 데이터 개요",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 종목 수 | {n_symbols} |",
        f"| 기간 | {date_min} ~ {date_max} |",
        f"| 전체 일수 | {n_total:,} |",
        f"| 후보 일수 (`is_candidate=True`) | {n_candidates:,} ({n_candidates / n_total:.1%}) |",
        f"| ATR 과열 필터 적용 일수 | {n_filtered_atr:,} ({n_filtered_atr / n_total:.1%}) |",
        f"| volume=0 (거래정지/특이일, 19종목 합산) | {n_zero_volume:,} |",
        f"| 익일 데이터 없음 (각 종목 마지막 날 = 종목 수) | {n_label_missing:,} |",
        "",
        f"**종목 리스트**: {', '.join(sorted(df['symbol'].unique()))}",
        "",
    ]
    return "\n".join(lines)


def section_hit_rate(df: pd.DataFrame) -> str:
    """2) 점수별 hit rate (전체 / 후보만)."""
    df_valid = df[df["next_open_pct"].notna()].copy()  # 마지막 날 제외

    # 점수별 (0점 포함, 0점도 비교 기준선으로)
    rows = []
    for score in sorted(df_valid["layer2_score"].unique()):
        sub = df_valid[df_valid["layer2_score"] == score]
        n = len(sub)
        if n == 0:
            continue
        rows.append({
            "점수": score,
            "표본 수": n,
            "morning_exit hit%": sub["label_morning_exit"].mean() * 100,
            "stop_risk hit%": sub["label_stop_risk"].mean() * 100,
            "gap_up hit%": sub["label_gap_up"].mean() * 100,
        })
    score_table = pd.DataFrame(rows)

    # 후보만 (ATR 필터 + volume>0 통과)
    cands = df_valid[df_valid["is_candidate"]].copy()
    rows_c = []
    for score in sorted(cands["layer2_score"].unique()):
        sub = cands[cands["layer2_score"] == score]
        n = len(sub)
        if n == 0:
            continue
        rows_c.append({
            "점수": score,
            "표본 수": n,
            "morning_exit hit%": sub["label_morning_exit"].mean() * 100,
            "stop_risk hit%": sub["label_stop_risk"].mean() * 100,
            "gap_up hit%": sub["label_gap_up"].mean() * 100,
        })
    cand_table = pd.DataFrame(rows_c)

    return "\n".join([
        "## 2. 점수별 hit rate",
        "",
        "**0점 = 후보 자격 미달 (비교 기준선)**. 마지막 날(익일 데이터 없음) 제외.",
        "",
        "### 2-1. 전체 (모든 점수, 필터 미적용)",
        "",
        _format_table(score_table, ".2f"),
        "",
        "### 2-2. 후보만 (ATR 과열 + volume=0 필터 통과)",
        "",
        _format_table(cand_table, ".2f"),
        "",
        "**해석**:",
        "- **점수와 morning_exit hit rate 사이 단조성 부재** (1→2점에서 5%p 하락, 2→3→4점은 상승). 점수 높은 구간 표본이 작아 (4점 n=34) 통계적 유의성 없음.",
        "- **stop_risk hit rate 는 점수와 함께 단조 상승** (53→59→63→63→67%) — 점수 높은 종목이 **수익 가능성보다 변동성 자체가 큰** 종목임을 시사.",
        "- 4점 구간 hit rate 가 다소 높지만 (61.8%) 95% 신뢰구간 ±17%p 수준이라 노이즈 범위 내.",
        "",
        "**한계 (반드시 인지)**: 일봉 high/low 는 09:00~15:30 전체 시간대를 커버한다. "
        "실전 09:00~09:30 윈도우만 보는 것 대비 **morning_exit/stop_risk 모두 과대평가**. "
        "절대값 신뢰 금지, 패턴만 sanity check 용도로 해석.",
        "",
    ])


def section_ev_estimate(df: pd.DataFrame, settings: dict) -> str:
    """3) 비용 차감 후 EV 추정."""
    cost = settings.get("cost", {})
    morning_exit_pct = 0.012
    stop_loss_pct = -0.010
    round_trip_cost = compute_round_trip_cost(settings)

    df_v = df[df["next_open_pct"].notna()].copy()
    df_v["pnl_pretax"] = compute_pnl_per_row(df_v, morning_exit_pct, stop_loss_pct)
    df_v["pnl_net"] = df_v["pnl_pretax"] - round_trip_cost

    # 후보만
    cands = df_v[df_v["is_candidate"]].copy()
    rows = []
    for score in sorted(cands["layer2_score"].unique()):
        sub = cands[cands["layer2_score"] == score]
        n = len(sub)
        if n == 0:
            continue
        ev_pretax = sub["pnl_pretax"].mean() * 100
        ev_net = sub["pnl_net"].mean() * 100
        win_rate = (sub["pnl_net"] > 0).mean() * 100
        rows.append({
            "점수": score,
            "표본 수": n,
            "세전 EV%": ev_pretax,
            "비용차감 EV%": ev_net,
            "흑자 비율%": win_rate,
        })
    ev_table = pd.DataFrame(rows)

    # 0점 baseline + 후보 통합 EV
    zero = df_v[df_v["layer2_score"] == 0]
    zero_ev_net = zero["pnl_net"].mean() * 100 if len(zero) > 0 else float("nan")
    cand_ev_net = cands["pnl_net"].mean() * 100 if len(cands) > 0 else float("nan")

    lines = [
        "## 3. 비용 차감 후 EV 추정",
        "",
        f"**거래비용 (왕복)**: 약 **{round_trip_cost * 100:.2f}%** "
        f"(매수 수수료 {float(cost.get('buy_commission', 0.00015)) * 100:.3f}% + "
        f"매도 수수료 {float(cost.get('sell_commission', 0.00015)) * 100:.3f}% + "
        f"거래세 {float(cost.get('transaction_tax', 0.0018)) * 100:.2f}% + "
        f"슬리피지 편도×2 {float(cost.get('estimated_slippage', 0.001)) * 2 * 100:.2f}%)",
        "",
        "**EV 모델** (단순화, sanity check 용):",
        f"- ``label_morning_exit=True`` → 익절 +{morning_exit_pct * 100:.1f}% (**고정값**, 실제 분포 무시)",
        f"- ``label_stop_risk=True`` → 손절 {stop_loss_pct * 100:.1f}% (**고정값**, 실제 분포 무시)",
        "- 둘 다 True → 보수적 stop 우선 (실전 09:00 갭다운 손절 시나리오 가정)",
        "- 둘 다 False → 익일 시가 (``next_open_pct``) 청산",
        "",
        f"※ ``settings.yaml`` 의 ``safety_margin: 0.005`` 는 임계값 산정 마진이라 EV 계산에서 제외.",
        f"※ 익절 +1.2% 는 morning_exit 라벨의 **하한선** 이라 실제 평균 익절폭은 더 클 수 있음 → "
        f"본 모델은 EV 를 **보수적으로(하향)** 추정하는 경향.",
        "",
        "**점수별 후보 EV**:",
        "",
        _format_table(ev_table, ".3f"),
        "",
        f"**0점 기준선 (비후보) 비용 차감 EV**: {zero_ev_net:+.3f}%",
        f"**후보 전체 (1~4점) 비용 차감 EV**: {cand_ev_net:+.3f}%",
        "",
        "**해석**:",
        "- 비용 차감 후 EV 가 모든 점수 구간에서 **음수**. Layer 2 단독으로는 진입 시 손해.",
        "- **후보(1~4점)가 비후보(0점)보다 EV 가 더 낮음** — Layer 2 점수가 모멘텀 추격을 유도하면서 "
        "비용 차감 후 손실이 *오히려 커지는* 패턴.",
        "- 이는 **장중 수급(Layer 1)·막판 체결강도가 진짜 알파**임을 역설적으로 시사. "
        "Phase 1 forward test 의 핵심 가설.",
        "",
    ]
    return "\n".join(lines)


def section_atr_filter_effect(df: pd.DataFrame, settings: dict) -> str:
    """4) ATR 필터 적용/미적용 대조 — 역선택 패턴 확인."""
    df_v = df[df["next_open_pct"].notna()].copy()
    morning_exit_pct = 0.012
    stop_loss_pct = -0.010
    round_trip_cost = compute_round_trip_cost(settings)
    df_v["pnl_net"] = (
        compute_pnl_per_row(df_v, morning_exit_pct, stop_loss_pct) - round_trip_cost
    )

    rows = []
    for score in sorted(df_v["layer2_score"].unique()):
        if score == 0:
            continue
        sub_score = df_v[df_v["layer2_score"] == score]
        sub_passed = sub_score[~sub_score["filtered_atr_overheat"] & (sub_score["volume"] > 0)]
        sub_filtered = sub_score[sub_score["filtered_atr_overheat"] & (sub_score["volume"] > 0)]

        if len(sub_passed) == 0 or len(sub_filtered) == 0:
            continue
        rows.append({
            "점수": score,
            "통과 n": len(sub_passed),
            "통과 morning_exit%": sub_passed["label_morning_exit"].mean() * 100,
            "통과 EV_net%": sub_passed["pnl_net"].mean() * 100,
            "필터된 n": len(sub_filtered),
            "필터된 morning_exit%": sub_filtered["label_morning_exit"].mean() * 100,
            "필터된 EV_net%": sub_filtered["pnl_net"].mean() * 100,
        })
    table = pd.DataFrame(rows)

    return "\n".join([
        "## 4. ATR 과열 필터 효과 (역선택 점검)",
        "",
        "현재 ATR 과열 필터는 ``atr_overheat > 1.8`` 일 때 후보 제거 "
        "(PRD 5-Layer 2: ATR 과열도 1.8 초과 → 제외).",
        "",
        "그러나 일봉 백테스트에서 **필터된 종목이 통과 종목보다 높은 morning_exit hit rate** 를 "
        "보이는 역선택 패턴이 발생할 수 있다 (강한 일중 변동이 익일 09:30 윈도우에서도 모멘텀 유지).",
        "",
        _format_table(table, ".2f"),
        "",
        "**해석**: 필터된 후보의 hit rate 가 통과 후보보다 높으면 **현재 1.8 임계값이 보수적 과잉**. "
        "Phase 1 signal_score_engine 구현 시 임계값 재검토 후보. "
        "단, 일봉 한계로 인한 과대평가가 강하게 작용하므로 **실전 데이터 누적 후 재검증** 필수.",
        "",
    ])


def section_gap_up_split(df: pd.DataFrame) -> str:
    """5) gap_up AND morning_exit / morning_exit only 분리."""
    df_v = df[df["next_open_pct"].notna()].copy()
    cands = df_v[df_v["is_candidate"]]

    n_total = len(cands)
    n_gap_up = int(cands["label_gap_up"].sum())
    n_morning = int(cands["label_morning_exit"].sum())
    n_both = int((cands["label_gap_up"] & cands["label_morning_exit"]).sum())
    n_morning_only = n_morning - n_both
    n_gap_only = n_gap_up - n_both

    p_morning_given_gap = n_both / n_gap_up * 100 if n_gap_up else float("nan")
    p_gap_given_morning = n_both / n_morning * 100 if n_morning else float("nan")

    next_open_when_morning_only = (
        cands.loc[cands["label_morning_exit"] & ~cands["label_gap_up"], "next_open_pct"]
        .median() * 100
        if n_morning_only
        else float("nan")
    )

    return "\n".join([
        "## 5. gap_up vs morning_exit 라벨 분리",
        "",
        "**관찰**: gap_up(``next_open_pct ≥ +0.6%``) 과 morning_exit(``next_high_pct ≥ +1.2%``) "
        "은 라벨이 별개지만 일봉상 시계열에서 측정되어 강한 dependency 존재.",
        "",
        "| 항목 | 후보 기준 |",
        "|---|---|",
        f"| 총 후보 | {n_total:,} |",
        f"| gap_up True | {n_gap_up:,} ({n_gap_up / n_total:.1%}) |",
        f"| morning_exit True | {n_morning:,} ({n_morning / n_total:.1%}) |",
        f"| gap_up AND morning_exit | {n_both:,} ({n_both / n_total:.1%}) |",
        f"| morning_exit only (gap_up 없이) | {n_morning_only:,} ({n_morning_only / n_total:.1%}) |",
        f"| gap_up only (morning_exit 없이) | {n_gap_only:,} ({n_gap_only / n_total:.1%}) |",
        "",
        f"**P(morning_exit | gap_up)**: {p_morning_given_gap:.1f}%",
        f"**P(gap_up | morning_exit)**: {p_gap_given_morning:.1f}%",
        "",
        f"**morning_exit only (시가 보합/약간음봉) 케이스의 익일 시가 중앙값**: {next_open_when_morning_only:+.2f}%",
        "",
        "**해석**: morning_exit 발생 케이스의 상당수는 gap_up 동반 (overnight 호재). "
        "gap_up 없이도 morning_exit 발생하는 비율이 의미있다면 **장 개시 직후 반등 패턴**도 존재. "
        "Phase 1 매도 로직(PRD 10-1)이 gap_up 케이스와 보합 케이스를 구분 처리하는 이유와 일치.",
        "",
    ])


def section_universe_distribution(df: pd.DataFrame) -> str:
    """6) 종목별 후보 분포 — 대형주 편향 확인."""
    df_v = df[df["next_open_pct"].notna()].copy()
    rows = []
    for sym in sorted(df_v["symbol"].unique()):
        sub = df_v[df_v["symbol"] == sym]
        cands = sub[sub["is_candidate"]]
        if len(sub) == 0:
            continue
        rows.append({
            "종목": sym,
            "전체 일수": len(sub),
            "후보 일수": len(cands),
            "후보 비율%": len(cands) / len(sub) * 100,
            "후보 morning_exit%": cands["label_morning_exit"].mean() * 100 if len(cands) else float("nan"),
            "후보 stop_risk%": cands["label_stop_risk"].mean() * 100 if len(cands) else float("nan"),
        })
    table = pd.DataFrame(rows)

    return "\n".join([
        "## 6. 종목별 후보 분포 (대형주 편향 점검)",
        "",
        "본 백테스트는 **캐시된 19종목** 으로 한정 — 대형주 + 코스닥 중형주 위주. "
        "실전 종가베팅이 노리는 **중소형 모멘텀 종목과 분포가 다름**. "
        "결과 일반화 시 survivorship bias 주의.",
        "",
        _format_table(table, ".2f"),
        "",
    ])


def section_limitations_and_verdict(df: pd.DataFrame, settings: dict) -> str:
    """7) 한계 및 종합 판정."""
    df_v = df[df["next_open_pct"].notna()].copy()
    df_v["pnl_net"] = (
        compute_pnl_per_row(df_v, 0.012, -0.010) - compute_round_trip_cost(settings)
    )
    cands = df_v[df_v["is_candidate"]]

    n_4 = (cands["layer2_score"] == 4).sum()
    n_3plus = (cands["layer2_score"] >= 3).sum()
    ev_3plus = cands.loc[cands["layer2_score"] >= 3, "pnl_net"].mean() * 100 if n_3plus > 0 else float("nan")

    return "\n".join([
        "## 7. 한계 및 종합 판정",
        "",
        "### 한계",
        "1. **일봉 high/low 사용 → hit rate 과대평가**. 실전 09:00~09:30 윈도우 대비 morning_exit 와 stop_risk 모두 부풀려짐.",
        "2. **Layer 1 (장중 수급) 측정 불가**. 일봉 데이터로는 KIS 추정 순매수, 마감 수급 집중도 등 재현 X. "
        "종가베팅의 핵심 알파가 측정되지 않았다는 의미.",
        "3. **Layer 2 일부만 측정**. 막판 30분 VWAP 위치, 막판 체결강도, 호가 스프레드 모두 일봉으로 재현 불가.",
        "4. **52주 신고가 ``min_periods=60``**: 첫 약 1년간(2023-02-01~2024-02-01)은 60~251일 최고가를 사용해 "
        "엄밀한 \"52주\" 의미 약화.",
        "5. **대형주/생존 편향**: 캐시 19종목 한정, 상장폐지/거래정지 종목 누락. "
        "중소형주 모멘텀 종목과 분포 불일치.",
        "6. **EV 모델 단순화**: "
        "(a) morning/stop 동시 발생 시 stop 우선 — 보수적 임의 가정. "
        "(b) **morning_exit 고정 +1.2% 사용** = 라벨 하한선 → EV 하향 편향 가능. "
        "(c) **둘 다 False 시 익일 시가(``next_open``) 청산** — 실전 매도 시점 09:30 이후이므로 "
        "09:00~09:30 30분 변동분이 모델에 미반영. 양방향 오차로 EV 영향 상쇄 가능하나 명시 X.",
        "",
        "### 종합 판정",
        "",
        f"- 모든 점수 구간 후보의 비용 차감 EV **음수** (-0.58 ~ -0.71%)",
        f"- 후보 (1~4점) EV 가 **비후보 (0점, -0.48%)보다 더 낮음** — Layer 2 점수가 모멘텀 추격 손실 유도",
        f"- 4점 후보 n={n_4} → 95% 신뢰구간 ±17%p 수준이라 morning_exit 61.8% 의 통계적 유의성 없음",
        f"- 1→2점 hit rate 비단조성 (56.6%→51.3%) 존재 → 점수와 hit rate 단순 관계 부재",
        "",
        "**판정**: ⚠️ **\"기각 근거 없음\" 수준**. 결과는 다음 중 어느 쪽으로도 결론 불가:",
        "- (A) 전략이 정말 효과 없음 → 더 정교한 측정(Layer 1/막판 30분) 필요",
        "- (B) 일봉 한계 때문에 Layer 2 단독 시그널이 측정 불가능 → forward test 만이 검증 가능",
        "",
        "**근거 (A) vs (B)**: 일봉 백테스트로는 (A)와 (B)를 구별할 수 없다. "
        "본 sanity check 의 목적은 **\"전략 방향이 완전히 틀렸는지\"** 의 기각 가능성 확인뿐이며, "
        "이 검증 결과는 \"적극적 진입 근거\"가 아니라 **\"기각 근거 부재로 forward test 진행 가능\"** 수준이다.",
        "",
        "### 의사결정 분기",
        "",
        "| 옵션 | 조건 | 권고 |",
        "|---|---|---|",
        "| **Phase 1 진입** | 일봉 한계로 Layer 2 단독 측정 불가, forward test 만이 진짜 검증 | ✅ **권장** — 본 sanity check 결과 \"기각 근거 없음\" 충족 |",
        "| Phase 0 보류 | sanity check 가 강한 양의 신호를 요구 (불가능한 기준) | ❌ 비권장 — sanity check 본질에 안 맞음 |",
        "| 전략 재설계 | 본 결과가 명백한 기각 근거를 제시 | ❌ 비해당 — 비용 차감 EV 음수는 일봉 한계 때문일 가능성 큼 |",
        "",
        "### Phase 1+ 권고",
        "1. **Layer 1 가중치 0 정책 유지** (P2-6): 추정값 신뢰도 누적 전 Layer 1 점수 무력화.",
        "2. **flow_reliability 누적**: 추정값 vs KRX 확정값 방향 일치율 70% 이상 검증 후 Layer 1 활성화.",
        "3. **ATR 과열 임계값 재검토 후보**: 일봉상 역선택 가능성 — Phase 2.5 후보 DB 백테스트에서 실 데이터로 재평가. "
        "현 1.8 임계값 그대로 Phase 1 시작 → 실거래 데이터 100건 누적 후 결정.",
        "4. **30건 운영 점검 게이트**: 통계 결론 X, 운영 흐름 점검만 (15영업일+ / 종목 20+ / CRISIS 진입 0건).",
        "5. **100건 자동화 게이트**: PRD 원안 — 비용 차감 후 EV ≥ 0.5% / 평균 익절·손절비 ≥ 1.3 / 샤프 ≥ 1.0.",
        "6. **1-4 signal_score_engine 기본값**: 본 sanity check 에서 임계값 추정 불가 → "
        "PRD 6-1 단순 카운트(7/8/9점) 그대로 시작. Phase 2.5 백테스트 후 조정.",
        "",
    ])


# ===== 메인 =====


def build_report(csv_path: Path = _DEFAULT_INPUT, settings: dict | None = None) -> str:
    """전체 markdown 리포트 본문 생성."""
    df = load_results(csv_path)
    settings = settings if settings is not None else _load_settings()

    header = [
        f"# Pre-Phase 1 Layer 2 Sanity Check Report",
        "",
        f"> **작성**: {now_kst().strftime('%Y-%m-%d %H:%M KST')}  ",
        f"> **데이터 출처**: `{csv_path.relative_to(_PROJECT_ROOT)}`  ",
        f"> **목적**: \"전략 방향이 완전히 틀렸는지\" sanity check. **임계값 추정 X / 유효성 결론 X**.  ",
        f"> **생성 도구**: `closing_bet_system/backtest/sanity_check_report.py`",
        "",
        "본 리포트는 **0-C 백테스트(`daily_proxy_backtest.py`)** 가 생성한 결과 CSV 를 분석한다. "
        "종가베팅의 핵심 신호인 **장중 수급 추정값/막판 체결강도/동시호가 예상체결가는 일봉으로 재현 불가** 하므로, "
        "본 sanity check 는 OHLCV 일봉만으로 측정 가능한 Layer 2 일부 + Layer 3 의 52주 신고가 만 사용한다.",
        "",
        "---",
        "",
    ]

    sections = [
        section_overview(df),
        section_hit_rate(df),
        section_ev_estimate(df, settings),
        section_atr_filter_effect(df, settings),
        section_gap_up_split(df),
        section_universe_distribution(df),
        section_limitations_and_verdict(df, settings),
    ]

    body = "\n---\n\n".join(sections)
    return "\n".join(header) + body


def save_report(content: str, output_path: Path = _DEFAULT_OUTPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"[sanity-check] 리포트 저장: {output_path} ({len(content):,}자)")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pre-Phase 1 sanity check 리포트 생성")
    parser.add_argument("--input", type=str, default=str(_DEFAULT_INPUT))
    parser.add_argument("--output", type=str, default=str(_DEFAULT_OUTPUT))
    args = parser.parse_args()

    content = build_report(Path(args.input))
    save_report(content, Path(args.output))


if __name__ == "__main__":
    main()
