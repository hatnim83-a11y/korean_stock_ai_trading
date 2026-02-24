"""
web/auth.py - 대시보드 인증 모듈

비밀번호 검증 + JWT 토큰 관리
"""

import os
import time
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from jose import jwt, JWTError
from fastapi import Request, HTTPException, status

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import now_kst

# ===== 설정 =====
SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
COOKIE_NAME = "dashboard_token"

# 비밀번호: SHA-256 해시 (bcrypt/passlib 호환 문제 회피)
_raw_password = os.getenv("DASHBOARD_PASSWORD", "")
_PASSWORD_HASH = hashlib.sha256((_raw_password + SECRET_KEY).encode()).hexdigest() if _raw_password else ""

# 로그인 시도 제한 (IP별 5회/분)
_login_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 60


def verify_password(plain: str) -> bool:
    if not _PASSWORD_HASH or not plain:
        return False
    check = hashlib.sha256((plain + SECRET_KEY).encode()).hexdigest()
    return hmac.compare_digest(check, _PASSWORD_HASH)


def create_token() -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": "dashboard", "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> bool:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub") == "dashboard"
    except JWTError:
        return False


def check_rate_limit(client_ip: str) -> bool:
    """로그인 시도 횟수 제한. True이면 허용, False이면 차단."""
    now = time.time()
    attempts = _login_attempts[client_ip]
    # 윈도우 밖 기록 제거
    _login_attempts[client_ip] = [t for t in attempts if now - t < WINDOW_SECONDS]
    if len(_login_attempts[client_ip]) >= MAX_ATTEMPTS:
        return False
    _login_attempts[client_ip].append(now)
    return True


def get_current_user(request: Request) -> bool:
    """쿠키에서 JWT 검증. 실패 시 False 반환."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return verify_token(token)


def require_auth(request: Request):
    """인증 필수 의존성. 실패 시 401."""
    if not get_current_user(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증 필요")
