"""
web/sse_routes.py - SSE 실시간 가격 스트림
"""

import asyncio
import json

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from web.auth import require_auth
from web import dashboard_service as svc

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


async def _price_stream(request: Request):
    """장중 5초 간격 가격 푸시 (연속 에러 5회 시 스트림 종료)"""
    error_count = 0
    MAX_ERRORS = 5
    while True:
        if await request.is_disconnected():
            break
        try:
            data = await svc.get_portfolio_data()
            payload = {
                "holdings": data["holdings"],
                "total_eval": data["total_eval"],
                "unrealized_pnl": data["unrealized_pnl"],
                "unrealized_rate": data["unrealized_rate"],
                "current_total": data["current_total"],
                "total_return": data["total_return"],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            error_count = 0
        except Exception as e:
            error_count += 1
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            if error_count >= MAX_ERRORS:
                break
        await asyncio.sleep(5)


@router.get("/stream/prices")
async def stream_prices(request: Request):
    return StreamingResponse(
        _price_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
