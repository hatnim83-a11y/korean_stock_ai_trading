"""
price_tracker.py - 매도 후 주가 추이 수집

yfinance 우선, KIS API 폴백으로 매도 후 N영업일 일봉 데이터를 수집한다.
"""

import time
from datetime import date, timedelta
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst, is_trading_day

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    import pandas as pd
    _has_pandas = True
except ImportError:
    _has_pandas = False


def _safe_num(v, default=0.0, as_int=False):
    """NaN-safe 숫자 변환 (yfinance DataFrame 값 방어)"""
    if _has_pandas and pd.isna(v):
        return int(default) if as_int else float(default)
    try:
        return int(v) if as_int else float(v)
    except (ValueError, TypeError):
        return int(default) if as_int else float(default)


class PostTradePriceTracker:
    """매도 후 주가 추이 수집기"""

    def __init__(self):
        self._kis_api = None

    def _get_kis_api(self):
        """KIS API 싱글톤 (lazy init)"""
        if self._kis_api is None:
            from modules.stock_screener.kis_api import KISApi
            self._kis_api = KISApi()
        return self._kis_api

    def collect_post_trade_prices(
        self,
        stock_code: str,
        sell_date: date,
        sell_price: float,
        num_days: int = 5,
    ) -> list[dict]:
        """
        매도 후 N영업일간 주가 추이 수집

        Args:
            stock_code: 종목코드
            sell_date: 매도일
            sell_price: 매도가
            num_days: 수집할 영업일 수

        Returns:
            [{stock_code, sell_date, check_date, days_after_sell,
              close_price, high_price, low_price, volume, change_from_sell}, ...]
        """
        # 매도일 다음날부터 num_days 영업일의 날짜 목록 계산
        trading_days = self._get_trading_days_after(sell_date, num_days)
        if not trading_days:
            logger.warning(f"[PostTracker] {stock_code}: 매도 후 영업일 계산 실패")
            return []

        # yfinance로 일봉 조회
        daily_data = self._fetch_daily_prices(stock_code, sell_date, trading_days[-1])

        if not daily_data:
            logger.warning(f"[PostTracker] {stock_code}: 주가 데이터 조회 실패")
            return []

        results = []
        day_count = 0
        for td in trading_days:
            td_str = td.strftime("%Y-%m-%d")
            if td_str in daily_data:
                day_count += 1
                row = daily_data[td_str]
                close = row.get("close", 0)
                change = ((close - sell_price) / sell_price * 100) if sell_price > 0 else 0

                results.append({
                    "stock_code": stock_code,
                    "sell_date": sell_date.isoformat(),
                    "check_date": td_str,
                    "days_after_sell": day_count,
                    "close_price": close,
                    "high_price": row.get("high", 0),
                    "low_price": row.get("low", 0),
                    "volume": int(row.get("volume", 0)),
                    "change_from_sell": round(change, 2),
                })

        logger.info(f"[PostTracker] {stock_code}: 매도 후 {len(results)}일 주가 수집 완료")
        return results

    def _get_trading_days_after(self, base_date: date, num_days: int) -> list[date]:
        """base_date 다음 영업일부터 num_days개 영업일 목록"""
        result = []
        current = base_date + timedelta(days=1)
        today = now_kst().date()
        max_iter = num_days * 3  # 주말/공휴일 대비 여유

        for _ in range(max_iter):
            if current > today:
                break
            if is_trading_day(current):
                result.append(current)
                if len(result) >= num_days:
                    break
            current += timedelta(days=1)

        return result

    def _fetch_daily_prices(
        self, stock_code: str, sell_date: date, end_date: date
    ) -> dict:
        """
        일봉 데이터 조회. yfinance 우선, 실패 시 KIS API 폴백.

        Returns:
            {"YYYY-MM-DD": {"open":..., "high":..., "low":..., "close":..., "volume":...}, ...}
        """
        # 1) yfinance
        if YFINANCE_AVAILABLE:
            data = self._fetch_yfinance(stock_code, sell_date, end_date)
            if data:
                return data

        # 2) KIS API fallback
        return self._fetch_kis(stock_code)

    def _fetch_yfinance(
        self, stock_code: str, sell_date: date, end_date: date
    ) -> Optional[dict]:
        """yfinance에서 일봉 조회"""
        try:
            start_str = (sell_date + timedelta(days=1)).isoformat()
            end_str = (end_date + timedelta(days=1)).isoformat()  # yfinance end는 exclusive

            for suffix in (".KS", ".KQ"):
                ticker = yf.Ticker(f"{stock_code}{suffix}")
                df = ticker.history(start=start_str, end=end_str)
                if df is not None and not df.empty:
                    df.columns = df.columns.str.lower()
                    result = {}
                    for idx, row in df.iterrows():
                        date_str = idx.strftime("%Y-%m-%d")
                        result[date_str] = {
                            "open": _safe_num(row.get("open", 0)),
                            "high": _safe_num(row.get("high", 0)),
                            "low": _safe_num(row.get("low", 0)),
                            "close": _safe_num(row.get("close", 0)),
                            "volume": _safe_num(row.get("volume", 0), as_int=True),
                        }
                    if result:
                        return result

        except Exception as e:
            logger.debug(f"[PostTracker] yfinance 실패 ({stock_code}): {e}")

        return None

    def _fetch_kis(self, stock_code: str) -> dict:
        """KIS API 일봉 조회 (폴백)"""
        try:
            kis = self._get_kis_api()
            daily = kis.get_daily_price(stock_code, period="D", count=10)
            if not daily:
                return {}

            result = {}
            for d in daily:
                raw_date = d.get("date", "")
                if len(raw_date) == 8:
                    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                else:
                    date_str = raw_date
                result[date_str] = {
                    "open": d.get("open", 0),
                    "high": d.get("high", 0),
                    "low": d.get("low", 0),
                    "close": d.get("close", 0),
                    "volume": d.get("volume", 0),
                }
            return result

        except Exception as e:
            logger.debug(f"[PostTracker] KIS API 실패 ({stock_code}): {e}")
            return {}
