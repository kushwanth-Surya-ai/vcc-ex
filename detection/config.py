"""
config.py — Central configuration for the VCC detection layer.

All runtime-tunable values come from environment variables so the same
image can be deployed to any environment without code changes.  Call
`load_dotenv()` before importing this module if you use a .env file.
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# Model & tracker
# ---------------------------------------------------------------------------

MODEL_PATH: str = os.getenv("VCC_MODEL_PATH", "yolo11s.pt")
"""Primary model file path.  Set VCC_MODEL_PATH to override."""

FALLBACK_MODEL: str = os.getenv("VCC_FALLBACK_MODEL", "yolo11n.pt")
"""Fallback model used when MODEL_PATH cannot be loaded."""


TRACKER: str = os.getenv("VCC_TRACKER", "bytetrack.yaml")
"""ByteTrack configuration file passed to ultralytics .track()."""

CONF_THRESHOLD: float = float(os.getenv("VCC_CONF", "0.45"))
"""Minimum YOLO confidence score to keep a detection."""

IOU_THRESHOLD: float = float(os.getenv("VCC_IOU", "0.45"))
"""IoU threshold for NMS during detection."""

# ---------------------------------------------------------------------------
# Backend API
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.getenv("VCC_API_URL", "http://localhost:8000")
"""Root URL of the VCC backend REST API (no trailing slash)."""

SERVICE_API_KEY: str = os.getenv("VCC_SERVICE_API_KEY") or os.getenv("SERVICE_API_KEY", "")
"""API key sent in the X-API-Key header with every backend request."""

# ---------------------------------------------------------------------------
# Streamer
# ---------------------------------------------------------------------------

STREAM_PORT: int = int(os.getenv("VCC_STREAM_PORT", "8001"))
"""TCP port the MJPEG aiohttp server listens on."""

FRAME_BUFFER_SIZE: int = int(os.getenv("VCC_FRAME_BUFFER", "4"))
"""Maximum frames held in each per-camera asyncio.Queue before dropping."""

# ---------------------------------------------------------------------------
# Materialized-view refresh
# ---------------------------------------------------------------------------

MV_REFRESH_INTERVAL_MINUTES: int = int(os.getenv("VCC_MV_REFRESH", "5"))
"""How often (minutes) the backend should refresh aggregated views."""

# ---------------------------------------------------------------------------
# Default Pipeline Parameters
# ---------------------------------------------------------------------------

DEFAULT_LANE_ID: int = int(os.getenv("VCC_DEFAULT_LANE_ID", "1"))
DEFAULT_DIRECTION: str = os.getenv("VCC_DEFAULT_DIRECTION", "both")
DEFAULT_LINE_Y: float = float(os.getenv("VCC_DEFAULT_LINE_Y", "0.5"))

# ---------------------------------------------------------------------------
# Vehicle class mapping  (COCO class ids → label strings)
# ---------------------------------------------------------------------------

VEHICLE_CLASS_MAP: dict[int, str] = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}
"""Maps COCO numeric class ids to human-readable vehicle names."""

# ---------------------------------------------------------------------------
# Dashboard category mapping
# Collapses raw YOLO classes into broader UI categories.
# ---------------------------------------------------------------------------

DASHBOARD_CATEGORY: dict[str, str] = {
    "bicycle":    "Non-Motorised",
    "car":        "Light Vehicle",
    "motorcycle": "Two-Wheeler",
    "bus":        "Heavy Vehicle",
    "truck":      "Heavy Vehicle",
}
"""Maps raw vehicle class labels to high-level dashboard display categories."""

# ---------------------------------------------------------------------------
# Camera registry
# Each dict describes one camera / lane.
#
# Fields
# -------
# camera_id   : unique string identifier
# source      : RTSP URL, device index, or local video path
# location    : human-readable location name
# lane_id     : integer lane number at the location
# direction   : counting direction — 'down' | 'up' | 'both'
# line_y      : fractional Y position of the virtual counting line (0-1)
# ---------------------------------------------------------------------------

CAMERAS: list[dict[str, Any]] = [
    {
        "camera_id":  "cam_001",
        "source":     os.getenv("VCC_CAM_001_SRC", "0"),          # webcam / RTSP
        "location":   "MG Road Junction",
        "lane_id":    1,
        "direction":  "both",
        "line_y":     0.55,
    },
    {
        "camera_id":  "cam_002",
        "source":     os.getenv("VCC_CAM_002_SRC", "1"),
        "location":   "Airport Road",
        "lane_id":    1,
        "direction":  "down",
        "line_y":     0.50,
    },
    {
        "camera_id":  "cam_003",
        "source":     os.getenv("VCC_CAM_003_SRC", "2"),
        "location":   "City Centre",
        "lane_id":    2,
        "direction":  "up",
        "line_y":     0.45,
    },
]

# ---------------------------------------------------------------------------
# Drawing / annotation colours  (BGR for OpenCV)
# ---------------------------------------------------------------------------

COLOUR_LINE       = (0, 255, 255)    # cyan
COLOUR_BOX_DOWN   = (0, 200, 0)     # green
COLOUR_BOX_UP     = (0, 100, 255)   # orange
COLOUR_BOX_NONE   = (200, 200, 200) # grey
COLOUR_TEXT       = (255, 255, 255)  # white
LINE_THICKNESS    = 2
BOX_THICKNESS     = 2
FONT_SCALE        = 0.55
