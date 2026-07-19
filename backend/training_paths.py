"""
training_paths.py - Neutral, dependency-free configuration for the training dataset.

This module intentionally has NO heavy imports (no ultralytics, no torch, no
fastapi, no sqlalchemy).  It exists so that the live-processing app
(``scheduler.py``, port 8000) can learn where captured frames live *without*
importing ``routers.training`` and dragging the whole ML stack into the
real-time process.

Both ``scheduler.py`` and ``routers/training.py`` import from here.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BACKEND_DIR)

BASE_DIR = os.path.join(BACKEND_DIR, "training_data")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
LABELS_DIR = os.path.join(BASE_DIR, "labels")
SPLIT_DIR = os.path.join(BASE_DIR, "split")

#: Scratch directory used as the *explicit* CWD of the training subprocess.
#: Ultralytics writes its ``runs/`` tree here, so nothing is ever created in
#: (or deleted from) whatever directory the server happened to be started in.
TRAIN_WORK_DIR = os.path.join(BASE_DIR, "work")

#: Where freshly trained ``.pt`` weights are published.
#:
#: ``detection/config.py`` resolves ``VCC_MODEL_PATH`` (default
#: ``"yolo11s.pt"``) as a *relative* path, and the detection process is
#: launched from the repository root via ``start_detection.py``.  Relative
#: model names therefore resolve against the repository root, which is where
#: the shipped ``yolo11s.pt`` / ``yolo26s.pt`` weights actually live.
#: Set ``VCC_TRAINED_MODEL_DIR`` to publish elsewhere.
TRAINED_MODEL_DIR = os.getenv("VCC_TRAINED_MODEL_DIR", REPO_ROOT)

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LABELS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Service endpoints & thresholds
# ---------------------------------------------------------------------------

STREAM_BASE_URL = os.getenv("STREAM_BASE_URL", "http://localhost:8001")

MIN_LABELED_IMAGES = int(os.getenv("VCC_MIN_LABELED_IMAGES", "5"))
VCC_AUTO_TRAIN_THRESHOLD = int(os.getenv("VCC_AUTO_TRAIN_THRESHOLD", "50"))

# ---------------------------------------------------------------------------
# Training subprocess tunables
# ---------------------------------------------------------------------------

TRAIN_BASE_MODEL = os.getenv("VCC_TRAIN_BASE_MODEL", "yolo11n.pt")
TRAIN_IMGSZ = int(os.getenv("VCC_TRAIN_IMGSZ", "480"))

#: Seconds to wait after SIGTERM before escalating to SIGKILL on cancel.
TRAIN_CANCEL_GRACE_SECONDS = float(os.getenv("VCC_TRAIN_CANCEL_GRACE", "10"))

#: Maximum log lines retained in memory for ``GET /api/training/status``.
TRAIN_LOG_LIMIT = int(os.getenv("VCC_TRAIN_LOG_LIMIT", "1000"))

#: Prefix used by the worker subprocess for structured (JSON) status events.
#: Any stdout line not carrying this prefix is forwarded verbatim to the UI
#: log terminal.
EVENT_PREFIX = "@@VCC "
