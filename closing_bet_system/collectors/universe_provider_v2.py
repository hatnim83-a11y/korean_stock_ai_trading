"""closing_bet_system.collectors.universe_provider_v2

종가베팅 일일 후보 universe **v2** 산출 — PRD 16-3 본래 의도(Layer 1+3 다중 출처).

v1 한계
    - v1 은 스윙 시스템 주간 선정 테마 5개에서만 종목 추출 (대략 19종목/일)
    - PRD 16-3 일별 흐름 중 **Layer 3 (테마)** 부분만 구현
    - 종가베팅 핵심 시그니처 (거래대금 폭발 / 외국인 매수 우위 / 모멘텀 강세) 누락

v2 설계
    - **4 출처 합집합**:
        1) Layer 3 (테마): 스윙 top_themes 종목 (v1 헬퍼 재사용)
        2) Layer 3 (모멘텀): pykrx 거래대금 상위 N (KOSPI+KOSDAQ)
        3) Layer 3 (모멘텀): pykrx 당일 등락률 상위 N (KOSPI+KOSDAQ)
        4) Layer 1 (수급): pykrx 외국인 순매수 상위 N (KOSPI+KOSDAQ)
    - 6자리 정규식 검증 + 중복 제거 + 스윙 보유 제외 + hard_cap (default 100)
    - in-memory 캐시 (같은 거래일 1회만 pykrx 호출)
    - 각 출처 try/except 격리 (1개 실패가 다른 출처 영향 없음)
    - pykrx import lazy (모듈 로드 부담 회피)

호출 시점: APScheduler 15:10 KST (``MainOrchestrator.run_daily_pipeline``).

PRD 4-1 속성 필터 / 4-4 유동성 필터는 **단위 2-9b** 에서 별도 모듈로 처리.
본 모듈은 universe **풀(pool)** 산출까지만 책임진다.
"""

from __future__ import annotations

import re
import sys
import threading
from datetime import date as date_cls
from pathlib import Path
from typing import Optional, Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from config import now_kst

# v1 helper 재사용 (스윙 테마 + 보유 제외 로직)
from closing_bet_system.collectors.universe_provider import (
    _fetch_top_themes,
    _fetch_swing_holdings,
    _crawl_theme_codes,
    DEFAULT_THEME_COUNT as V1_DEFAULT_THEME_COUNT,
    DEFAULT_STOCKS_PER_THEME as V1_DEFAULT_STOCKS_PER_THEME,
)


# ===== 모듈 상수 =====

_TICKER_PATTERN = re.compile(r"^\d{6}$")

# v2 신규 상수
DEFAULT_THEME_COUNT_V2 = V1_DEFAULT_THEME_COUNT          # 5 — v1 동일 (테마 5개)
DEFAULT_STOCKS_PER_THEME_V2 = V1_DEFAULT_STOCKS_PER_THEME  # 4 — v1 동일

DEFAULT_TOP_VALUE_COUNT = 30        # 거래대금 상위 N
DEFAULT_TOP_CHANGE_COUNT = 30       # 등락률 상위 N
DEFAULT_TOP_FOREIGN_COUNT = 30      # 외국인 순매수 상위 N
DEFAULT_UNIVERSE_HARD_CAP_V2 = 100  # v2 hard_cap (v1 의 20 → 100)

# pykrx market 코드
_PYKRX_MARKETS = ("KOSPI", "KOSDAQ")
# 외국인 investor 카테고리 — PRD 5장 Layer 1 확정 "외국인" (한글 정확 매칭)
_FOREIGN_INVESTOR = "외국인"


# ===== 캐시 (v1 과 분리된 별도 dict) =====

_cache_lock = threading.Lock()
_cache: dict[str, list[str]] = {}  # key=YYYY-MM-DD → universe list


def _cache_key(target_date: date_cls) -> str:
    return target_date.isoformat()


def reset_cache() -> None:
    """테스트용: v2 캐시 초기화."""
    with _cache_lock:
        _cache.clear()


# ===== 메인 진입점 =====


