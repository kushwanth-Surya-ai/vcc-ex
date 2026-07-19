"""
routers/counting_lines.py - CRUD operations for camera counting lines (Bearer auth required).
"""
from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token
from database import get_db
from models import CountingLine, Camera
from schemas import CountingLineCreate, CountingLineRead, CountingLineUpdate
from audit import log_action

router = APIRouter(prefix="/api/counting-lines", tags=["counting-lines"])


@router.post(
    "",
    response_model=CountingLineRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a counting line for a camera",
)
async def create_counting_line(
    body: CountingLineCreate,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> CountingLineRead:
    # Verify camera exists
    camera = await db.get(Camera, body.camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    line = CountingLine(**body.model_dump())
    db.add(line)
    await db.commit()
    await db.refresh(line)

    await log_action(
        db,
        token.get("sub", "unknown"),
        "COUNTING_LINE_CREATED",
        f"Created counting line '{line.name}' (ID: {line.id}) for camera '{camera.name}'",
    )

    return CountingLineRead.model_validate(line)


@router.get(
    "",
    response_model=List[CountingLineRead],
    summary="List counting lines for a camera",
)
async def list_counting_lines(
    camera_id: int = Query(..., description="Camera ID to filter lines"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[CountingLineRead]:
    q = select(CountingLine).where(CountingLine.camera_id == camera_id).order_by(CountingLine.id)
    res = await db.execute(q)
    lines = res.scalars().all()
    return [CountingLineRead.model_validate(l) for l in lines]


@router.patch(
    "/{line_id}",
    response_model=CountingLineRead,
    summary="Update a counting line",
)
async def update_counting_line(
    line_id: int,
    body: CountingLineUpdate,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> CountingLineRead:
    line = await db.get(CountingLine, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Counting line not found")

    old_name = line.name
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(line, field, value)

    await db.commit()
    await db.refresh(line)

    await log_action(
        db,
        token.get("sub", "unknown"),
        "COUNTING_LINE_EDITED",
        f"Updated counting line '{old_name}' -> '{line.name}' (ID: {line.id})",
    )

    return CountingLineRead.model_validate(line)


@router.delete(
    "/{line_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a counting line",
)
async def delete_counting_line(
    line_id: int,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> None:
    line = await db.get(CountingLine, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Counting line not found")

    line_name = line.name
    await db.delete(line)
    await db.commit()

    await log_action(
        db,
        token.get("sub", "unknown"),
        "COUNTING_LINE_DELETED",
        f"Deleted counting line '{line_name}' (ID: {line_id})",
    )
