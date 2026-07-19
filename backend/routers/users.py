"""
routers/users.py - User management and audit logging endpoints.
"""
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token, hash_password
from database import get_db
from models import User, LoginLog, AuditLog
from schemas import UserCreate, UserRead, LoginLogRead, AuditLogRead, PaginatedResponse
from audit import log_action

router = APIRouter(prefix="/api", tags=["users"])


async def require_admin(token: dict = Depends(require_bearer_token)) -> dict:
    """Dependency to check if the current user has the 'admin' role."""
    role = token.get("role")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required to access this resource",
        )
    return token


# ---------------------------------------------------------------------------
# Users CRUD
# ---------------------------------------------------------------------------

@router.get(
    "/users",
    response_model=PaginatedResponse[UserRead],
    summary="List all users (admin only)",
)
async def list_users(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
) -> PaginatedResponse[UserRead]:
    total_q = select(func.count(User.id))
    total = (await db.execute(total_q)).scalar_one()

    q = select(User).order_by(User.id).limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[UserRead.model_validate(r) for r in rows],
    )


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (admin only)",
)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin_token: dict = Depends(require_admin),
) -> UserRead:
    # Check if email exists
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists"
        )

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Log the action
    await log_action(
        db,
        admin_token.get("sub", "unknown"),
        "USER_CREATED",
        f"Created user '{user.email}' with role '{user.role}'"
    )

    return UserRead.model_validate(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user (admin only)",
)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin_token: dict = Depends(require_admin),
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Prevent deleting own account
    if user.email == admin_token.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own logged-in account"
        )

    email = user.email
    await db.delete(user)
    await db.commit()

    # Log the action
    await log_action(
        db,
        admin_token.get("sub", "unknown"),
        "USER_DELETED",
        f"Deleted user '{email}' (ID: {user_id})"
    )


# ---------------------------------------------------------------------------
# Change Password
# ---------------------------------------------------------------------------

import pydantic as _pydantic


class ChangePasswordRequest(_pydantic.BaseModel):
    current_password: str
    new_password: str = _pydantic.Field(..., min_length=8)


@router.put(
    "/users/{user_id}/password",
    status_code=status.HTTP_200_OK,
    summary="Change user password (self or admin)",
)
async def change_password(
    user_id: int,
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> dict:
    """Change a user's password.
    - A user can change their OWN password (requires current_password verification).
    - Admin can change anyone's password (current_password still verified for own account).
    """
    from auth import verify_password, hash_password as _hash_password
    caller_email = token.get("sub", "")
    caller_role = token.get("role", "")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Only allow if: caller is the user themselves, OR caller is admin
    is_self = (user.email == caller_email)
    is_admin = (caller_role == "admin")

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only change your own password"
        )

    # Always verify current password when changing own account
    if is_self:
        if not verify_password(body.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )

    user.hashed_password = _hash_password(body.new_password)
    await db.commit()

    await log_action(
        db,
        caller_email,
        "PASSWORD_CHANGED",
        f"Password changed for user '{user.email}' (ID: {user_id})"
    )

    return {"status": "ok", "message": "Password updated successfully"}


# ---------------------------------------------------------------------------
# Audit & Login Logs
# ---------------------------------------------------------------------------

@router.get(
    "/logs/login",
    response_model=PaginatedResponse[LoginLogRead],
    summary="List login attempts (admin only)",
)
async def list_login_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    email: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
) -> PaginatedResponse[LoginLogRead]:
    q = select(LoginLog)
    count_q = select(func.count(LoginLog.id))

    filters = []
    if email:
        filters.append(LoginLog.email.ilike(f"%{email}%"))
    if success is not None:
        filters.append(LoginLog.success == success)

    if filters:
        q = q.where(*filters)
        count_q = count_q.where(*filters)

    total = (await db.execute(count_q)).scalar_one()
    rows = (await db.execute(q.order_by(LoginLog.timestamp.desc()).limit(limit).offset(offset))).scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[LoginLogRead.model_validate(r) for r in rows],
    )


@router.get(
    "/logs/audit",
    response_model=PaginatedResponse[AuditLogRead],
    summary="List action audit logs (admin only)",
)
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
) -> PaginatedResponse[AuditLogRead]:
    q = select(AuditLog)
    count_q = select(func.count(AuditLog.id))

    filters = []
    if email:
        filters.append(AuditLog.email.ilike(f"%{email}%"))
    if action:
        filters.append(AuditLog.action == action.upper())

    if filters:
        q = q.where(*filters)
        count_q = count_q.where(*filters)

    total = (await db.execute(count_q)).scalar_one()
    rows = (await db.execute(q.order_by(AuditLog.timestamp.desc()).limit(limit).offset(offset))).scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditLogRead.model_validate(r) for r in rows],
    )
