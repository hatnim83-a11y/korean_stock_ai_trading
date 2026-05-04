"""closing_bet_system.collectors.universe_filters

PRD 4-1 종목 속성 + 4-4 유동성 하드 필터 모듈.

PRD 의도: 스코어링 이전에 적용하는 **무조건 제외** 조건 — 점수와 무관하게 진입 금지.
universe_provider_v2 가 산출한 풀(pool)에서 PRD 하드 필터를 적용해 최종 universe 산출.

본 단위(2-9b) 적용 항목
    - PRD 4-1 (속성):
        - 시가총액 < 500억 제외
        - 주가 < 1,000원 제외
        - 당일 상한가 종목 제외 (등락률 +29.5% 이상 — 정확한 +30% 매칭 어려움)
        - 52주 고점 -30% 초과 하락 제외
    - PRD 4-4 (유동성):
        - 20일 평균 거래대금 < 50억 제외
        - 당일 거래대금 < 100억 제외

후속 단위로 분리 (별도 데이터 출처 필요)
    - PRD 4-1: 보호예수/의무보유 D-7 (SEIBro 데이터)
    - PRD 4-1: 관리/투자경고/거래정지 (KIND severity, 단위 2-2 의 KindHttpProvider 활성 후)
    - PRD 4-4: 호가 스프레드 1.5배 (orderbook 누적 후)
    - PRD 4-4: 주문금액 vs 유동성 3% (실시간 주문 시점)

설계 결정
    - **bulk fetch 우선**: 시총/OHLCV 는 KOSPI+KOSDAQ 시장 단위 일괄 호출 (4 호출, ~4초)
    - **종목별 fetch 최소화**: 52주 고점/20일 평균만 종목별 호출 (~30~50초)
    - settings.yaml `stock_filter` / `liquidity` 섹션 로드 (폴백 default 보유)
    - 첫 위반 사유만 기록 — `rejected_reason` 단일값 (PRD 11-1 candidates 스키마 정합)
    - 모든 pykrx 호출 try/except 격리 → bulk fetch 실패 시 ``data_not_found`` 탈락
      (보수적 하드 필터 — 데이터 없으면 진입 안 함)
"""

from __future__ import annotations

import math
import sys
import threading
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== 모듈 상수 (settings.yaml 폴백 default) =====

# PRD 4-1 stock_filter 폴백 default
DEFAULT_MIN_MARKET_CAP = 50_000_000_000          # 500억 (settings.yaml: stock_filter.min_market_cap)
DEFAULT_MIN_PRICE = 1_000                         # 1,000원 (stock_filter.min_price)
DEFAULT_MAX_DROP_FROM_52W_HIGH = 0.30             # -30% (stock_filter.max_drop_from_52w_high)
DEFAULT_EXCLUDE_UPPER_LIMIT = True                # 상한가 제외 (stock_filter.exclude_upper_limit)

# PRD 4-4 liquidity 폴백 default
DEFAULT_MIN_AVG_VALUE_20D = 5_000_000_000         # 50억 (liquidity.min_avg_value_20d)
DEFAULT_MIN_TODAY_VALUE = 10_000_000_000          # 100억 (liquidity.min_today_value)

# 상한가 임계값 — PRD 4-1 "당일 상한가" 정확한 +30% 매칭 어려움 (실제 +29.97% 등)
# CONTEXT.md 권장값 +29.5% 사용
UPPER_LIMIT_THRESHOLD_PCT = 29.5

# 52주 = 약 365 캘린더 일 (영업일 ~ 250)
LOOKBACK_52W_DAYS = 365

# 20일 평균 거래대금 = 영업일 30 캘린더 일 (영업일 ~ 20~22)
LOOKBACK_20D_DAYS = 30

# pykrx market 코드
_PYKRX_MARKETS = ("KOSPI", "KOSDAQ")


# ===== 캐시 (같은 거래일 1회만 bulk + 종목별 fetch) =====

_market_data_cache_lock = threading.Lock()
_market_data_cache: dict[str, dict[str, dict]] = {}  # key=YYYYMMDD → {ticker: {market_cap, close, change_rate, trading_value}}

_per_ticker_cache_lock = threading.Lock()
_high_52w_cache: dict[tuple[str, str], Optional[float]] = {}  # (today_str, ticker) → high_52w
_avg_value_20d_cache: dict[tuple[str, str], Optional[float]] = {}  # (today_str, ticker) → avg_20d


