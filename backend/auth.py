"""
auth.py – JWT helpers, password hashing, FastAPI dependencies, and rate-limiter setup.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from limits.storage import storage_from_string
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (read once at import time)
# ---------------------------------------------------------------------------

JWT_SECRET: str = os.environ["JWT_SECRET"]
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
SERVICE_API_KEY: str = os.environ["SERVICE_API_KEY"]

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token.

    Args:
        data: Claims to embed (must include ``sub``).
        expires_delta: Override the default expiry window.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Create a signed JWT refresh token with a longer expiry."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> dict:
    """Decode and validate a JWT.

    Raises:
        HTTPException 401 if the token is invalid, expired, or wrong type.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise credentials_exception

    if payload.get("type") != expected_type:
        raise credentials_exception

    sub: Optional[str] = payload.get("sub")
    if sub is None:
        raise credentials_exception

    return payload


# ---------------------------------------------------------------------------
# FastAPI security scheme
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=True)


async def require_bearer_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> dict:
    """Dependency: validate Bearer JWT and return the decoded payload."""
    return decode_token(credentials.credentials, expected_type="access")


async def require_api_key(request: Request) -> None:
    """Dependency: validate X-API-Key header using constant-time comparison."""
    api_key: Optional[str] = request.headers.get("X-API-Key")
    if api_key is None or not secrets.compare_digest(api_key, SERVICE_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )


# ---------------------------------------------------------------------------
# Rate limiter setup
# ---------------------------------------------------------------------------

_rate_limit_storage_uri: str = os.getenv("RATE_LIMIT_STORAGE_URI", "").strip()

if _rate_limit_storage_uri:
    # Use Redis (or any limits-compatible backend) for distributed state
    _storage = storage_from_string(_rate_limit_storage_uri)
    limiter = Limiter(key_func=get_remote_address, storage_uri=_rate_limit_storage_uri)
else:
    # Fall back to in-process memory (suitable for single-instance dev)
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
