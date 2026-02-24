"""
dart_api.py - DART 공시 API 연동 모듈

이 파일은 금융감독원 전자공시시스템(DART) API를 통해 공시 데이터를 수집합니다.

주요 기능:
- 종목별 최근 공시 조회
- 공시 유형별 필터링
- 주요 공시 감지 (실적, 계약, M&A 등)

사용법:
    from modules.ai_verifier.dart_api import (
        fetch_dart_disclosures,
        fetch_important_disclosures
    )
    
    disclosures = fetch_dart_disclosures("005930", days=30)
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst, validate_stock_code


# ===== 상수 정의 =====
DART_BASE_URL = "https://opendart.fss.or.kr/api"

# 주요 공시 유형 (투자에 중요한 것들)
IMPORTANT_REPORT_TYPES = [
    "A001",  # 사업보고서
    "A002",  # 반기보고서
    "A003",  # 분기보고서
    "B001",  # 주요사항보고서
    "C001",  # 증권신고서
    "D001",  # 공개매수
    "E001",  # 합병등 신고서
    "F001",  # 자산양수도
    "G001",  # 주식교환
    "I001",  # 전환사채
    "J001",  # 주식등의대량보유
]

# 긍정적 키워드
POSITIVE_KEYWORDS = [
    "수주", "계약", "체결", "증가", "상승", "흑자", "전환",
    "배당", "자사주", "매입", "투자", "확대", "신규", "수출"
]

# 부정적 키워드
NEGATIVE_KEYWORDS = [
    "소송", "손실", "적자", "감소", "하락", "철회", "취소",
    "부도", "파산", "횡령", "분식", "조사", "제재", "위반"
]


def _get_api_key() -> str:
    """DART API 키 반환"""
    api_key = os.getenv("DART_API_KEY")
    
    if not api_key:
        try:
            from config import settings
            api_key = settings.DART_API_KEY
        except ImportError:
            pass
    
    return api_key or ""


def _get_corp_code(stock_code: str) -> Optional[str]:
    """
    종목코드로 DART 고유번호(corp_code) 조회
    
    Note: 실제로는 DART에서 제공하는 기업 코드 파일을 다운로드하여
          매핑 테이블을 구축해야 합니다.
    """
    # TODO: 종목코드 → DART 고유번호 매핑 구현
    # 현재는 테스트용으로 동일하게 반환
    return stock_code


def fetch_dart_disclosures(
    stock_code: str,
    days: int = 30,
    max_count: int = 20
) -> list[dict]:
    """
    종목별 DART 공시 조회
    
    Args:
        stock_code: 종목코드
        days: 조회 기간 (일)
        max_count: 최대 조회 수
    
    Returns:
        공시 리스트:
        [
            {
                'title': '분기보고서 (2024.03)',
                'date': '2024-05-15',
                'type': 'A003',
                'url': 'https://...'
            },
            ...
        ]
    """
    stock_code = validate_stock_code(stock_code)
    api_key = _get_api_key()

    if not api_key or api_key.startswith("your_"):
        logger.warning("DART API 키가 설정되지 않았습니다")
        return []
    
    # 날짜 범위 설정
    end_date = now_kst().strftime("%Y%m%d")
    start_date = (now_kst() - timedelta(days=days)).strftime("%Y%m%d")
    
    url = f"{DART_BASE_URL}/list.json"
    
    params = {
        "crtfc_key": api_key,
        "corp_code": _get_corp_code(stock_code),
        "bgn_de": start_date,
        "end_de": end_date,
        "page_count": max_count,
        "sort": "date",
        "sort_mth": "desc"
    }
    
    try:
        response = httpx.get(url, params=params, timeout=15.0)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get("status") != "000":
            logger.warning(f"[{stock_code}] DART 조회 실패: {data.get('message')}")
            return []
        
        disclosures = []
        
        for item in data.get("list", []):
            disclosures.append({
                "title": item.get("report_nm", ""),
                "date": item.get("rcept_dt", "")[:10],
                "type": item.get("pblntf_ty", ""),
                "type_name": item.get("pblntf_detail_ty", ""),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no', '')}",
                "corp_name": item.get("corp_name", ""),
                "corp_code": item.get("corp_code", "")
            })
        
        logger.info(f"[{stock_code}] DART 공시 {len(disclosures)}건 조회")
        return disclosures
        
    except Exception as e:
        logger.error(f"[{stock_code}] DART 공시 조회 실패: {e}")
        return []


def fetch_important_disclosures(
    stock_code: str,
    days: int = 30
) -> list[dict]:
    """
    주요 공시만 필터링하여 조회
    
    투자에 중요한 공시만 반환합니다.
    (실적, 계약, M&A, 유상증자 등)
    
    Args:
        stock_code: 종목코드
        days: 조회 기간
    
    Returns:
        주요 공시 리스트 (중요도 표시 포함)
    """
    all_disclosures = fetch_dart_disclosures(stock_code, days)
    
    important = []
    
    for disc in all_disclosures:
        title = disc.get("title", "").lower()
        
        # 중요도 판단
        importance = "normal"
        sentiment = "neutral"
        
        # 주요 공시 유형 체크
        if disc.get("type") in IMPORTANT_REPORT_TYPES:
            importance = "high"
        
        # 긍정적 키워드 체크
        for keyword in POSITIVE_KEYWORDS:
            if keyword in title:
                sentiment = "positive"
                importance = "high"
                break
        
        # 부정적 키워드 체크
        for keyword in NEGATIVE_KEYWORDS:
            if keyword in title:
                sentiment = "negative"
                importance = "critical"
                break
        
        if importance in ["high", "critical"]:
            disc["importance"] = importance
            disc["sentiment"] = sentiment
            important.append(disc)
    
    logger.info(f"[{stock_code}] 주요 공시 {len(important)}건")
    return important


def analyze_disclosure_sentiment(disclosures: list[dict]) -> dict:
    """
    공시 감성 분석 (간단한 규칙 기반)
    
    Args:
        disclosures: 공시 리스트
    
    Returns:
        감성 분석 결과:
        {
            'positive_count': 3,
            'negative_count': 1,
            'neutral_count': 5,
            'overall_sentiment': 'positive',
            'risk_flags': ['소송']
        }
    """
    positive = 0
    negative = 0
    neutral = 0
    risk_flags = []
    
    for disc in disclosures:
        title = disc.get("title", "").lower()
        
        has_positive = any(kw in title for kw in POSITIVE_KEYWORDS)
        has_negative = any(kw in title for kw in NEGATIVE_KEYWORDS)
        
        if has_negative:
            negative += 1
            # 리스크 플래그 추가
            for kw in NEGATIVE_KEYWORDS:
                if kw in title and kw not in risk_flags:
                    risk_flags.append(kw)
        elif has_positive:
            positive += 1
        else:
            neutral += 1
    
    # 전체 감성 판단
    if negative > positive:
        overall = "negative"
    elif positive > negative:
        overall = "positive"
    else:
        overall = "neutral"
    
    return {
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
        "overall_sentiment": overall,
        "risk_flags": risk_flags
    }


def format_disclosures_for_ai(disclosures: list[dict]) -> str:
    """
    공시를 AI 분석용 텍스트로 포맷팅
    
    Args:
        disclosures: 공시 리스트
    
    Returns:
        포맷팅된 텍스트
    """
    if not disclosures:
        return "최근 30일 내 주요 공시가 없습니다."
    
    lines = []
    lines.append(f"최근 공시 ({len(disclosures)}건):")
    lines.append("")
    
    for i, disc in enumerate(disclosures[:10], 1):
        importance = disc.get("importance", "normal")
        importance_mark = "⚠️" if importance == "critical" else "📌" if importance == "high" else "📄"
        
        lines.append(f"{importance_mark} [{disc.get('date', '')}] {disc.get('title', '')}")
    
    return "\n".join(lines)


# ===== Mock 데이터 (API 키 없을 때 테스트용) =====

def get_mock_disclosures(stock_code: str) -> list[dict]:
    """
    테스트용 모의 공시 데이터 반환
    """
    today = now_kst()

    mock_data = {
        "005930": [  # 삼성전자
            {
                "title": "분기보고서 (2024.03)",
                "date": (today - timedelta(days=5)).strftime("%Y-%m-%d"),
                "type": "A003",
                "importance": "high",
                "sentiment": "neutral"
            },
            {
                "title": "신규 시설투자 결정",
                "date": (today - timedelta(days=10)).strftime("%Y-%m-%d"),
                "type": "B001",
                "importance": "high",
                "sentiment": "positive"
            }
        ],
        "000660": [  # SK하이닉스
            {
                "title": "대규모 공급계약 체결",
                "date": (today - timedelta(days=3)).strftime("%Y-%m-%d"),
                "type": "B001",
                "importance": "high",
                "sentiment": "positive"
            }
        ]
    }
    
    return mock_data.get(stock_code, [])


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("📋 DART 공시 API 테스트")
    print("=" * 60)
    
    api_key = _get_api_key()
    
    if not api_key or api_key.startswith("your_"):
        print("\n⚠️  DART API 키가 설정되지 않았습니다.")
        print("   모의 데이터로 테스트합니다...\n")
        
        # 모의 데이터 테스트
        mock = get_mock_disclosures("005930")
        print(f"삼성전자 공시: {len(mock)}건")
        for d in mock:
            print(f"  - [{d['date']}] {d['title']}")
        
        # 감성 분석 테스트
        sentiment = analyze_disclosure_sentiment(mock)
        print(f"\n감성 분석 결과: {sentiment['overall_sentiment']}")
    else:
        print("\n🔑 API 키 확인됨")
        
        # 실제 API 테스트
        disclosures = fetch_dart_disclosures("005930", days=30)
        print(f"\n삼성전자 공시: {len(disclosures)}건")
        for d in disclosures[:5]:
            print(f"  - [{d['date']}] {d['title'][:30]}...")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
