"""
weekly_aggregator.py - 화요일 가중 집계 로직

최근 6영업일의 일별 테마 점수를 최근일 가중 평균으로 집계하여
화요일 테마 선정에 사용한다.
"""

from datetime import date, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import now_kst, is_trading_day

# 가중치: 가장 최근→오래된 순 (6일)
# D-0(월)=0.25, D-1(금)=0.20, D-2(목)=0.18, D-3(수)=0.15, D-4(화)=0.12, D-5(전주 월)=0.10
DAILY_WEIGHTS = [0.25, 0.20, 0.18, 0.15, 0.12, 0.10]
NUM_LOOKBACK_DAYS = 6


def get_recent_trading_days(base_date: date, count: int = NUM_LOOKBACK_DAYS) -> list[date]:
    """
    base_date 이전(포함)의 최근 N영업일 목록 반환 (최근→오래된 순)

    Args:
        base_date: 기준일 (화요일 아침이면 전날 월요일부터)
        count: 필요한 영업일 수

    Returns:
        영업일 리스트 (최근일 먼저)
    """
    result = []
    current = base_date
    max_iter = count * 3  # 주말/공휴일 여유

    for _ in range(max_iter):
        if is_trading_day(current):
            result.append(current)
            if len(result) >= count:
                break
        current -= timedelta(days=1)

    return result


def aggregate_weekly_scores(db, base_date: date = None) -> list[dict]:
    """
    최근 6영업일 테마 점수를 가중 평균으로 집계

    Args:
        db: Database 인스턴스
        base_date: 기준일 (None이면 어제 기준)

    Returns:
        가중 평균 점수가 적용된 테마 리스트 (점수 내림차순)
        각 항목: {name, total_score, score, momentum, news_count, ai_sentiment,
                  category, stock_count, days_found, daily_scores}
    """
    if base_date is None:
        base_date = now_kst().date() - timedelta(days=1)

    # 최근 6영업일 구하기
    trading_days = get_recent_trading_days(base_date, NUM_LOOKBACK_DAYS)
    if not trading_days:
        logger.warning("[WeeklyAgg] 최근 영업일 없음")
        return []

    logger.info(f"[WeeklyAgg] 기준일={base_date}, 조회 영업일={[d.isoformat() for d in trading_days]}")

    # DB에서 해당 기간 테마 데이터 조회
    date_list = [d.isoformat() for d in trading_days]
    placeholders = ",".join(["?"] * len(date_list))

    with db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT date, theme_name, score, momentum, news_count, ai_sentiment, category,
                   COALESCE(url, '') as url
            FROM themes
            WHERE date IN ({placeholders})
            ORDER BY date DESC
        """, date_list)
        rows = [dict(row) for row in cursor.fetchall()]

    if not rows:
        logger.warning("[WeeklyAgg] 조회 기간에 테마 데이터 없음")
        return []

    # 날짜→가중치 매핑 (trading_days[0]이 가장 최근 → weight[0])
    date_weight = {}
    for i, td in enumerate(trading_days):
        if i < len(DAILY_WEIGHTS):
            date_weight[td.isoformat()] = DAILY_WEIGHTS[i]

    # 테마별 가중 집계
    theme_data = {}  # theme_name → {weighted_score, total_weight, category, ...}

    for row in rows:
        name = row["theme_name"]
        row_date = row["date"]
        weight = date_weight.get(row_date, 0)

        if weight == 0:
            continue

        if name not in theme_data:
            theme_data[name] = {
                "weighted_score": 0.0,
                "weighted_momentum": 0.0,
                "total_weight": 0.0,
                "total_news": 0,
                "total_ai": 0.0,
                "ai_count": 0,
                "category": row.get("category", "기타"),
                "days_found": 0,
                "daily_scores": {},
                "url": "",
            }

        td = theme_data[name]
        td["weighted_score"] += (row.get("score", 0) or 0) * weight
        td["weighted_momentum"] += (row.get("momentum", 0) or 0) * weight
        td["total_weight"] += weight
        td["total_news"] += row.get("news_count", 0) or 0
        if row.get("ai_sentiment", 0):
            td["total_ai"] += row["ai_sentiment"]
            td["ai_count"] += 1
        td["days_found"] += 1
        td["daily_scores"][row_date] = round(row.get("score", 0) or 0, 1)
        # 가장 최근 날짜의 url 보존
        if row.get("url") and not td["url"]:
            td["url"] = row["url"]

    # 가중 평균 계산
    results = []
    for name, td in theme_data.items():
        tw = td["total_weight"]
        if tw <= 0:
            continue

        avg_score = td["weighted_score"] / tw
        avg_momentum = td["weighted_momentum"] / tw
        avg_ai = td["total_ai"] / td["ai_count"] if td["ai_count"] > 0 else 0

        results.append({
            "name": name,
            "theme": name,
            "total_score": round(avg_score, 2),
            "score": round(avg_score, 2),
            "momentum": round(avg_momentum, 2),
            "momentum_score": round(avg_momentum, 2),
            "news_count": td["total_news"],
            "ai_sentiment": round(avg_ai, 2),
            "category": td["category"],
            "days_found": td["days_found"],
            "daily_scores": td["daily_scores"],
            "url": td["url"],
            "selection_reason": f"{td['days_found']}일누적",
            "grade": _grade(avg_score),
        })

    # 점수 순 정렬
    results.sort(key=lambda x: x["total_score"], reverse=True)

    logger.info(
        f"[WeeklyAgg] {len(results)}개 테마 가중 집계 완료 "
        f"(조회 {len(trading_days)}영업일, 데이터 {len(rows)}행)"
    )
    return results


def _grade(score: float) -> str:
    if score >= 58:
        return "S"
    elif score >= 48:
        return "A"
    elif score >= 38:
        return "B"
    elif score >= 30:
        return "C"
    return "D"
