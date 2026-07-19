"""
routers/cameras.py - Full CRUD for Camera resources (Bearer auth required) with event count and audit logging.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token
from database import get_db
from models import Camera, Event
from schemas import CameraCreate, CameraRead, CameraUpdate, PaginatedResponse, CountingLineRead
from audit import log_action

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


@router.post(
    "",
    response_model=CameraRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a camera",
)
async def create_camera(
    body: CameraCreate,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> CameraRead:
    cam = Camera(**body.model_dump())
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    
    # Log the action
    await log_action(db, token.get("sub", "unknown"), "CAMERA_ADDED", f"Created camera '{cam.name}' (ID: {cam.id})")
    
    return CameraRead(
        id=cam.id,
        name=cam.name,
        location_id=cam.location_id,
        lane_count=cam.lane_count,
        rtsp_url=cam.rtsp_url,
        status=cam.status,
        latitude=cam.latitude,
        longitude=cam.longitude,
        counting_line=cam.counting_line,
        source_type=cam.source_type or "live",
        processing_status=cam.processing_status,
        counting_lines=[],
        event_count=0
    )




@router.get(
    "",
    response_model=PaginatedResponse[CameraRead],
    summary="List cameras (paginated)",
)
async def list_cameras(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    location_id: int | None = Query(None, description="Filter by location"),
    source_type: str | None = Query(
        None,
        pattern="^(live|upload)$",
        description="Filter by source: 'live' for real cameras, 'upload' for uploaded videos",
    ),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> PaginatedResponse[CameraRead]:
    count_sub = (
        select(func.count(Event.id))
        .where(Event.camera_id == Camera.id)
        .correlate(Camera)
        .scalar_subquery()
    )
    
    base_q = select(Camera, count_sub.label("event_count")).options(selectinload(Camera.counting_lines))
    count_q = select(func.count(Camera.id))

    if location_id is not None:
        base_q = base_q.where(Camera.location_id == location_id)
        count_q = count_q.where(Camera.location_id == location_id)

    if source_type is not None:
        # Omitting the filter still returns everything, so the existing
        # response shape and default behaviour are unchanged. Rows predating the
        # column read as NULL, and a NULL source is a live camera - hence the
        # explicit IS NULL arm for 'live', which a bare == would drop.
        if source_type == "live":
            filter_expr = (Camera.source_type == "live") | (Camera.source_type.is_(None))
        else:
            filter_expr = Camera.source_type == source_type
        base_q = base_q.where(filter_expr)
        count_q = count_q.where(filter_expr)

    total = (await db.execute(count_q)).scalar_one()
    
    res = await db.execute(base_q.order_by(Camera.id).limit(limit).offset(offset))
    rows = res.all()

    items = []
    for cam, count in rows:
        cam_read = CameraRead(
            id=cam.id,
            name=cam.name,
            location_id=cam.location_id,
            lane_count=cam.lane_count,
            rtsp_url=cam.rtsp_url,
            status=cam.status,
            latitude=cam.latitude,
            longitude=cam.longitude,
            counting_line=cam.counting_line,
            source_type=cam.source_type or "live",
            processing_status=cam.processing_status,
            counting_lines=[CountingLineRead.model_validate(l) for l in cam.counting_lines],
            event_count=count
        )
        items.append(cam_read)


    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/{camera_id}",
    response_model=CameraRead,
    summary="Get a camera by ID",
)
async def get_camera(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> CameraRead:
    count_sub = (
        select(func.count(Event.id))
        .where(Event.camera_id == Camera.id)
        .correlate(Camera)
        .scalar_subquery()
    )
    
    res = await db.execute(
        select(Camera, count_sub.label("event_count"))
        .options(selectinload(Camera.counting_lines))
        .where(Camera.id == camera_id)
    )
    row = res.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    
    cam, count = row
    return CameraRead(
        id=cam.id,
        name=cam.name,
        location_id=cam.location_id,
        lane_count=cam.lane_count,
        rtsp_url=cam.rtsp_url,
        status=cam.status,
        latitude=cam.latitude,
        longitude=cam.longitude,
        counting_line=cam.counting_line,
        source_type=cam.source_type or "live",
        processing_status=cam.processing_status,
        counting_lines=[CountingLineRead.model_validate(l) for l in cam.counting_lines],
        event_count=count
    )



@router.patch(
    "/{camera_id}",
    response_model=CameraRead,
    summary="Update a camera",
)
async def update_camera(
    camera_id: int,
    body: CameraUpdate,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> CameraRead:
    res = await db.execute(
        select(Camera)
        .options(selectinload(Camera.counting_lines))
        .where(Camera.id == camera_id)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    old_name = row.name
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)
    
    # Log the action
    await log_action(db, token.get("sub", "unknown"), "CAMERA_EDITED", f"Updated camera '{old_name}' -> '{row.name}' (ID: {row.id})")
    
    count = (await db.execute(select(func.count(Event.id)).where(Event.camera_id == camera_id))).scalar_one()
    
    return CameraRead(
        id=row.id,
        name=row.name,
        location_id=row.location_id,
        lane_count=row.lane_count,
        rtsp_url=row.rtsp_url,
        status=row.status,
        latitude=row.latitude,
        longitude=row.longitude,
        counting_line=row.counting_line,
        source_type=row.source_type or "live",
        processing_status=row.processing_status,
        counting_lines=[CountingLineRead.model_validate(l) for l in row.counting_lines],
        event_count=count
    )




@router.delete(
    "/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a camera",
)
async def delete_camera(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> None:
    row = await db.get(Camera, camera_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    
    cam_name = row.name
    await db.delete(row)
    await db.commit()
    
    # Log the action
    await log_action(db, token.get("sub", "unknown"), "CAMERA_DELETED", f"Deleted camera '{cam_name}' (ID: {camera_id})")
