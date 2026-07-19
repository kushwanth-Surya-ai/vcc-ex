"""
routers/alerts.py - Alert listing and acknowledgement endpoints with audit logging.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token
from database import get_db
from models import Alert
from schemas import AlertAcknowledge, AlertRead, PaginatedResponse
from audit import log_action

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get(
    "",
    response_model=PaginatedResponse[AlertRead],
    summary="List alerts (paginated, filterable)",
)
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    severity: Optional[str] = Query(None, description="Filter by severity (LOW/MEDIUM/HIGH)"),
    acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgement status"),
    camera_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> PaginatedResponse[AlertRead]:
    base = select(Alert)
    count_base = select(func.count(Alert.id))

    filters = []
    if severity is not None:
        filters.append(Alert.severity == severity.upper())
    if acknowledged is not None:
        filters.append(Alert.acknowledged == acknowledged)
    if camera_id is not None:
        filters.append(Alert.camera_id == camera_id)

    if filters:
        base = base.where(*filters)
        count_base = count_base.where(*filters)

    total = (await db.execute(count_base)).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Alert.timestamp.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[AlertRead.model_validate(r) for r in rows],
    )


@router.post(
    "/acknowledge-all",
    summary="Acknowledge all unacknowledged alerts",
)
async def acknowledge_all_alerts(
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    from sqlalchemy import update
    stmt = (
        update(Alert)
        .where(Alert.acknowledged == False)
        .values(acknowledged=True)
    )
    await db.execute(stmt)
    await db.commit()
    
    # Log the action
    await log_action(db, token.get("sub", "unknown"), "ALL_ALERTS_ACKNOWLEDGED", "Acknowledged all unacknowledged alerts")
    return {"status": "ok", "message": "All alerts acknowledged"}


@router.patch(
    "/{alert_id}/acknowledge",
    response_model=AlertRead,
    summary="Acknowledge an alert",
)
async def acknowledge_alert(
    alert_id: int,
    body: AlertAcknowledge,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> AlertRead:
    """Set ``acknowledged = True`` (or False) on the specified alert."""
    row = await db.get(Alert, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    row.acknowledged = body.acknowledged
    await db.commit()
    await db.refresh(row)
    
    # Log the action
    await log_action(db, token.get("sub", "unknown"), "ALERT_ACKNOWLEDGED", f"Alert ID {alert_id} acknowledged: {body.acknowledged}")
    
    return AlertRead.model_validate(row)
