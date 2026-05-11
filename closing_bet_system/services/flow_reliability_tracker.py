"""closing_bet_system.services.flow_reliability_tracker

단위 2-3 (Phase 2 옵션 H, 2026-05-11 도입) — flow 데이터 신뢰도 추적기.

**책임**:
1. 네이버 금융 ``finance.naver.com/item/frgn.naver`` 크롤링으로 종목별 일별 외인/기관
   순매매 수량(KRX 확정값) 수집.
2. ``flow_data_reliability`` 테이블 N영업일 윈도우 일치율 집계.

**책임 외** (다른 단위에서):
- 매칭 잡 (cron 19:27) → ``main_orchestrator.run_flow_reliability_check`` (Step 3)
- INSERT 자체 → ``candidate_logger.log_flow_reliability`` (기존 함수 재사용)

**데이터 소스 / 정책**:
- 네이버 frgn.naver: KRX 확정값 발표 후 (T일 18~19시 KST) 갱신 → 19:27 잡 시점 사용 가능
- KOSPI/KOSDAQ 무관 단일 endpoint
- 단위 = 주(qty). candidate_features.inst_net_buy_estimated 는 원(금액)이지만 매칭은
  부호 비교만 하므로 단위 불일치 무관 (``_direction_match`` 는 sign 비교).

**관련 코드**:
- ``closing_bet_system/storage/candidate_logger.py:435`` (log_flow_reliability)
- ``closing_bet_system/storage/db.py:323`` (flow_data_reliability 스키마)
- ``modules/stock_screener/kis_api.py`` (HHPTJ04160200 추정치 소스 — 단위 2-3 Step 1)
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst, is_trading_day


__all__ = [
    "fetch_naver_confirmed_flow",
    "compute_direction_match_rate",
]


# ===== 모듈 상수 =====

_NAVER_FRGN_URL = "https://finance.naver.com/item/frgn.naver"
_NAVER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_DEFAULT_DB_PATH = "data/closing_bet.db"
_DEFAULT_SLEEP_PER_TICKER = 0.4   # 네이버 차단 회피
_DEFAULT_RETRY = 3                # 셀트리온 사례 패턴
_DEFAULT_BACKOFF = 5.0
_DEFAULT_TIMEOUT = 15.0

# 활성화 임계값 — PRD Phase 2-6 ("flow_reliability 70% 이상 검증 후 layer1_weight 활성화")
MATCH_RATE_THRESHOLD = 0.7

# 컬럼 매핑 (indicator → SQL column) — f-string SQL injection 방어 + 확장 안전성
_INDICATOR_COL_MAP = {
    "inst": "inst_direction_match",
    "foreign": "foreign_direction_match",
}


# ===== 1. 네이버 확정값 수집 =====


def fetch_naver_confirmed_flow(
    trade_date: date,
    tickers: Iterable[str],
    sleep_per_ticker: float = _DEFAULT_SLEEP_PER_TICKER,
    retry: int = _DEFAULT_RETRY,
    backoff: float = _DEFAULT_BACKOFF,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """네이버 frgn.naver 크롤링으로 종목별 일별 외인/기관 순매매 수량 수집.

    KOSPI/KOSDAQ 무관 단일 endpoint. 수량 단위(주). 부호 비교 목적.

    Args:
        trade_date: 매칭 대상 영업일 (네이버 표 형식 ``2026.05.08`` 으로 비교).
        tickers: 6자리 종목코드 iterable. 중복은 호출자 책임.
        sleep_per_ticker: ticker 간 지연 (네이버 차단 회피).
        retry: HTTP 실패 시 재시도 횟수.
        backoff: 재시도 사이 대기 (초).
        timeout: 단일 HTTP 요청 timeout.

    Returns:
        ``{ticker: {"inst_confirmed_qty": int, "foreign_confirmed_qty": int, "close": int}}``

        해당 일자 데이터가 표에 없거나 ticker 무효 시 dict 항목 누락 (graceful).
    """
    target_date_str = trade_date.strftime("%Y.%m.%d")
    headers = {"User-Agent": _NAVER_USER_AGENT}
    out: dict[str, dict] = {}

    ticker_list = list(tickers)
    total = len(ticker_list)
    success = 0

    for i, ticker in enumerate(ticker_list, 1):
        if not _is_valid_ticker(ticker):
            logger.warning(f"[reliability] {ticker!r} 유효하지 않은 종목코드 — 스킵")
            continue

        row_data = _fetch_naver_for_ticker(
            ticker=ticker,
            target_date_str=target_date_str,
            headers=headers,
            retry=retry,
            backoff=backoff,
            timeout=timeout,
        )
        if row_data is not None:
            out[ticker] = row_data
            success += 1

        # 마지막 ticker 직후엔 sleep 생략
        if i < total:
            time.sleep(sleep_per_ticker)

    logger.info(
        f"[reliability] 네이버 확정값 수집: {success}/{total}건 ({trade_date.isoformat()})"
    )
    return out


def _fetch_naver_for_ticker(
    ticker: str,
    target_date_str: str,
    headers: dict,
    retry: int,
    backoff: float,
    timeout: float,
) -> Optional[dict]:
    """단일 ticker 네이버 호출 + 파싱. 실패 시 ``None``."""
    last_err: Optional[Exception] = None
    for attempt in range(1, retry + 1):
        try:
            r = requests.get(
                _NAVER_FRGN_URL,
                params={"code": ticker},
                headers=headers,
                timeout=timeout,
            )
            r.raise_for_status()
            r.encoding = "euc-kr"
            return _parse_naver_html(r.text, target_date_str)
        except Exception as e:
            last_err = e
            if attempt < retry:
                logger.warning(
                    f"[reliability] {ticker} 재시도 {attempt}/{retry}: {e}"
                )
                time.sleep(backoff)

    logger.error(f"[reliability] {ticker} 최종 실패 ({retry}회 재시도): {last_err}")
    return None


def _parse_naver_html(html_text: str, target_date_str: str) -> Optional[dict]:
    """네이버 frgn.naver HTML 파싱. target_date_str 일치 행만 추출.

    표 인덱스: tables[1] 외인/기관 매매현황. row.find_all('td'):
    [0]=날짜 / [1]=종가 / [2]=전일비 / [3]=등락률 / [4]=거래량 / [5]=기관 / [6]=외국인 / ...

    **방어 정책** (네이버 HTML 개편/차단 페이지 대응):
    1차: ``len(tables) < 2`` 가드 — 표 수 변경 시 None 반환.
    2차: ``date_str != target_date_str`` 비교 — tables[1]이 다른 표를 가리킬 때
         날짜 형식 불일치로 행 매칭 실패 → 빈 dict 누락 (graceful).
    날짜 형식이 변경되면 두 방어막 모두 우회 가능 — 장기 모니터링 필요.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    tables = soup.find_all("table", class_="type2")
    if len(tables) < 2:
        # 종목코드 무효, 차단 페이지, HTML 구조 변경 등
        return None

    rows = tables[1].find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        date_str = cells[0].get_text(strip=True)
        if date_str != target_date_str:
            continue

        close = _parse_int(cells[1].get_text(strip=True))
        inst = _parse_int(cells[5].get_text(strip=True))
        foreign = _parse_int(cells[6].get_text(strip=True))
        return {
            "inst_confirmed_qty": inst,
            "foreign_confirmed_qty": foreign,
            "close": close,
        }

    # target_date_str 일치 행 없음 (당일 데이터 미갱신 또는 일자 표기 불일치)
    return None


