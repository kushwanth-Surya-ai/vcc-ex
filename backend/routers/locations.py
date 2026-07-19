"""
routers/locations.py - Full CRUD for Location resources (Bearer auth required).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token
from database import get_db
from models import Location
from schemas import LocationCreate, LocationRead, LocationUpdate, PaginatedResponse

router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.post(
    "",
    response_model=LocationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a location",
)
async def create_location(
    body: LocationCreate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> LocationRead:
    loc = Location(**body.model_dump())
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return LocationRead.model_validate(loc)


@router.get(
    "",
    response_model=PaginatedResponse[LocationRead],
    summary="List locations (paginated)",
)
async def list_locations(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> PaginatedResponse[LocationRead]:
    total_q = await db.execute(select(func.count(Location.id)))
    total: int = total_q.scalar_one()

    rows_q = await db.execute(
        select(Location).order_by(Location.id).limit(limit).offset(offset)
    )
    rows = rows_q.scalars().all()

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[LocationRead.model_validate(r) for r in rows],
    )


@router.get(
    "/{location_id}",
    response_model=LocationRead,
    summary="Get a location by ID",
)
async def get_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> LocationRead:
    row = await db.get(Location, location_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    return LocationRead.model_validate(row)


@router.patch(
    "/{location_id}",
    response_model=LocationRead,
    summary="Update a location",
)
async def update_location(
    location_id: int,
    body: LocationUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> LocationRead:
    row = await db.get(Location, location_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)
    return LocationRead.model_validate(row)


@router.delete(
    "/{location_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a location",
)
async def delete_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> None:
    row = await db.get(Location, location_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    await db.delete(row)
    await db.commit()
