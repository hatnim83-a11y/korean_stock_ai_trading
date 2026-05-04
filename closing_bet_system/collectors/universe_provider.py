"""closing_bet_system.collectors.universe_provider

종가베팅 일일 후보 universe 산출.

PRD 5-Layer 3 의 "테마 모멘텀 후보" 를 스윙 시스템의 주간 선정 테마로 대체한다
(`database.get_top_themes()` 에 v11 selected=1 우선). 각 테마의 url 에서
``crawl_naver_theme_stocks()`` 로 종목 코드를 추출, 6자리 검증 + 중복 제거 +
스윙 보유 종목 제외 후 list[str] 반환.

호출 시점: APScheduler 15:10 (``MainOrchestrator.run_daily_pipeline``).

같은 거래일 다회 호출 시 in-memory 캐시 히트 → 네이버 크롤링 1회로 제한.
"""

from __future__ import annotations

import re
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

_TICKER_PATTERN = re.compile(r"^\d{6}$")
DEFAULT_THEME_COUNT = 5            # 상위 5개 테마
DEFAULT_STOCKS_PER_THEME = 4       # 테마당 최대 4종목 (5×4=20 종목 universe 상한)
DEFAULT_UNIVERSE_HARD_CAP = 20     # 안전 상한 (혹시 모를 폭주 방지)


# ===== 캐시 =====

_cache_lock = threading.Lock()
_cache: dict[str, list[str]] = {}  # key=YYYY-MM-DD → universe list


def _cache_key(target_date: date_cls) -> str:
    return target_date.isoformat()


def reset_cache() -> None:
    """테스트용: 캐시 초기화."""
    with _cache_lock:
        _cache.clear()


# ===== 메인 진입점 =====


def get_universe(
    *,
    theme_count: int = DEFAULT_THEME_COUNT,
    stocks_per_theme: int = DEFAULT_STOCKS_PER_THEME,
    hard_cap: int = DEFAULT_UNIVERSE_HARD_CAP,
    exclude_swing_holdings: bool = True,
) -> list[str]:
    """오늘 거래일의 종가베팅 후보 universe 반환.

    캐시 히트 시 즉시 반환. 미스 시:
        1) ``database.get_top_themes(today, theme_count)`` 로 상위 테마 조회
        2) 각 테마의 url 에서 ``crawl_naver_theme_stocks()`` 호출
        3) 6자리 종목코드 정규식 검증 + 중복 제거 + 스윙 보유 제외
        4) ``hard_cap`` 으로 상한 적용

    실패 시 빈 리스트 반환 (orchestrator 가 자연스럽게 스킵).

    Args:
        theme_count: 조회할 상위 테마 수.
        stocks_per_theme: 테마당 최대 종목 수.
        hard_cap: 전체 universe 상한.
        exclude_swing_holdings: 스윙 보유 종목 제외 여부.

    Returns:
        6자리 종목코드 리스트 (중복 없음, hard_cap 이내).
    """
    today = now_kst().date()
    key = _cache_key(today)

    # 1차 lock-free read (대부분의 경로 — 캐시 히트)
    cached = _cache.get(key)
    if cached is not None:
        logger.debug(
            f"[universe_provider] 캐시 히트 ({key}) — {len(cached)}건"
        )
        return list(cached)

    # 캐시 미스: lock 획득 후 더블체크 — 동시 진입한 다른 호출이 빌드를 마쳤으면 캐시 사용
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            return list(cached)

        universe = _build_universe(
            today,
            theme_count=theme_count,
            stocks_per_theme=stocks_per_theme,
            hard_cap=hard_cap,
            exclude_swing_holdings=exclude_swing_holdings,
        )
        _cache[key] = list(universe)

    logger.info(
        f"[universe_provider] universe 산출 완료 ({key}) — {len(universe)}건"
    )
    return universe


# ===== 내부 빌더 =====


