"""
web/api_routes.py - REST API 엔드포인트
"""

import asyncio

from fastapi import APIRouter, Depends, Query, HTTPException

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from web.auth import require_auth
from web import dashboard_service as svc
from web import improvements_service as improvements

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


@router.get("/portfolio")
async def portfolio():
    data = await svc.get_portfolio_data()
    return data


@router.get("/trades")
async def trades(days: int = Query(30, ge=1, le=365), page: int = Query(1, ge=1)):
    data = await svc.get_trades_data(days=days, page=page)
    return data


@router.get("/performance")
async def performance(days: int = Query(90, ge=1, le=365)):
    data = await svc.get_performance_data(days=days)
    return data


@router.get("/themes")
async def themes(days: int = Query(30, ge=1, le=365)):
    data = await svc.get_themes_data(days=days)
    return data


@router.get("/news")
async def news(stock_code: str = Query(None)):
    try:
        data = await svc.get_news_data(stock_code=stock_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return data


@router.get("/system/status")
async def system_status():
    data = await svc.get_system_status()
    return data


# ===== 개선 보고서 (read-only, docs/improvements) =====
# 파일 시스템 읽기 전용. 경로 가드는 improvements_service 에서 강제.


@router.get("/improvements")
async def improvements_list():
    """개선 보고서 목록(최신순). 디렉토리 부재 시 빈 리스트."""
    try:
        reports = await asyncio.to_thread(improvements.list_improvements)
    except Exception:
        reports = []
    return {"reports": reports, "count": len(reports)}


@router.get("/improvements/{filename}")
async def improvements_detail(filename: str):
    """단일 개선 보고서 상세. 유효하지 않으면 400, 없으면 404."""
    try:
        data = await asyncio.to_thread(improvements.get_improvement, filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="유효하지 않은 파일명")
    except Exception:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없음")
    if data is None:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없음")
    return data


# ===== 종가베팅 (Phase 2-6) =====
# closing_bet_system/dashboard/data_adapter.py 의 read-only 헬퍼를 호출.
# 모든 응답은 closing_bet.db 가 비어있어도 빈 dict / 0 카운트 정상 반환.

from closing_bet_system.dashboard import data_adapter as cb_adapter  # noqa: E402


@router.get("/closing-bet/today")
async def closing_bet_today():
    """오늘 후보 status별 카운트 + 리스트."""
    return await asyncio.to_thread(cb_adapter.get_today_candidates)


@router.get("/closing-bet/gate-progress")
async def closing_bet_gate_progress():
    """운영 점검 게이트 진척도 (30건 / 15영업일 / 20종목)."""
    return await asyncio.to_thread(cb_adapter.get_gate_progress)


@router.get("/closing-bet/orderbook-history")
async def closing_bet_orderbook_history(
    days: int = Query(1, ge=1, le=7),
    limit: int = Query(200, ge=1, le=1000),
):
    """최근 N일 호가 스냅샷 (Phase 2-1 데이터)."""
    return await asyncio.to_thread(cb_adapter.get_orderbook_history, days, limit)


@router.get("/closing-bet/rejections")
async def closing_bet_rejections(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(50, ge=1, le=200),
):
    """최근 N일 rejected_filter / rejected_manual 사유."""
    return await asyncio.to_thread(cb_adapter.get_recent_rejections, days, limit)


@router.get("/closing-bet/fund-guard-status")
async def closing_bet_fund_guard_status():
    """fund_guard 자금 사용/한도/주간 손실 등."""
    return await asyncio.to_thread(cb_adapter.get_fund_guard_status)