def reset_cache() -> None:
    """테스트용: 모든 캐시 초기화."""
    with _market_data_cache_lock:
        _market_data_cache.clear()
    with _per_ticker_cache_lock:
        _high_52w_cache.clear()
        _avg_value_20d_cache.clear()


# ===== NaN-safe 변환 =====


def _safe_float(value) -> Optional[float]:
    """pandas NaN/None 가드 후 float 변환. 실패 시 None.

    CLAUDE.md 규칙: ``pandas 값 → float 변환 전 pd.isna() 체크 필수``.
    NaN 값을 그대로 float() 하면 NaN 이 비교 연산에서 항상 False 라
    하드 필터가 누락되는 위험 회피.
    """
    try:
        if value is None:
            return None
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


# ===== settings 로드 =====


def _load_filter_config() -> dict:
    """settings.yaml `stock_filter` + `liquidity` 섹션 로드. 폴백 default 보유.

    Returns:
        dict with keys:
            - min_market_cap, min_price, max_drop_from_52w_high, exclude_upper_limit
            - min_avg_value_20d, min_today_value
    """
    try:
        from closing_bet_system.storage.db import _load_settings
        settings = _load_settings()
    except Exception as e:
        logger.warning(f"[universe_filters] settings.yaml 로드 실패: {e} — default 사용")
        settings = {}

    stock_filter = settings.get("stock_filter", {}) or {}
    liquidity = settings.get("liquidity", {}) or {}

    return {
        "min_market_cap": stock_filter.get("min_market_cap", DEFAULT_MIN_MARKET_CAP),
        "min_price": stock_filter.get("min_price", DEFAULT_MIN_PRICE),
        "max_drop_from_52w_high": stock_filter.get(
            "max_drop_from_52w_high", DEFAULT_MAX_DROP_FROM_52W_HIGH
        ),
        "exclude_upper_limit": stock_filter.get(
            "exclude_upper_limit", DEFAULT_EXCLUDE_UPPER_LIMIT
        ),
        "min_avg_value_20d": liquidity.get("min_avg_value_20d", DEFAULT_MIN_AVG_VALUE_20D),
        "min_today_value": liquidity.get("min_today_value", DEFAULT_MIN_TODAY_VALUE),
    }


# ===== pykrx lazy import =====


def _import_pykrx():
    from pykrx import stock as krx
    return krx


# ===== Bulk fetch: 시총 + OHLCV (시장당 1회) =====


def _fetch_market_data_bulk(today_str: str) -> dict[str, dict]:
    """KOSPI+KOSDAQ 일괄 시총/OHLCV 조회 → ticker 단위 dict.

    캐시 히트 시 즉시 반환.

    Returns:
        ``{ticker: {"market_cap", "close", "change_rate", "trading_value"}}``
    """
    cached = _market_data_cache.get(today_str)
    if cached is not None:
        return cached

    with _market_data_cache_lock:
        cached = _market_data_cache.get(today_str)
        if cached is not None:
            return cached

        result: dict[str, dict] = {}
        krx = None
        try:
            krx = _import_pykrx()
        except Exception as e:
            logger.warning(f"[universe_filters] pykrx import 실패: {e}")
            _market_data_cache[today_str] = result
            return result

        for market in _PYKRX_MARKETS:
            # 시가총액
            try:
                df_cap = krx.get_market_cap_by_ticker(today_str, market=market)
                if df_cap is not None and len(df_cap) > 0 and "시가총액" in df_cap.columns:
                    for ticker, row in df_cap.iterrows():
                        if not isinstance(ticker, str):
                            ticker = str(ticker)
                        v = _safe_float(row["시가총액"])
                        if v is not None:
                            result.setdefault(ticker, {})["market_cap"] = v
            except Exception as e:
                logger.debug(f"[universe_filters] cap_by_ticker {market} 실패: {e}")

            # OHLCV (종가, 등락률, 거래대금)
            try:
                df_ohlcv = krx.get_market_ohlcv_by_ticker(today_str, market=market)
                if df_ohlcv is not None and len(df_ohlcv) > 0:
                    cols = df_ohlcv.columns
                    has_close = "종가" in cols
                    has_chg = "등락률" in cols
                    has_value = "거래대금" in cols
                    for ticker, row in df_ohlcv.iterrows():
                        if not isinstance(ticker, str):
                            ticker = str(ticker)
                        d = result.setdefault(ticker, {})
                        if has_close:
                            v = _safe_float(row["종가"])
                            if v is not None:
                                d["close"] = v
                        if has_chg:
                            v = _safe_float(row["등락률"])
                            if v is not None:
                                d["change_rate"] = v
                        if has_value:
                            v = _safe_float(row["거래대금"])
                            if v is not None:
                                d["trading_value"] = v
            except Exception as e:
                logger.debug(f"[universe_filters] ohlcv_by_ticker {market} 실패: {e}")

        _market_data_cache[today_str] = result
        logger.info(
            f"[universe_filters] bulk market data 캐시 저장 — {len(result)}종목 ({today_str})"
        )
        return result