def get_universe_v2(
    *,
    theme_count: int = DEFAULT_THEME_COUNT_V2,
    stocks_per_theme: int = DEFAULT_STOCKS_PER_THEME_V2,
    top_value_count: int = DEFAULT_TOP_VALUE_COUNT,
    top_change_count: int = DEFAULT_TOP_CHANGE_COUNT,
    top_foreign_count: int = DEFAULT_TOP_FOREIGN_COUNT,
    hard_cap: int = DEFAULT_UNIVERSE_HARD_CAP_V2,
    exclude_swing_holdings: bool = True,
) -> list[str]:
    """오늘 거래일의 종가베팅 후보 universe **v2** 반환 (4 출처 합집합).

    캐시 히트 시 즉시 반환. 미스 시 4 출처 병합 후 캐시.

    출처 1 실패 → 다른 3개로 진행 (각 출처 try/except 격리).
    4 출처 모두 실패 → 빈 리스트 반환 (orchestrator graceful skip).

    Args:
        theme_count: Layer 3 (테마) 상위 테마 수.
        stocks_per_theme: Layer 3 (테마) 테마당 종목 수.
        top_value_count: Layer 3 (모멘텀) 거래대금 상위 N.
        top_change_count: Layer 3 (모멘텀) 등락률 상위 N.
        top_foreign_count: Layer 1 (수급) 외국인 순매수 상위 N.
        hard_cap: 전체 universe 상한 (default 100).
        exclude_swing_holdings: 스윙 보유 종목 제외 여부.

    Returns:
        6자리 종목코드 리스트 (중복 없음, hard_cap 이내).
    """
    today = now_kst().date()
    key = _cache_key(today)

    cached = _cache.get(key)
    if cached is not None:
        logger.debug(f"[universe_v2] 캐시 히트 ({key}) — {len(cached)}건")
        return list(cached)

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            return list(cached)

        universe = _build_universe_v2(
            today,
            theme_count=theme_count,
            stocks_per_theme=stocks_per_theme,
            top_value_count=top_value_count,
            top_change_count=top_change_count,
            top_foreign_count=top_foreign_count,
            hard_cap=hard_cap,
            exclude_swing_holdings=exclude_swing_holdings,
        )
        _cache[key] = list(universe)

    logger.info(
        f"[universe_v2] 산출 완료 ({key}) — {len(universe)}건 (4 출처 합집합)"
    )
    return universe


# ===== 내부 빌더 =====


def _build_universe_v2(
    today: date_cls,
    *,
    theme_count: int,
    stocks_per_theme: int,
    top_value_count: int,
    top_change_count: int,
    top_foreign_count: int,
    hard_cap: int,
    exclude_swing_holdings: bool,
) -> list[str]:
    """4 출처 합집합 → 6자리 검증 → 중복 제거 → 스윙 보유 제외 → hard_cap."""
    today_str = today.strftime("%Y%m%d")

    # 출처 1: Layer 3 (테마) — 스윙 top_themes
    theme_codes = _safe_call(
        _fetch_theme_codes_v2, today, theme_count, stocks_per_theme,
        source="theme",
    )

    # 출처 2: Layer 3 (모멘텀) — 거래대금 상위 N
    top_value_codes = _safe_call(
        _fetch_top_value_codes, today_str, top_value_count,
        source="top_value",
    )

    # 출처 3: Layer 3 (모멘텀) — 당일 등락률 상위 N
    top_change_codes = _safe_call(
        _fetch_top_change_codes, today_str, top_change_count,
        source="top_change",
    )

    # 출처 4: Layer 1 (수급) — 외국인 순매수 상위 N
    top_foreign_codes = _safe_call(
        _fetch_top_foreign_buy_codes, today_str, top_foreign_count,
        source="top_foreign",
    )

    # 합집합 + 중복 제거 (출처 순서 보존)
    excluded: set[str] = set()
    if exclude_swing_holdings:
        excluded = _fetch_swing_holdings()
        if excluded:
            logger.debug(f"[universe_v2] 스윙 보유 {len(excluded)}종목 제외 대상")

    seen: set[str] = set()
    universe: list[str] = []
    sources_summary: dict[str, int] = {
        "theme": 0, "top_value": 0, "top_change": 0, "top_foreign": 0,
    }

    for source_name, codes in (
        ("theme", theme_codes),
        ("top_value", top_value_codes),
        ("top_change", top_change_codes),
        ("top_foreign", top_foreign_codes),
    ):
        for code in codes:
            if not isinstance(code, str) or not _TICKER_PATTERN.match(code):
                continue
            if code in seen or code in excluded:
                continue
            seen.add(code)
            universe.append(code)
            sources_summary[source_name] += 1
            if len(universe) >= hard_cap:
                logger.warning(
                    f"[universe_v2] hard_cap {hard_cap} 도달 — 추가 종목 잘라냄"
                )
                _log_sources(sources_summary)
                return universe

    _log_sources(sources_summary)
    return universe


