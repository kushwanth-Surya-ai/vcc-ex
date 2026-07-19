"""
routers/auth.py - Login and token-refresh endpoints.

POST /auth/login  : rate-limited; returns access token in body + refresh
                    token in httpOnly SameSite=Strict cookie (Path=/auth/refresh).
POST /auth/refresh: reads refresh cookie, validates it, rotates both tokens.
"""
from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    limiter,
    verify_password,
)
from database import get_db
from models import User
from schemas import LoginRequest, Token, TokenRefresh

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_RATE_LIMIT: str = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
_REFRESH_COOKIE = "refresh_token"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Attach the refresh token as a secure httpOnly cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/auth/refresh",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86_400,
    )


@router.post(
    "/login",
    response_model=Token,
    summary="Obtain access + refresh tokens",
)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Authenticate a user and issue JWT tokens.

    - Access token is returned in the response body only (never in a cookie).
    - Refresh token is set in an httpOnly; Secure; SameSite=Strict cookie
      scoped to Path=/auth/refresh.
    """
    # Look up user
    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    from audit import log_login

    ip_address = request.client.host if request.client else "unknown"

    if user is None or not verify_password(body.password, user.hashed_password):
        await log_login(db, body.email, ip_address, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await log_login(db, body.email, ip_address, True)

    token_data = {"sub": user.email, "role": user.role.value if hasattr(user.role, 'value') else user.role}

    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token(data=token_data)

    _set_refresh_cookie(response, refresh_token)

    return Token(access_token=access_token, token_type="bearer")


@router.post(
    "/refresh",
    response_model=TokenRefresh,
    summary="Rotate refresh token and issue new access token",
)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
) -> TokenRefresh:
    """Validate the refresh cookie and issue a new access + refresh token pair.

    The old refresh cookie is replaced (rotation strategy).
    """
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token cookie missing",
        )

    payload = decode_token(refresh_token, expected_type="refresh")
    sub: str = payload["sub"]
    role: str = payload.get("role", "viewer")

    token_data = {"sub": sub, "role": role}

    new_access = create_access_token(data=token_data)
    new_refresh = create_refresh_token(data=token_data)

    _set_refresh_cookie(response, new_refresh)

    return TokenRefresh(access_token=new_access, token_type="bearer")
