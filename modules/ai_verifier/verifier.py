"""
verifier.py - AI 검증 파이프라인

이 파일은 스크리닝된 종목에 대해 AI 검증을 수행합니다.

파이프라인:
1. 종목별 뉴스 수집
2. 종목별 공시 수집
3. Claude AI 분석
4. 최종 점수 계산
5. 투자 부적합 종목 필터링

사용법:
    from modules.ai_verifier.verifier import (
        verify_stocks,
        run_daily_verification
    )
    
    verified = verify_stocks(candidates)
    final = run_daily_verification(candidates)
"""

import asyncio
from datetime import date, datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst


# ===== 검증 상수 =====
MIN_AI_SCORE = 5.0  # AI 점수 최소 기준
EXCLUDE_RECOMMENDATIONS = ["No", "Hold"]  # 제외할 추천 유형


def verify_single_stock(
    stock: dict,
    fetch_news: bool = True,
    fetch_disclosure: bool = True
) -> dict:
    """
    단일 종목 AI 검증
    
    뉴스와 공시를 수집하고 AI 분석을 수행합니다.
    
    Args:
        stock: 종목 정보 딕셔너리
        fetch_news: 뉴스 수집 여부
        fetch_disclosure: 공시 수집 여부
    
    Returns:
        AI 검증 결과가 추가된 종목 정보
    """
    from .news_crawler import fetch_stock_news, format_news_for_ai
    from .dart_api import fetch_important_disclosures, format_disclosures_for_ai
    from .claude_analyzer import analyze_stock
    
    result = {**stock}
    stock_code = stock.get("code", "")
    stock_name = stock.get("name", "")
    
    logger.debug(f"[{stock_code}] {stock_name} AI 검증 시작")
    
    # 1. 뉴스 수집
    news_text = ""
    if fetch_news:
        try:
            news = fetch_stock_news(stock_code, days=7, max_articles=5)
            news_text = format_news_for_ai(news)
            result["news_count"] = len(news)
        except Exception as e:
            logger.warning(f"[{stock_code}] 뉴스 수집 실패: {e}")
            result["news_count"] = 0
    
    # 2. 공시 수집
    disclosure_text = ""
    if fetch_disclosure:
        try:
            disclosures = fetch_important_disclosures(stock_code, days=30)
            disclosure_text = format_disclosures_for_ai(disclosures)
            result["disclosure_count"] = len(disclosures)
        except Exception as e:
            logger.warning(f"[{stock_code}] 공시 수집 실패: {e}")
            result["disclosure_count"] = 0
    
    # 3. AI 분석
    try:
        ai_result = analyze_stock(stock, news_text, disclosure_text)
        
        if ai_result:
            result["ai_sentiment"] = ai_result.get("sentiment", 5.0)
            result["ai_recommend"] = ai_result.get("recommend", "Hold")
            result["ai_reason"] = ai_result.get("reason", "")
            result["ai_risk"] = ai_result.get("risk", "")
            result["ai_target_return"] = ai_result.get("target_return", 0)
            result["ai_confidence"] = ai_result.get("confidence", 0.5)
        else:
            result["ai_sentiment"] = 5.0
            result["ai_recommend"] = "Hold"
            result["ai_reason"] = "AI 분석 실패"
            result["ai_confidence"] = 0.0
            
    except Exception as e:
        logger.error(f"[{stock_code}] AI 분석 실패: {e}")
        result["ai_sentiment"] = 5.0
        result["ai_recommend"] = "Hold"
        result["ai_confidence"] = 0.0
    
    # 4. 검증 통과 여부
    ai_score = result.get("ai_sentiment", 0)
    recommend = result.get("ai_recommend", "Hold")
    
    result["ai_passed"] = (
        ai_score >= MIN_AI_SCORE and
        recommend not in EXCLUDE_RECOMMENDATIONS
    )
    
    if result["ai_passed"]:
        logger.info(
            f"✅ [{stock_code}] {stock_name} AI 검증 통과: "
            f"{ai_score}/10 ({recommend})"
        )
    else:
        logger.info(
            f"❌ [{stock_code}] {stock_name} AI 검증 미통과: "
            f"{ai_score}/10 ({recommend})"
        )
    
    return result


