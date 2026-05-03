"""closing_bet_system.backtest.daily_proxy_backtest

Pre-Phase 1 Layer 2 단독 일봉 sanity check 백테스트.

**목적**: "전략 방향이 완전히 틀렸는지" 만 확인. **임계값 추정 X**.
종가베팅의 결정적 신호(장중 수급 추정값, 막판 30분 체결강도, 동시호가 예상체결가)는
실시간 스냅샷이라 일봉으로 재현 불가능하다. 따라서 본 백테스트는:

- Layer 1 (수급) — 완전 제외 (proxy 사용 시 선택편향)
- Layer 2 OHLCV — Close Strength / ATR 과열 / 20일선 / 거래량 서프라이즈 사용
- Layer 3 중 OHLCV 재현 가능 — 52주 신고가 근접만 사용

**라벨**: PRD 12-1 동일
- ``label_morning_exit``: 익일 09:30 고가 / 진입가(당일 종가) - 1 ≥ +1.2%
- ``label_stop_risk``: 익일 09:30 저가 / 진입가 - 1 ≤ -1.0%

본 백테스트는 **익일 시가/고가/저가를 일봉의 open/high/low로 근사**한다.
실제 09:00~09:30 윈도우와 차이가 있으므로 결과의 절대값은 신뢰하지 말 것
(09:30 이후 변동분이 일봉 high/low에 포함되므로 hit rate 가 과대평가됨).

실행:
    venv/bin/python -m closing_bet_system.backtest.daily_proxy_backtest \\
        --symbols 005930,000660,035420,... \\
        --start 2023-02-01 --end 2026-02-01 \\
        --output data/backtest_cache/daily_proxy_results.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from logger import logger
from modules.backtester.data_loader import DataLoader, MarketData


# ===== 지표 계산 =====


def compute_layer2_features(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """OHLCV DataFrame 에 Layer 2 지표 컬럼 추가.

    필요 입력 컬럼: ``open, high, low, close, volume`` (소문자)

    추가 컬럼:
    - ``close_strength``: (close - low) / (high - low). high == low → NaN
    - ``atr14``: 14일 평균 True Range
    - ``daily_change_pct``: 당일 (close - prev_close) / prev_close
    - ``atr_overheat``: 당일 상승폭(close-prev_close) / atr (PRD 5-Layer 2 의 "ATR 과열도")
    - ``ma20``: 20일 단순이평
    - ``above_ma20``: close > ma20
    - ``vol_avg20``: 20일 평균 거래량
    - ``vol_surprise``: volume / vol_avg20
    - ``high_52w``: 252일 최고가
    - ``near_52w_high``: close / high_52w >= 0.95
    """
    if df.empty:
        return df.copy()

    out = df.copy()

    # Close Strength
    spread = (out["high"] - out["low"])
    out["close_strength"] = np.where(spread > 0, (out["close"] - out["low"]) / spread, np.nan)

    # ATR (Wilder's, 14일 단순이동평균 근사 — sanity check 용도라 단순 SMA 사용)
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(window=atr_period, min_periods=atr_period).mean()

    # 당일 변화율 + ATR 과열도
    out["daily_change_pct"] = (out["close"] - prev_close) / prev_close
    daily_diff = out["close"] - prev_close
    # ATR 0 또는 NaN → 과열도 NaN (필터에서 제외)
    out["atr_overheat"] = np.where(out["atr14"] > 0, daily_diff / out["atr14"], np.nan)

    # 20일선
    out["ma20"] = out["close"].rolling(window=20, min_periods=20).mean()
    out["above_ma20"] = out["close"] > out["ma20"]

    # 거래량 서프라이즈
    out["vol_avg20"] = out["volume"].rolling(window=20, min_periods=20).mean()
    out["vol_surprise"] = np.where(out["vol_avg20"] > 0, out["volume"] / out["vol_avg20"], np.nan)

    # 52주 신고가 근접 (252영업일 기준)
    out["high_52w"] = out["high"].rolling(window=252, min_periods=60).max()
    out["near_52w_high"] = (out["close"] / out["high_52w"]) >= 0.95

    return out


def score_candidates(
    df: pd.DataFrame,
    close_strength_min: float = 0.85,
    vol_surprise_min: float = 2.0,
    atr_overheat_max: float = 1.8,
) -> pd.DataFrame:
    """Layer 2 sanity check 점수 (0~4점) + 필터 컬럼.

    점수 (각 1점):
    - ``cond_close_strength``: close_strength ≥ 0.85
    - ``cond_vol_surprise``: vol_surprise ≥ 2.0
    - ``cond_above_ma20``: above_ma20 == True
    - ``cond_near_52w_high``: near_52w_high == True

    하드 필터:
    - ``filtered_atr_overheat``: atr_overheat > 1.8 → 후보 제외
    """
    out = df.copy()

    out["cond_close_strength"] = (out["close_strength"] >= close_strength_min).fillna(False)
    out["cond_vol_surprise"] = (out["vol_surprise"] >= vol_surprise_min).fillna(False)
    out["cond_above_ma20"] = out["above_ma20"].fillna(False)
    out["cond_near_52w_high"] = out["near_52w_high"].fillna(False)

    out["layer2_score"] = (
        out["cond_close_strength"].astype(int)
        + out["cond_vol_surprise"].astype(int)
        + out["cond_above_ma20"].astype(int)
        + out["cond_near_52w_high"].astype(int)
    )

    out["filtered_atr_overheat"] = (out["atr_overheat"] > atr_overheat_max).fillna(False)
    # 거래정지 / 거래량 0 인 날은 후보 자격 박탈 (가격 동결 + 일봉 미체결).
    # 207940 같은 종목에서 일시 거래정지 시 atr14가 0/NaN으로 떨어지면서 ATR 필터가 비활성화되고,
    # close가 ma20/52w high 위에 머물러 cond_above_ma20/cond_near_52w_high 가 True로 잡혀 score 가 부풀려진다.
    has_volume = (out["volume"] > 0).fillna(False)
    out["is_candidate"] = (out["layer2_score"] >= 1) & (~out["filtered_atr_overheat"]) & has_volume

    return out


# ===== 라벨링 =====


def label_outcomes(
    df: pd.DataFrame,
    morning_exit_threshold: float = 0.012,
    stop_risk_threshold: float = -0.010,
    gap_up_threshold: float = 0.006,
) -> pd.DataFrame:
    """익일 시가/고가/저가로 사후 라벨 부여 (PRD 12-1).

    **한계**: 일봉의 open/high/low 는 09:00~15:30 전 구간이므로,
    09:00~09:30 윈도우만 사용하는 실전 환경 대비 **hit rate 과대평가** 가능.
    sanity check 용도로만 해석할 것.

    추가 컬럼:
    - ``next_open_pct``: 익일 open / 당일 close - 1
    - ``next_high_pct``: 익일 high / 당일 close - 1
    - ``next_low_pct``: 익일 low / 당일 close - 1
    - ``label_gap_up``: next_open_pct ≥ +0.6%
    - ``label_morning_exit``: next_high_pct ≥ +1.2%
    - ``label_stop_risk``: next_low_pct ≤ -1.0%
    """
    out = df.copy()

    next_open = out["open"].shift(-1)
    next_high = out["high"].shift(-1)
    next_low = out["low"].shift(-1)

    out["next_open_pct"] = next_open / out["close"] - 1
    out["next_high_pct"] = next_high / out["close"] - 1
    out["next_low_pct"] = next_low / out["close"] - 1

    out["label_gap_up"] = (out["next_open_pct"] >= gap_up_threshold).fillna(False)
    out["label_morning_exit"] = (out["next_high_pct"] >= morning_exit_threshold).fillna(False)
    out["label_stop_risk"] = (out["next_low_pct"] <= stop_risk_threshold).fillna(False)

    return out


# ===== Universe 처리 =====


def run_single_symbol(
    symbol: str,
    start_date: str,
    end_date: str,
    loader: Optional[DataLoader] = None,
) -> pd.DataFrame:
    """1종목 백테스트. 후보+라벨이 채워진 long-format DataFrame 반환."""
    if loader is None:
        loader = DataLoader()

    market = loader.load_stock_data(symbol, start_date, end_date, name=symbol)
    if market.data.empty:
        logger.warning(f"[backtest] {symbol} 데이터 없음, 건너뜀")
        return pd.DataFrame()

    df = market.data.copy()
    df = compute_layer2_features(df)
    df = score_candidates(df)
    df = label_outcomes(df)

    df["symbol"] = symbol
    df = df.reset_index().rename(columns={"index": "date", "Date": "date"})
    if "date" not in df.columns:
        # yfinance 인덱스명이 'Date' 인 경우 처리
        df = df.rename(columns={df.columns[0]: "date"})

    return df


def run_universe(
    symbols: list[str],
    start_date: str,
    end_date: str,
    loader: Optional[DataLoader] = None,
) -> pd.DataFrame:
    """다종목 백테스트. 통합 long-format DataFrame 반환."""
    if loader is None:
        loader = DataLoader()

    frames = []
    for symbol in symbols:
        try:
            df = run_single_symbol(symbol, start_date, end_date, loader)
        except Exception as e:
            logger.error(f"[backtest] {symbol} 처리 실패: {e}")
            continue
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info(f"[backtest] 통합 완료: {len(symbols)}종목, 총 {len(combined):,}행")
    return combined


# ===== Output =====


def save_results(df: pd.DataFrame, output_path: Path | str) -> None:
    """결과 DataFrame 을 CSV 로 저장 (0-D 리포트에서 로드).

    포맷 선택: parquet 대신 CSV — 19종목 × 3년 ≈ 14k 행 수준이라 크기 부담 없음,
    pyarrow 의존성 회피.

    종목코드는 6자리 zero-padded 문자열로 강제 (CSV 자동 추론으로 leading zero 손실 방지).
    로드 시: ``pd.read_csv(path, dtype={"symbol": str})`` 권장.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    out.to_csv(output_path, index=False)
    logger.info(f"[backtest] 결과 저장: {output_path} ({len(out):,}행)")


