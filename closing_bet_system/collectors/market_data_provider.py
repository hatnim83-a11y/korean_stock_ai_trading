"""closing_bet_system.collectors.market_data_provider

종가베팅 외부 리스크 평가용 시장 데이터 수집.

PRD 4-2/4-3 외부 리스크 필터(``OvernightRiskFilter``) 가 사용하는 시장 키:
- ``us_futures_change_pct`` (미국 선물 등락률, 소수)
- ``vkospi`` (V-KOSPI 지수)
- ``kospi_change_pct`` (KOSPI 전일 대비, 소수)
- ``usd_krw_change_pct`` (USD/KRW 전일 대비, 소수)

추가로 ``MainOrchestrator._extract_market_regime`` 가 ``candidate_features.market_regime``
저장에 사용하는 키:
- ``kospi_above_200ma`` (Phase 2 placeholder, None)
- ``foreign_5d_cumulative`` (Phase 2 placeholder, None)

**Phase 1 결손 정책**: 한 키 수집 실패 시 None 반환 → ``OvernightRiskFilter`` 가
해당 룰 비활성. 한 키 실패가 다른 키에 영향 없도록 모든 호출 try/except 격리.

데이터 출처:
| 키 | 1차 | 폴백 |
|---|---|---|
| KOSPI | KISApi.get_index_price("0001") | None |
| V-KOSPI | yfinance "^VKOSPI" | None |
| 미국 선물 | yfinance "ES=F" | None |
| USD-KRW | yfinance "KRW=X" | None |

호출 시점: APScheduler 15:10 (``MainOrchestrator.run_daily_pipeline``).
같은 거래일 다회 호출 시 in-memory 캐시 히트.
"""

from __future__ import annotations

import sys
import threading
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst


# ===== 모듈 상수 =====

# yfinance 심볼 (시장 폐장 시 None 가능)
YF_VKOSPI_SYMBOL = "^VKOSPI"
YF_US_FUTURES_SYMBOL = "ES=F"        # E-mini S&P 500 futures
YF_USD_KRW_SYMBOL = "KRW=X"          # USD/KRW

# yfinance 호출 timeout (초)
_YF_HISTORY_PERIOD = "5d"             # 최근 5일 (전일 비교용)


# ===== 캐시 =====

_cache_lock = threading.Lock()
_cache: dict[str, dict] = {}


def _cache_key(target_date: date_cls) -> str:
    return target_date.isoformat()


def reset_cache() -> None:
    """테스트용: 캐시 초기화."""
    with _cache_lock:
        _cache.clear()


# ===== 메인 진입점 =====


def get_market_data() -> dict:
    """오늘 거래일의 시장 데이터 dict 반환.

    실패 키는 None 으로 채워 ``OvernightRiskFilter`` 결손 정책에 의해 룰 비활성.

    Returns:
        ``{us_futures_change_pct, vkospi, kospi_change_pct, usd_krw_change_pct,
        kospi_above_200ma, foreign_5d_cumulative}`` — 모든 값 Optional[float]
    """
    today = now_kst().date()
    key = _cache_key(today)

    cached = _cache.get(key)
    if cached is not None:
        logger.debug(f"[market_data_provider] 캐시 히트 ({key})")
        return dict(cached)

    data = {
        "kospi_change_pct": _safe_call(_fetch_kospi_change_pct, "kospi"),
        "vkospi": _safe_call(_fetch_vkospi, "vkospi"),
        "us_futures_change_pct": _safe_call(_fetch_us_futures_change_pct, "us_futures"),
        "usd_krw_change_pct": _safe_call(_fetch_usd_krw_change_pct, "usd_krw"),
        # Phase 2 placeholder (별도 collector 도입 시 채움)
        "kospi_above_200ma": None,
        "foreign_5d_cumulative": None,
    }

    with _cache_lock:
        _cache[key] = dict(data)

    # Phase 1 가용 키 4종만 카운트 (placeholder 2종은 항상 None 이라 모니터링에서 제외)
    market_keys = ("kospi_change_pct", "vkospi", "us_futures_change_pct", "usd_krw_change_pct")
    available = [k for k in market_keys if data.get(k) is not None]
    logger.info(
        f"[market_data_provider] 시장 데이터 수집 완료 ({key}) — "
        f"{len(available)}/{len(market_keys)} 가용 (가용: {available})"
    )
    return data


