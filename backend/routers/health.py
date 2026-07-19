"""
routers/health.py - Public health-check endpoint.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import HealthResponse
from auth import require_bearer_token

router = APIRouter(tags=["health"])

# Record process start time for uptime calculation
_START_TIME: float = time.monotonic()


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Return API health status including database connectivity and uptime."""
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db_ok=db_ok,
        uptime_seconds=time.monotonic() - _START_TIME,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/api/health/system-metrics", summary="Live system resource utilization")
async def system_metrics(_: dict = Depends(require_bearer_token)):
    import psutil
    try:
        import torch
        cuda_available = torch.cuda.is_available()
    except ImportError:
        cuda_available = False

    # CPU usage
    cpu = psutil.cpu_percent(interval=None)
    # Virtual Memory
    ram = psutil.virtual_memory().percent
    # Disk usage
    try:
        disk = psutil.disk_usage('/').percent
    except Exception:
        disk = 0.0
        
    # GPU usage
    gpu = 0.0
    gpu_name = "N/A"
    if cuda_available:
        try:
            device_id = torch.cuda.current_device()
            gpu_name = torch.cuda.get_device_name(device_id)
            mem_allocated = torch.cuda.memory_allocated(device_id)
            total_mem = torch.cuda.get_device_properties(device_id).total_memory
            if total_mem > 0:
                gpu = round((mem_allocated / total_mem) * 100, 1)
        except Exception:
            gpu = 0.0
            
    return {
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "gpu": gpu,
        "gpu_name": gpu_name
    }

