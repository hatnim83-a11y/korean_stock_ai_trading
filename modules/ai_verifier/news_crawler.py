"""
news_crawler.py - 뉴스 크롤링 모듈

이 파일은 종목별 최근 뉴스를 수집합니다.

주요 기능:
- 네이버 금융 종목 뉴스 크롤링
- 뉴스 본문 추출
- 뉴스 요약 생성

사용법:
    from modules.ai_verifier.news_crawler import (
        fetch_stock_news,
        fetch_multiple_stocks_news
    )
    
    news = fetch_stock_news("005930", days=7)
"""

import time
import random
from datetime import datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst


# ===== 상수 정의 =====
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Referer": "https://finance.naver.com/"
}

MIN_DELAY = 0.5
MAX_DELAY = 1.5


def _random_delay():
    """차단 방지를 위한 랜덤 대기"""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def fetch_stock_news(
    stock_code: str,
    days: int = 7,
    max_articles: int = 10
) -> list[dict]:
    """
    종목 관련 뉴스 수집 (네이버 금융)
    
    Args:
        stock_code: 종목코드 (예: "005930")
        days: 수집할 기간 (일)
        max_articles: 최대 수집 기사 수
    
    Returns:
        뉴스 리스트:
        [
            {
                'title': '삼성전자, 반도체 투자 확대...',
                'summary': '삼성전자가 반도체 시설 투자를...',
                'date': '2024-02-01',
                'source': '한국경제',
                'url': 'https://...'
            },
            ...
        ]
        
    Example:
        >>> news = fetch_stock_news("005930", days=7)
        >>> print(news[0]['title'])
        '삼성전자, AI 반도체 투자 확대 발표'
    """
    news_list = []
    
    # 네이버 금융 종목 뉴스 URL
    url = f"https://finance.naver.com/item/news_news.naver"
    
    params = {
        "code": stock_code,
        "page": 1,
        "sm": "title_entity_id.basic",
        "clusterId": ""
    }
    
    logger.debug(f"[{stock_code}] 뉴스 수집 시작 (최근 {days}일)")
    
    try:
        page = 1
        cutoff_date = now_kst() - timedelta(days=days)
        
        while len(news_list) < max_articles and page <= 5:
            params["page"] = page
            
            response = httpx.get(
                url,
                params=params,
                headers=DEFAULT_HEADERS,
                timeout=15.0,
                follow_redirects=True
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            
            # 뉴스 테이블 찾기
            news_table = soup.find("table", class_="type5")
            if not news_table:
                break
            
            rows = news_table.find_all("tr")
            
            found_old = False
            for row in rows:
                # 제목 추출
                title_elem = row.find("a", class_="tit")
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                news_url = "https://finance.naver.com" + title_elem.get("href", "")
                
                # 날짜 추출
                date_elem = row.find("td", class_="date")
                if date_elem:
                    date_str = date_elem.get_text(strip=True)
                    try:
                        news_date = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
                    except ValueError:
                        news_date = now_kst()
                else:
                    news_date = now_kst()
                
                # 기간 체크
                if news_date < cutoff_date:
                    found_old = True
                    break
                
                # 출처 추출
                source_elem = row.find("td", class_="info")
                source = source_elem.get_text(strip=True) if source_elem else "Unknown"
                
                news_list.append({
                    "title": title,
                    "summary": "",  # 상세 페이지에서 추출 필요
                    "date": news_date.strftime("%Y-%m-%d"),
                    "source": source,
                    "url": news_url
                })
                
                if len(news_list) >= max_articles:
                    break
            
            if found_old:
                break
            
            page += 1
            _random_delay()
        
        logger.info(f"[{stock_code}] 뉴스 {len(news_list)}건 수집 완료")
        
    except Exception as e:
        logger.error(f"[{stock_code}] 뉴스 수집 실패: {e}")
    
    return news_list


def fetch_news_content(news_url: str) -> Optional[str]:
    """
    뉴스 본문 추출
    
    Args:
        news_url: 뉴스 URL
    
    Returns:
        뉴스 본문 텍스트 (최대 2000자)
    """
    try:
        response = httpx.get(
            news_url,
            headers=DEFAULT_HEADERS,
            timeout=10.0,
            follow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "lxml")
        
        # 본문 영역 찾기
        content_elem = soup.find("div", id="news_read") or soup.find("div", class_="article_body")
        
        if content_elem:
            # 불필요한 태그 제거
            for tag in content_elem.find_all(["script", "style", "iframe"]):
                tag.decompose()
            
            text = content_elem.get_text(separator=" ", strip=True)
            
            # 길이 제한
            if len(text) > 2000:
                text = text[:2000] + "..."
            
            return text
        
        return None
        
    except Exception as e:
        logger.warning(f"뉴스 본문 추출 실패: {e}")
        return None


def fetch_stock_news_with_content(
    stock_code: str,
    days: int = 7,
    max_articles: int = 5
) -> list[dict]:
    """
    종목 뉴스 수집 (본문 포함)
    
    Args:
        stock_code: 종목코드
        days: 수집 기간
        max_articles: 최대 기사 수
    
    Returns:
        본문이 포함된 뉴스 리스트
    """
    news_list = fetch_stock_news(stock_code, days, max_articles)
    
    for news in news_list:
        if news.get("url"):
            content = fetch_news_content(news["url"])
            if content:
                news["content"] = content
                news["summary"] = content[:200] + "..." if len(content) > 200 else content
            _random_delay()
    
    return news_list


def fetch_multiple_stocks_news(
    stock_codes: list[str],
    days: int = 7,
    max_per_stock: int = 5
) -> dict[str, list[dict]]:
    """
    여러 종목의 뉴스 일괄 수집
    
    Args:
        stock_codes: 종목코드 리스트
        days: 수집 기간
        max_per_stock: 종목당 최대 기사 수
    
    Returns:
        {종목코드: [뉴스 리스트]} 딕셔너리
        
    Example:
        >>> news_dict = fetch_multiple_stocks_news(["005930", "000660"])
        >>> print(len(news_dict["005930"]))
        5
    """
    logger.info(f"📰 {len(stock_codes)}개 종목 뉴스 수집 시작")
    
    result = {}
    
    for code in stock_codes:
        news = fetch_stock_news(code, days, max_per_stock)
        result[code] = news
        _random_delay()
    
    total = sum(len(v) for v in result.values())
    logger.info(f"✅ 총 {total}건 뉴스 수집 완료")
    
    return result


def format_news_for_ai(news_list: list[dict]) -> str:
    """
    뉴스를 AI 분석용 텍스트로 포맷팅
    
    Args:
        news_list: 뉴스 리스트
    
    Returns:
        포맷팅된 텍스트
    """
    if not news_list:
        return "최근 뉴스가 없습니다."
    
    lines = []
    
    for i, news in enumerate(news_list, 1):
        lines.append(f"[뉴스 {i}] ({news.get('date', '날짜 미상')})")
        lines.append(f"제목: {news.get('title', '')}")
        
        content = news.get("content") or news.get("summary", "")
        if content:
            lines.append(f"내용: {content}")
        
        lines.append("")
    
    return "\n".join(lines)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📰 뉴스 크롤러 테스트")
    print("=" * 60)
    
    # 삼성전자 뉴스 테스트
    test_code = "005930"
    print(f"\n종목: {test_code}")
    print("-" * 40)
    
    news = fetch_stock_news(test_code, days=7, max_articles=5)
    
    print(f"수집된 뉴스: {len(news)}건")
    for n in news[:3]:
        print(f"  - [{n['date']}] {n['title'][:40]}...")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