# ===== 종목별 fetch: 52주 고점 / 20일 평균 거래대금 =====


def _fetch_52w_high(ticker: str, today: date_cls) -> Optional[float]:
    """종목 1년치 OHLCV → 고가 컬럼의 max → 52주 고점.

    실패 시 None (필터 미적용 graceful pass).
    """
    today_str = today.strftime("%Y%m%d")
    cache_key = (today_str, ticker)
    cached = _high_52w_cache.get(cache_key)
    if cached is not None or cache_key in _high_52w_cache:
        return cached

    fromdate = (today - timedelta(days=LOOKBACK_52W_DAYS)).strftime("%Y%m%d")

    high: Optional[float] = None
    try:
        krx = _import_pykrx()
        df = krx.get_market_ohlcv_by_date(fromdate, today_str, ticker)
        if df is not None and len(df) > 0 and "고가" in df.columns:
            max_high = _safe_float(df["고가"].max())
            if max_high is not None and max_high > 0:
                high = max_high
    except Exception as e:
        logger.debug(f"[universe_filters] 52w_high {ticker} 실패: {e}")

    with _per_ticker_cache_lock:
        _high_52w_cache[cache_key] = high
    return high


def _fetch_avg_value_20d(ticker: str, today: date_cls) -> Optional[float]:
    """종목 30일치 OHLCV → 거래대금 컬럼의 평균 → 20일 평균 거래대금.

    영업일 기준 약 20~22 행 평균 (캘린더 30일 = 영업일 ~21).
    실패 시 None (필터 미적용 graceful pass).
    """
    today_str = today.strftime("%Y%m%d")
    cache_key = (today_str, ticker)
    cached = _avg_value_20d_cache.get(cache_key)
    if cached is not None or cache_key in _avg_value_20d_cache:
        return cached

    fromdate = (today - timedelta(days=LOOKBACK_20D_DAYS)).strftime("%Y%m%d")

    avg: Optional[float] = None
    try:
        krx = _import_pykrx()
        df = krx.get_market_ohlcv_by_date(fromdate, today_str, ticker)
        if df is not None and len(df) > 0 and "거래대금" in df.columns:
            mean_value = _safe_float(df["거래대금"].mean())
            if mean_value is not None and mean_value > 0:
                avg = mean_value
    except Exception as e:
        logger.debug(f"[universe_filters] avg_value_20d {ticker} 실패: {e}")

    with _per_ticker_cache_lock:
        _avg_value_20d_cache[cache_key] = avg
    return avg


# ===== 메인 진입점 =====


