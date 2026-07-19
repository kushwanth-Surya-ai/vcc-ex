"""
models.py – SQLAlchemy ORM models for the VCC system.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database import Base

# ---------------------------------------------------------------------------
# Enum types
# ---------------------------------------------------------------------------


class CameraStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    maintenance = "maintenance"


class VehicleClass(str, enum.Enum):
    car = "car"
    truck = "truck"
    bus = "bus"
    motorcycle = "motorcycle"
    bicycle = "bicycle"
    pedestrian = "pedestrian"
    van = "van"
    unknown = "unknown"


class CrossingDir(str, enum.Enum):
    in_ = "in"
    out = "out"
    both = "both"


class UserRole(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class AlertSeverity(str, enum.Enum):
    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"


class AlertType(str, enum.Enum):
    count_spike = "COUNT_SPIKE"
    camera_offline = "CAMERA_OFFLINE"
    lane_saturation = "LANE_SATURATION"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    cameras: list["Camera"] = relationship("Camera", back_populates="location")
    events: list["Event"] = relationship("Event", back_populates="location")


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False)
    lane_count = Column(Integer, nullable=False, default=1)
    rtsp_url = Column(String(1024), nullable=True)
    status = Column(String(32), nullable=False, default=CameraStatus.active.value)
    last_seen_at = Column(DateTime(timezone=True), nullable=True, default=func.now())
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    counting_line = Column(String(255), nullable=True)


    location: "Location" = relationship("Location", back_populates="cameras")
    events: list["Event"] = relationship("Event", back_populates="camera", cascade="all, delete-orphan", passive_deletes=True)
    alerts: list["Alert"] = relationship("Alert", back_populates="camera", cascade="all, delete-orphan", passive_deletes=True)
    counting_lines: list["CountingLine"] = relationship("CountingLine", back_populates="camera", cascade="all, delete-orphan", passive_deletes=True)


class CountingLine(Base):
    __tablename__ = "counting_lines"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    x1 = Column(Float, nullable=False)
    y1 = Column(Float, nullable=False)
    x2 = Column(Float, nullable=False)
    y2 = Column(Float, nullable=False)
    lane_id = Column(Integer, nullable=False, default=1)
    direction = Column(String(16), nullable=False, default="both")
    color = Column(String(16), nullable=False, default="#00d4ff")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    camera: "Camera" = relationship("Camera", back_populates="counting_lines")



class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default=UserRole.viewer.value)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Event(Base):
    __tablename__ = "events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    lane_id = Column(Integer, nullable=False)
    vehicle_class = Column(String(32), nullable=False)
    confidence = Column(Float, nullable=False)
    crossing_dir = Column(String(8), nullable=False, default=CrossingDir.in_.value)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    camera: "Camera" = relationship("Camera", back_populates="events")
    location: "Location" = relationship("Location", back_populates="events")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True)
    alert_type = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    severity = Column(String(16), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledged = Column(Boolean, nullable=False, default=False)

    camera: "Camera" = relationship("Camera", back_populates="alerts")


class LoginLog(Base):
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    email = Column(String(320), nullable=False)
    ip_address = Column(String(45), nullable=False)
    success = Column(Boolean, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    email = Column(String(320), nullable=False)
    action = Column(String(255), nullable=False)
    details = Column(Text, nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(255), primary_key=True)
    value = Column(String(1024), nullable=False)

