"""
ai_analyzer.py - Claude AI 테마 감성 분석 모듈

이 파일은 Claude API를 사용하여 테마에 대한 투자 전망을 분석합니다.

주요 기능:
- 테마별 뉴스 기반 감성 분석
- 투자 매력도 점수 산출 (0-10)
- 리스크 요인 분석
- 비동기 병렬 처리 지원

사용법:
    from modules.theme_analyzer.ai_analyzer import (
        analyze_theme_sentiment,
        analyze_themes_batch
    )
    
    # 단일 테마 분석
    result = analyze_theme_sentiment("2차전지", news_text)
    
    # 여러 테마 비동기 분석
    results = await analyze_themes_batch(themes_with_news)
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
    logger.warning("anthropic 패키지가 설치되지 않았습니다. pip install anthropic")


# ===== 상수 정의 =====
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1000
TEMPERATURE = 0.3  # 일관성을 위해 낮게 설정

# 분석 프롬프트 템플릿
THEME_ANALYSIS_PROMPT = """
당신은 한국 주식시장 전문 애널리스트입니다.
아래 테마에 대한 최근 뉴스를 분석하여 투자 전망을 평가해주세요.

## 분석 대상 테마: {theme_name}

## 최근 뉴스 (최근 7일):
{news_text}

## 분석 요청사항:
1. **투자 매력도 점수** (0-10점)
   - 0점: 매우 부정적 (강력한 악재, 규제, 산업 위축)
   - 5점: 중립 (특별한 모멘텀 없음)
   - 10점: 매우 긍정적 (강력한 호재, 정책 지원, 수요 폭발)

2. **핵심 근거** (2-3줄 요약)

3. **리스크 요인** (있다면)

4. **향후 1개월 전망** (상승/중립/하락)

## 응답 형식 (반드시 JSON으로):
```json
{{
  "score": 7.5,
  "reason": "정부의 2차전지 R&D 예산 증액과 미국 IRA 보조금 수혜 지속. 글로벌 전기차 판매량 회복세.",
  "risk": "중국 저가 배터리 공세, 원자재 가격 변동성",
  "outlook": "상승",
  "confidence": 0.8
}}
```

