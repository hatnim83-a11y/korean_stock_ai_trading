"""
crawlers.py - 테마 데이터 크롤링 모듈

이 파일은 네이버 증권, KRX 테마지수 등에서 테마 정보를 크롤링합니다.

주요 기능:
- 네이버 증권 인기 테마 크롤링
- KRX 공식 테마지수 수집 (pykrx)
- 테마별 종목 목록 수집
- 뉴스 언급 빈도 수집

사용법:
    from modules.theme_analyzer.crawlers import (
        crawl_naver_themes,
        crawl_krx_themes,
        crawl_theme_stocks,
        crawl_theme_news_count
    )

    naver_themes = crawl_naver_themes()
    krx_themes = crawl_krx_themes()
"""

import json
import re
import time
import random
from html import unescape
from typing import Optional
from datetime import datetime, timedelta, timezone
from config import now_kst

import httpx
from bs4 import BeautifulSoup

# 프로젝트 루트의 logger 사용
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger


# ===== 상수 정의 =====
# 크롤링 시 사용할 User-Agent (브라우저처럼 보이게)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/"
}

# 요청 간 대기 시간 (초) - 차단 방지
MIN_DELAY = 1.0
MAX_DELAY = 2.5


def _random_delay():
    """차단 방지를 위한 랜덤 대기"""
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)


def _safe_float(value: str, default: float = 0.0) -> float:
    """
    문자열을 안전하게 float으로 변환
    
    Args:
        value: 변환할 문자열 (예: "+3.5%", "-2.1%", "1,234")
        default: 변환 실패 시 기본값
        
    Returns:
        변환된 float 값
    """
    if not value:
        return default
    
    try:
        # 쉼표, %, +, 공백 등 제거
        cleaned = value.replace(",", "").replace("%", "").replace("+", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default


def _safe_int(value: str, default: int = 0) -> int:
    """
    문자열을 안전하게 int로 변환
    
    Args:
        value: 변환할 문자열 (예: "1,234개")
        default: 변환 실패 시 기본값
        
    Returns:
        변환된 int 값
    """
    if not value:
        return default
    
    try:
        # 쉼표, '개' 등 제거
        cleaned = value.replace(",", "").replace("개", "").strip()
        return int(cleaned)
    except (ValueError, TypeError):
        return default


# ===== 네이버 증권 테마 크롤링 =====

def crawl_naver_themes(max_pages: int = 3) -> list[dict]:
    """
    네이버 증권 인기 테마 크롤링
    
    네이버 증권의 테마 페이지에서 테마 목록과 등락률을 수집합니다.
    
    Args:
        max_pages: 크롤링할 최대 페이지 수 (기본 3페이지)
    
    Returns:
        테마 정보 리스트:
        [
            {
                'name': '2차전지',
                'stock_count': 45,
                'avg_change_rate': 3.2,
                'source': 'naver',
                'url': 'https://...'
            },
            ...
        ]
    
    Example:
        >>> themes = crawl_naver_themes()
        >>> print(themes[0])
        {'name': '2차전지', 'stock_count': 45, 'avg_change_rate': 3.2, ...}
    """
    themes = []
    base_url = "https://finance.naver.com/sise/theme.naver"
    
    logger.info("📊 네이버 증권 테마 크롤링 시작")
    
    for page in range(1, max_pages + 1):
        try:
            url = f"{base_url}?&page={page}"
            
            response = httpx.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=15.0,
                follow_redirects=True
            )
            response.raise_for_status()
            
            # HTML 파싱
            soup = BeautifulSoup(response.text, "lxml")
            
            # 테마 테이블 찾기
            table = soup.find("table", class_="type_1")
            if not table:
                logger.warning(f"테마 테이블을 찾을 수 없습니다 (페이지 {page})")
                continue
            
            rows = table.find_all("tr")
            
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 5:
                    continue
                
                # 테마명 추출
                theme_link = cols[0].find("a")
                if not theme_link:
                    continue
                
                theme_name = theme_link.get_text(strip=True)
                theme_url = "https://finance.naver.com" + theme_link.get("href", "")
                
                # 전일대비(%) 추출
                change_rate_elem = cols[1].find("span")
                change_rate = 0.0
                if change_rate_elem:
                    change_text = change_rate_elem.get_text(strip=True)
                    change_rate = _safe_float(change_text)
                    # 하락인 경우 음수 처리
                    if "하락" in cols[1].get_text() or "down" in str(cols[1]).lower():
                        change_rate = -abs(change_rate)
                
                # 최근 3일 등락률 (있는 경우)
                three_day_rate = _safe_float(cols[2].get_text(strip=True)) if len(cols) > 2 else 0.0

                # 종목 수 = 상승(cols[3]) + 보합(cols[4]) + 하락(cols[5])
                stock_count = 0
                if len(cols) >= 6:
                    up_count = _safe_int(cols[3].get_text(strip=True))
                    flat_count = _safe_int(cols[4].get_text(strip=True))
                    down_count = _safe_int(cols[5].get_text(strip=True))
                    stock_count = up_count + flat_count + down_count

                themes.append({
                    "name": theme_name,
                    "stock_count": stock_count,
                    "avg_change_rate": change_rate,
                    "three_day_rate": three_day_rate,
                    "source": "naver",
                    "url": theme_url
                })
            
            logger.debug(f"페이지 {page} 크롤링 완료: {len(themes)}개 테마")
            _random_delay()
            
        except httpx.TimeoutException:
            logger.warning(f"네이버 테마 크롤링 타임아웃 (페이지 {page})")
            continue
            
        except httpx.HTTPStatusError as e:
            logger.error(f"네이버 테마 HTTP 에러: {e.response.status_code}")
            break
            
        except Exception as e:
            logger.error(f"네이버 테마 크롤링 실패: {e}")
            continue
    
    # 중복 제거 (테마명 기준)
    seen = set()
    unique_themes = []
    for theme in themes:
        if theme["name"] not in seen:
            seen.add(theme["name"])
            unique_themes.append(theme)
    
    logger.info(f"✅ 네이버 테마 {len(unique_themes)}개 수집 완료")
    return unique_themes


