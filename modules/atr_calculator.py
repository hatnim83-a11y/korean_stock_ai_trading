"""
atr_calculator.py - ATR(Average True Range) 계산 모듈

v17 분할 진입 + 불타기 + ATR 트레일링 도입의 일부.
매수 시점에 종목별 ATR(14일)을 박제하여 트레일링 폭 계산에 사용.

설계 원칙:
- pykrx 우선, KIS API 폴백 (양쪽 실패 시 0.0 반환 → 호출 측에서 고정값 폴백)
- 일 단위 캐시 (동일 종목 중복 호출 방지, key=(stock_code, KST date))
- 09:20 prefetch 잡으로 후보 종목 일괄 비동기 계산 (매수 골든타임 침해 방지)
- 종목별 0.5초 타임아웃 (전체 5종목 1초 내 완료 목표)

ATR 정의:
- True Range = max(high - low, |high - prev_close|, |low - prev_close|)
- ATR(N) = SMA(TR, N)  -- 본 모듈은 단순 평균 사용 (Wilder smoothing은 Phase 2)
"""

from __future__ import annotations

import asyncio
import threading
from datetime import date as _date
from typing import Optional

from logger import logger
from config import now_kst


# ===== 캐시 (모듈 내부, 일 단위 만료) =====
# key: (stock_code, KST date string 'YYYY-MM-DD'), value: float ATR 값
_atr_cache: dict[tuple[str, str], float] = {}
_cache_lock = threading.Lock()


def _cache_key(stock_code: str) -> tuple[str, str]:
    """캐시 키 생성 (종목코드 + KST 오늘 날짜)."""
    return (stock_code, now_kst().strftime("%Y-%m-%d"))


def _cache_get(stock_code: str) -> Optional[float]:
    with _cache_lock:
        return _atr_cache.get(_cache_key(stock_code))


def _cache_set(stock_code: str, atr_value: float) -> None:
    with _cache_lock:
        # 자정 경계: 새 날짜 키 추가 시 어제 키들 정리
        today_str = now_kst().strftime("%Y-%m-%d")
        stale_keys = [k for k in _atr_cache.keys() if k[1] != today_str]
        for k in stale_keys:
            _atr_cache.pop(k, None)
        _atr_cache[_cache_key(stock_code)] = atr_value


