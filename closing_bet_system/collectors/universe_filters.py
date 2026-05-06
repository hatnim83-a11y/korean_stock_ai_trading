"""closing_bet_system.collectors.universe_filters

PRD 4-1 종목 속성 + 4-4 유동성 하드 필터 모듈.

PRD 의도: 스코어링 이전에 적용하는 **무조건 제외** 조건 — 점수와 무관하게 진입 금지.
universe_provider_v2 가 산출한 풀(pool)에서 PRD 하드 필터를 적용해 최종 universe 산출.

본 단위(2-9b + 2-2b 추가) 적용 항목
    - PRD 4-1 (속성):
        - **KIND severity ≥ 3 (관리/거래정지/투자위험) 사전 제외** (단위 2-2b, severity_map 인자 주입 시)
        - 시가총액 < 500억 제외
        - 주가 < 1,000원 제외
        - 당일 상한가 종목 제외 (등락률 +29.5% 이상 — 정확한 +30% 매칭 어려움)
        - 52주 고점 -30% 초과 하락 제외
    - PRD 4-4 (유동성):
        - 20일 평균 거래대금 < 50억 제외
        - 당일 거래대금 < 100억 제외

후속 단위로 분리 (별도 데이터 출처 필요)
    - PRD 4-1: 보호예수/의무보유 D-7 (SEIBro 데이터)
    - PRD 4-4: 호가 스프레드 1.5배 (orderbook 20일 평균 누적 후)
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
import time
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

# 단위 2-9c — KRX bulk 빈 응답 시 종목별 by_date 폴백 상한 (hard_cap 정합)
MAX_FALLBACK_TICKERS = 100

# 종목별 폴백 호출 사이 sleep — KRX 차단 방어 (50ms × 100건 = 최대 5초 추가)
FALLBACK_RATE_LIMIT_SEC = 0.05

# data_source 토글 default — settings.yaml `data_source.fallback_per_ticker_enabled`
DEFAULT_FALLBACK_PER_TICKER_ENABLED = True

# 단위 2-9d — KIS market-cap ranking 으로 시총 보강 default
# 단위 2-9e — True → False 변경: settings.yaml(false→true 활성)와 default 가 정반대였음 → settings 로드 실패 시
# cfg fallback 분기에서 의도와 반대로 활성화 위험. 단위 2-9e 활성 후에는 settings ON 이라 무관, 향후 롤백 시 안전.
DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = False
# KIS 시총 ranking 호출 시 가져올 상위 N — 시장 규모 대비 충분히 커야 함 (KOSPI+KOSDAQ ~2,500종목 중 상위 200)
DEFAULT_KIS_MARKET_CAP_TOP_N = 200

# PRD 4-1 KIND severity 사전 제외 임계값 — kind_alert_collector 단방향 import (순환 없음).
# kind_alert_collector 가 본 모듈을 import 하지 않으므로 안전.
from closing_bet_system.collectors.kind_alert_collector import (
    SEVERITY_EXCLUDE_THRESHOLD,
)


# ===== 캐시 (같은 거래일 1회만 bulk + 종목별 fetch) =====

_market_data_cache_lock = threading.Lock()
_market_data_cache: dict[str, dict[str, dict]] = {}  # key=YYYYMMDD → {ticker: {market_cap, close, change_rate, trading_value}}

# 단위 2-9c — bulk 빈 응답 폴백 결과 별도 격리 (bulk 캐시 오염 방지)
_per_ticker_market_cache: dict[str, dict[str, dict]] = {}  # key=YYYYMMDD → {ticker: {close, change_rate, trading_value}}

_per_ticker_cache_lock = threading.Lock()
_high_52w_cache: dict[tuple[str, str], Optional[float]] = {}  # (today_str, ticker) → high_52w
_avg_value_20d_cache: dict[tuple[str, str], Optional[float]] = {}  # (today_str, ticker) → avg_20d


def reset_cache() -> None:
    """테스트용: 모든 캐시 초기화."""
    with _market_data_cache_lock:
        _market_data_cache.clear()
        _per_ticker_market_cache.clear()
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
    data_source = settings.get("data_source", {}) or {}

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
        # 단위 2-9c — bulk 빈 응답 시 종목별 by_date 폴백 토글
        "fallback_per_ticker_enabled": bool(
            data_source.get("fallback_per_ticker_enabled", DEFAULT_FALLBACK_PER_TICKER_ENABLED)
        ),
        # 단위 2-9d — KIS market-cap ranking 으로 시총 보강 (옵션 B 자연 활성)
        "fallback_include_market_cap": bool(
            data_source.get(
                "fallback_include_market_cap", DEFAULT_FALLBACK_INCLUDE_MARKET_CAP
            )
        ),
        "kis_market_cap_top_n": int(
            data_source.get("kis_market_cap_top_n", DEFAULT_KIS_MARKET_CAP_TOP_N)
        ),
    }


# ===== pykrx lazy import =====


def _import_pykrx():
    from pykrx import stock as krx
    return krx


# ===== Bulk fetch: 시총 + OHLCV (시장당 1회) =====


def _fetch_per_ticker_today_data(ticker: str, today_str: str, krx) -> dict:
    """단위 2-9c — 종목별 1일치 OHLCV 폴백.

    bulk(`get_market_*_by_ticker`)이 KRX 정책 변경으로 빈 응답을 반환할 때,
    종목별 ``get_market_ohlcv_by_date(today, today, ticker)``로 보강.

    옵션 A (보수): 시가총액은 채우지 않음(``market_cap`` 키 부재) →
    후속 ``apply_attribute_filters``가 ``data_not_found`` 보수 탈락.
    본격 시총 보강은 단위 2-9d (KIS Open API ranking 대체).

    Args:
        ticker: 6자리 종목코드.
        today_str: ``YYYYMMDD``.
        krx: ``pykrx.stock`` 모듈 (호출자에서 lazy import 후 주입).

    Returns:
        ``{close, change_rate, trading_value}`` (성공 시).
        실패 또는 빈 응답 → ``{}`` (graceful 격리).
    """
    try:
        df = krx.get_market_ohlcv_by_date(today_str, today_str, ticker)
        if df is None or len(df) == 0:
            return {}
        cols = df.columns
        row = df.iloc[-1]  # 1일치라 1행이지만 안전하게 마지막 행 사용
        out: dict = {}
        if "종가" in cols:
            v = _safe_float(row["종가"])
            if v is not None:
                out["close"] = v
        if "등락률" in cols:
            v = _safe_float(row["등락률"])
            if v is not None:
                out["change_rate"] = v
        if "거래대금" in cols:
            v = _safe_float(row["거래대금"])
            if v is not None:
                out["trading_value"] = v
        return out
    except Exception as e:
        logger.debug(f"[universe_filters] per_ticker_today {ticker} 실패: {e}")
        return {}


def _fetch_market_data_bulk(
    today_str: str,
    tickers: Optional[list[str]] = None,
) -> dict[str, dict]:
    """KOSPI+KOSDAQ 일괄 시총/OHLCV 조회 → ticker 단위 dict.

    캐시 히트 시 즉시 반환.

    단위 2-9c — bulk 가 빈 응답이고 ``tickers``가 제공되었으며 토글이 켜진 경우,
    종목별 ``get_market_ohlcv_by_date`` 폴백으로 보강. 결과는 별도 캐시
    (`_per_ticker_market_cache`)에 격리해 bulk 캐시와 충돌 방지.

    Args:
        today_str: ``YYYYMMDD``.
        tickers: 폴백 후보 종목 리스트 (None 시 폴백 비활성). hard_cap 정합 위해
            ``MAX_FALLBACK_TICKERS=100`` 까지만 처리.

    Returns:
        ``{ticker: {"market_cap"?, "close", "change_rate", "trading_value"}}`` —
        폴백 모드에서는 ``market_cap`` 키가 없을 수 있음 (옵션 A 보수).
    """
    cached = _market_data_cache.get(today_str)
    if cached is not None:
        return _merge_with_fallback_cache(cached, today_str)

    with _market_data_cache_lock:
        cached = _market_data_cache.get(today_str)
        if cached is not None:
            return _merge_with_fallback_cache(cached, today_str)

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

        # 단위 2-9c — bulk 빈 응답 폴백
        _maybe_run_per_ticker_fallback(today_str, tickers, krx, result)
        return _merge_with_fallback_cache(result, today_str)


def _maybe_run_per_ticker_fallback(
    today_str: str,
    tickers: Optional[list[str]],
    krx,
    bulk_result: dict[str, dict],
) -> None:
    """단위 2-9c — bulk 빈 응답 시 종목별 폴백 실행. 결과는 ``_per_ticker_market_cache``에 격리.

    **반드시 ``bulk_result`` 가 빈 dict 일 때만 호출되어야 한다** (단위 2-9c 설계).
    이 함수는 현재 ``_fetch_market_data_bulk`` 한 곳에서만 호출되며, 라인 354 의
    ``if bulk_result: return`` 가드로 진입 자체가 차단된다. 다른 호출 지점이 추가될
    경우 이 가드 외부에서 호출되지 않도록 주의.

    호출 조건 (모두 AND):
        - bulk_result 가 빈 dict (KRX bulk API 차단 시그널)
        - tickers 인자 제공 (호출자가 후보 리스트 알고 있음)
        - 토글 ``fallback_per_ticker_enabled`` True
        - 같은 거래일 폴백 캐시 히트 X

    호출량 가드:
        ``MAX_FALLBACK_TICKERS=100`` (hard_cap 정합), 사이 ``FALLBACK_RATE_LIMIT_SEC=0.05``초.
    """
    if bulk_result:
        return  # bulk 정상 → 폴백 스킵
    if not tickers:
        return
    if today_str in _per_ticker_market_cache:
        return  # 이미 폴백 실행 (캐시 히트)

    # cfg 명시적 default 폴백 — settings 로드 실패 시 NameError 방어 (코더 검토 심각 #1, 단위 2-9d)
    cfg: dict = {
        "fallback_per_ticker_enabled": DEFAULT_FALLBACK_PER_TICKER_ENABLED,
        "fallback_include_market_cap": DEFAULT_FALLBACK_INCLUDE_MARKET_CAP,
        "kis_market_cap_top_n": DEFAULT_KIS_MARKET_CAP_TOP_N,
    }
    try:
        cfg = _load_filter_config()
    except Exception as e:
        logger.debug(
            f"[universe_filters] 폴백 토글 로드 실패: {e} — default 적용 (cfg 폴백)"
        )
    if not cfg.get("fallback_per_ticker_enabled", DEFAULT_FALLBACK_PER_TICKER_ENABLED):
        return  # 토글 OFF — 폴백 진입 X

    # 중복 제거 + 상한 적용 (입력 순서 보존)
    seen: set[str] = set()
    candidates: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            candidates.append(t)
        if len(candidates) >= MAX_FALLBACK_TICKERS:
            break

    n_input = len(candidates)
    logger.warning(
        f"[universe_filters] bulk 빈 응답 — 종목별 폴백 진입 N={n_input}건 "
        f"(상한 {MAX_FALLBACK_TICKERS}, sleep {FALLBACK_RATE_LIMIT_SEC}s/건)"
    )

    fallback: dict[str, dict] = {}

    # 단위 2-9d — KIS market-cap ranking 으로 시총 보강 (옵션 B 자연 활성)
    # KIS top N(default 200) 시총 데이터를 1회 호출 → candidates 중 매칭된 종목에 market_cap 채움.
    # 매칭 안 된 종목은 market_cap None 유지 → 옵션 A 보수 탈락 (PRD 4-1 정합).
    if cfg.get("fallback_include_market_cap", DEFAULT_FALLBACK_INCLUDE_MARKET_CAP):
        try:
            from closing_bet_system.collectors.kis_market_provider import (
                get_kis_market_provider,
            )
            mcap_top_n = int(
                cfg.get("kis_market_cap_top_n", DEFAULT_KIS_MARKET_CAP_TOP_N)
            )
            mcap_data = get_kis_market_provider().get_top_market_cap_data(top_n=mcap_top_n)
            matched = 0
            for ticker in candidates:
                if ticker in mcap_data:
                    fallback[ticker] = dict(mcap_data[ticker])
                    matched += 1
            logger.info(
                f"[universe_filters] 시총 보강 (단위 2-9d) — KIS top {mcap_top_n} 중 "
                f"{matched}/{n_input}건 매치"
            )
        except Exception as e:
            logger.warning(
                f"[universe_filters] 시총 보강 실패 → 옵션 A 보수 적용: {e}"
            )

    # 종목별 by_date 폴백 (close/change/value 채움 — 시총 보강 데이터 위에 누적)
    success = 0
    start = time.monotonic()
    for ticker in candidates:
        data = _fetch_per_ticker_today_data(ticker, today_str, krx)
        if data:
            # 시총 보강 데이터(있다면) 위에 OHLCV 추가
            fallback.setdefault(ticker, {}).update(data)
            success += 1
        try:
            time.sleep(FALLBACK_RATE_LIMIT_SEC)
        except Exception:
            pass
    elapsed = time.monotonic() - start

    _per_ticker_market_cache[today_str] = fallback
    logger.info(
        f"[universe_filters] 종목별 폴백 완료 — {success}/{n_input}건 성공, "
        f"{elapsed:.2f}초 소요 ({today_str})"
    )


def _merge_with_fallback_cache(bulk: dict[str, dict], today_str: str) -> dict[str, dict]:
    """단위 2-9c — bulk 캐시 + 폴백 캐시 통합 view 반환.

    bulk 가 비어 있으면 폴백 캐시만 반환.
    bulk 정상 시 폴백은 사용 안 함(캐시도 비어 있음).

    동시성: ``_per_ticker_market_cache`` 는 ``_market_data_cache_lock`` 안에서만 쓰여지며,
    본 함수는 fast-path(락 외)와 lock 보유 중 모두에서 호출된다. ``threading.Lock`` 은
    non-reentrant 라 lock 보유 중에 lock 재획득 시 데드락 발생 → lock 미사용.
    CPython GIL 이 dict.get 을 atomic 보장하므로 단일 프로세스에서 안전.
    """
    fb = _per_ticker_market_cache.get(today_str)
    if not fb:
        return bulk
    if not bulk:
        return fb
    # 양쪽 모두 데이터가 있는 비정상 상황 — bulk 우선
    merged = dict(fb)
    merged.update(bulk)
    return merged


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
    severity_map: Optional[dict[str, int]] = None,
) -> tuple[list[str], dict[str, str]]:
    """PRD 4-1 속성 기반 필터 적용 — KIND severity / 시총/주가/상한가/52주 고점.

    KIND severity ≥ 3 (관리/거래정지/투자위험) → PRD 4-1 명시 "제외" 하드 룰로
    속성 필터 첫 단계에서 사전 차단 (first-rejection-only 정합).

    Args:
        tickers: 6자리 종목코드 리스트.
        today: 평가 기준 거래일. None 시 KST 오늘.
        config: 필터 임계값 dict (None 시 settings.yaml 로드).
        severity_map: KIND 시장경보 ``{ticker: severity}``. None/빈 dict 시 KIND 사전 제외 스킵.

    Returns:
        (passed, rejected) — passed: list[str], rejected: ``{ticker: reason}``
    """
    if not tickers:
        return [], {}

    today = today or now_kst().date()
    today_str = today.strftime("%Y%m%d")
    cfg = config or _load_filter_config()

    passed: list[str] = []
    rejected: dict[str, str] = {}
    original_total = len(tickers)
    kind_rejected_count = 0  # 로그 분해용 (속성 탈락과 분리 표시)

    # 0. KIND severity ≥ 3 사전 제외 (PRD 4-1 — "관리/투자경고/거래정지 제외")
    # 속성 필터 첫 단계에서 차단해 후속 fetch 비용 절감.
    if severity_map:
        survivors: list[str] = []
        for t in tickers:
            sev = int(severity_map.get(t, 0) or 0)
            if sev >= SEVERITY_EXCLUDE_THRESHOLD:
                rejected[t] = f"kind_severity_{sev}"
            else:
                survivors.append(t)
        kind_rejected_count = len(rejected)
        if kind_rejected_count:
            logger.info(
                f"[universe_filters] KIND severity ≥ {SEVERITY_EXCLUDE_THRESHOLD} 사전 제외 — "
                f"{kind_rejected_count}/{original_total} 탈락"
            )
        tickers = survivors
        if not tickers:
            return [], rejected

    # 단위 2-9c — KIND 사전 제외 후 survivors 를 폴백 후보로 전달.
    # bulk 정상 시 가드(`if not bulk_result`)로 폴백 스킵.
    market_data = _fetch_market_data_bulk(today_str, tickers=tickers)

    for ticker in tickers:
        data = market_data.get(ticker)
        if not data:
            rejected[ticker] = "data_not_found"
            continue

        # 1. 시가총액 — 옵션 A 보수: 시총 불명(폴백 경로 등)은 data_not_found 탈락 (단위 2-9c)
        market_cap = data.get("market_cap")
        if market_cap is None:
            rejected[ticker] = "data_not_found"
            continue
        if market_cap < cfg["min_market_cap"]:
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

    attr_rejected_count = len(rejected) - kind_rejected_count
    logger.info(
        f"[universe_filters] 속성 필터: {len(passed)}/{original_total} 통과 "
        f"(KIND 사전 제외 {kind_rejected_count} + 속성 탈락 {attr_rejected_count})"
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

    # 단위 2-9c — bulk 빈 응답 시 입력 tickers 로 종목별 폴백.
    market_data = _fetch_market_data_bulk(today_str, tickers=tickers)

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
    severity_map: Optional[dict[str, int]] = None,
) -> tuple[list[str], dict[str, str]]:
    """PRD 4-1 속성 + 4-4 유동성 통합 필터.

    KIND severity → 속성 → 유동성 순으로 적용. 한 종목이 여러 필터 위반 시 첫 위반만 기록.

    Args:
        tickers: 6자리 종목코드 리스트.
        today: 평가 기준 거래일.
        config: 필터 임계값 dict.
        severity_map: KIND 시장경보 ``{ticker: severity}``. severity ≥ 3 사전 제외 (PRD 4-1).
            None/빈 dict 시 KIND 단계 스킵 (기존 동작 유지 — 회귀).

    Returns:
        (passed, rejected) — 두 단계 통합 dict
    """
    if not tickers:
        return [], {}

    today = today or now_kst().date()
    cfg = config or _load_filter_config()

    after_attr, rejected_attr = apply_attribute_filters(
        tickers, today=today, config=cfg, severity_map=severity_map
    )
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
