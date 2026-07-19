"""
routers/events.py - Detection event ingestion and retrieval.

POST /api/events : X-API-Key auth, creates event, triggers alert checks,
                   broadcasts via WebSocket.
GET  /api/events : Bearer auth, paginated with from/to datetime filters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from alerts import run_alert_checks
from auth import require_api_key, require_bearer_token
from database import get_db
from models import Event
from schemas import EventCreate, EventRead, PaginatedResponse
from websocket import manager

router = APIRouter(prefix="/api/events", tags=["events"])


@router.post(
    "",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a detection event (API-key auth)",
    dependencies=[Depends(require_api_key)],
)
async def create_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db),
) -> EventRead:
    """Ingest a single vehicle-detection event from the detection pipeline.

    - Requires ``X-API-Key`` header.
    - Triggers alert rule evaluation after insertion.
    - Broadcasts the new event to all active WebSocket clients.
    """
    event_data = body.model_dump()
    # Use provided timestamp or default to server UTC now
    if event_data.get("timestamp") is None:
        event_data["timestamp"] = datetime.now(timezone.utc)

    event = Event(**event_data)
    db.add(event)
    
    # Update the camera's status to active and record the heartbeat
    from models import Camera, CameraStatus
    camera = await db.get(Camera, event.camera_id)
    if camera:
        camera.status = CameraStatus.active.value
        camera.last_seen_at = datetime.now(timezone.utc)
        
    from sqlalchemy.exc import IntegrityError

    try:
        await db.commit()
        await db.refresh(event)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Camera ID {event.camera_id} does not exist in database.",
        )


    # Run alert checks asynchronously (same session context)
    await run_alert_checks(db, camera_id=event.camera_id, lane_id=event.lane_id)

    # Broadcast to all WebSocket subscribers
    await manager.broadcast(
        {
            "type": "new_event",
            "event": {
                "id": event.id,
                "camera_id": event.camera_id,
                "location_id": event.location_id,
                "lane_id": event.lane_id,
                "vehicle_class": event.vehicle_class.value
                if hasattr(event.vehicle_class, "value")
                else event.vehicle_class,
                "confidence": event.confidence,
                "crossing_dir": event.crossing_dir.value
                if hasattr(event.crossing_dir, "value")
                else event.crossing_dir,
                "timestamp": event.timestamp.isoformat(),
            },
        }
    )

    return EventRead.model_validate(event)


@router.get(
    "",
    response_model=PaginatedResponse[EventRead],
    summary="List detection events (Bearer auth, paginated)",
)
async def list_events(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    from_: Optional[datetime] = Query(None, alias="from", description="Start timestamp (inclusive)"),
    to: Optional[datetime] = Query(None, description="End timestamp (inclusive)"),
    camera_id: Optional[int] = Query(None),
    location_id: Optional[int] = Query(None),
    vehicle_class: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> PaginatedResponse[EventRead]:
    """List events with optional time-range, camera, location, and class filters."""
    base = select(Event)
    count_base = select(func.count(Event.id))

    filters = []
    if from_ is not None:
        filters.append(Event.timestamp >= from_)
    if to is not None:
        filters.append(Event.timestamp <= to)
    if camera_id is not None:
        filters.append(Event.camera_id == camera_id)
    if location_id is not None:
        filters.append(Event.location_id == location_id)
    if vehicle_class is not None:
        filters.append(Event.vehicle_class == vehicle_class)

    if filters:
        base = base.where(*filters)
        count_base = count_base.where(*filters)

    total = (await db.execute(count_base)).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Event.timestamp.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[EventRead.model_validate(r) for r in rows],
    )
