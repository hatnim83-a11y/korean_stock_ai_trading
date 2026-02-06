"""
claude_analyzer.py - Claude AI 종목 분석 모듈

이 파일은 Claude API를 사용하여 개별 종목을 분석합니다.

주요 기능:
- 뉴스/공시 기반 종목 분석
- 투자 매력도 점수 산출 (0-10)
- 리스크 요인 분석
- 비동기 병렬 처리

사용법:
    from modules.ai_verifier.claude_analyzer import (
        analyze_stock,
        analyze_stocks_batch
    )
    
    result = analyze_stock(stock_info, news, disclosures)
"""

import os
import json
import asyncio
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger

# Anthropic 클라이언트 import
try:
    from anthropic import Anthropic, AsyncAnthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic 패키지가 설치되지 않았습니다")


# ===== 상수 정의 =====
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
MAX_TOKENS = 1500
TEMPERATURE = 0.3

# 종목 분석 프롬프트
STOCK_ANALYSIS_PROMPT = """
당신은 한국 주식시장 전문 애널리스트입니다.
아래 종목에 대한 정보를 분석하여 투자 적합성을 평가해주세요.

## 분석 대상 종목
- 종목명: {stock_name}
- 종목코드: {stock_code}
- 현재가: {price:,}원
- 테마: {theme}
- 수급: 외국인 {foreign:+.0f}억원, 기관 {institution:+.0f}억원 (5일)

## 최근 뉴스 (7일 이내)
{news_text}

## 최근 공시 (30일 이내)
{disclosure_text}

## 분석 요청사항

1. **투자 매력도 점수** (0-10점)
   - 0: 즉시 매도 필요 (심각한 악재)
   - 3: 투자 부적합 (리스크 과다)
   - 5: 중립 (특별한 모멘텀 없음)
   - 7: 투자 양호 (긍정적 전망)
   - 10: 적극 매수 추천 (강력한 호재)

2. **추천 여부** (Yes/No/Hold)
   - Yes: 매수 추천
   - No: 매수 비추천 (악재/리스크)
   - Hold: 관망 (추가 확인 필요)

3. **핵심 근거** (2-3줄)

4. **리스크 요인** (있다면)

5. **목표 수익률** (향후 1-2주)

## 제외 조건 (No로 판단해야 함)
- 심각한 악재 발생 (횡령, 분식회계, 대규모 소송)
- 실적 급격 악화
- 수급 급반전 (외국인/기관 대규모 매도)
- 테마 재료 소진

## 응답 형식 (반드시 JSON으로)
```json
{{
  "sentiment": 7.5,
  "recommend": "Yes",
  "reason": "외국인 대규모 매수 지속, 신규 수주 계약 체결로 실적 개선 기대",
  "risk": "원자재 가격 변동, 경쟁 심화",
  "target_return": 12,
  "confidence": 0.8
}}
```

주의: 반드시 위 JSON 형식으로만 응답하세요.
"""


