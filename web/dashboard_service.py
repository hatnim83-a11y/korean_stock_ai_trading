"""
web/dashboard_service.py - 대시보드 비즈니스 로직

DB/KIS API를 래핑하여 대시보드 API에서 사용할 데이터를 제공합니다.
"""

import json
import os
import time
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import logger
from config import settings, now_kst, validate_stock_code, KST
from database import Database

# ===== 가격 캐시 (5초) =====
_price_cache: dict[str, tuple[float, dict]] = {}  # code -> (timestamp, data)
PRICE_CACHE_TTL = 5.0

# ===== DB 싱글턴 (커넥션 재사용) =====
_db_instance: Optional[Database] = None


def _get_db() -> Database:
    global _db_instance
    if _db_instance is None or _db_instance.conn is None:
        _db_instance = Database()
        _db_instance.connect()
    return _db_instance


def _get_kis_api():
    from modules.stock_screener.kis_api import KISApi
    return KISApi()


def _get_order_api():
    from modules.trading_engine.kis_order_api import KISOrderApi
    return KISOrderApi()


def get_cached_price(kis, stock_code: str) -> Optional[dict]:
    """5초 캐시 적용 현재가 조회"""
    now = time.time()
    cached = _price_cache.get(stock_code)
    if cached and (now - cached[0]) < PRICE_CACHE_TTL:
        return cached[1]
    price_info = kis.get_current_price(stock_code)
    if price_info:
        _price_cache[stock_code] = (now, price_info)
    return price_info


# ===== 포트폴리오 =====

async def get_portfolio_data() -> dict:
    """포트폴리오 조회 (DB + 실시간 가격)"""
    db = _get_db()
    holdings = db.get_portfolio(status="holding")
    sell_trades = db.get_all_sell_trades()

    realized_pnl = sum(t.get("profit_amount") or 0 for t in sell_trades)

    if not holdings:
        return {
            "holdings": [],
            "total_invest": 0,
            "total_eval": 0,
            "unrealized_pnl": 0,
            "unrealized_rate": 0,
            "realized_pnl": int(realized_pnl),
            "realized_count": len(sell_trades),
            "total_capital": settings.TOTAL_CAPITAL,
            "current_total": settings.TOTAL_CAPITAL + int(realized_pnl),
            "total_return": (realized_pnl / settings.TOTAL_CAPITAL * 100) if settings.TOTAL_CAPITAL > 0 else 0,
        }

    kis = _get_kis_api()
    result_holdings = []
    total_invest = 0
    total_eval = 0

    # 트레일링 상태 로드
    monitor_state = _load_monitor_state()

    for h in holdings:
        stock_code = h["stock_code"]
        shares = h.get("shares") or 0
        buy_price = int(h.get("buy_price") or 0)

        price_info = await asyncio.to_thread(get_cached_price, kis, stock_code)
        current_price = price_info.get("price", buy_price) if price_info else buy_price

        invest = shares * buy_price
        eval_amount = shares * current_price
        profit = eval_amount - invest
        profit_rate = (profit / invest * 100) if invest > 0 else 0

        total_invest += invest
        total_eval += eval_amount

        # 트레일링 상태
        ts = monitor_state.get(stock_code, {})

        result_holdings.append({
            "stock_code": stock_code,
            "stock_name": h["stock_name"],
            "theme": h.get("theme", ""),
            "shares": shares,
            "buy_price": buy_price,
            "current_price": current_price,
            "invest": invest,
            "eval_amount": eval_amount,
            "profit": profit,
            "profit_rate": round(profit_rate, 2),
            "buy_date": h.get("date", ""),
            "hold_days": _calc_hold_days(h.get("date")),
            "trailing_level": ts.get("trailing_level", 0),
            "trailing_stop_price": ts.get("trailing_stop_price"),
            "highest_price": ts.get("highest_price"),
            "max_profit_rate": ts.get("max_profit_rate"),
        })

    unrealized_pnl = total_eval - total_invest
    unrealized_rate = (unrealized_pnl / total_invest * 100) if total_invest > 0 else 0
    cash_remaining = max(0, settings.TOTAL_CAPITAL - total_invest)
    current_total = cash_remaining + total_eval + int(realized_pnl)
    total_return = ((current_total - settings.TOTAL_CAPITAL) / settings.TOTAL_CAPITAL * 100) if settings.TOTAL_CAPITAL > 0 else 0

    return {
        "holdings": result_holdings,
        "total_invest": total_invest,
        "total_eval": total_eval,
        "unrealized_pnl": int(unrealized_pnl),
        "unrealized_rate": round(unrealized_rate, 2),
        "realized_pnl": int(realized_pnl),
        "realized_count": len(sell_trades),
        "total_capital": settings.TOTAL_CAPITAL,
        "cash": cash_remaining,
        "current_total": int(current_total),
        "total_return": round(total_return, 2),
    }