def apply_attribute_filters(
    tickers: list[str],
    *,
    today: Optional[date_cls] = None,
    config: Optional[dict] = None,
) -> tuple[list[str], dict[str, str]]:
    """PRD 4-1 속성 기반 필터 적용 — 시총/주가/상한가/52주 고점.

    Args:
        tickers: 6자리 종목코드 리스트.
        today: 평가 기준 거래일. None 시 KST 오늘.
        config: 필터 임계값 dict (None 시 settings.yaml 로드).

    Returns:
        (passed, rejected) — passed: list[str], rejected: ``{ticker: reason}``
    """
    if not tickers:
        return [], {}

    today = today or now_kst().date()
    today_str = today.strftime("%Y%m%d")
    cfg = config or _load_filter_config()

    market_data = _fetch_market_data_bulk(today_str)

    passed: list[str] = []
    rejected: dict[str, str] = {}

    for ticker in tickers:
        data = market_data.get(ticker)
        if not data:
            rejected[ticker] = "data_not_found"
            continue

        # 1. 시가총액
        market_cap = data.get("market_cap")
        if market_cap is not None and market_cap < cfg["min_market_cap"]:
            rejected[ticker] = "market_cap_too_low"
            continue

        # 2. 주가
        close = data.get("close")
        if close is not None and close < cfg["min_price"]:
            rejected[ticker] = "price_too_low"
            continue

        # 3. 상한가
        if cfg["exclude_upper_limit"]:
            change_rate = data.get("change_rate")
            if change_rate is not None and change_rate >= UPPER_LIMIT_THRESHOLD_PCT:
                rejected[ticker] = "upper_limit"
                continue

        # 4. 52주 고점 -30% 초과 하락
        if close is not None:
            high_52w = _fetch_52w_high(ticker, today)
            if high_52w is not None and high_52w > 0:
                drop = (high_52w - close) / high_52w
                if drop > cfg["max_drop_from_52w_high"]:
                    rejected[ticker] = "below_52w_high_drop"
                    continue

        passed.append(ticker)

    logger.info(
        f"[universe_filters] 속성 필터: {len(passed)}/{len(tickers)} 통과 "
        f"({len(rejected)} 탈락)"
    )
    return passed, rejected


def apply_liquidity_filters(
    tickers: list[str],
    *,
    today: Optional[date_cls] = None,
    config: Optional[dict] = None,
) -> tuple[list[str], dict[str, str]]:
    """PRD 4-4 유동성 기반 필터 — 당일 거래대금 / 20일 평균.

    Args:
        tickers: 6자리 종목코드 리스트.
        today: 평가 기준 거래일. None 시 KST 오늘.
        config: 필터 임계값 dict (None 시 settings.yaml 로드).

    Returns:
        (passed, rejected)
    """
    if not tickers:
        return [], {}

    today = today or now_kst().date()
    today_str = today.strftime("%Y%m%d")
    cfg = config or _load_filter_config()

    market_data = _fetch_market_data_bulk(today_str)

    passed: list[str] = []
    rejected: dict[str, str] = {}

    for ticker in tickers:
        data = market_data.get(ticker)
        if not data:
            rejected[ticker] = "data_not_found"
            continue

        # 1. 당일 거래대금
        trading_value = data.get("trading_value")
        if trading_value is not None and trading_value < cfg["min_today_value"]:
            rejected[ticker] = "today_value_too_low"
            continue

        # 2. 20일 평균 거래대금
        avg_20d = _fetch_avg_value_20d(ticker, today)
        if avg_20d is not None and avg_20d < cfg["min_avg_value_20d"]:
            rejected[ticker] = "avg_value_20d_too_low"
            continue

        passed.append(ticker)

    logger.info(
        f"[universe_filters] 유동성 필터: {len(passed)}/{len(tickers)} 통과 "
        f"({len(rejected)} 탈락)"
    )
    return passed, rejected


def apply_all_filters(
    tickers: list[str],
    *,
    today: Optional[date_cls] = None,
    config: Optional[dict] = None,
) -> tuple[list[str], dict[str, str]]:
    """PRD 4-1 속성 + 4-4 유동성 통합 필터.

    속성 → 유동성 순으로 적용. 한 종목이 여러 필터 위반 시 첫 위반만 기록.

    Args:
        tickers: 6자리 종목코드 리스트.
        today: 평가 기준 거래일.
        config: 필터 임계값 dict.

    Returns:
        (passed, rejected) — 두 단계 통합 dict
    """
    if not tickers:
        return [], {}

    today = today or now_kst().date()
    cfg = config or _load_filter_config()

    after_attr, rejected_attr = apply_attribute_filters(tickers, today=today, config=cfg)
    after_liq, rejected_liq = apply_liquidity_filters(after_attr, today=today, config=cfg)

    # 통합 rejected — 속성 단계 사유가 우선 (첫 위반)
    rejected: dict[str, str] = dict(rejected_attr)
    for t, reason in rejected_liq.items():
        if t not in rejected:
            rejected[t] = reason

    logger.info(
        f"[universe_filters] 통합 필터: {len(after_liq)}/{len(tickers)} 최종 통과 "
        f"(속성 탈락 {len(rejected_attr)} / 유동성 탈락 {len(rejected_liq)})"
    )
    return after_liq, rejected