async def verify_stocks_async(
    stocks: list[dict],
    concurrent_limit: int = 5
) -> list[dict]:
    """
    여러 종목 병렬 AI 검증
    
    Args:
        stocks: 종목 리스트
        concurrent_limit: 동시 처리 수
    
    Returns:
        검증 결과 리스트
    """
    from .news_crawler import fetch_stock_news, format_news_for_ai
    from .dart_api import get_mock_disclosures, format_disclosures_for_ai
    from .claude_analyzer import analyze_stocks_batch
    
    logger.info(f"🤖 {len(stocks)}개 종목 AI 검증 시작")
    
    # 1. 뉴스 수집 (동기)
    logger.info("📰 뉴스 수집 중...")
    news_dict = {}
    for stock in stocks:
        code = stock.get("code", "")
        try:
            news = fetch_stock_news(code, days=7, max_articles=5)
            news_dict[code] = format_news_for_ai(news)
        except Exception:
            news_dict[code] = "뉴스 수집 실패"
    
    # 2. 공시 수집 (모의 데이터 사용)
    logger.info("📋 공시 수집 중...")
    disclosure_dict = {}
    for stock in stocks:
        code = stock.get("code", "")
        try:
            disclosures = get_mock_disclosures(code)
            disclosure_dict[code] = format_disclosures_for_ai(disclosures)
        except Exception:
            disclosure_dict[code] = "공시 수집 실패"
    
    # 3. AI 분석 (비동기 병렬)
    logger.info("🧠 AI 분석 중...")
    ai_results = await analyze_stocks_batch(
        stocks=stocks,
        news_dict=news_dict,
        disclosure_dict=disclosure_dict,
        concurrent_limit=concurrent_limit
    )
    
    # 4. 결과 병합
    ai_dict = {r.get("stock_code"): r for r in ai_results}
    
    verified = []
    for stock in stocks:
        code = stock.get("code", "")
        result = {**stock}
        
        if code in ai_dict:
            ai = ai_dict[code]
            result["ai_sentiment"] = ai.get("sentiment", 5.0)
            result["ai_recommend"] = ai.get("recommend", "Hold")
            result["ai_reason"] = ai.get("reason", "")
            result["ai_risk"] = ai.get("risk", "")
            result["ai_target_return"] = ai.get("target_return", 0)
            result["ai_confidence"] = ai.get("confidence", 0.5)
        else:
            result["ai_sentiment"] = 5.0
            result["ai_recommend"] = "Hold"
            result["ai_confidence"] = 0.0
        
        # 통과 여부
        result["ai_passed"] = (
            result.get("ai_sentiment", 0) >= MIN_AI_SCORE and
            result.get("ai_recommend") not in EXCLUDE_RECOMMENDATIONS
        )
        
        verified.append(result)
    
    passed = sum(1 for v in verified if v.get("ai_passed"))
    logger.info(f"✅ AI 검증 완료: {passed}/{len(stocks)}개 통과")
    
    return verified


def verify_stocks(
    stocks: list[dict],
    concurrent_limit: int = 5
) -> list[dict]:
    """여러 종목 AI 검증 (동기 래퍼)"""
    return asyncio.run(verify_stocks_async(stocks, concurrent_limit))


def calculate_final_score_with_ai(stock: dict) -> float:
    """
    AI 점수를 포함한 최종 점수 계산
    
    가중치:
    - 기존 점수: 70%
    - AI 점수: 30%
    
    Args:
        stock: AI 검증이 완료된 종목
    
    Returns:
        최종 점수 (0-100)
    """
    base_score = stock.get("final_score", 50)
    ai_score = stock.get("ai_sentiment", 5) * 10  # 0-10 → 0-100
    
    # 가중 평균
    final = base_score * 0.7 + ai_score * 0.3
    
    # AI 추천이 No면 페널티
    if stock.get("ai_recommend") == "No":
        final *= 0.5
    
    return round(final, 2)


def format_verification_report(stocks: list[dict]) -> str:
    """
    AI 검증 결과 리포트 생성
    
    Args:
        stocks: 검증된 종목 리스트
    
    Returns:
        포맷팅된 리포트
    """
    if not stocks:
        return "검증된 종목이 없습니다."
    
    lines = []
    lines.append("━" * 70)
    lines.append(f"🤖 AI 검증 결과 ({now_kst().strftime('%Y-%m-%d %H:%M')})")
    lines.append("━" * 70)
    
    # 통과/미통과 분류
    passed = [s for s in stocks if s.get("ai_passed")]
    failed = [s for s in stocks if not s.get("ai_passed")]
    
    lines.append(f"\n✅ 검증 통과: {len(passed)}개")
    lines.append("─" * 70)
    
    for stock in passed[:10]:
        code = stock.get("code", "?")
        name = stock.get("name", "?")
        sentiment = stock.get("ai_sentiment", 0)
        recommend = stock.get("ai_recommend", "?")
        reason = stock.get("ai_reason", "")[:50]
        
        lines.append(
            f"  {name} ({code}) | "
            f"AI: {sentiment}/10 ({recommend})"
        )
        if reason:
            lines.append(f"    └ {reason}...")
    
    if failed:
        lines.append(f"\n❌ 검증 미통과: {len(failed)}개")
        lines.append("─" * 70)
        
        for stock in failed[:5]:
            code = stock.get("code", "?")
            name = stock.get("name", "?")
            sentiment = stock.get("ai_sentiment", 0)
            recommend = stock.get("ai_recommend", "?")
            
            lines.append(
                f"  {name} ({code}) | "
                f"AI: {sentiment}/10 ({recommend})"
            )
    
    lines.append("")
    lines.append("━" * 70)
    
    return "\n".join(lines)