def _calc_hold_days(date_str) -> int:
    if not date_str:
        return 0
    try:
        buy_date = datetime.strptime(str(date_str), "%Y-%m-%d")
        return (now_kst().replace(tzinfo=None) - buy_date).days
    except Exception:
        return 0


def _load_monitor_state() -> dict:
    """트레일링 상태 로드 (DB 우선, JSON 폴백)"""
    # DB에서 position_state 읽기
    try:
        db = _get_db()
        states = db.get_all_position_states()
        if states:
            return states
    except Exception:
        pass

    # JSON 폴백
    state_path = Path(settings.DATABASE_PATH).parent / "monitor_state.json"
    try:
        if state_path.exists():
            with open(state_path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ===== 매매 내역 =====

async def get_trades_data(days: int = 30, page: int = 1, per_page: int = 50) -> dict:
    db = _get_db()
    with db.get_cursor() as cursor:
        start_date = (now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT * FROM trades WHERE date >= ? ORDER BY date DESC, time DESC",
            (start_date,)
        )
        all_rows = [dict(r) for r in cursor.fetchall()]

    total = len(all_rows)
    start = (page - 1) * per_page
    trades = all_rows[start:start + per_page]

    return {"trades": trades, "total": total, "page": page, "per_page": per_page}


# ===== 성과 =====

async def get_performance_data(days: int = 90) -> dict:
    db = _get_db()

    # daily_snapshots 우선 사용, 없으면 performance 폴백
    snapshots = db.get_daily_snapshots(days=days)

    if snapshots:
        # 역순 정렬 (오래된 순)
        snapshots = list(reversed(snapshots))
        dates = [s["date"] for s in snapshots]
        cumulative_returns = [s.get("cumulative_return") or 0 for s in snapshots]
        daily_returns = [s.get("daily_return") or 0 for s in snapshots]
        latest = snapshots[-1] if snapshots else {}

        return {
            "dates": dates,
            "cumulative_returns": cumulative_returns,
            "daily_returns": daily_returns,
            "sharpe_ratio": 0,
            "mdd": latest.get("mdd") or 0,
            "win_rate": latest.get("win_rate") or 0,
            "win_count": latest.get("win_count_cumulative") or 0,
            "loss_count": latest.get("loss_count_cumulative") or 0,
            "total_trades": (latest.get("win_count_cumulative") or 0) + (latest.get("loss_count_cumulative") or 0),
        }

    # 폴백: 기존 performance 테이블
    perf_history = db.get_performance_history(days=days)
    sell_trades = db.get_all_sell_trades()

    # 역순 정렬 (오래된 순)
    perf_history = list(reversed(perf_history))

    dates = [p["date"] for p in perf_history]
    cumulative_returns = [p.get("cumulative_return") or 0 for p in perf_history]
    daily_returns = [p.get("daily_return") or 0 for p in perf_history]

    # 최신 지표
    latest = perf_history[-1] if perf_history else {}

    # 승률 계산 (매도 거래 기준)
    wins = sum(1 for t in sell_trades if (t.get("profit_amount") or 0) > 0)
    losses = sum(1 for t in sell_trades if (t.get("profit_amount") or 0) <= 0)
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    return {
        "dates": dates,
        "cumulative_returns": cumulative_returns,
        "daily_returns": daily_returns,
        "sharpe_ratio": latest.get("sharpe_ratio") or 0,
        "mdd": latest.get("mdd") or 0,
        "win_rate": round(win_rate, 1),
        "win_count": wins,
        "loss_count": losses,
        "total_trades": len(sell_trades),
    }


# ===== 테마 =====

async def get_themes_data(days: int = 30) -> dict:
    db = _get_db()
    with db.get_cursor() as cursor:
        start_date = (now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT * FROM themes WHERE date >= ? ORDER BY date DESC, score DESC",
            (start_date,)
        )
        all_themes = [dict(r) for r in cursor.fetchall()]

    # 최신 날짜 테마
    current_themes = []
    if all_themes:
        latest_date = all_themes[0]["date"]
        current_themes = [t for t in all_themes if t["date"] == latest_date]

    return {"current_themes": current_themes, "history": all_themes}


# ===== 뉴스 =====

async def get_news_data(stock_code: Optional[str] = None) -> dict:
    if stock_code:
        stock_code = validate_stock_code(stock_code)
        from modules.ai_verifier.news_crawler import fetch_stock_news
        news = await asyncio.to_thread(fetch_stock_news, stock_code, days=7, max_articles=10)
        return {"news": news, "stock_code": stock_code}

    # 전체 보유 종목 뉴스
    db = _get_db()
    holdings = db.get_portfolio(status="holding")

    from modules.ai_verifier.news_crawler import fetch_stock_news
    all_news = []
    for h in holdings[:5]:
        code = h["stock_code"]
        news = await asyncio.to_thread(fetch_stock_news, code, days=3, max_articles=5)
        for n in news:
            n["stock_code"] = code
            n["stock_name"] = h["stock_name"]
        all_news.extend(news)

    return {"news": all_news}


# ===== 시스템 상태 =====

async def get_system_status() -> dict:
    import subprocess

    # 트레이딩 봇 프로세스 확인
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "trading_system"],
            capture_output=True, text=True, timeout=5
        )
        bot_status = result.stdout.strip()
    except Exception:
        bot_status = "unknown"

    # 최근 에러 로그
    log_path = Path(settings.LOG_PATH)
    recent_errors = []
    try:
        log_files = sorted(log_path.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        if log_files:
            with open(log_files[0], "r") as f:
                lines = f.readlines()[-100:]
                recent_errors = [l.strip() for l in lines if "ERROR" in l][-10:]
    except Exception:
        pass

    # 스케줄러 상태
    pid_path = Path(settings.DATABASE_PATH).parent.parent / "trading_system.pid"
    pid = None
    try:
        if pid_path.exists():
            pid = int(pid_path.read_text().strip())
    except Exception:
        pass

    return {
        "bot_status": bot_status,
        "pid": pid,
        "recent_errors": recent_errors,
        "server_time": now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
        "is_mock": settings.IS_MOCK,
        "total_capital": settings.TOTAL_CAPITAL,
    }


# ===== 매도 액션 =====

async def execute_sell(stock_code: str, quantity: Optional[int] = None) -> dict:
    """개별 종목 매도"""
    stock_code = validate_stock_code(stock_code)
    order_api = _get_order_api()

    if quantity is None:
        # DB에서 보유 수량 조회
        db = _get_db()
        holdings = db.get_portfolio(status="holding")
        pos = next((h for h in holdings if h["stock_code"] == stock_code), None)
        if not pos:
            return {"success": False, "message": f"{stock_code} 보유 없음"}
        quantity = pos.get("shares") or 0

    if quantity <= 0:
        return {"success": False, "message": "수량이 0 이하입니다"}

    # DB에서 현재 보유 수량 조회
    db = _get_db()
    holdings = db.get_portfolio(status="holding")
    pos = next((h for h in holdings if h["stock_code"] == stock_code), None)
    total_shares = pos.get("shares", 0) if pos else 0

    result = await asyncio.to_thread(order_api.sell_market_order, stock_code, quantity)
    if result.get("success"):
        remaining = total_shares - quantity
        if remaining <= 0:
            # 전량 매도: 포지션 청산
            db.close_position(stock_code, "대시보드 수동 매도")
        else:
            # 부분 매도: 보유 수량만 업데이트
            db.update_portfolio_shares(stock_code, remaining)

        db.save_trade({
            "date": now_kst().date(),
            "stock_code": stock_code,
            "stock_name": result.get("stock_name", stock_code),
            "action": "sell",
            "shares": quantity,
            "price": result.get("price", 0),
            "amount": result.get("amount", 0),
            "reason": "대시보드 수동 매도",
        })

    return result


async def execute_sell_all() -> dict:
    """전 종목 시장가 매도"""
    db = _get_db()
    holdings = db.get_portfolio(status="holding")

    if not holdings:
        return {"success": True, "message": "보유 종목 없음", "results": []}

    results = []
    for h in holdings:
        r = await execute_sell(h["stock_code"], h.get("shares") or 0)
        results.append({"stock_code": h["stock_code"], "stock_name": h["stock_name"], **r})

    all_success = all(r.get("success") for r in results)
    fail_count = sum(1 for r in results if not r.get("success"))
    return {"success": all_success, "fail_count": fail_count, "results": results}