주의사항:
- 반드시 위 JSON 형식으로만 응답하세요.
- score는 소수점 한자리까지 (예: 7.5)
- confidence는 분석 확신도 (0.0 ~ 1.0)
"""


def _parse_claude_response(response_text: str) -> dict:
    """
    Claude 응답에서 JSON 추출 및 파싱
    
    Claude가 가끔 ```json ``` 마크다운으로 감싸서 응답하므로
    이를 처리합니다.
    
    Args:
        response_text: Claude의 원본 응답 텍스트
        
    Returns:
        파싱된 딕셔너리 또는 기본값
    """
    try:
        # JSON 블록 추출 시도
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

        # JSON 파싱
        result = json.loads(text.strip())

        # 필수 필드 검증
        required_fields = ["score", "reason", "outlook"]
        for field in required_fields:
            if field not in result:
                raise ValueError(f"필수 필드 누락: {field}")

        # score 범위 검증 (0-10)
        try:
            result["score"] = max(0.0, min(10.0, float(result["score"])))
        except (ValueError, TypeError):
            result["score"] = 5.0
        
        # confidence 기본값
        if "confidence" not in result:
            result["confidence"] = 0.7
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 실패: {e}")
        logger.debug(f"원본 응답: {response_text[:500]}")
        return None
        
    except Exception as e:
        logger.error(f"응답 파싱 실패: {e}")
        return None


def _get_api_key() -> str:
    """
    Anthropic API 키 반환
    
    환경 변수 또는 config에서 가져옵니다.
    """
    # 환경 변수에서 먼저 확인
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if not api_key:
        try:
            from config import settings
            api_key = settings.ANTHROPIC_API_KEY
        except ImportError:
            pass
    
    return api_key or ""


def _get_model() -> str:
    """
    사용할 Claude 모델 반환
    """
    try:
        from config import settings
        return settings.CLAUDE_MODEL
    except ImportError:
        return DEFAULT_MODEL


# ===== 동기 분석 함수 =====

def analyze_theme_sentiment(
    theme_name: str,
    news_text: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None
) -> Optional[dict]:
    """
    단일 테마에 대한 AI 감성 분석 (동기)
    
    Args:
        theme_name: 테마명 (예: "2차전지")
        news_text: 최근 뉴스 텍스트
        model: 사용할 Claude 모델 (기본값: config에서 로드)
        api_key: API 키 (기본값: 환경변수에서 로드)
    
    Returns:
        분석 결과 딕셔너리:
        {
            'score': 7.5,
            'reason': '...',
            'risk': '...',
            'outlook': '상승',
            'confidence': 0.8
        }
        
        분석 실패 시 None
        
    Example:
        >>> result = analyze_theme_sentiment(
        >>>     "2차전지",
        >>>     "LG에너지솔루션이 미국 공장 증설을 발표..."
        >>> )
        >>> print(result['score'])
        7.5
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("anthropic 패키지가 설치되지 않았습니다")
        return None
    
    # API 키 확인
    key = api_key or _get_api_key()
    if not key:
        logger.error("ANTHROPIC_API_KEY가 설정되지 않았습니다")
        return None
    
    # 모델 확인
    model_name = model or _get_model()
    
    # 뉴스 텍스트가 너무 길면 잘라내기 (토큰 절약)
    max_news_length = 8000  # 약 2000 토큰
    if len(news_text) > max_news_length:
        news_text = news_text[:max_news_length] + "\n\n[... 이하 생략 ...]"
    
    # 뉴스가 없는 경우 기본 분석
    if not news_text or len(news_text.strip()) < 50:
        logger.warning(f"[{theme_name}] 뉴스 데이터 부족, 기본값 반환")
        return {
            "score": 5.0,
            "reason": "분석 가능한 뉴스가 부족합니다",
            "risk": "정보 부족",
            "outlook": "중립",
            "confidence": 0.3
        }
    
    try:
        # Claude 클라이언트 생성
        client = Anthropic(api_key=key)
        
        # 프롬프트 생성
        prompt = THEME_ANALYSIS_PROMPT.format(
            theme_name=theme_name,
            news_text=news_text
        )
        
        logger.debug(f"[{theme_name}] AI 분석 요청 중...")
        
        # API 호출
        message = client.messages.create(
            model=model_name,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        # 응답 추출
        response_text = message.content[0].text
        
        # 응답 파싱
        result = _parse_claude_response(response_text)
        
        if result:
            logger.info(
                f"[{theme_name}] AI 분석 완료: "
                f"{result['score']}/10 ({result['outlook']})"
            )
            return result
        else:
            logger.error(f"[{theme_name}] AI 응답 파싱 실패")
            return None
        
    except Exception as e:
        logger.error(f"[{theme_name}] AI 분석 실패: {e}")
        return None


# ===== 비동기 분석 함수 =====

async def analyze_theme_sentiment_async(
    theme_name: str,
    news_text: str,
    client: "AsyncAnthropic",
    semaphore: asyncio.Semaphore
) -> Optional[dict]:
    """
    단일 테마에 대한 AI 감성 분석 (비동기)
    
    여러 테마를 병렬로 분석할 때 사용합니다.
    
    Args:
        theme_name: 테마명
        news_text: 뉴스 텍스트
        client: AsyncAnthropic 클라이언트
        semaphore: 동시 실행 제한 세마포어
    
    Returns:
        분석 결과 딕셔너리 또는 None
    """
    async with semaphore:
        try:
            # 뉴스 텍스트 길이 제한
            max_news_length = 8000
            if len(news_text) > max_news_length:
                news_text = news_text[:max_news_length] + "\n\n[... 이하 생략 ...]"
            
            # 뉴스 부족 시 기본값
            if not news_text or len(news_text.strip()) < 50:
                return {
                    "theme_name": theme_name,
                    "score": 5.0,
                    "reason": "분석 가능한 뉴스가 부족합니다",
                    "risk": "정보 부족",
                    "outlook": "중립",
                    "confidence": 0.3
                }
            
            # 프롬프트 생성
            prompt = THEME_ANALYSIS_PROMPT.format(
                theme_name=theme_name,
                news_text=news_text
            )
            
            # API 호출 (비동기)
            model_name = _get_model()
            message = await client.messages.create(
                model=model_name,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            # 응답 파싱
            response_text = message.content[0].text
            result = _parse_claude_response(response_text)
            
            if result:
                result["theme_name"] = theme_name
                logger.info(
                    f"[{theme_name}] AI 분석 완료: "
                    f"{result['score']}/10 ({result['outlook']})"
                )
                return result
            else:
                logger.error(f"[{theme_name}] AI 응답 파싱 실패")
                return None
                
        except Exception as e:
            logger.error(f"[{theme_name}] AI 분석 실패: {e}")
            return None


async def analyze_themes_batch(
    themes_with_news: list[dict],
    concurrent_limit: int = 5,
    api_key: Optional[str] = None
) -> list[dict]:
    """
    여러 테마에 대한 AI 감성 분석 (비동기 병렬)
    
    동시 실행 제한을 두고 여러 테마를 병렬로 분석합니다.
    
    Args:
        themes_with_news: 테마 및 뉴스 리스트
            [
                {'name': '2차전지', 'news': '뉴스 텍스트...'},
                {'name': 'AI반도체', 'news': '뉴스 텍스트...'},
                ...
            ]
        concurrent_limit: 동시 실행 제한 (기본 5개)
        api_key: API 키 (기본값: 환경변수)
    
    Returns:
        분석 결과 리스트 (실패한 것 제외)
        
    Example:
        >>> themes = [
        >>>     {'name': '2차전지', 'news': '...'},
        >>>     {'name': 'AI반도체', 'news': '...'}
        >>> ]
        >>> results = await analyze_themes_batch(themes)
        >>> print(len(results))
        2
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("anthropic 패키지가 설치되지 않았습니다")
        return []
    
    # API 키 확인
    key = api_key or _get_api_key()
    if not key:
        logger.error("ANTHROPIC_API_KEY가 설정되지 않았습니다")
        return []
    
    logger.info(f"🤖 {len(themes_with_news)}개 테마 AI 분석 시작 (동시 {concurrent_limit}개)")
    
    # 비동기 클라이언트 생성
    client = AsyncAnthropic(api_key=key)
    
    # 동시 실행 제한 세마포어
    semaphore = asyncio.Semaphore(concurrent_limit)
    
    # 비동기 태스크 생성
    tasks = [
        analyze_theme_sentiment_async(
            theme_name=theme.get("name", theme.get("theme_name", "Unknown")),
            news_text=theme.get("news", theme.get("news_text", "")),
            client=client,
            semaphore=semaphore
        )
        for theme in themes_with_news
    ]
    
    # 병렬 실행
    results = await asyncio.gather(*tasks)
    
    # None 제외
    valid_results = [r for r in results if r is not None]
    
    logger.info(
        f"✅ AI 분석 완료: {len(valid_results)}/{len(themes_with_news)}개 성공"
    )
    
    return valid_results


def analyze_themes_sync(
    themes_with_news: list[dict],
    concurrent_limit: int = 5,
    api_key: Optional[str] = None
) -> list[dict]:
    """
    여러 테마에 대한 AI 감성 분석 (동기 래퍼)
    
    asyncio를 직접 다루지 않고 동기 함수처럼 호출할 수 있습니다.
    
    Args:
        themes_with_news: 테마 및 뉴스 리스트
        concurrent_limit: 동시 실행 제한
        api_key: API 키
    
    Returns:
        분석 결과 리스트
        
    Example:
        >>> themes = [
        >>>     {'name': '2차전지', 'news': '...'},
        >>>     {'name': 'AI반도체', 'news': '...'}
        >>> ]
        >>> results = analyze_themes_sync(themes)
    """
    return asyncio.run(
        analyze_themes_batch(themes_with_news, concurrent_limit, api_key)
    )


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 AI 테마 분석 테스트")
    print("=" * 60)
    
    # API 키 확인
    api_key = _get_api_key()
    if not api_key or api_key.startswith("your_") or api_key.startswith("sk-ant-api03-xxx"):
        print("\n⚠️  ANTHROPIC_API_KEY가 설정되지 않았습니다.")
        print("   .env 파일에 실제 API 키를 설정해주세요.")
        print("\n테스트 데이터로 파싱 로직만 테스트합니다...")
        
        # 파싱 테스트
        test_response = '''
```json
{
  "score": 8.0,
  "reason": "정부의 2차전지 R&D 예산 증액과 글로벌 전기차 수요 회복",
  "risk": "중국 저가 배터리 공세",
  "outlook": "상승",
  "confidence": 0.85
}
```
'''
        result = _parse_claude_response(test_response)
        if result:
            print(f"\n✅ 파싱 테스트 성공:")
            print(f"   점수: {result['score']}/10")
            print(f"   전망: {result['outlook']}")
            print(f"   이유: {result['reason']}")
    else:
        print("\n🔑 API 키 확인됨")
        print(f"   모델: {_get_model()}")
        
        # 실제 API 테스트
        print("\n📊 테마 분석 테스트...")
        test_news = """
        [뉴스1] LG에너지솔루션, 미국 제2공장 착공... 2025년 양산 목표
        전기차 배터리 수요 급증에 대응하기 위해 LG에너지솔루션이 
        미국 오하이오주에 제2공장을 착공했다고 밝혔다.
        
        [뉴스2] 정부, 2차전지 R&D에 5조원 투자 계획 발표
        산업통상자원부는 2025년까지 2차전지 분야에 5조원 규모의 
        R&D 투자를 진행한다고 발표했다.
        
        [뉴스3] 글로벌 전기차 판매량 전년 대비 35% 증가
        국제에너지기구(IEA)에 따르면 2024년 전기차 판매량이 
        전년 대비 35% 증가한 것으로 나타났다.
        """
        
        result = analyze_theme_sentiment("2차전지", test_news)
        
        if result:
            print(f"\n✅ 분석 결과:")
            print(f"   점수: {result['score']}/10")
            print(f"   전망: {result['outlook']}")
            print(f"   이유: {result['reason']}")
            print(f"   리스크: {result.get('risk', 'N/A')}")
            print(f"   확신도: {result.get('confidence', 'N/A')}")
        else:
            print("\n❌ 분석 실패")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