# ===== 일일 검증 파이프라인 =====

def run_daily_verification(
    candidates: list[dict],
    save_to_db: bool = True,
    use_mock_data: bool = False
) -> list[dict]:
    """
    일일 AI 검증 파이프라인
    
    스크리닝 통과 종목에 대해 AI 검증을 수행합니다.
    
    Args:
        candidates: 스크리닝 통과 종목 리스트
        save_to_db: DB 저장 여부
        use_mock_data: 모의 데이터 사용 여부
    
    Returns:
        최종 투자 후보 리스트
    """
    logger.info("=" * 60)
    logger.info("🤖 일일 AI 검증 시작")
    logger.info("=" * 60)
    
    start_time = now_kst()
    
    if not candidates:
        logger.warning("검증할 종목이 없습니다")
        return []
    
    logger.info(f"검증 대상: {len(candidates)}개 종목")
    
    try:
        if use_mock_data:
            # 모의 검증 (API 없이)
            verified = _mock_verification(candidates)
        else:
            # 실제 AI 검증
            verified = verify_stocks(candidates, concurrent_limit=5)
        
        # AI 점수 포함 최종 점수 재계산
        for stock in verified:
            stock["final_score_with_ai"] = calculate_final_score_with_ai(stock)
        
        # 통과한 종목만 필터링 및 정렬
        passed = [s for s in verified if s.get("ai_passed")]
        passed.sort(key=lambda x: x.get("final_score_with_ai", 0), reverse=True)
        
        # DB 저장
        if save_to_db and passed:
            try:
                from database import get_database
                
                db = get_database()
                stocks_to_save = [
                    {
                        "stock_code": s.get("code"),
                        "stock_name": s.get("name"),
                        "theme": s.get("theme"),
                        "supply_score": s.get("supply_score"),
                        "technical_score": s.get("technical_score"),
                        "ai_sentiment": s.get("ai_sentiment"),
                        "final_score": s.get("final_score_with_ai"),
                        "selected": True
                    }
                    for s in passed
                ]
                db.save_screened_stocks(stocks_to_save, now_kst().date())
                db.close()
                
                logger.info("💾 검증 결과 DB 저장 완료")
                
            except Exception as e:
                logger.error(f"DB 저장 실패: {e}")
        
        # 리포트 출력
        report = format_verification_report(verified)
        print(report)
        
        elapsed = (now_kst() - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"✅ AI 검증 완료 ({elapsed:.1f}초, {len(passed)}개 통과)")
        logger.info("=" * 60)
        
        return passed
        
    except Exception as e:
        logger.error(f"AI 검증 실패: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return []


def _mock_verification(stocks: list[dict]) -> list[dict]:
    """모의 AI 검증 (테스트용)"""
    import random
    
    verified = []
    
    for stock in stocks:
        result = {**stock}
        
        # 랜덤 AI 점수 (테스트용)
        ai_score = random.uniform(4, 9)
        recommend = "Yes" if ai_score >= 6 else "Hold" if ai_score >= 4.5 else "No"
        
        result["ai_sentiment"] = round(ai_score, 1)
        result["ai_recommend"] = recommend
        result["ai_reason"] = "모의 검증 데이터"
        result["ai_risk"] = "테스트용"
        result["ai_target_return"] = int(ai_score * 2)
        result["ai_confidence"] = 0.8
        result["ai_passed"] = ai_score >= MIN_AI_SCORE and recommend not in EXCLUDE_RECOMMENDATIONS
        
        verified.append(result)
    
    return verified


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 AI 검증 모듈 테스트")
    print("=" * 60)
    
    # 테스트 후보 종목
    test_candidates = [
        {
            "code": "005930", "name": "삼성전자",
            "price": 75000, "theme": "AI반도체",
            "foreign_net": 50_000_000_000,
            "institution_net": 30_000_000_000,
            "final_score": 85
        },
        {
            "code": "373220", "name": "LG에너지솔루션",
            "price": 420000, "theme": "2차전지",
            "foreign_net": 40_000_000_000,
            "institution_net": 25_000_000_000,
            "final_score": 82
        },
        {
            "code": "000660", "name": "SK하이닉스",
            "price": 150000, "theme": "AI반도체",
            "foreign_net": 80_000_000_000,
            "institution_net": 45_000_000_000,
            "final_score": 88
        }
    ]
    
    print(f"\n테스트 종목: {len(test_candidates)}개")
    for s in test_candidates:
        print(f"  - {s['name']} ({s['code']})")
    
    print("\n모의 AI 검증 실행...")
    print("-" * 60)
    
    verified = run_daily_verification(
        candidates=test_candidates,
        save_to_db=False,
        use_mock_data=True
    )
    
    print(f"\n최종 통과: {len(verified)}개")
    for v in verified:
        print(
            f"  - {v['name']}: "
            f"AI {v['ai_sentiment']}/10, "
            f"최종 {v['final_score_with_ai']:.1f}점"
        )
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)
