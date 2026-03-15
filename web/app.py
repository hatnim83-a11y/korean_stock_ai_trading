"""
web/app.py - FastAPI 앱 팩토리 + 미들웨어
"""

from pathlib import Path

from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from web.auth import (
    verify_password, create_token, get_current_user,
    check_rate_limit, COOKIE_NAME,
)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="Trading Dashboard", docs_url=None, redoc_url=None)

    # 정적 파일
    static_dir = WEB_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ===== 인증 미들웨어 =====
    _EXACT_PUBLIC = {"/login", "/api/v1/auth/login", "/favicon.ico"}
    _PREFIX_PUBLIC = ("/static/",)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if path in _EXACT_PUBLIC or path.startswith(_PREFIX_PUBLIC):
            return await call_next(request)
        if not get_current_user(request):
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "인증 필요"})
            return RedirectResponse("/login", status_code=302)
        return await call_next(request)

    # ===== 로그인 페이지 =====
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return templates.TemplateResponse("login.html", {"request": request, "error": error})

    @app.post("/api/v1/auth/login")
    async def login(request: Request, password: str = Form(...)):
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip):
            return JSONResponse(status_code=429, content={"detail": "너무 많은 시도. 1분 후 재시도."})
        if not verify_password(password):
            return RedirectResponse("/login?error=1", status_code=302)
        token = create_token()
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, max_age=86400, samesite="lax", secure=True)
        return resp

    @app.post("/api/v1/auth/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get("/api/v1/auth/logout")
    async def logout_get():
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ===== 메인 대시보드 =====
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse("dashboard.html", {"request": request})

    # API 라우트 등록
    from web.api_routes import router as api_router
    from web.sse_routes import router as sse_router
    app.include_router(api_router)
    app.include_router(sse_router)

    return app
