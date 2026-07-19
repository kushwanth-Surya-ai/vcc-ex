from __future__ import annotations
import os
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from database import get_db
from models import SystemSetting, UserRole
from auth import require_bearer_token
import pydantic

router = APIRouter(prefix="/api/settings", tags=["settings"])

class ConfigResponse(pydantic.BaseModel):
    confidence_threshold: float

class ConfigUpdate(pydantic.BaseModel):
    confidence_threshold: float

@router.get("/config", response_model=ConfigResponse, summary="Get dynamic system configuration")
async def get_config(db: AsyncSession = Depends(get_db)):
    """Retrieve system configuration settings (public or accessible by tracker/operators)."""
    stmt = select(SystemSetting).where(SystemSetting.key == "confidence_threshold")
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    
    val = float(row.value) if row else float(os.getenv("VCC_CONF", "0.45"))
    return ConfigResponse(confidence_threshold=val)

@router.post("/config", response_model=ConfigResponse, summary="Update dynamic system configuration (Admin Only)")
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Update settings. Requires admin permissions."""
    role = token.get("role")
    if role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can modify system configuration",
        )
        
    if body.confidence_threshold < 0.10 or body.confidence_threshold > 0.90:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Confidence threshold must be between 0.10 and 0.90",
        )

    # Insert or update
    stmt = select(SystemSetting).where(SystemSetting.key == "confidence_threshold")
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    
    if row:
        row.value = str(body.confidence_threshold)
    else:
        db.add(SystemSetting(key="confidence_threshold", value=str(body.confidence_threshold)))
        
    await db.commit()
    
    # Log audit event
    from audit import log_action
    await log_action(
        db,
        token.get("sub", "unknown"),
        "CONFIG_UPDATED",
        f"confidence_threshold updated to {body.confidence_threshold}"
    )
    
    return ConfigResponse(confidence_threshold=body.confidence_threshold)