def crawl_naver_theme_stocks(theme_url: str) -> list[dict]:
    """
    네이버 증권 특정 테마의 종목 목록 크롤링
    
    Args:
        theme_url: 테마 상세 페이지 URL
    
    Returns:
        종목 정보 리스트:
        [
            {
                'code': '005930',
                'name': '삼성전자',
                'price': 75000,
                'change_rate': 2.5
            },
            ...
        ]
    """
    stocks = []
    
    try:
        response = httpx.get(
            theme_url,
            headers=DEFAULT_HEADERS,
            timeout=15.0,
            follow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "lxml")
        
        # 종목 테이블 찾기
        table = soup.find("table", class_="type_5")
        if not table:
            logger.warning("종목 테이블을 찾을 수 없습니다")
            return stocks
        
        rows = table.find_all("tr")
        
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            
            # 종목명 및 코드 추출
            stock_link = cols[0].find("a")
            if not stock_link:
                continue
            
            stock_name = stock_link.get_text(strip=True)
            href = stock_link.get("href", "")
            
            # URL에서 종목 코드 추출
            stock_code = ""
            if "code=" in href:
                import re
                match = re.search(r'code=([^&]+)', href)
                stock_code = match.group(1) if match else ""
            
            # 현재가
            price = _safe_int(cols[1].get_text(strip=True).replace(",", ""))
            
            # 등락률
            change_rate = _safe_float(cols[2].get_text(strip=True))
            
            if stock_code and re.match(r'^\d{6}$', stock_code):
                stocks.append({
                    "code": stock_code,
                    "name": stock_name,
                    "price": price,
                    "change_rate": change_rate
                })
        
        logger.debug(f"테마 종목 {len(stocks)}개 수집")
        
    except Exception as e:
        logger.error(f"테마 종목 크롤링 실패: {e}")
    
    return stocks


# ===== KRX 테마지수 크롤링 (pykrx) =====

def crawl_krx_themes() -> list[dict]:
    """
    KRX 공식 테마지수 데이터 수집 (pykrx)

    KRX에서 제공하는 테마지수 42개의 등락률과 구성종목 수를 조회합니다.

    Returns:
        테마 정보 리스트:
        [
            {
                'name': '2차전지',
                'avg_change_rate': 2.5,
                'stock_count': 15,
                'source': 'krx'
            },
            ...
        ]
    """
    themes = []

    logger.info("📊 KRX 테마지수 수집 시작")

    try:
        from pykrx import stock

        try:
            tickers = stock.get_index_ticker_list(market='테마')
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"KRX 테마지수 티커 조회 실패 (pykrx API 변경 가능): {e}")
            return themes

        if not tickers:
            logger.warning("KRX 테마지수 티커 조회 결과 0개")
            return themes

        # 최근 영업일 기준 5일간 데이터로 등락률 계산
        end = now_kst().strftime('%Y%m%d')
        start = (now_kst() - timedelta(days=14)).strftime('%Y%m%d')

        for ticker in tickers:
            try:
                name = stock.get_index_ticker_name(ticker)

                # OHLCV 조회
                df = stock.get_index_ohlcv(start, end, ticker)
                if df.empty or len(df) < 2:
                    continue

                # 최근일 등락률 계산
                last_close = df['종가'].iloc[-1]
                prev_close = df['종가'].iloc[-2]
                change_rate = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0

                # 구성종목 수
                try:
                    components = stock.get_index_portfolio_deposit_file(ticker)
                    stock_count = len(components)
                except Exception:
                    stock_count = 10  # 기본값

                themes.append({
                    'name': name,
                    'avg_change_rate': round(change_rate, 2),
                    'stock_count': stock_count,
                    'source': 'krx',
                })
            except Exception as e:
                logger.debug(f"KRX 테마 [{ticker}] 처리 실패: {e}")
                continue

        logger.info(f"✅ KRX 테마지수 {len(themes)}개 수집 완료")

    except ImportError:
        logger.error("pykrx 라이브러리 미설치: pip install pykrx")
    except Exception as e:
        logger.error(f"KRX 테마지수 수집 실패: {e}")

    return themes