def _get_api_key() -> str:
    """Anthropic API 키 반환"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if not api_key:
        try:
            from config import settings
            api_key = settings.ANTHROPIC_API_KEY
        except ImportError:
            pass
    
    return api_key or ""


def _get_model() -> str:
    """사용할 Claude 모델 반환"""
    try:
        from config import settings
        return settings.CLAUDE_MODEL
    except ImportError:
        return DEFAULT_MODEL


def _parse_response(response_text: str) -> Optional[dict]:
    """Claude 응답에서 JSON 추출 및 파싱"""
    try:
        text = response_text.strip()
        
        # ```json ... ``` 형식 제거
        if "```json" in text:
            parts = text.split("```json")
            if len(parts) >= 2:
                inner = parts[1].split("```")
                text = inner[0] if inner else parts[1]
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                if text.startswith(("json", "JSON")):
                    text = text[4:]

        result = json.loads(text.strip())

        # 필수 필드 검증
        if "sentiment" not in result or "recommend" not in result:
            raise ValueError("필수 필드 누락")

        # 값 범위 검증
        try:
            result["sentiment"] = max(0.0, min(10.0, float(result["sentiment"])))
        except (ValueError, TypeError):
            result["sentiment"] = 5.0
        
        if "confidence" not in result:
            result["confidence"] = 0.7
        
        if "target_return" not in result:
            result["target_return"] = 10
        
        return result
        
    except Exception as e:
        logger.error(f"JSON 파싱 실패: {e}")
        return None


# ===== 동기 분석 함수 =====

def analyze_stock(
    stock: dict,
    news_text: str = "",
    disclosure_text: str = "",
    model: Optional[str] = None,
    api_key: Optional[str] = None
) -> Optional[dict]:
    """
    단일 종목 AI 분석 (동기)
    
    Args:
        stock: 종목 정보
            {
                'code': '005930',
                'name': '삼성전자',
                'price': 75000,
                'theme': '반도체',
                'foreign_net': 50000000000,
                'institution_net': 30000000000
            }
        news_text: 뉴스 텍스트
        disclosure_text: 공시 텍스트
        model: Claude 모델
        api_key: API 키
    
    Returns:
        분석 결과:
        {
            'sentiment': 7.5,
            'recommend': 'Yes',
            'reason': '...',
            'risk': '...',
            'target_return': 12,
            'confidence': 0.8
        }
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("anthropic 패키지가 설치되지 않았습니다")
        return None
    
    key = api_key or _get_api_key()
    if not key or key.startswith("sk-ant-api03-xxx"):
        logger.warning(f"[{stock.get('code')}] API 키 미설정, 기본값 반환")
        return _get_default_analysis(stock)
    
    model_name = model or _get_model()
    stock_code = stock.get("code", "Unknown")
    stock_name = stock.get("name", "Unknown")
    
    # 프롬프트 생성
    prompt = STOCK_ANALYSIS_PROMPT.format(
        stock_name=stock_name,
        stock_code=stock_code,
        price=stock.get("price", 0),
        theme=stock.get("theme", "미분류"),
        foreign=stock.get("foreign_net", 0) / 100_000_000,
        institution=stock.get("institution_net", 0) / 100_000_000,
        news_text=news_text or "최근 뉴스가 없습니다.",
        disclosure_text=disclosure_text or "최근 공시가 없습니다."
    )
    
    try:
        client = Anthropic(api_key=key)
        
        message = client.messages.create(
            model=model_name,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text
        result = _parse_response(response_text)
        
        if result:
            result["stock_code"] = stock_code
            result["stock_name"] = stock_name
            logger.info(
                f"[{stock_code}] AI 분석 완료: "
                f"{result['sentiment']}/10 ({result['recommend']})"
            )
            return result
        else:
            logger.error(f"[{stock_code}] AI 응답 파싱 실패")
            return None
            
    except Exception as e:
        logger.error(f"[{stock_code}] AI 분석 실패: {e}")
        return None


def _get_default_analysis(stock: dict) -> dict:
    """API 키 없을 때 기본 분석 결과 반환"""
    return {
        "stock_code": stock.get("code", ""),
        "stock_name": stock.get("name", ""),
        "sentiment": 5.0,
        "recommend": "Hold",
        "reason": "AI 분석을 위한 API 키가 설정되지 않았습니다",
        "risk": "API 키 미설정",
        "target_return": 0,
        "confidence": 0.0
    }


# ===== 비동기 분석 함수 =====

async def analyze_stock_async(
    stock: dict,
    news_text: str,
    disclosure_text: str,
    client: "AsyncAnthropic",
    semaphore: asyncio.Semaphore
) -> Optional[dict]:
    """단일 종목 AI 분석 (비동기)"""
    async with semaphore:
        stock_code = stock.get("code", "Unknown")
        stock_name = stock.get("name", "Unknown")
        
        try:
            prompt = STOCK_ANALYSIS_PROMPT.format(
                stock_name=stock_name,
                stock_code=stock_code,
                price=stock.get("price", 0),
                theme=stock.get("theme", "미분류"),
                foreign=stock.get("foreign_net", 0) / 100_000_000,
                institution=stock.get("institution_net", 0) / 100_000_000,
                news_text=news_text or "최근 뉴스가 없습니다.",
                disclosure_text=disclosure_text or "최근 공시가 없습니다."
            )
            
            model_name = _get_model()
            message = await client.messages.create(
                model=model_name,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text
            result = _parse_response(response_text)
            
            if result:
                result["stock_code"] = stock_code
                result["stock_name"] = stock_name
                logger.info(
                    f"[{stock_code}] AI 분석 완료: "
                    f"{result['sentiment']}/10 ({result['recommend']})"
                )
                return result
            
            return None
            
        except Exception as e:
            logger.error(f"[{stock_code}] AI 분석 실패: {e}")
            return None


async def analyze_stocks_batch(
    stocks: list[dict],
    news_dict: dict[str, str],
    disclosure_dict: dict[str, str],
    concurrent_limit: int = 5,
    api_key: Optional[str] = None
) -> list[dict]:
    """
    여러 종목 병렬 AI 분석
    
    Args:
        stocks: 종목 리스트
        news_dict: {종목코드: 뉴스 텍스트} 딕셔너리
        disclosure_dict: {종목코드: 공시 텍스트} 딕셔너리
        concurrent_limit: 동시 실행 제한
        api_key: API 키
    
    Returns:
        분석 결과 리스트
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("anthropic 패키지가 설치되지 않았습니다")
        return []
    
    key = api_key or _get_api_key()
    if not key or key.startswith("sk-ant-api03-xxx"):
        logger.warning("API 키 미설정, 기본값 반환")
        return [_get_default_analysis(s) for s in stocks]
    
    logger.info(f"🤖 {len(stocks)}개 종목 AI 분석 시작 (동시 {concurrent_limit}개)")
    
    client = AsyncAnthropic(api_key=key)
    semaphore = asyncio.Semaphore(concurrent_limit)
    
    tasks = [
        analyze_stock_async(
            stock=stock,
            news_text=news_dict.get(stock.get("code", ""), ""),
            disclosure_text=disclosure_dict.get(stock.get("code", ""), ""),
            client=client,
            semaphore=semaphore
        )
        for stock in stocks
    ]
    
    results = await asyncio.gather(*tasks)
    valid_results = [r for r in results if r is not None]
    
    logger.info(f"✅ AI 분석 완료: {len(valid_results)}/{len(stocks)}개 성공")
    
    return valid_results


def analyze_stocks_sync(
    stocks: list[dict],
    news_dict: dict[str, str],
    disclosure_dict: dict[str, str],
    concurrent_limit: int = 5
) -> list[dict]:
    """여러 종목 병렬 AI 분석 (동기 래퍼)"""
    return asyncio.run(
        analyze_stocks_batch(stocks, news_dict, disclosure_dict, concurrent_limit)
    )


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 Claude 종목 분석 테스트")
    print("=" * 60)
    
    api_key = _get_api_key()
    
    # 테스트 종목
    test_stock = {
        "code": "005930",
        "name": "삼성전자",
        "price": 75000,
        "theme": "AI반도체",
        "foreign_net": 50_000_000_000,
        "institution_net": 30_000_000_000
    }
    
    test_news = """
    [뉴스1] 삼성전자, AI 반도체 수요 급증에 HBM 생산 확대
    삼성전자가 AI 서버용 HBM(고대역폭메모리) 생산을 대폭 늘린다.
    
    [뉴스2] 외국인, 삼성전자 5거래일 연속 순매수
    외국인 투자자들이 삼성전자를 집중 매수하고 있다.
    """
    
    test_disclosure = """
    [공시1] 분기보고서 제출 (2024.03)
    [공시2] 신규 시설투자 결정 (5조원 규모)
    """
    
    if not api_key or api_key.startswith("sk-ant-api03-xxx"):
        print("\n⚠️  API 키가 설정되지 않았습니다.")
        print("   기본 분석 결과를 반환합니다...\n")
        
        result = _get_default_analysis(test_stock)
    else:
        print("\n🔑 API 키 확인됨")
        print(f"모델: {_get_model()}\n")
        
        result = analyze_stock(test_stock, test_news, test_disclosure)
    
    if result:
        print(f"분석 결과:")
        print(f"  점수: {result['sentiment']}/10")
        print(f"  추천: {result['recommend']}")
        print(f"  이유: {result.get('reason', 'N/A')}")
        print(f"  리스크: {result.get('risk', 'N/A')}")
        print(f"  목표 수익률: {result.get('target_return', 0)}%")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