def _log_sources(summary: dict[str, int]) -> None:
    """출처별 기여 종목 수 로깅."""
    total = sum(summary.values())
    logger.info(
        f"[universe_v2] 출처별 기여: theme={summary['theme']} / "
        f"top_value={summary['top_value']} / top_change={summary['top_change']} / "
        f"top_foreign={summary['top_foreign']} (총 {total}종목)"
    )


def _safe_call(
    fn: Callable[..., list[str]],
    *args,
    source: str,
) -> list[str]:
    """출처별 try/except 격리 헬퍼.

    pykrx 호출 실패 / 빈 응답 / KeyError 등 모든 예외 흡수 → 빈 리스트.
    """
    try:
        result = fn(*args) or []
        if result:
            logger.debug(f"[universe_v2] 출처 '{source}': {len(result)}건 수집")
        return result
    except Exception as e:
        logger.warning(f"[universe_v2] 출처 '{source}' 실패: {e}")
        return []


# ===== 출처 1: 스윙 테마 (v1 재사용) =====


def _fetch_theme_codes_v2(
    today: date_cls,
    theme_count: int,
    stocks_per_theme: int,
) -> list[str]:
    """스윙 top_themes → 네이버 크롤링 → 종목코드 list."""
    themes = _fetch_top_themes(today, theme_count)
    if not themes:
        return []

    seen: set[str] = set()
    codes: list[str] = []
    for theme in themes:
        url = (theme.get("url") or "").strip() if isinstance(theme, dict) else ""
        theme_name = theme.get("name", "(이름없음)") if isinstance(theme, dict) else "(이름없음)"
        if not url:
            continue
        theme_codes = _crawl_theme_codes(url, theme_name, stocks_per_theme)
        for code in theme_codes:
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


# ===== 출처 2~4: pykrx 헬퍼 (lazy import) =====


def _import_pykrx():
    """pykrx lazy import — 모듈 로드 시 부담 회피."""
    from pykrx import stock as krx
    return krx


def _fetch_top_value_codes(today_str: str, top_n: int) -> list[str]:
    """거래대금 상위 N 종목코드 (KOSPI+KOSDAQ 합산).

    Args:
        today_str: 'YYYYMMDD' 형식 거래일.
        top_n: 상위 N (시장별 N → KOSPI N + KOSDAQ N → 합쳐서 nlargest N).

    Returns:
        종목코드 list (최대 ``top_n`` 건). 실패 시 빈 리스트.
    """
    krx = _import_pykrx()
    frames = []
    for market in _PYKRX_MARKETS:
        try:
            df = krx.get_market_ohlcv_by_ticker(today_str, market=market)
            if df is None or len(df) == 0 or "거래대금" not in df.columns:
                continue
            frames.append(df[["거래대금"]])
        except Exception as e:
            logger.debug(f"[universe_v2] OHLCV {market} 실패: {e}")
            continue

    if not frames:
        return []

    import pandas as pd
    combined = pd.concat(frames)
    top = combined.nlargest(top_n, "거래대금")
    return [str(t) for t in top.index.tolist() if isinstance(t, str)]


def _fetch_top_change_codes(today_str: str, top_n: int) -> list[str]:
    """당일 등락률 상위 N 종목코드 (KOSPI+KOSDAQ 합산).

    Args:
        today_str: 'YYYYMMDD' 형식 거래일.
        top_n: 상위 N.

    Returns:
        종목코드 list. 실패 시 빈 리스트.
    """
    krx = _import_pykrx()
    frames = []
    for market in _PYKRX_MARKETS:
        try:
            df = krx.get_market_price_change_by_ticker(
                today_str, today_str, market=market
            )
            if df is None or len(df) == 0 or "등락률" not in df.columns:
                continue
            frames.append(df[["등락률"]])
        except Exception as e:
            logger.debug(f"[universe_v2] price_change {market} 실패: {e}")
            continue

    if not frames:
        return []

    import pandas as pd
    combined = pd.concat(frames)
    top = combined.nlargest(top_n, "등락률")
    return [str(t) for t in top.index.tolist() if isinstance(t, str)]


