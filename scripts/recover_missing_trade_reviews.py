"""누락된 trade_reviews 5건 소급 복구 스크립트.

Bug context:
- main.py의 3개 매도 함수(run_hold_period_sells, _execute_midweek_profit_sells,
  _execute_midweek_loss_sells)가 portfolio_monitor_v2._close_position_in_db를
  우회하면서 save_trade_review 호출이 누락됨 → trade_reviews에 적재 안 됨.
- 코드 픽스(2026-05-01)와 별도로, 2026-04-09~04-27 사이 발생한 5건을 소급 INSERT.

복구 데이터 출처:
- trades (sell_id, sell_price, shares, profit_rate, profit_amount, sell_reason, sell_date)
- portfolio status='closed' (theme, buy_date, buy_price)
- 매수 trade (buy_date 폴백 검증)
- position_state (max_profit_rate, trailing_level — 2건만 잔존)

UNIQUE 보호:
- trade_id가 trade_reviews에 이미 존재하면 SKIP (멱등성).

실행:
    source venv/bin/activate
    python scripts/recover_missing_trade_reviews.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import count_trading_days
from database import Database
from logger import logger


# 누락 5건 (monthly 분석 + DB 검증으로 확정)
MISSING_TRADE_IDS = [74, 75, 80, 92, 101]


def classify_strategy(reason: str) -> str:
    """portfolio_monitor_v2._classify_strategy와 동일 로직."""
    if "손절" in reason:
        return "stop_loss"
    elif "트레일링" in reason:
        return "trailing_stop"
    elif "익절" in reason:
        return "take_profit"
    elif "보유" in reason:
        return "max_hold"
    elif "수급" in reason:
        return "supply_exit"
    return "manual"


def main() -> int:
    db = Database()
    db.connect()

    try:
        # 이미 적재된 trade_id 확인 (멱등성)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT trade_id FROM trade_reviews WHERE trade_id IN ({','.join(['?']*len(MISSING_TRADE_IDS))})",
                MISSING_TRADE_IDS,
            )
            existing = {row[0] for row in cursor.fetchall()}

        if existing:
            logger.info(f"이미 적재된 trade_id: {existing} → 스킵 대상")

        # 복구 후보 조회
        with db.get_cursor() as cursor:
            placeholders = ",".join(["?"] * len(MISSING_TRADE_IDS))
            cursor.execute(
                f"""
                SELECT
                    s.id AS sell_trade_id,
                    s.stock_code, s.stock_name, s.date AS sell_date,
                    s.price AS sell_price, s.shares, s.profit_rate, s.profit_amount,
                    s.reason AS sell_reason, s.buy_price AS trade_buy_price,
                    p.theme, p.buy_date, p.buy_price AS port_buy_price,
                    ps.max_profit_rate, ps.trailing_level
                FROM trades s
                LEFT JOIN portfolio p
                    ON p.stock_code = s.stock_code
                    AND p.status = 'closed'
                    AND date(p.updated_at) = s.date
                LEFT JOIN position_state ps ON ps.stock_code = s.stock_code
                WHERE s.id IN ({placeholders})
                ORDER BY s.id
                """,
                MISSING_TRADE_IDS,
            )
            rows = [dict(r) for r in cursor.fetchall()]

        if len(rows) != len(MISSING_TRADE_IDS):
            logger.error(f"⚠️ 일부 trade_id가 trades에 없음: 기대 {len(MISSING_TRADE_IDS)}건, 실제 {len(rows)}건")
            return 1

        recovered = 0
        skipped = 0
        for r in rows:
            trade_id = r["sell_trade_id"]
            if trade_id in existing:
                logger.info(f"[SKIP] trade_id={trade_id} ({r['stock_name']}) — 이미 적재됨")
                skipped += 1
                continue

            buy_date_str = r["buy_date"] or ""
            sell_date_str = r["sell_date"] or ""
            try:
                buy_d = datetime.strptime(str(buy_date_str), "%Y-%m-%d").date()
                sell_d = datetime.strptime(str(sell_date_str), "%Y-%m-%d").date()
                hold_days = count_trading_days(buy_d, sell_d)
            except Exception as e:
                logger.warning(f"hold_days 계산 실패 ({r['stock_name']}): {e} — 0으로 폴백")
                hold_days = 0

            buy_price = r["port_buy_price"] or r["trade_buy_price"] or 0
            max_profit = r["max_profit_rate"]  # None일 수 있음
            trailing_lv = r["trailing_level"] or 0

            review = {
                "trade_id": trade_id,
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "buy_date": buy_date_str,
                "sell_date": sell_date_str,
                "buy_price": buy_price,
                "sell_price": r["sell_price"],
                "shares": r["shares"],
                "hold_days": hold_days,
                "profit_rate": r["profit_rate"],
                "profit_amount": r["profit_amount"],
                "sell_reason": r["sell_reason"],
                "strategy_type": classify_strategy(r["sell_reason"] or ""),
                "trailing_level": trailing_lv,
                "max_profit_during_hold": (round(max_profit * 100, 2) if max_profit is not None else None),
                "theme": r["theme"] or "",
            }

            db.save_trade_review(review)
            logger.info(
                f"[RECOVER] trade_id={trade_id} {r['stock_name']} "
                f"({sell_date_str}, {r['sell_reason']}, hold={hold_days}일, "
                f"profit={r['profit_rate']:.2f}%)"
            )
            recovered += 1

        logger.info("=" * 60)
        logger.info(f"복구 완료: {recovered}건 신규 INSERT, {skipped}건 스킵")
        logger.info("=" * 60)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
