"""
web/api_routes.py - REST API 엔드포인트
"""

from fastapi import APIRouter, Depends, Query, HTTPException

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from web.auth import require_auth
from web import dashboard_service as svc

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