def _build_universe(
    today: date_cls,
    *,
    theme_count: int,
    stocks_per_theme: int,
    hard_cap: int,
    exclude_swing_holdings: bool,
) -> list[str]:
    """스윙 DB top_themes → 네이버 크롤링 → 검증/중복 제거 후 list[str]."""
    themes = _fetch_top_themes(today, theme_count)
    if not themes:
        logger.warning("[universe_provider] 스윙 DB top_themes 조회 결과 없음")
        return []

    excluded: set[str] = set()
    if exclude_swing_holdings:
        excluded = _fetch_swing_holdings()
        if excluded:
            logger.debug(
                f"[universe_provider] 스윙 보유 {len(excluded)}종목 제외 대상"
            )

    seen: set[str] = set()
    universe: list[str] = []
    for theme in themes:
        url = (theme.get("url") or "").strip() if isinstance(theme, dict) else ""
        theme_name = theme.get("name", "(이름없음)") if isinstance(theme, dict) else "(이름없음)"
        if not url:
            logger.debug(f"[universe_provider] {theme_name} url 없음 — 스킵")
            continue

        codes = _crawl_theme_codes(url, theme_name, stocks_per_theme)
        for code in codes:
            if code in seen or code in excluded:
                continue
            seen.add(code)
            universe.append(code)
            if len(universe) >= hard_cap:
                logger.warning(
                    f"[universe_provider] hard_cap {hard_cap} 도달 — 추가 종목 잘라냄"
                )
                return universe

    return universe


def _open_swing_db():
    """스윙 ``Database`` 인스턴스 생성 + connect. 실패 시 None.

    호출 측이 try/except 으로 close 책임짐. ``database.Database`` 는 모듈 수준
    싱글톤이 없고 클래스만 export 됨 (main.py:53 / scheduler.py:44 패턴 동일).
    """
    try:
        from database import Database
        db = Database()
        db.connect()
        return db
    except Exception as e:
        logger.warning(f"[universe_provider] 스윙 DB 연결 실패: {e}")
        return None


def _fetch_top_themes(today: date_cls, count: int) -> list[dict]:
    """``Database.get_top_themes()`` 호출 + 예외 격리.

    스윙 DB 미존재/스키마 미생성/조회 실패 등 모든 예외를 흡수해 빈 리스트 반환
    → 메인 시스템 영향 없음.
    """
    db = _open_swing_db()
    if db is None:
        return []
    try:
        return db.get_top_themes(today, count=count) or []
    except Exception as e:
        logger.warning(f"[universe_provider] top_themes 조회 실패: {e}")
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


def _fetch_swing_holdings() -> set[str]:
    """``Database.get_portfolio()`` → 보유 종목 코드 set. 실패 시 빈 set."""
    db = _open_swing_db()
    if db is None:
        return set()
    try:
        portfolio = db.get_portfolio(status="holding") or []
        return {
            p.get("stock_code")
            for p in portfolio
            if isinstance(p, dict) and p.get("stock_code")
        }
    except Exception as e:
        logger.debug(f"[universe_provider] 스윙 보유 조회 실패: {e}")
        return set()
    finally:
        try:
            db.close()
        except Exception:
            pass


def _crawl_theme_codes(
    theme_url: str,
    theme_name: str,
    max_stocks: int,
) -> list[str]:
    """테마 url → 6자리 종목코드 리스트 (max_stocks 제한). 실패 시 빈 리스트."""
    try:
        from modules.theme_analyzer.crawlers import crawl_naver_theme_stocks
        stocks = crawl_naver_theme_stocks(theme_url) or []
    except Exception as e:
        logger.warning(
            f"[universe_provider] {theme_name} 크롤링 예외: {e}"
        )
        return []

    codes: list[str] = []
    for stock in stocks:
        code = stock.get("code") if isinstance(stock, dict) else None
        if isinstance(code, str) and _TICKER_PATTERN.match(code):
            codes.append(code)
        if len(codes) >= max_stocks:
            break
    return codes
