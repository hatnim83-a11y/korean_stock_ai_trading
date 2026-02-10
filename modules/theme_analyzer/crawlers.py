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
import time
import random
from typing import Optional
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

# 프로젝트 루트의 logger 사용
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst


# ===== 상수 정의 =====
# 크롤링 시 사용할 User-Agent (브라우저처럼 보이게)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
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
            
            if stock_code:
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

        tickers = stock.get_index_ticker_list(market='테마')

        if not tickers:
            logger.warning("KRX 테마지수 티커 조회 결과 0개")
            return themes

        # 최근 영업일 기준 5일간 데이터로 등락률 계산
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=14)).strftime('%Y%m%d')

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


# ===== 뉴스 언급 빈도 크롤링 =====

def crawl_theme_news_count(theme_name: str, days: int = 3) -> int:
    """
    특정 테마의 최근 N일 뉴스 언급 횟수 조회
    
    네이버 뉴스 검색을 통해 테마 관련 뉴스 개수를 파악합니다.
    
    Args:
        theme_name: 테마명 (예: "2차전지")
        days: 조회할 일수 (기본 3일)
    
    Returns:
        뉴스 언급 횟수
        
    Example:
        >>> count = crawl_theme_news_count("2차전지", days=3)
        >>> print(count)
        127
    """
    try:
        # 네이버 뉴스 검색 URL
        # 날짜 필터: ds (시작일), de (종료일)
        today = now_kst()
        start_date = (today - timedelta(days=days)).strftime("%Y.%m.%d")
        end_date = today.strftime("%Y.%m.%d")
        
        # 증권/주식 관련 키워드 추가
        search_query = f"{theme_name} 주식"
        
        url = "https://search.naver.com/search.naver"
        params = {
            "where": "news",
            "query": search_query,
            "sm": "tab_opt",
            "sort": "1",  # 최신순
            "ds": start_date,
            "de": end_date,
            "nso": f"so:dd,p:from{start_date.replace('.', '')}to{end_date.replace('.', '')}"
        }
        
        response = httpx.get(
            url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=10.0,
            follow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "lxml")
        
        # 검색 결과 수 추출
        # "뉴스 약 1,234건" 형태로 표시됨
        result_info = soup.find("div", class_="title_desc")
        if result_info:
            text = result_info.get_text()
            # 숫자 추출
            import re
            numbers = re.findall(r"[\d,]+", text)
            if numbers:
                count = _safe_int(numbers[0])
                logger.debug(f"[{theme_name}] 뉴스 {count}건")
                return count
        
        # 뉴스 아이템 수로 대략 추정 (검색 결과가 없는 경우)
        news_items = soup.select(".news_area, .bx, .list_news li")
        count = len(news_items)
        
        logger.debug(f"[{theme_name}] 뉴스 약 {count}건 (추정)")
        return count
        
    except Exception as e:
        logger.warning(f"[{theme_name}] 뉴스 카운트 조회 실패: {e}")
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
    age_days = (datetime.now() - cached_time).days

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


# ===== 자체 정의 테마 목록 =====