# ===== CLI =====


_DEFAULT_SYMBOLS = [
    # 캐시에 보유한 19개 (대형주 + 코스닥 중형주 다양한 섹터)
    "000270", "000660", "005380", "005930", "006400",
    "035420", "035720", "035900", "036930", "041510",
    "042700", "047810", "051910", "068270", "086520",
    "207940", "247540", "272210", "373220",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-Phase 1 Layer 2 sanity check 백테스트")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(_DEFAULT_SYMBOLS),
        help="콤마 구분 종목코드 (기본: 캐시 보유 19종목)",
    )
    parser.add_argument("--start", type=str, default="2023-02-01", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2026-02-01", help="종료일 YYYY-MM-DD")
    parser.add_argument(
        "--output",
        type=str,
        default="data/backtest_cache/daily_proxy_results.csv",
        help="결과 저장 경로 (CSV)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    logger.info(
        f"[backtest] 시작: {len(symbols)}종목, {args.start} ~ {args.end}, output={args.output}"
    )

    df = run_universe(symbols, args.start, args.end)
    if df.empty:
        logger.error("[backtest] 결과 비어있음. 데이터 로드 실패 가능성")
        sys.exit(1)

    output_path = _PROJECT_ROOT / args.output
    save_results(df, output_path)

    # 즉석 요약 (0-D 정식 리포트는 별도)
    candidates = df[df["is_candidate"]]
    logger.info(
        f"[backtest] 요약 — 전체 {len(df):,}행, 후보 {len(candidates):,}건 "
        f"({len(candidates) / len(df):.1%})"
    )
    if not candidates.empty:
        for score in sorted(candidates["layer2_score"].unique()):
            subset = candidates[candidates["layer2_score"] == score]
            n_total = len(subset)
            morning_exit_n = subset["label_morning_exit"].sum()
            stop_risk_n = subset["label_stop_risk"].sum()
            logger.info(
                f"  점수 {score}점: {n_total:,}건 / "
                f"morning_exit hit {morning_exit_n / n_total:.1%} / "
                f"stop_risk hit {stop_risk_n / n_total:.1%}"
            )


if __name__ == "__main__":
    main()
