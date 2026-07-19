"""
schemas.py – Pydantic v2 request/response schemas for all VCC resources.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---------------------------------------------------------------------------
# Generic pagination wrapper
# ---------------------------------------------------------------------------

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    limit: int
    offset: int
    items: List[T]


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


class LocationBase(BaseModel):
    name: str = Field(..., max_length=255)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class LocationCreate(LocationBase):
    pass


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)


class LocationRead(LocationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


class CameraBase(BaseModel):
    name: str = Field(..., max_length=255)
    location_id: int
    lane_count: int = Field(1, ge=1, le=64)
    rtsp_url: str = Field(..., max_length=1024)
    status: str = Field("active")
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    counting_line: Optional[str] = Field(None, max_length=255)


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    location_id: Optional[int] = None
    lane_count: Optional[int] = Field(None, ge=1, le=64)
    rtsp_url: Optional[str] = Field(None, max_length=1024)
    status: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    counting_line: Optional[str] = Field(None, max_length=255)


class CountingLineBase(BaseModel):
    name: str = Field(..., max_length=100)
    x1: float = Field(..., ge=0.0, le=1.0)
    y1: float = Field(..., ge=0.0, le=1.0)
    x2: float = Field(..., ge=0.0, le=1.0)
    y2: float = Field(..., ge=0.0, le=1.0)
    lane_id: int = Field(1, ge=1, le=64)
    direction: str = Field("both", pattern="^(both|down|up)$")
    color: str = Field("#00d4ff", max_length=16)


class CountingLineCreate(CountingLineBase):
    camera_id: int


class CountingLineUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    x1: Optional[float] = Field(None, ge=0.0, le=1.0)
    y1: Optional[float] = Field(None, ge=0.0, le=1.0)
    x2: Optional[float] = Field(None, ge=0.0, le=1.0)
    y2: Optional[float] = Field(None, ge=0.0, le=1.0)
    lane_id: Optional[int] = Field(None, ge=1, le=64)
    direction: Optional[str] = Field(None, pattern="^(both|down|up)$")
    color: Optional[str] = Field(None, max_length=16)


class CountingLineRead(CountingLineBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    camera_id: int
    created_at: datetime


class CameraRead(CameraBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_count: int = 0
    counting_lines: List[CountingLineRead] = []



# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    email: str
    password: str = Field(..., min_length=8)
    role: str = Field("viewer")


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


class EventCreate(BaseModel):
    camera_id: int
    location_id: int
    lane_id: int = Field(..., ge=0)
    vehicle_class: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    crossing_dir: str = Field("in")
    timestamp: Optional[datetime] = None  # defaults to server now() if omitted


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_id: int
    location_id: int
    lane_id: int
    vehicle_class: str
    confidence: float
    crossing_dir: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_id: Optional[int]
    alert_type: str
    message: str
    severity: str
    timestamp: datetime
    acknowledged: bool


class AlertAcknowledge(BaseModel):
    acknowledged: bool = True


# ---------------------------------------------------------------------------
# Auth tokens
# ---------------------------------------------------------------------------


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class AnalyticsSummary(BaseModel):
    total_today: int
    total_yesterday: int
    pct_change: float
    total_vehicles: int
    class_counts: Dict[str, int]
    deltas: Dict[str, float]  # percentage; can be positive, negative, or None → 0.0


class ClassCount(BaseModel):
    vehicle_class: str
    count: int


class LaneCount(BaseModel):
    camera_id: int
    lane_id: int
    vehicle_class: str
    count: int


class HeatmapCell(BaseModel):
    location_id: int
    vehicle_class: str
    hour: datetime  # truncated to the hour
    count: int


class TimeseriesPoint(BaseModel):
    ts: datetime
    count: int
    car: int = 0
    bike: int = 0
    heavy: int = 0
    bus: int = 0
    bicycle: int = 0


class TopLocation(BaseModel):
    location_id: int
    location_name: str
    total_count: int


class LoginLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    email: str
    ip_address: str
    success: bool


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    email: str
    action: str
    details: Optional[str] = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    db_ok: bool
    uptime_seconds: float
    timestamp: datetime
