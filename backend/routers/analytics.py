"""
routers/analytics.py - Analytics endpoints reading from materialized views and raw events.

All endpoints require Bearer token auth.

Endpoints:
  GET /api/analytics/summary         - Total today vs yesterday, % change
  GET /api/analytics/by-class        - Count per vehicle class (today)
  GET /api/analytics/by-lane         - Lane counts from mv_lane_counts
  GET /api/analytics/hourly-heatmap  - 2D grid from mv_hourly_counts
  GET /api/analytics/top-locations   - Top 5 locations by total count
  GET /api/analytics/timeseries      - Time-bucketed counts (interval=hour|day|week)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_bearer_token
from database import get_db
from models import Event, Location
from schemas import (
    AnalyticsSummary,
    ClassCount,
    HeatmapCell,
    LaneCount,
    TimeseriesPoint,
    TopLocation,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_utc() -> datetime:
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    summary="Total today vs yesterday with % change",
)
async def get_summary(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> AnalyticsSummary:
    today_start = _today_utc()
    yesterday_start = today_start - timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)

    # 1. Total vehicles counts today and yesterday
    total_today = (
        await db.execute(
            select(func.count(Event.id))
            .where(Event.timestamp >= today_start, Event.timestamp < tomorrow_start)
        )
    ).scalar_one() or 0

    total_yesterday = (
        await db.execute(
            select(func.count(Event.id))
            .where(Event.timestamp >= yesterday_start, Event.timestamp < today_start)
        )
    ).scalar_one() or 0

    if total_yesterday > 0:
        pct_change = ((total_today - total_yesterday) / total_yesterday) * 100.0
    else:
        pct_change = 0.0

    # 2. Counts per class for today and yesterday
    class_rows_today = await db.execute(
        select(Event.vehicle_class, func.count(Event.id))
        .where(Event.timestamp >= today_start, Event.timestamp < tomorrow_start)
        .group_by(Event.vehicle_class)
    )
    class_rows_yesterday = await db.execute(
        select(Event.vehicle_class, func.count(Event.id))
        .where(Event.timestamp >= yesterday_start, Event.timestamp < today_start)
        .group_by(Event.vehicle_class)
    )

    classes = ["car", "motorcycle", "bicycle", "bus", "truck"]
    today_counts = {cls: 0 for cls in classes}
    yesterday_counts = {cls: 0 for cls in classes}

    for vc, count in class_rows_today.all():
        vc_str = vc.value if hasattr(vc, "value") else vc
        if vc_str in today_counts:
            today_counts[vc_str] = count

    for vc, count in class_rows_yesterday.all():
        vc_str = vc.value if hasattr(vc, "value") else vc
        if vc_str in yesterday_counts:
            yesterday_counts[vc_str] = count

    def calc_delta(t_val, y_val):
        if y_val > 0:
            return round(((t_val - y_val) / y_val) * 100.0, 2)
        return 0.0

    deltas = {
        "total": calc_delta(total_today, total_yesterday),
        "car": calc_delta(today_counts["car"], yesterday_counts["car"]),
        "motorcycle": calc_delta(today_counts["motorcycle"], yesterday_counts["motorcycle"]),
        "bicycle": calc_delta(today_counts["bicycle"], yesterday_counts["bicycle"]),
        "bus": calc_delta(today_counts["bus"], yesterday_counts["bus"]),
        "truck": calc_delta(today_counts["truck"], yesterday_counts["truck"]),
    }

    return AnalyticsSummary(
        total_today=total_today,
        total_yesterday=total_yesterday,
        pct_change=round(pct_change, 2),
        total_vehicles=total_today,
        class_counts=today_counts,
        deltas=deltas,
    )


# ---------------------------------------------------------------------------
# By class
# ---------------------------------------------------------------------------


@router.get(
    "/by-class",
    response_model=List[ClassCount],
    summary="Vehicle counts grouped by class (today)",
)
async def get_by_class(
    date: Optional[str] = Query(None, description="ISO date e.g. 2024-01-15; defaults to today"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[ClassCount]:
    local_now = datetime.now().astimezone()
    local_tz = local_now.tzinfo

    if date:
        try:
            parsed_dt = datetime.fromisoformat(date)
            target_date = parsed_dt.date()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid date format")
    else:
        target_date = local_now.date()

    start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=local_tz)
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)

    rows = await db.execute(
        select(Event.vehicle_class, func.count(Event.id).label("cnt"))
        .where(Event.timestamp >= start_utc, Event.timestamp < end_utc)
        .group_by(Event.vehicle_class)
        .order_by(func.count(Event.id).desc())
    )
    return [
        ClassCount(
            vehicle_class=r.vehicle_class.value if hasattr(r.vehicle_class, "value") else r.vehicle_class,
            count=r.cnt,
        )
        for r in rows.fetchall()
    ]


# ---------------------------------------------------------------------------
# By lane
# ---------------------------------------------------------------------------


@router.get(
    "/by-lane",
    response_model=List[LaneCount],
    summary="Lane vehicle counts",
)
async def get_by_lane(
    camera_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[LaneCount]:
    q = (
        select(
            Event.camera_id,
            Event.lane_id,
            Event.vehicle_class,
            func.count(Event.id).label("cnt"),
        )
        .group_by(Event.camera_id, Event.lane_id, Event.vehicle_class)
        .order_by(Event.camera_id, Event.lane_id, Event.vehicle_class)
    )
    if camera_id is not None:
        q = q.where(Event.camera_id == camera_id)
    rows = await db.execute(q)
    return [
        LaneCount(
            camera_id=r.camera_id,
            lane_id=r.lane_id,
            vehicle_class=r.vehicle_class.value if hasattr(r.vehicle_class, "value") else r.vehicle_class,
            count=r.cnt,
        )
        for r in rows.fetchall()
    ]


# ---------------------------------------------------------------------------
# Hourly heatmap
# ---------------------------------------------------------------------------


@router.get(
    "/hourly-heatmap",
    response_model=List[HeatmapCell],
    summary="2D hourly heatmap from mv_hourly_counts and live events",
)
async def get_hourly_heatmap(
    location_id: Optional[int] = Query(None),
    days: int = Query(7, ge=1, le=30, description="Number of past days to include"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[HeatmapCell]:
    today_start = _today_utc()
    since = today_start - timedelta(days=days - 1)

    # 1. Query materialized view for historical days [since, today_start)
    historical_cells = []
    try:
        sql = (
            "SELECT location_id, vehicle_class, hour, total_count "
            "FROM mv_hourly_counts "
            "WHERE hour >= :since AND hour < :today_start"
        )
        params: dict = {"since": since, "today_start": today_start}
        if location_id is not None:
            sql += " AND location_id = :lid"
            params["lid"] = location_id
        sql += " ORDER BY hour, location_id, vehicle_class"

        rows = await db.execute(text(sql), params)
        results = rows.fetchall()
        for r in results:
            historical_cells.append(
                HeatmapCell(
                    location_id=r[0],
                    vehicle_class=r[1],
                    hour=r[2].replace(tzinfo=timezone.utc) if r[2].tzinfo is None else r[2],
                    count=int(r[3]),
                )
            )
    except Exception:
        await db.rollback()
        # Fallback for historical range (raw events query)
        q = (
            select(
                Event.location_id,
                Event.vehicle_class,
                literal_column("date_trunc('hour', timestamp)").label("hour"),
                func.count(Event.id).label("cnt"),
            )
            .where(Event.timestamp >= since, Event.timestamp < today_start)
            .group_by(Event.location_id, Event.vehicle_class, literal_column("date_trunc('hour', timestamp)"))
        )
        if location_id is not None:
            q = q.where(Event.location_id == location_id)
        rows = await db.execute(q)
        for r in rows.fetchall():
            historical_cells.append(
                HeatmapCell(
                    location_id=r.location_id,
                    vehicle_class=r.vehicle_class.value if hasattr(r.vehicle_class, "value") else r.vehicle_class,
                    hour=r.hour.replace(tzinfo=timezone.utc) if r.hour.tzinfo is None else r.hour,
                    count=r.cnt,
                )
            )

    # 2. Query raw events for today [today_start, tomorrow)
    live_cells = []
    q_live = (
        select(
            Event.location_id,
            Event.vehicle_class,
            literal_column("date_trunc('hour', timestamp)").label("hour"),
            func.count(Event.id).label("cnt"),
        )
        .where(Event.timestamp >= today_start)
        .group_by(Event.location_id, Event.vehicle_class, literal_column("date_trunc('hour', timestamp)"))
    )
    if location_id is not None:
        q_live = q_live.where(Event.location_id == location_id)
    rows_live = await db.execute(q_live)
    for r in rows_live.fetchall():
        live_cells.append(
            HeatmapCell(
                location_id=r.location_id,
                vehicle_class=r.vehicle_class.value if hasattr(r.vehicle_class, "value") else r.vehicle_class,
                hour=r.hour.replace(tzinfo=timezone.utc) if r.hour.tzinfo is None else r.hour,
                count=r.cnt,
            )
        )

    # 3. Combine and sort
    combined = historical_cells + live_cells
    combined.sort(key=lambda x: (x.hour, x.location_id, x.vehicle_class))
    return combined


# ---------------------------------------------------------------------------
# Top locations
# ---------------------------------------------------------------------------


@router.get(
    "/top-locations",
    response_model=List[TopLocation],
    summary="Top 5 locations by total event count",
)
async def get_top_locations(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[TopLocation]:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = await db.execute(
        select(
            Event.location_id,
            Location.name.label("location_name"),
            func.count(Event.id).label("total_count"),
        )
        .join(Location, Event.location_id == Location.id)
        .where(Event.timestamp >= since)
        .group_by(Event.location_id, Location.name)
        .order_by(func.count(Event.id).desc())
        .limit(5)
    )

    return [
        TopLocation(
            location_id=r.location_id,
            location_name=r.location_name,
            total_count=r.total_count,
        )
        for r in rows.fetchall()
    ]


# ---------------------------------------------------------------------------
# Timeseries
# ---------------------------------------------------------------------------

_VALID_INTERVALS = {"hour", "day", "week"}


@router.get(
    "/timeseries",
    response_model=List[TimeseriesPoint],
    summary="Time-bucketed event counts",
)
async def get_timeseries(
    from_: Optional[datetime] = Query(
        None,
        alias="from",
        description="Start of time range (ISO 8601 with timezone)",
    ),
    to: Optional[datetime] = Query(
        None,
        description="End of time range (ISO 8601 with timezone)",
    ),
    interval: str = Query("hour", description="Bucket size: hour | day | week"),
    camera_id: Optional[int] = Query(None),
    location_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> List[TimeseriesPoint]:
    if interval not in _VALID_INTERVALS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"interval must be one of: {', '.join(sorted(_VALID_INTERVALS))}",
        )

    # Helper to enforce timezone-awareness
    def make_aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    # Defaults & Timezone enforcement
    if to is None:
        to = datetime.now(timezone.utc)
    else:
        to = make_aware(to)
        # If time is exactly midnight (00:00:00), it represents a date-only selector (like "2026-07-15")
        # so extend it to the end of that day (23:59:59.999999) to include all events on that day.
        if to.hour == 0 and to.minute == 0 and to.second == 0 and to.microsecond == 0:
            to = to.replace(hour=23, minute=59, second=59, microsecond=999999)

    if from_ is None:
        if interval == "hour":
            from_ = to - timedelta(hours=24)
        elif interval == "day":
            from_ = to - timedelta(days=30)
        else:
            from_ = to - timedelta(weeks=12)
    else:
        from_ = make_aware(from_)

    if from_ >= to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'from' must be before 'to'",
        )

    from sqlalchemy import case
    trunc_expr = func.date_trunc(interval, Event.timestamp).label("ts")

    q = (
        select(
            trunc_expr,
            func.sum(case((Event.vehicle_class == 'car', 1), else_=0)).label("car"),
            func.sum(case((Event.vehicle_class == 'motorcycle', 1), else_=0)).label("motorcycle"),
            func.sum(case((Event.vehicle_class == 'bicycle', 1), else_=0)).label("bicycle"),
            func.sum(case((Event.vehicle_class == 'truck', 1), else_=0)).label("heavy"),
            func.sum(case((Event.vehicle_class == 'bus', 1), else_=0)).label("bus"),
            func.count(Event.id).label("total")
        )
        .where(Event.timestamp >= from_, Event.timestamp <= to)
        .group_by(trunc_expr)
        .order_by(trunc_expr)
    )

    if camera_id is not None:
        q = q.where(Event.camera_id == camera_id)
    if location_id is not None:
        q = q.where(Event.location_id == location_id)

    rows = await db.execute(q)
    return [
        TimeseriesPoint(
            ts=r.ts,
            count=int(r.total or 0),
            car=int(r.car or 0),
            bike=int((r.motorcycle or 0) + (r.bicycle or 0)),
            heavy=int(r.heavy or 0),  # In models we use truck, in timeseries we use heavy
            bus=int(r.bus or 0),
            bicycle=int(r.bicycle or 0)
        )
        for r in rows.fetchall()
    ]
