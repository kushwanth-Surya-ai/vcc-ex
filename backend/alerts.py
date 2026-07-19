"""
alerts.py - Rule-based alert engine with duplicate alert prevention.

Rules implemented:
  - COUNT_SPIKE     : count in last 5 min > 2x rolling average -> HIGH
  - CAMERA_OFFLINE  : no events for > 2 min                   -> MEDIUM
  - LANE_SATURATION : lane count > threshold                   -> LOW
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Alert, AlertSeverity, AlertType, Camera, Event

logger = logging.getLogger(__name__)

LANE_SATURATION_THRESHOLD: int = int(os.getenv("LANE_SATURATION_THRESHOLD", "50"))


async def _alert_exists(
    db: AsyncSession,
    camera_id: int,
    alert_type: AlertType,
) -> bool:
    """Return True if an unacknowledged alert of the same type exists for this camera."""
    q = select(Alert).where(
        Alert.camera_id == camera_id,
        Alert.alert_type == alert_type.value,
        Alert.acknowledged == False
    )
    res = await db.execute(q)
    return res.scalars().first() is not None


async def check_count_spike(
    db: AsyncSession,
    camera_id: int,
) -> Optional[Alert]:
    """Return a HIGH alert when recent 5-min count > 2x rolling average.

    Baseline window = previous 55 minutes (11 x 5-min buckets).
    """
    if await _alert_exists(db, camera_id, AlertType.count_spike):
        return None

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=5)
    baseline_start = now - timedelta(minutes=60)

    recent_q = await db.execute(
        select(func.count(Event.id)).where(
            Event.camera_id == camera_id,
            Event.timestamp >= window_start,
        )
    )
    recent_count: int = recent_q.scalar_one()

    baseline_q = await db.execute(
        select(func.count(Event.id)).where(
            Event.camera_id == camera_id,
            Event.timestamp >= baseline_start,
            Event.timestamp < window_start,
        )
    )
    baseline_count: int = baseline_q.scalar_one()

    rolling_avg = baseline_count / 11.0  # avg events per 5-min bucket

    if rolling_avg > 0 and recent_count > 2 * rolling_avg:
        logger.info(
            "COUNT_SPIKE camera=%d recent=%d avg=%.1f",
            camera_id, recent_count, rolling_avg,
        )
        return Alert(
            camera_id=camera_id,
            alert_type=AlertType.count_spike.value,
            severity=AlertSeverity.high.value,
            message=(
                f"Count spike on camera {camera_id}: "
                f"{recent_count} events in last 5 min "
                f"(rolling avg {rolling_avg:.1f})"
            ),
        )
    return None


async def check_camera_offline(db: AsyncSession) -> List[Alert]:
    """Return MEDIUM alerts for cameras silent for > 2 min, active or inactive."""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=2)

    # Check all cameras (active or inactive) that don't have an active unacknowledged alert
    cameras_q = await db.execute(select(Camera))
    cameras: List[Camera] = list(cameras_q.scalars().all())

    alerts: List[Alert] = []
    for cam in cameras:
        if await _alert_exists(db, cam.id, AlertType.camera_offline):
            continue

        # If camera has not sent events in last 2 mins, trigger offline alert
        last_q = await db.execute(
            select(func.max(Event.timestamp)).where(Event.camera_id == cam.id)
        )
        last_event: Optional[datetime] = last_q.scalar_one()

        if last_event is None or last_event < threshold:
            logger.info("CAMERA_OFFLINE camera_id=%d", cam.id)
            alerts.append(
                Alert(
                    camera_id=cam.id,
                    alert_type=AlertType.camera_offline.value,
                    severity=AlertSeverity.medium.value,
                    message=(
                        f"Camera {cam.id} ({cam.name}) "
                        f"has not sent events for > 2 minutes"
                    ),
                )
            )
    return alerts


async def check_lane_saturation(
    db: AsyncSession,
    camera_id: int,
    lane_id: int,
    threshold: int = LANE_SATURATION_THRESHOLD,
) -> Optional[Alert]:
    """Return a LOW alert when a lane's 5-min count exceeds threshold."""
    if await _alert_exists(db, camera_id, AlertType.lane_saturation):
        return None

    window_start = datetime.now(timezone.utc) - timedelta(minutes=5)

    count_q = await db.execute(
        select(func.count(Event.id)).where(
            Event.camera_id == camera_id,
            Event.lane_id == lane_id,
            Event.timestamp >= window_start,
        )
    )
    count: int = count_q.scalar_one()

    if count > threshold:
        logger.info(
            "LANE_SATURATION camera=%d lane=%d count=%d threshold=%d",
            camera_id, lane_id, count, threshold,
        )
        return Alert(
            camera_id=camera_id,
            alert_type=AlertType.lane_saturation.value,
            severity=AlertSeverity.low.value,
            message=(
                f"Lane {lane_id} on camera {camera_id} saturated: "
                f"{count} events in last 5 min (threshold {threshold})"
            ),
        )
    return None


async def run_alert_checks(
    db: AsyncSession,
    camera_id: Optional[int] = None,
    lane_id: Optional[int] = None,
) -> List[Alert]:
    """Run all alert rules; persist and return any new Alert objects."""
    new_alerts: List[Alert] = []

    if camera_id is not None:
        spike = await check_count_spike(db, camera_id)
        if spike:
            new_alerts.append(spike)

        if lane_id is not None:
            sat = await check_lane_saturation(db, camera_id, lane_id)
            if sat:
                new_alerts.append(sat)

    offline = await check_camera_offline(db)
    new_alerts.extend(offline)

    if new_alerts:
        db.add_all(new_alerts)
        await db.commit()
        for alert in new_alerts:
            await db.refresh(alert)

    return new_alerts