# ===== 뉴스 HTML 클렌징 =====

def _clean_news_html(raw: str) -> str:
    """HTML 태그 및 엔티티 제거 (unescape 먼저 → 태그 제거)"""
    text = unescape(raw)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


# ===== 뉴스 텍스트 + 건수 수집 =====

def crawl_theme_news(theme_name: str, days: int = 3, max_items: int = 5) -> dict:
    """
    뉴스 건수 + 텍스트 수집 (AI 감성분석용)

    네이버 Open API display=max_items로 제목+본문 수집.
    API 키 없으면 스크래핑 폴백.

    Args:
        theme_name: 테마명
        days: 조회 일수 (스크래핑 폴백에서만 사용, API 경로는 날짜 필터 없음)
        max_items: 수집할 뉴스 수 (기본 5)

    Returns:
        {"count": int, "text": str}
    """
    from config import settings

    client_id = settings.NAVER_CLIENT_ID
    client_secret = settings.NAVER_CLIENT_SECRET

    if not client_id or not client_secret:
        logger.warning(f"[{theme_name}] 네이버 API 키 미설정 — 스크래핑 fallback")
        count = _crawl_theme_news_count_scrape(theme_name, days)
        return {"count": count, "text": ""}

    try:
        search_query = f"{theme_name} 주식"
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        params = {
            "query": search_query,
            "display": max_items,
            "sort": "date",
        }

        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        response.raise_for_status()

        data = response.json()
        count = data.get("total", 0)
        items = data.get("items", [])

        # 제목 + 설명(본문 요약)을 합쳐 텍스트 생성
        text_parts = []
        for item in items:
            title = _clean_news_html(item.get("title", ""))
            desc = _clean_news_html(item.get("description", ""))
            if title:
                text_parts.append(f"- {title}: {desc}" if desc else f"- {title}")

        news_text = "\n".join(text_parts)
        logger.debug(f"[{theme_name}] 뉴스 {count}건, 텍스트 {len(news_text)}자 (API)")
        return {"count": count, "text": news_text}

    except Exception as e:
        logger.warning(f"[{theme_name}] 뉴스 API 조회 실패: {e}")
        return {"count": 0, "text": ""}


# ===== 뉴스 언급 빈도 크롤링 (기존 호환) =====

def crawl_theme_news_count(theme_name: str, days: int = 3) -> int:
    """
    특정 테마의 최근 N일 뉴스 언급 횟수 조회 (네이버 Open API)

    Args:
        theme_name: 테마명 (예: "2차전지")
        days: 조회할 일수 (기본 3일)

    Returns:
        뉴스 언급 횟수
    """
    from config import settings

    client_id = settings.NAVER_CLIENT_ID
    client_secret = settings.NAVER_CLIENT_SECRET

    if not client_id or not client_secret:
        logger.warning(f"[{theme_name}] 네이버 API 키 미설정 — 스크래핑 fallback")
        return _crawl_theme_news_count_scrape(theme_name, days)

    try:
        search_query = f"{theme_name} 주식"
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        params = {
            "query": search_query,
            "display": 1,
            "sort": "date",
        }

        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        response.raise_for_status()

        data = response.json()
        count = data.get("total", 0)
        logger.debug(f"[{theme_name}] 뉴스 {count}건 (API)")
        return count

    except Exception as e:
        logger.warning(f"[{theme_name}] 뉴스 API 조회 실패: {e}")
        return 0