def _compute_atr_from_ohlc(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """OHLC 시계열에서 ATR(period) 계산.

    Args:
        highs, lows, closes: 시간 오름차순 리스트 (최소 period+1개 필요)
        period: ATR 기간 (14)

    Returns:
        ATR 값 (>0). 데이터 부족 시 0.0.
    """
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return 0.0

    true_ranges: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    # 최근 period개의 TR 평균 (SMA)
    if len(true_ranges) < period:
        return 0.0
    recent_tr = true_ranges[-period:]
    return sum(recent_tr) / period


def _compute_atr_via_pykrx(stock_code: str, period: int = 14) -> float:
    """pykrx로 ATR 계산. 실패 시 0.0 반환."""
    try:
        from pykrx import stock as krx
    except ImportError:
        logger.debug(f"[ATR] pykrx 미설치 → KIS API 폴백 시도: {stock_code}")
        return 0.0

    try:
        # 충분한 기간 조회 (period × 3 영업일 ≈ 42일 → 60 calendar day 조회로 여유)
        today = now_kst().strftime("%Y%m%d")
        from datetime import timedelta
        start = (now_kst() - timedelta(days=60)).strftime("%Y%m%d")

        df = krx.get_market_ohlcv_by_date(start, today, stock_code)
        if df is None or df.empty or len(df) < period + 1:
            logger.debug(f"[ATR] pykrx 데이터 부족: {stock_code} rows={0 if df is None else len(df)}")
            return 0.0

        highs = df["고가"].tolist()
        lows = df["저가"].tolist()
        closes = df["종가"].tolist()
        return _compute_atr_from_ohlc(highs, lows, closes, period)
    except Exception as e:
        logger.debug(f"[ATR] pykrx 실패 {stock_code}: {e}")
        return 0.0


def _compute_atr_via_kis(stock_code: str, period: int = 14) -> float:
    """KIS API itempricelist로 ATR 계산. 실패 시 0.0 반환."""
    try:
        from modules.kis_api import KISApi
        api = KISApi()
        # 일봉 30~40일 조회 (KIS itempricelist는 최대 100일 지원)
        ohlc_rows = api.get_daily_price(stock_code, period_count=period + 10)
        if not ohlc_rows or len(ohlc_rows) < period + 1:
            return 0.0

        # KIS 응답은 보통 최근→과거 역순. 시간 오름차순 정렬 필요.
        # 컬럼명은 KIS 응답 스펙에 따라 조정 (stck_hgpr/stck_lwpr/stck_clpr).
        rows_sorted = sorted(ohlc_rows, key=lambda r: r.get("stck_bsop_date") or r.get("date") or "")
        highs, lows, closes = [], [], []
        for r in rows_sorted:
            h = r.get("stck_hgpr") or r.get("high") or 0
            l = r.get("stck_lwpr") or r.get("low") or 0
            c = r.get("stck_clpr") or r.get("close") or 0
            try:
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
            except (ValueError, TypeError):
                continue

        return _compute_atr_from_ohlc(highs, lows, closes, period)
    except Exception as e:
        logger.debug(f"[ATR] KIS 폴백 실패 {stock_code}: {e}")
        return 0.0


def compute_atr(stock_code: str, period: int = 14) -> float:
    """단일 종목 ATR 동기 계산.

    캐시 확인 → pykrx 시도 → KIS 폴백 → 캐시 저장.
    모두 실패 시 0.0 반환 (호출 측에서 트레일링 고정값 폴백).

    Args:
        stock_code: 6자리 종목코드
        period: ATR 기간 (기본 14)

    Returns:
        ATR 값 (원 단위). 실패 시 0.0.
    """
    if not stock_code or len(stock_code) != 6:
        return 0.0

    cached = _cache_get(stock_code)
    if cached is not None:
        return cached

    atr_value = _compute_atr_via_pykrx(stock_code, period)
    if atr_value <= 0:
        atr_value = _compute_atr_via_kis(stock_code, period)

    _cache_set(stock_code, atr_value)
    if atr_value > 0:
        logger.info(f"[ATR] {stock_code} ATR({period})={atr_value:.2f}")
    else:
        logger.warning(f"[ATR] {stock_code} 계산 실패 — 트레일링 고정값 폴백")
    return atr_value


async def compute_atr_batch(
    stock_codes: list[str], period: int = 14, timeout: float = 0.5
) -> dict[str, float]:
    """여러 종목 ATR 일괄 비동기 계산.

    asyncio.gather + 종목별 timeout으로 5종목 동시 처리 (전체 1초 내 목표).
    매수 골든타임(09:25) 침해 방지 — 09:20 prefetch 잡에서 호출.

    Args:
        stock_codes: 종목코드 리스트
        period: ATR 기간
        timeout: 종목별 타임아웃 (초)

    Returns:
        {stock_code: atr_value} 딕셔너리. 실패 종목은 0.0.
    """

    async def _one(code: str) -> tuple[str, float]:
        try:
            value = await asyncio.wait_for(
                asyncio.to_thread(compute_atr, code, period), timeout=timeout
            )
            return code, value
        except asyncio.TimeoutError:
            logger.warning(f"[ATR] {code} 타임아웃 ({timeout}초)")
            return code, 0.0
        except Exception as e:
            logger.warning(f"[ATR] {code} 예외: {e}")
            return code, 0.0

    results = await asyncio.gather(*[_one(c) for c in stock_codes], return_exceptions=False)
    return dict(results)


def clear_cache() -> int:
    """캐시 전체 초기화 (테스트/디버그용). 삭제된 항목 수 반환."""
    with _cache_lock:
        count = len(_atr_cache)
        _atr_cache.clear()
        return count


def get_cache_stats() -> dict:
    """캐시 상태 스냅샷 (디버그/모니터링용)."""
    with _cache_lock:
        return {
            "size": len(_atr_cache),
            "today_keys": [k[0] for k in _atr_cache.keys() if k[1] == now_kst().strftime("%Y-%m-%d")],
        }