# ===== 안전 호출 헬퍼 =====


def _safe_call(fn, label: str):
    """fn 실행 + 예외 흡수 → 실패 시 None."""
    try:
        result = fn()
        return result
    except Exception as e:
        logger.warning(f"[market_data_provider] {label} 수집 예외: {e}")
        return None


# ===== 개별 수집기 =====


def _fetch_kospi_change_pct() -> Optional[float]:
    """KOSPI 전일 대비 등락률 (소수). KIS API 활용.

    ``KISApi.get_index_price`` 가 등락률을 % 단위(예: -1.5)로 반환하므로 /100 변환.
    """
    from closing_bet_system.infra.kis_client import get_kis_api

    kis = get_kis_api()
    result = kis.get_index_price("0001")
    if not result or "change_rate" not in result:
        return None
    rate = result.get("change_rate")
    if rate is None:
        return None
    # KIS 등락률은 % 단위 → 외부 리스크 필터는 소수 (-0.02 = -2%) → /100
    return float(rate) / 100.0


def _fetch_vkospi() -> Optional[float]:
    """V-KOSPI 현재 수치. yfinance "^VKOSPI" 활용.

    Phase 2 에서 KIS 자체 변동성 지수 또는 외부 데이터소스로 대체 검토.
    """
    return _yf_latest_close(YF_VKOSPI_SYMBOL)


def _fetch_us_futures_change_pct() -> Optional[float]:
    """미국 선물(E-mini S&P 500) 전일 대비 등락률 (소수). yfinance ES=F."""
    return _yf_change_pct(YF_US_FUTURES_SYMBOL)


def _fetch_usd_krw_change_pct() -> Optional[float]:
    """USD/KRW 환율 전일 대비 등락률 (소수, 양수=원화 약세). yfinance KRW=X."""
    return _yf_change_pct(YF_USD_KRW_SYMBOL)


# ===== yfinance 헬퍼 =====


def _yf_latest_close(symbol: str) -> Optional[float]:
    """yfinance 최근 종가. 데이터 없거나 NaN 시 None."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[market_data_provider] yfinance 미설치 — None 반환")
        return None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=_YF_HISTORY_PERIOD)
        if hist is None or hist.empty:
            return None
        last_close = hist["Close"].iloc[-1]
        return _safe_num(last_close)
    except Exception as e:
        logger.debug(f"[market_data_provider] yfinance {symbol} 조회 예외: {e}")
        return None


def _yf_change_pct(symbol: str) -> Optional[float]:
    """yfinance 최근 종가 / 전일 종가 - 1 (소수). 데이터 부족 시 None."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=_YF_HISTORY_PERIOD)
        if hist is None or len(hist) < 2:
            return None
        last_close = _safe_num(hist["Close"].iloc[-1])
        prev_close = _safe_num(hist["Close"].iloc[-2])
        if last_close is None or prev_close is None or prev_close <= 0:
            return None
        return round((last_close - prev_close) / prev_close, 6)
    except Exception as e:
        logger.debug(f"[market_data_provider] yfinance {symbol} 변동률 예외: {e}")
        return None


def _safe_num(value) -> Optional[float]:
    """NaN-safe 숫자 변환 (yfinance DataFrame 값 방어).

    ``modules/post_trade_analyzer/price_tracker.py`` 의 ``_safe_num`` 패턴과 일치.
    """
    try:
        import math
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None