def _crawl_theme_news_count_scrape(theme_name: str, days: int = 3) -> int:
    """네이버 API 키 없을 때 스크래핑 fallback"""
    try:
        today = now_kst()
        start_date = (today - timedelta(days=days)).strftime("%Y.%m.%d")
        end_date = today.strftime("%Y.%m.%d")

        search_query = f"{theme_name} 주식"
        url = "https://search.naver.com/search.naver"
        params = {
            "where": "news",
            "query": search_query,
            "sm": "tab_opt",
            "sort": "1",
            "ds": start_date,
            "de": end_date,
            "nso": f"so:dd,p:from{start_date.replace('.', '')}to{end_date.replace('.', '')}"
        }

        news_headers = {**DEFAULT_HEADERS, "Referer": "https://www.naver.com/"}
        response = httpx.get(
            url, params=params, headers=news_headers,
            timeout=10.0, follow_redirects=True
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        news_items = soup.select(".news_area, .bx, .list_news li")
        count = len(news_items)
        logger.debug(f"[{theme_name}] 뉴스 약 {count}건 (스크래핑)")
        return count

    except Exception as e:
        logger.warning(f"[{theme_name}] 뉴스 스크래핑 실패: {e}")
        return 0


def crawl_multiple_theme_news(theme_names: list[str], days: int = 3) -> dict[str, int]:
    """
    여러 테마의 뉴스 언급 횟수 일괄 조회
    
    Args:
        theme_names: 테마명 리스트
        days: 조회할 일수
    
    Returns:
        {테마명: 뉴스 수} 딕셔너리
        
    Example:
        >>> counts = crawl_multiple_theme_news(["2차전지", "AI반도체"])
        >>> print(counts)
        {'2차전지': 127, 'AI반도체': 95}
    """
    results = {}
    
    logger.info(f"📰 {len(theme_names)}개 테마 뉴스 수집 시작")
    
    for theme_name in theme_names:
        count = crawl_theme_news_count(theme_name, days)
        results[theme_name] = count
        _random_delay()  # 차단 방지
    
    logger.info(f"✅ 테마 뉴스 수집 완료")
    return results


# ===== 테마 캐시 =====
THEME_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "theme_cache.json"
THEME_CACHE_MAX_AGE_DAYS = 7


def _load_theme_cache() -> dict:
    """테마 캐시 파일 로드"""
    if THEME_CACHE_FILE.exists():
        try:
            return json.loads(THEME_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"테마 캐시 로드 실패: {e}")
    return {}


def _save_theme_cache(cache: dict):
    """테마 캐시 파일 저장"""
    THEME_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cached_theme(cache: dict, theme_name: str) -> Optional[dict]:
    """캐시에서 유효한 테마 데이터 조회 (7일 이내)"""
    entry = cache.get(theme_name)
    if not entry:
        return None

    cached_time = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
    if cached_time.tzinfo is None:
        cached_time = cached_time.replace(tzinfo=timezone.utc)  # 기존 캐시는 naive(UTC)
    age_days = (now_kst() - cached_time).days

    if age_days <= THEME_CACHE_MAX_AGE_DAYS:
        logger.info(f"  ♻ [{theme_name}] 캐시 데이터 사용 ({age_days}일 전)")
        return {
            "name": theme_name,
            "avg_change_rate": entry.get("avg_change_rate", 0.0),
            "stock_count": entry.get("stock_count", 15),
            "three_day_rate": entry.get("three_day_rate", 0.0),
            "source": "cache",
        }

    return None


# ===== 테마명 정규화 =====

def _build_theme_name_map() -> dict[str, str]:
    """predefined 테마의 naver_aliases에서 역매핑(alias→표준명) 자동 생성"""
    name_map = {}
    for t in _PREDEFINED_THEMES:
        canonical = t["name"]
        for alias in t.get("naver_aliases", []):
            if alias != canonical:
                name_map[alias] = canonical
    return name_map


# predefined 테마 원본 데이터 (모듈 로드 시 1회 정의)
_PREDEFINED_THEMES = [
    {"name": "2차전지", "category": "신성장", "keywords": ["배터리", "리튬", "전기차"],
     "naver_aliases": ["2차전지"]},
    {"name": "AI반도체", "category": "반도체", "keywords": ["AI칩", "HBM", "GPU"],
     "naver_aliases": ["온디바이스 AI", "AI 반도체"]},
    {"name": "반도체", "category": "반도체", "keywords": ["반도체", "메모리", "파운드리"],
     "naver_aliases": ["반도체 대표주", "반도체 장비", "반도체 재료"]},
    {"name": "K-방산", "category": "방위산업", "keywords": ["방산", "무기", "수출"],
     "naver_aliases": ["방위산업", "방산"]},
    {"name": "바이오", "category": "헬스케어", "keywords": ["신약", "임상", "바이오텍"],
     "naver_aliases": ["바이오", "바이오시밀러"]},
    {"name": "로봇", "category": "신성장", "keywords": ["로봇", "자동화", "휴머노이드"],
     "naver_aliases": ["로봇", "휴머노이드", "피지컬 AI"]},
    {"name": "자율주행", "category": "모빌리티", "keywords": ["자율주행", "라이다", "센서"],
     "naver_aliases": ["자율주행", "자율주행차"]},
    {"name": "원자력", "category": "에너지", "keywords": ["원전", "SMR", "핵융합"],
     "naver_aliases": ["원자력발전", "원자력"]},
    {"name": "수소", "category": "에너지", "keywords": ["수소", "연료전지", "그린수소"],
     "naver_aliases": ["수소에너지", "수소차"]},
    {"name": "조선", "category": "산업재", "keywords": ["조선", "LNG선", "컨테이너선"],
     "naver_aliases": ["조선"]},
    {"name": "건설", "category": "산업재", "keywords": ["건설", "아파트", "인프라"],
     "naver_aliases": ["건설", "건설 대표주"]},
    {"name": "금융", "category": "금융", "keywords": ["은행", "증권", "보험"],
     "naver_aliases": ["은행", "증권", "금융"]},
    {"name": "엔터테인먼트", "category": "소비재", "keywords": ["K-POP", "드라마", "콘텐츠"],
     "naver_aliases": ["엔터테인먼트"]},
    {"name": "게임", "category": "IT서비스", "keywords": ["게임", "모바일게임", "e스포츠"],
     "naver_aliases": ["게임", "모바일게임"]},
    {"name": "플랫폼", "category": "IT서비스", "keywords": ["플랫폼", "이커머스", "핀테크"],
     "naver_aliases": ["인터넷 대표주", "인터넷은행"]},
    {"name": "클라우드", "category": "IT서비스", "keywords": ["클라우드", "SaaS", "데이터센터"],
     "naver_aliases": ["클라우드 컴퓨팅", "클라우드"]},
    {"name": "음식료", "category": "소비재", "keywords": ["식품", "음료", "주류"],
     "naver_aliases": ["음식료업종", "음식료"]},
    {"name": "화장품", "category": "소비재", "keywords": ["화장품", "K-뷰티", "스킨케어"],
     "naver_aliases": ["화장품"]},
    {"name": "철강", "category": "소재", "keywords": ["철강", "스테인리스", "특수강"],
     "naver_aliases": ["철강"]},
    {"name": "화학", "category": "소재", "keywords": ["화학", "석유화학", "정밀화학"],
     "naver_aliases": ["석유화학", "화학"]},
    {"name": "통신", "category": "통신", "keywords": ["5G", "6G", "통신장비"],
     "naver_aliases": ["5G", "통신장비", "통신"]},
]

# alias → 표준명 역매핑 (모듈 로드 시 자동 생성)
THEME_NAME_MAP = _build_theme_name_map()


def normalize_theme_name(name: str) -> str:
    """크롤링된 테마명을 표준명으로 정규화. 매핑 없으면 원본 반환."""
    return THEME_NAME_MAP.get(name, name)


# ===== 테마 카테고리 자동 분류 =====

# 키워드 → 카테고리 매핑 (우선순위 순서: 먼저 매칭되면 해당 카테고리 사용)
_CATEGORY_KEYWORDS = [
    # (카테고리, [키워드 리스트])
    ("반도체", ["반도체", "HBM", "CXL", "MLCC", "뉴로모픽", "유리 기판", "소캠", "SOCAMM", "퓨리오사"]),
    ("2차전지/에너지", ["2차전지", "전고체", "나트륨이온", "태양광", "풍력", "ESS", "원자력", "원전",
                    "SMR", "수소", "SOFC", "연료전지", "전선", "LPG", "도시가스", "에너지",
                    "페로브스카이트"]),
    ("바이오", ["바이오", "제약", "의료", "보톡스", "보툴리눔", "마이코플라스마", "마이크로바이옴",
              "제대혈", "코로나", "백신", "헬스케어", "구제역", "돼지열병", "폐렴"]),
    ("IT/SW", ["AI", "게임", "로봇", "휴머노이드", "3D 프린터", "메타버스", "클라우드", "블록체인",
              "STO", "토큰", "스테이블코인", "NFT", "핀테크", "SI(", "재택근무", "스마트",
              "LED", "CCTV", "전자파", "냉각", "액침", "피지컬", "영상콘텐츠", "지역화폐",
              "자율주행", "통신", "5G", "6G", "사물인터넷", "IoT", "빅데이터"]),
    ("방위/우주", ["방위", "방산", "전쟁", "테러", "우주", "스페이스X", "SpaceX", "항공기부품",
                "드론", "인공위성", "누리호"]),
    ("소비재", ["엔터", "음식료", "패션", "의류", "홈쇼핑", "광고", "캐릭터", "주류", "교육",
              "스포츠", "올림픽", "월드컵", "여름", "겨울", "출산", "면세", "화장품", "렌터카",
              "야놀자", "엔젤"]),
    ("산업재", ["조선", "해운", "건설", "철강", "화학", "자동차부품", "골판지", "아스콘", "페인트",
              "제지", "윤활유", "사료", "수산", "육계", "콩", "대두", "보험", "금융", "증권",
              "은행", "리츠", "REITs", "SPAC"]),
]


def classify_theme_category(theme_name: str) -> str:
    """
    테마명에서 카테고리 자동 분류

    키워드 매칭으로 분류하며, 매칭 실패 시 '기타' 반환.

    Args:
        theme_name: 테마명

    Returns:
        카테고리 문자열
    """
    if not theme_name:
        return "기타"
    name_upper = theme_name.upper()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw.upper() in name_upper:
                return category
    return "기타"


# ===== 자체 정의 테마 목록 =====

def get_predefined_themes() -> list[dict]:
    """
    자체 정의된 20개 핵심 테마 반환

    네이버/한경에 없거나 중요한 테마를 직접 정의합니다.
    테마 원본 데이터는 _PREDEFINED_THEMES 모듈 변수 참조.

    Returns:
        테마 정보 리스트
    """
    return [
        {
            "name": t["name"],
            "category": t["category"],
            "keywords": t["keywords"],
            "naver_aliases": t.get("naver_aliases", []),
            "source": "predefined"
        }
        for t in _PREDEFINED_THEMES
    ]


def _match_theme_name(found_name: str, theme_name: str, aliases: list[str] = None) -> bool:
    """
    테마명 매칭: 정규화 후 정확 매칭 우선, 별칭 정확 매칭 허용.
    부분 매칭은 사용하지 않아 거짓양성을 방지한다.
    """
    # 1. 정규화 후 정확 매칭
    norm = normalize_theme_name(found_name)
    if norm == theme_name:
        return True
    # 2. 원본 이름 정확 매칭
    if found_name == theme_name:
        return True
    # 3. 별칭 정확 매칭
    if aliases and found_name in aliases:
        return True
    return False


def _search_naver_theme_once(theme_name: str, aliases: list[str] = None) -> Optional[dict]:
    """
    네이버 증권에서 특정 테마를 1회 검색 (내부 헬퍼)

    Args:
        theme_name: 검색할 테마명
        aliases: 네이버 매칭용 별칭 리스트

    Returns:
        테마 정보 딕셔너리 또는 None
    """
    # 네이버 테마 검색 URL (테마명으로 검색)
    search_url = "https://finance.naver.com/sise/theme.naver"

    response = httpx.get(
        search_url,
        headers=DEFAULT_HEADERS,
        timeout=10.0,
        follow_redirects=True
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table", class_="type_1")

    if not table:
        return None

    rows = table.find_all("tr")

    # 테마명과 유사한 것 찾기 (이름 + 별칭 매칭)
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        theme_link = cols[0].find("a")
        if not theme_link:
            continue

        found_name = theme_link.get_text(strip=True)

        if _match_theme_name(found_name, theme_name, aliases):
            theme_url = "https://finance.naver.com" + theme_link.get("href", "")

            # 등락률 추출
            change_rate_elem = cols[1].find("span")
            change_rate = 0.0
            if change_rate_elem:
                change_text = change_rate_elem.get_text(strip=True)
                change_rate = _safe_float(change_text)
                if "하락" in cols[1].get_text() or "down" in str(cols[1]).lower():
                    change_rate = -abs(change_rate)

            # 3일 등락률
            three_day_rate = _safe_float(cols[2].get_text(strip=True)) if len(cols) > 2 else 0.0

            # 종목 수 = 상승 + 보합 + 하락
            stock_count = 0
            if len(cols) >= 6:
                stock_count = _safe_int(cols[3].get_text(strip=True)) + _safe_int(cols[4].get_text(strip=True)) + _safe_int(cols[5].get_text(strip=True))

            logger.debug(f"[{theme_name}] 네이버에서 발견: {found_name} ({change_rate:+.2f}%, {stock_count}종목)")

            return {
                "name": theme_name,  # 원래 검색한 이름 유지
                "naver_name": found_name,
                "stock_count": stock_count,
                "avg_change_rate": change_rate,
                "three_day_rate": three_day_rate,
                "source": "naver_search",
                "url": theme_url
            }

    # 전체 페이지 검색 (최대 5페이지)
    for page in range(2, 6):
        _random_delay()

        page_url = f"{search_url}?&page={page}"
        response = httpx.get(page_url, headers=DEFAULT_HEADERS, timeout=10.0, follow_redirects=True)
        soup = BeautifulSoup(response.text, "lxml")
        table = soup.find("table", class_="type_1")

        if not table:
            continue

        rows = table.find_all("tr")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            theme_link = cols[0].find("a")
            if not theme_link:
                continue

            found_name = theme_link.get_text(strip=True)

            if _match_theme_name(found_name, theme_name, aliases):
                theme_url = "https://finance.naver.com" + theme_link.get("href", "")

                change_rate_elem = cols[1].find("span")
                change_rate = 0.0
                if change_rate_elem:
                    change_text = change_rate_elem.get_text(strip=True)
                    change_rate = _safe_float(change_text)
                    if "하락" in cols[1].get_text() or "down" in str(cols[1]).lower():
                        change_rate = -abs(change_rate)

                three_day_rate = _safe_float(cols[2].get_text(strip=True)) if len(cols) > 2 else 0.0

                # 종목 수 = 상승 + 보합 + 하락
                stock_count = 0
                if len(cols) >= 6:
                    stock_count = _safe_int(cols[3].get_text(strip=True)) + _safe_int(cols[4].get_text(strip=True)) + _safe_int(cols[5].get_text(strip=True))

                logger.debug(f"[{theme_name}] 네이버 페이지{page}에서 발견: {found_name} ({change_rate:+.2f}%, {stock_count}종목)")

                return {
                    "name": theme_name,
                    "naver_name": found_name,
                    "stock_count": stock_count,
                    "avg_change_rate": change_rate,
                    "three_day_rate": three_day_rate,
                    "source": "naver_search",
                    "url": theme_url
                }

    logger.debug(f"[{theme_name}] 네이버에서 찾지 못함")
    return None


def search_naver_theme(theme_name: str, aliases: list[str] = None, max_retries: int = 2) -> Optional[dict]:
    """
    네이버 증권에서 특정 테마를 검색하여 데이터 반환 (재시도 포함)

    predefined 테마가 일반 크롤링에서 누락된 경우 직접 검색합니다.
    네트워크/파싱 에러 시 최대 max_retries회 재시도합니다.

    Args:
        theme_name: 검색할 테마명
        aliases: 네이버 매칭용 별칭 리스트
        max_retries: 최대 재시도 횟수 (기본 2, 총 3회 시도)

    Returns:
        테마 정보 딕셔너리 또는 None
    """
    for attempt in range(max_retries + 1):
        try:
            result = _search_naver_theme_once(theme_name, aliases=aliases)
            # None(찾지 못함)은 재시도 불필요 - 페이지에 없는 것
            return result
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"[{theme_name}] 네이버 검색 실패 ({attempt+1}/{max_retries+1}), 재시도...")
                time.sleep(1)
            else:
                logger.warning(f"[{theme_name}] 네이버 검색 최종 실패: {e}")
                return None


# ===== 통합 크롤링 함수 =====

def crawl_all_themes() -> list[dict]:
    """
    모든 소스에서 테마 데이터 통합 수집

    네이버, 한경, 자체정의 테마를 모두 수집하여 병합합니다.
    predefined 테마는 네이버에서 실제 시장 데이터를 조회합니다.

    Returns:
        통합된 테마 정보 리스트

    Example:
        >>> all_themes = crawl_all_themes()
        >>> print(f"총 {len(all_themes)}개 테마 수집")
    """
    all_themes = []

    logger.info("🔄 전체 테마 크롤링 시작")

    # 1. 네이버 테마 (최대 5페이지로 확장)
    try:
        naver_themes = crawl_naver_themes(max_pages=5)
        all_themes.extend(naver_themes)
    except Exception as e:
        logger.error(f"네이버 테마 수집 실패: {e}")

    _random_delay()

    # 2. KRX 테마지수
    try:
        krx_themes = crawl_krx_themes()
        all_themes.extend(krx_themes)
    except Exception as e:
        logger.error(f"KRX 테마 수집 실패: {e}")

    # 현재 수집된 테마명 목록
    collected_names = {t["name"] for t in all_themes}

    # 3. 자체 정의 테마 - 네이버에서 실제 데이터 조회 (캐시 활용)
    predefined_themes = get_predefined_themes()
    enriched_count = 0
    theme_cache = _load_theme_cache()
    cache_updated = False

    logger.info(f"📊 주요 테마 {len(predefined_themes)}개 데이터 보강 중...")

    for predef in predefined_themes:
        theme_name = predef["name"]
        aliases = predef.get("naver_aliases", [])

        # 이미 수집된 테마면 category/keywords만 보강하고 스킵
        already_collected = theme_name in collected_names
        matched_alias = None
        if not already_collected and aliases:
            for alias in aliases:
                if alias in collected_names:
                    already_collected = True
                    matched_alias = alias
                    break
        if already_collected:
            # 기존 테마에 category/keywords 보강 (네이버 원본에는 없으므로)
            match_name = matched_alias or theme_name
            for t in all_themes:
                if t["name"] == match_name:
                    t["category"] = predef.get("category", "기타")
                    t["keywords"] = predef.get("keywords", [])
                    break
            logger.debug(f"[{theme_name}] 이미 수집됨 - category 보강")
            continue

        # 네이버에서 검색하여 실제 데이터 가져오기 (별칭 포함)
        _random_delay()
        naver_data = search_naver_theme(theme_name, aliases=aliases)

        if naver_data:
            # 네이버에서 찾은 데이터와 predefined 정보 병합
            enriched_theme = {
                **predef,
                **naver_data,
                "category": predef.get("category", "기타"),
                "keywords": predef.get("keywords", []),
            }
            all_themes.append(enriched_theme)
            enriched_count += 1
            logger.info(f"  ✓ [{theme_name}] 데이터 보강 완료: {naver_data.get('avg_change_rate', 0):+.2f}%, {naver_data.get('stock_count', 0)}종목")

            # 캐시 업데이트
            theme_cache[theme_name] = {
                "avg_change_rate": naver_data.get("avg_change_rate", 0.0),
                "stock_count": naver_data.get("stock_count", 0),
                "three_day_rate": naver_data.get("three_day_rate", 0.0),
                "timestamp": now_kst().isoformat(),
            }
            cache_updated = True
        else:
            # 네이버 실패 → 캐시에서 7일 이내 데이터 조회
            cached = _get_cached_theme(theme_cache, theme_name)
            if cached:
                enriched_theme = {
                    **predef,
                    **cached,
                    "category": predef.get("category", "기타"),
                    "keywords": predef.get("keywords", []),
                }
                all_themes.append(enriched_theme)
                enriched_count += 1
            else:
                # 캐시도 없으면 기본값으로 추가 (모멘텀 0점이지만 포함)
                predef_with_defaults = {
                    **predef,
                    "avg_change_rate": 0.0,
                    "stock_count": 15,  # 주요 테마는 최소 15종목 가정
                    "source": "predefined_default"
                }
                all_themes.append(predef_with_defaults)
                logger.warning(f"  ✗ [{theme_name}] 네이버 미발견 & 캐시 없음 - 기본값 사용")

    # 캐시 저장
    if cache_updated:
        _save_theme_cache(theme_cache)
        logger.info(f"💾 테마 캐시 저장 완료 ({len(theme_cache)}개 테마)")

    logger.info(f"📊 주요 테마 {enriched_count}개 데이터 보강 완료")

    # 테마명 정규화 + 카테고리 자동 분류 + 중복 제거
    seen = set()
    unique_themes = []
    for theme in all_themes:
        theme["name"] = normalize_theme_name(theme["name"])
        # 카테고리가 없거나 '기타'이면 자동 분류 시도
        if theme.get("category", "기타") == "기타":
            theme["category"] = classify_theme_category(theme["name"])
        if theme["name"] not in seen:
            seen.add(theme["name"])
            unique_themes.append(theme)

    # 분류 통계 로깅
    cat_counts = {}
    for t in unique_themes:
        cat = t.get("category", "기타")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_stats = ", ".join(f"{k}:{v}" for k, v in sorted(cat_counts.items(), key=lambda x: -x[1]))
    logger.info(f"✅ 전체 테마 {len(unique_themes)}개 수집 완료 (카테고리: {cat_stats})")

    return unique_themes


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🔍 테마 크롤러 테스트")
    print("=" * 60)
    
    # 네이버 테마 테스트
    print("\n📊 네이버 테마 크롤링...")
    naver_themes = crawl_naver_themes(max_pages=1)
    print(f"수집된 테마: {len(naver_themes)}개")
    for theme in naver_themes[:5]:
        print(f"  - {theme['name']}: {theme['avg_change_rate']:+.2f}%")
    
    # 뉴스 카운트 테스트
    if naver_themes:
        print("\n📰 뉴스 카운트 테스트...")
        test_theme = naver_themes[0]["name"]
        news_count = crawl_theme_news_count(test_theme, days=3)
        print(f"  {test_theme}: {news_count}건")
    
    # 자체 정의 테마
    print("\n📋 자체 정의 테마...")
    predefined = get_predefined_themes()
    print(f"정의된 테마: {len(predefined)}개")
    for theme in predefined[:5]:
        print(f"  - {theme['name']} ({theme['category']})")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
