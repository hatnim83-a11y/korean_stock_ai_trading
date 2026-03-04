"""
analyzer.py - Post-Trade 핵심 분석 로직

매도 후 주가 추이를 수집하고, Claude AI로 분석하여 교훈을 도출한다.
"""

import json
import os
from datetime import date, datetime, timedelta
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst

from modules.post_trade_analyzer.price_tracker import PostTradePriceTracker
from modules.post_trade_analyzer.prompts import (
    INDIVIDUAL_ANALYSIS_PROMPT,
    WEEKLY_SUMMARY_PROMPT,
)

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("[PostTradeAnalyzer] anthropic 패키지 미설치")

# 상수
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500
MAX_TOKENS_WEEKLY = 2000
TEMPERATURE = 0.3
MIN_DAYS_AFTER_SELL = 5


class PostTradeAnalyzer:
    """매도 후 사후 분석 엔진"""

    def __init__(self, db=None):
        """
        Args:
            db: Database 인스턴스 (None이면 내부 생성)
        """
        self._db = db
        self._tracker = PostTradePriceTracker()
        self._client = None

    # ===== 내부 헬퍼 =====

    def _get_db(self):
        """DB 인스턴스 반환 (lazy init)"""
        if self._db is None:
            from database import Database
            self._db = Database()
            self._db.connect()
        return self._db

    def _get_client(self) -> Optional["Anthropic"]:
        """Anthropic 클라이언트 반환 (lazy init)"""
        if not ANTHROPIC_AVAILABLE:
            return None
        if self._client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                try:
                    from config import settings
                    api_key = settings.ANTHROPIC_API_KEY
                except (ImportError, AttributeError):
                    pass
            if not api_key:
                logger.warning("[PostTradeAnalyzer] ANTHROPIC_API_KEY 미설정")
                return None
            self._client = Anthropic(api_key=api_key)
        return self._client

    def _get_model(self) -> str:
        """사용할 Claude 모델 반환"""
        try:
            from config import settings
            return settings.CLAUDE_MODEL
        except (ImportError, AttributeError):
            return DEFAULT_MODEL

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """Claude 응답에서 JSON 추출"""
        try:
            raw = text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                parts = raw.split("```")
                if len(parts) >= 2:
                    raw = parts[1]
                    if raw.startswith(("json", "JSON")):
                        raw = raw[4:]
            return json.loads(raw.strip())
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            logger.warning(f"[PostTradeAnalyzer] JSON 파싱 실패: {e}")
            return None

    # ===== 개별 분석 =====

    def _build_price_table(self, prices: list[dict]) -> str:
        """주가 추이를 텍스트 테이블로 변환"""
        if not prices:
            return "(데이터 없음)"
        lines = ["| D+N | 날짜 | 종가 | 고가 | 저가 | 매도가 대비 |"]
        lines.append("|-----|------|------|------|------|------------|")
        for p in prices:
            lines.append(
                f"| D+{p['days_after_sell']} | {p['check_date']} "
                f"| {p.get('close_price', 0):,.0f} "
                f"| {p.get('high_price', 0):,.0f} "
                f"| {p.get('low_price', 0):,.0f} "
                f"| {p.get('change_from_sell', 0):+.2f}% |"
            )
        return "\n".join(lines)

    def _analyze_single(self, review: dict, prices: list[dict]) -> Optional[dict]:
        """단일 매매 건 AI 분석"""
        client = self._get_client()
        if not client:
            return None

        price_table = self._build_price_table(prices)

        prompt = INDIVIDUAL_ANALYSIS_PROMPT.format(
            stock_name=review.get("stock_name", ""),
            stock_code=review.get("stock_code", ""),
            theme=review.get("theme", "N/A"),
            buy_date=review.get("buy_date", ""),
            sell_date=review.get("sell_date", ""),
            buy_price=int(review.get("buy_price") or 0),
            sell_price=int(review.get("sell_price") or 0),
            hold_days=review.get("hold_days", 0),
            profit_rate=float(review.get("profit_rate") or 0),
            sell_reason=review.get("sell_reason", ""),
            strategy_type=review.get("strategy_type", ""),
            trailing_level=review.get("trailing_level") or "N/A",
            max_profit=float(review.get("max_profit_during_hold") or 0),
            price_table=price_table,
        )

        try:
            response = client.messages.create(
                model=self._get_model(),
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.content[0].text
            result = self._parse_json_response(response_text)

            if result:
                # 값 범위 검증
                score = result.get("timing_score", 5)
                try:
                    result["timing_score"] = max(0, min(10, int(score)))
                except (ValueError, TypeError):
                    result["timing_score"] = 5
                return result

        except Exception as e:
            logger.error(f"[PostTradeAnalyzer] AI 분석 실패 ({review.get('stock_name')}): {e}")

        return None

    # ===== 일일 분석 =====

    def run_daily_analysis(self) -> list[dict]:
        """
        매일 17:00에 실행되는 일일 분석

        Returns:
            분석 완료된 결과 리스트
        """
        db = self._get_db()
        reviews = db.get_reviews_ready_for_analysis(min_days=MIN_DAYS_AFTER_SELL)

        if not reviews:
            logger.info("[PostTradeAnalyzer] 분석 대상 없음 (D+5 미달 또는 전부 분석 완료)")
            return []

        logger.info(f"[PostTradeAnalyzer] 분석 대상: {len(reviews)}건")
        results = []

        for review in reviews:
            review_id = review["id"]
            stock_code = review["stock_code"]
            stock_name = review.get("stock_name", stock_code)
            sell_date_str = review.get("sell_date", "")
            sell_price = float(review.get("sell_price") or 0)

            try:
                sell_date = date.fromisoformat(str(sell_date_str))
            except (ValueError, TypeError):
                logger.warning(f"[PostTradeAnalyzer] sell_date 파싱 실패: {sell_date_str}")
                continue

            logger.info(f"[PostTradeAnalyzer] 분석 중: {stock_name} ({stock_code}), 매도일={sell_date}")

            # 1) 매도 후 주가 수집
            prices = self._tracker.collect_post_trade_prices(
                stock_code=stock_code,
                sell_date=sell_date,
                sell_price=sell_price,
                num_days=MIN_DAYS_AFTER_SELL,
            )

            # 2) DB에 주가 추이 저장
            if prices:
                db.save_post_trade_prices(review_id, prices)

            # 3) AI 분석
            ai_result = self._analyze_single(review, prices)

            if ai_result:
                ai_review_text = json.dumps(ai_result, ensure_ascii=False, indent=2)
                lesson = ai_result.get("lesson", "")

                # 4) DB 업데이트
                db.update_trade_review_ai(review_id, ai_review_text, lesson)

                results.append({
                    "review_id": review_id,
                    "stock_name": stock_name,
                    "stock_code": stock_code,
                    "profit_rate": review.get("profit_rate", 0),
                    "sell_price": sell_price,
                    "sell_reason": review.get("sell_reason", ""),
                    "timing_score": ai_result.get("timing_score", 5),
                    "lesson": lesson,
                    "overall": ai_result.get("overall_assessment", "Neutral"),
                    "post_prices": prices,
                })
                logger.info(
                    f"[PostTradeAnalyzer] 분석 완료: {stock_name} "
                    f"타이밍={ai_result.get('timing_score')}/10 "
                    f"평가={ai_result.get('overall_assessment')}"
                )
            else:
                logger.warning(f"[PostTradeAnalyzer] AI 분석 결과 없음: {stock_name}")

        logger.info(f"[PostTradeAnalyzer] 일일 분석 완료: {len(results)}/{len(reviews)}건")
        return results

    # ===== 주간 종합 =====

    def generate_weekly_summary(self) -> Optional[dict]:
        """
        주간 종합 분석 (금요일 17:30)

        Returns:
            주간 종합 분석 결과 dict (또는 None)
        """
        db = self._get_db()
        today = now_kst().date()

        # 이번 주 (월~금) 분석된 건 조회
        week_start = today - timedelta(days=today.weekday())
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM trade_reviews
                WHERE ai_review IS NOT NULL
                  AND sell_date >= ?
                  AND sell_date <= ?
                ORDER BY sell_date ASC
            """, (week_start.isoformat(), today.isoformat()))
            reviews = [dict(row) for row in cursor.fetchall()]

        if not reviews:
            logger.info("[PostTradeAnalyzer] 주간 분석 대상 없음")
            return None

        logger.info(f"[PostTradeAnalyzer] 주간 종합: {len(reviews)}건")

        # 전략별 집계
        strategy_stats = {}
        individual_lines = []

        for r in reviews:
            st = r.get("strategy_type", "unknown")
            if st not in strategy_stats:
                strategy_stats[st] = {"count": 0, "scores": [], "profits": []}
            strategy_stats[st]["count"] += 1
            strategy_stats[st]["profits"].append(float(r.get("profit_rate") or 0))

            try:
                ai_data = json.loads(r.get("ai_review", "{}"))
                score = ai_data.get("timing_score", 5)
                strategy_stats[st]["scores"].append(score)
            except (json.JSONDecodeError, TypeError):
                pass

            individual_lines.append(
                f"- {r.get('stock_name')} ({r.get('stock_code')}): "
                f"수익률 {r.get('profit_rate', 0):+.1f}%, "
                f"사유={r.get('sell_reason', '')}, "
                f"교훈={r.get('lesson_learned', 'N/A')}"
            )

        # 전략별 요약 텍스트
        strategy_lines = []
        for st, data in strategy_stats.items():
            avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
            avg_profit = sum(data["profits"]) / len(data["profits"]) if data["profits"] else 0
            strategy_lines.append(
                f"- {st}: {data['count']}건, 평균 타이밍 {avg_score:.1f}/10, 평균 수익률 {avg_profit:+.1f}%"
            )

        prompt = WEEKLY_SUMMARY_PROMPT.format(
            total_count=len(reviews),
            strategy_summary="\n".join(strategy_lines) or "(없음)",
            individual_summaries="\n".join(individual_lines[:15]) or "(없음)",
        )

        client = self._get_client()
        if not client:
            logger.warning("[PostTradeAnalyzer] Claude API 미사용 가능 — 주간 종합 스킵")
            return None

        try:
            response = client.messages.create(
                model=self._get_model(),
                max_tokens=MAX_TOKENS_WEEKLY,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
            result = self._parse_json_response(response.content[0].text)
            if result:
                logger.info(f"[PostTradeAnalyzer] 주간 종합 완료: 평균 타이밍 {result.get('avg_timing_score', 0)}/10")
                return result

        except Exception as e:
            logger.error(f"[PostTradeAnalyzer] 주간 종합 실패: {e}")

        return None