def get_predefined_themes() -> list[dict]:
    """
    자체 정의된 20개 핵심 테마 반환

    네이버/한경에 없거나 중요한 테마를 직접 정의합니다.

    Returns:
        테마 정보 리스트
    """
    predefined = [
        {"name": "2차전지", "category": "신성장", "keywords": ["배터리", "리튬", "전기차"]},
        {"name": "AI반도체", "category": "반도체", "keywords": ["AI칩", "HBM", "GPU"]},
        {"name": "반도체", "category": "반도체", "keywords": ["반도체", "메모리", "파운드리"]},
        {"name": "K-방산", "category": "방위산업", "keywords": ["방산", "무기", "수출"]},
        {"name": "바이오", "category": "헬스케어", "keywords": ["신약", "임상", "바이오텍"]},
        {"name": "로봇", "category": "신성장", "keywords": ["로봇", "자동화", "휴머노이드"]},
        {"name": "자율주행", "category": "모빌리티", "keywords": ["자율주행", "라이다", "센서"]},
        {"name": "원자력", "category": "에너지", "keywords": ["원전", "SMR", "핵융합"]},
        {"name": "수소", "category": "에너지", "keywords": ["수소", "연료전지", "그린수소"]},
        {"name": "조선", "category": "산업재", "keywords": ["조선", "LNG선", "컨테이너선"]},
        {"name": "건설", "category": "산업재", "keywords": ["건설", "아파트", "인프라"]},
        {"name": "금융", "category": "금융", "keywords": ["은행", "증권", "보험"]},
        {"name": "엔터테인먼트", "category": "소비재", "keywords": ["K-POP", "드라마", "콘텐츠"]},
        {"name": "게임", "category": "IT서비스", "keywords": ["게임", "모바일게임", "e스포츠"]},
        {"name": "플랫폼", "category": "IT서비스", "keywords": ["플랫폼", "이커머스", "핀테크"]},
        {"name": "클라우드", "category": "IT서비스", "keywords": ["클라우드", "SaaS", "데이터센터"]},
        {"name": "음식료", "category": "소비재", "keywords": ["식품", "음료", "주류"]},
        {"name": "화장품", "category": "소비재", "keywords": ["화장품", "K-뷰티", "스킨케어"]},
        {"name": "철강", "category": "소재", "keywords": ["철강", "스테인리스", "특수강"]},
        {"name": "화학", "category": "소재", "keywords": ["화학", "석유화학", "정밀화학"]},
        {"name": "통신", "category": "통신", "keywords": ["5G", "6G", "통신장비"]},
    ]

    return [
        {
            "name": t["name"],
            "category": t["category"],
            "keywords": t["keywords"],
            "source": "predefined"
        }
        for t in predefined
    ]


def _search_naver_theme_once(theme_name: str) -> Optional[dict]:
    """
    네이버 증권에서 특정 테마를 1회 검색 (내부 헬퍼)

    Args:
        theme_name: 검색할 테마명

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

    # 테마명과 유사한 것 찾기 (부분 매칭)
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        theme_link = cols[0].find("a")
        if not theme_link:
            continue

        found_name = theme_link.get_text(strip=True)

        # 부분 매칭 (예: "2차전지" in "2차전지 관련주")
        if theme_name in found_name or found_name in theme_name:
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

            if theme_name in found_name or found_name in theme_name:
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


def search_naver_theme(theme_name: str, max_retries: int = 2) -> Optional[dict]:
    """
    네이버 증권에서 특정 테마를 검색하여 데이터 반환 (재시도 포함)

    predefined 테마가 일반 크롤링에서 누락된 경우 직접 검색합니다.
    네트워크/파싱 에러 시 최대 max_retries회 재시도합니다.

    Args:
        theme_name: 검색할 테마명
        max_retries: 최대 재시도 횟수 (기본 2, 총 3회 시도)

    Returns:
        테마 정보 딕셔너리 또는 None
    """
    for attempt in range(max_retries + 1):
        try:
            result = _search_naver_theme_once(theme_name)
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

        # 이미 수집된 테마면 스킵 (실제 데이터가 있음)
        if theme_name in collected_names:
            logger.debug(f"[{theme_name}] 이미 수집됨 - 스킵")
            continue

        # 네이버에서 검색하여 실제 데이터 가져오기
        _random_delay()
        naver_data = search_naver_theme(theme_name)

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
                "timestamp": datetime.now().isoformat(),
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

    # 중복 제거 (테마명 기준, 첫 번째 것 유지)
    seen = set()
    unique_themes = []
    for theme in all_themes:
        if theme["name"] not in seen:
            seen.add(theme["name"])
            unique_themes.append(theme)

    logger.info(f"✅ 전체 테마 {len(unique_themes)}개 수집 완료")

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