def _parse_int(text: str) -> int:
    """네이버 표 셀 텍스트 → int.

    예: ``'+1,074,644'`` → 1074644 / ``'-9,595,937'`` → -9595937 / ``'232,500'`` → 232500
    빈 문자열, 변환 실패 시 0.
    """
    if not text:
        return 0
    cleaned = text.replace(",", "").replace("+", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _is_valid_ticker(ticker: str) -> bool:
    """6자리 숫자 검증."""
    return isinstance(ticker, str) and ticker.isdigit() and len(ticker) == 6


# ===== 2. 일치율 집계 =====


def compute_direction_match_rate(
    window_days: int = 7,
    indicator: str = "inst",
    db_path: str = _DEFAULT_DB_PATH,
    end_date: Optional[date] = None,
) -> dict:
    """flow_data_reliability 테이블 N영업일 윈도우 방향 일치율 집계.

    **윈도우 정책**: ``end_date`` **포함** 직전 N영업일.
    예: ``window_days=7, end_date=2026-05-12(화)`` → ``2026-05-02 ~ 2026-05-12`` 중
    영업일 7건 (5/2,5/4,5/6,5/7,5/8,5/11,5/12). SQL ``BETWEEN start AND end`` 양끝
    포함이며, 역산 루프는 end 자체를 1회로 카운트하여 정확히 N영업일 윈도우.

    Step 3 매칭 잡(19:27)은 오늘 INSERT 직후 본 함수를 호출 → 오늘 데이터 포함됨.

    Args:
        window_days: 윈도우 영업일 수 (>= 1).
        indicator: ``'inst'`` | ``'foreign'``.
        db_path: SQLite 경로 (기본 ``data/closing_bet.db``). 상대 경로는 cwd 기준.
        end_date: 윈도우 종료 일자(포함). ``None`` 이면 ``now_kst().date()``.

    Raises:
        ValueError: indicator 또는 window_days 잘못된 값.
        sqlite3.OperationalError: DB 파일/테이블 없음 (전파 — 호출자 try/except 권장).

    Returns:
        ``{
            "window_days": N,
            "indicator": str,
            "start_date": str (ISO),
            "end_date": str (ISO),
            "n_samples": int,
            "n_match": int,
            "match_rate": float | None,
            "passes_70_threshold": bool,
        }``
    """
    if indicator not in _INDICATOR_COL_MAP:
        raise ValueError(
            f"indicator 는 {sorted(_INDICATOR_COL_MAP)} 중 하나: got {indicator!r}"
        )
    if window_days < 1:
        raise ValueError(f"window_days >= 1: got {window_days}")

    col = _INDICATOR_COL_MAP[indicator]
    end = end_date or now_kst().date()

    # 영업일 N일 역산 — end 포함 N영업일.
    # end 자체가 영업일이면 1회로 카운트, 아니면 카운트하지 않고 더 거슬러 올라감.
    start = end
    remaining = window_days - 1 if is_trading_day(end) else window_days
    while remaining > 0:
        start = start - timedelta(days=1)
        if is_trading_day(start):
            remaining -= 1

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        row = cur.execute(
            f"""
            SELECT COUNT(*), SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END)
            FROM flow_data_reliability
            WHERE trade_date BETWEEN ? AND ?
              AND {col} IS NOT NULL
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        n_samples = int(row[0] or 0)
        n_match = int(row[1] or 0)
    finally:
        conn.close()

    rate: Optional[float] = (n_match / n_samples) if n_samples > 0 else None

    return {
        "window_days": window_days,
        "indicator": indicator,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "n_samples": n_samples,
        "n_match": n_match,
        "match_rate": rate,
        "passes_70_threshold": rate is not None and rate >= MATCH_RATE_THRESHOLD,
    }