def _fetch_top_foreign_buy_codes(today_str: str, top_n: int) -> list[str]:
    """외국인 순매수 상위 N 종목코드 (KOSPI+KOSDAQ 합산).

    pykrx 의 ``get_market_net_purchases_of_equities_by_ticker`` 결과에서
    "순매수거래대금" 컬럼(외국인 순매수 거래대금) 기준 상위 N.

    pykrx 반환 컬럼: 종목명 / 매도거래량 / 매수거래량 / 순매수거래량 /
                     매도거래대금 / 매수거래대금 / 순매수거래대금

    Args:
        today_str: 'YYYYMMDD' 형식 거래일.
        top_n: 상위 N.

    Returns:
        종목코드 list. 실패 시 빈 리스트.
    """
    _FOREIGN_COL = "순매수거래대금"
    krx = _import_pykrx()
    frames = []
    for market in _PYKRX_MARKETS:
        try:
            df = krx.get_market_net_purchases_of_equities_by_ticker(
                today_str, today_str, market=market, investor=_FOREIGN_INVESTOR
            )
            if df is None or len(df) == 0 or _FOREIGN_COL not in df.columns:
                continue
            frames.append(df[[_FOREIGN_COL]])
        except Exception as e:
            logger.debug(f"[universe_v2] foreign_purchase {market} 실패: {e}")
            continue

    if not frames:
        return []

    import pandas as pd
    combined = pd.concat(frames)
    top = combined.nlargest(top_n, _FOREIGN_COL)
    return [str(t) for t in top.index.tolist() if isinstance(t, str)]


# ===== 통합 함수: v2 산출 + PRD 4-1/4-4 필터 적용 =====


def get_universe_v2_filtered(
    *,
    theme_count: int = DEFAULT_THEME_COUNT_V2,
    stocks_per_theme: int = DEFAULT_STOCKS_PER_THEME_V2,
    top_value_count: int = DEFAULT_TOP_VALUE_COUNT,
    top_change_count: int = DEFAULT_TOP_CHANGE_COUNT,
    top_foreign_count: int = DEFAULT_TOP_FOREIGN_COUNT,
    hard_cap: int = DEFAULT_UNIVERSE_HARD_CAP_V2,
    exclude_swing_holdings: bool = True,
) -> list[str]:
    """**최종 universe 산출** — v2 4 출처 합집합 → PRD 4-1/4-4 하드 필터 적용.

    내부 흐름:
        1) :func:`get_universe_v2` — 4 출처 합집합 (스윙 테마 + pykrx 3종)
        2) :func:`universe_filters.apply_all_filters` — PRD 4-1 속성 + 4-4 유동성

    실패 격리: 필터 모듈 import/실행 실패 시 unfiltered universe 반환 (graceful).

    Returns:
        최종 universe 종목코드 리스트 (필터 통과).
    """
    universe_pool = get_universe_v2(
        theme_count=theme_count,
        stocks_per_theme=stocks_per_theme,
        top_value_count=top_value_count,
        top_change_count=top_change_count,
        top_foreign_count=top_foreign_count,
        hard_cap=hard_cap,
        exclude_swing_holdings=exclude_swing_holdings,
    )

    if not universe_pool:
        return []

    try:
        from closing_bet_system.collectors.universe_filters import apply_all_filters
        passed, rejected = apply_all_filters(universe_pool)
        logger.info(
            f"[universe_v2] 필터 후 최종 universe: {len(passed)}/{len(universe_pool)}"
            f" (탈락 {len(rejected)})"
        )
        return passed
    except Exception as e:
        logger.warning(
            f"[universe_v2] 필터 적용 실패 — unfiltered 반환: {e}"
        )
        return universe_pool


# ===== 호환성: v1 인터페이스 alias =====


def get_universe(*args, **kwargs) -> list[str]:
    """v1 인터페이스 호환 alias — scheduler.py 한 줄 교체용.

    내부적으로 :func:`get_universe_v2_filtered` 호출 (필터 적용 최종 universe).
    기존 v1 시그니처 인자 (``theme_count``, ``stocks_per_theme``, ``hard_cap``,
    ``exclude_swing_holdings``) 그대로 받음. v2 신규 인자(``top_value_count`` 등)는
    default 사용.

    **단위 2-9b 완료 시점부터 본 alias 가 PRD 4-1/4-4 필터까지 적용함**.
    """
    return get_universe_v2_filtered(*args, **kwargs)
