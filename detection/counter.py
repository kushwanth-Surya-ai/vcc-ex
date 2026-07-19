"""
counter.py — Virtual line crossing counter for the VCC detection layer.

Design decisions
----------------
* Two **independent** dedup sets (``counted_down`` / ``counted_up``) live on
  every ``LineCounter``.  A track-id being in ``counted_down`` does NOT prevent
  it from later being recorded in ``counted_up`` (critical for direction='both').
* ``line_y`` is expressed as a fraction [0, 1] of the frame height so the same
  config works at any resolution.
* ``process_tracks()`` is fully synchronous — no asyncio overhead — to keep the
  hot path as fast as possible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

class CrossingEvent(NamedTuple):
    """One vehicle crossing a virtual counting line."""
    track_id:      int
    direction:     str        # 'down' | 'up'
    vehicle_class: str
    confidence:    float
    camera_id:     str
    timestamp:     datetime
    lane_id:       int = 1


# ---------------------------------------------------------------------------
# LineCounter
# ---------------------------------------------------------------------------

def ccw(A: tuple[float, float], B: tuple[float, float], C: tuple[float, float]) -> bool:
    """Return True if points A, B, C are in counter-clockwise order."""
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])


def intersect(A: tuple[float, float], B: tuple[float, float], C: tuple[float, float], D: tuple[float, float]) -> bool:
    """Return True if line segments AB and CD intersect."""
    return ccw(A, C, D) != ccw(B, C, D) and ccw(A, B, C) != ccw(A, B, D)


@dataclass
class LineCounter:
    """
    Stateful per-camera multi-line crossing counter.
    """

    camera_id:       str
    line_y:          float           = 0.5
    direction:       str             = "both"
    counting_line:   str | None      = None
    lines:           list[dict]      = field(default_factory=list)

    # Internal state
    prev_centroids:  dict[int, tuple[float, float]] = field(default_factory=dict)
    counted_down_per_line: dict[int, set[int]] = field(default_factory=dict)
    counted_up_per_line:   dict[int, set[int]] = field(default_factory=dict)

    def __post_init__(self):
        # If no lines list is provided, translate from counting_line / line_y
        if not self.lines:
            if self.counting_line:
                try:
                    coords = [float(x) for x in self.counting_line.split(",")]
                    if len(coords) == 4:
                        self.lines.append({
                            "id": 1,
                            "name": "Line 1",
                            "x1": coords[0], "y1": coords[1], "x2": coords[2], "y2": coords[3],
                            "lane_id": 1,
                            "direction": self.direction,
                            "color": "#00d4ff"
                        })
                except Exception:
                    pass
            else:
                self.lines.append({
                    "id": 1,
                    "name": "Line 1",
                    "x1": 0.0, "y1": self.line_y, "x2": 1.0, "y2": self.line_y,
                    "lane_id": 1,
                    "direction": self.direction,
                    "color": "#00d4ff"
                })

        # Initialize tracking sets
        for line in self.lines:
            lid = line["id"]
            if lid not in self.counted_down_per_line:
                self.counted_down_per_line[lid] = set()
            if lid not in self.counted_up_per_line:
                self.counted_up_per_line[lid] = set()

    def process_tracks(
        self,
        tracks: list[Any],
        frame_h: int,
        frame_w: int | None = None,
    ) -> list[CrossingEvent]:
        events: list[CrossingEvent] = []
        now = datetime.now(timezone.utc)
        if not frame_w:
            frame_w = 1920

        for line in self.lines:
            lid = line["id"]
            if lid not in self.counted_down_per_line:
                self.counted_down_per_line[lid] = set()
            if lid not in self.counted_up_per_line:
                self.counted_up_per_line[lid] = set()

            x1_px = line["x1"] * frame_w
            y1_px = line["y1"] * frame_h
            x2_px = line["x2"] * frame_w
            y2_px = line["y2"] * frame_h
            line_seg = ((x1_px, y1_px), (x2_px, y2_px))
            line_dir = line.get("direction", "both")
            lane_id = line.get("lane_id", 1)

            for track in tracks:
                tid_raw = track.id
                if tid_raw is None:
                    continue
                try:
                    track_id = int(tid_raw.item()) if hasattr(tid_raw, "item") else int(tid_raw)
                except (TypeError, ValueError):
                    continue

                # Get bounding box
                try:
                    box = track.xyxy
                    if hasattr(box, "shape") and len(box.shape) == 2:
                      box = box[0]
                    cx = (float(box[0]) + float(box[2])) / 2.0
                    cy = (float(box[1]) + float(box[3])) / 2.0
                except Exception:
                    continue

                prev_val = self.prev_centroids.get(track_id)
                if prev_val is not None:
                    prev_x, prev_y = prev_val
                    A, B = line_seg
                    C = (prev_x, prev_y)
                    D = (cx, cy)

                    if intersect(A, B, C, D):
                        cross_prod = (B[0] - A[0]) * (D[1] - C[1]) - (B[1] - A[1]) * (D[0] - C[0])
                        crossed_down = cross_prod > 0
                        crossed_up = cross_prod < 0

                        if crossed_down and line_dir != "up" and track_id not in self.counted_down_per_line[lid]:
                            self.counted_down_per_line[lid].add(track_id)
                            events.append(CrossingEvent(
                                track_id=track_id,
                                direction="down",
                                vehicle_class=self._get_class(track),
                                confidence=self._get_conf(track),
                                camera_id=self.camera_id,
                                timestamp=now,
                                lane_id=lane_id
                            ))
                            logger.debug("cam=%s track=%d Line %s DOWN", self.camera_id, track_id, line["name"])

                        elif crossed_up and line_dir != "down" and track_id not in self.counted_up_per_line[lid]:
                            self.counted_up_per_line[lid].add(track_id)
                            events.append(CrossingEvent(
                                track_id=track_id,
                                direction="up",
                                vehicle_class=self._get_class(track),
                                confidence=self._get_conf(track),
                                camera_id=self.camera_id,
                                timestamp=now,
                                lane_id=lane_id
                            ))
                            logger.debug("cam=%s track=%d Line %s UP", self.camera_id, track_id, line["name"])

        # Update prev_centroids at the end of the frame
        for track in tracks:
            tid_raw = track.id
            if tid_raw is None:
                continue
            try:
                track_id = int(tid_raw.item()) if hasattr(tid_raw, "item") else int(tid_raw)
                box = track.xyxy
                if hasattr(box, "shape") and len(box.shape) == 2:
                    box = box[0]
                cx = (float(box[0]) + float(box[2])) / 2.0
                cy = (float(box[1]) + float(box[3])) / 2.0
                self.prev_centroids[track_id] = (cx, cy)
            except Exception:
                pass

        return events

    def _get_class(self, track: Any) -> str:
        try:
            cls_raw  = track.cls
            cls_id   = int(cls_raw.item() if hasattr(cls_raw, "item") else cls_raw)
            return config.VEHICLE_CLASS_MAP.get(cls_id, f"class_{cls_id}")
        except Exception:
            return "unknown"

    def _get_conf(self, track: Any) -> float:
        try:
            conf_raw = track.conf
            return float(conf_raw.item() if hasattr(conf_raw, "item") else conf_raw)
        except Exception:
            return 0.0

    def reset(self) -> None:
        self.prev_centroids.clear()
        self.counted_down_per_line.clear()
        self.counted_up_per_line.clear()
        for line in self.lines:
            lid = line["id"]
            self.counted_down_per_line[lid] = set()
            self.counted_up_per_line[lid] = set()

    @property
    def total_down(self) -> int:
        unique = set()
        for s in self.counted_down_per_line.values():
            unique.update(s)
        return len(unique)

    @property
    def total_up(self) -> int:
        unique = set()
        for s in self.counted_up_per_line.values():
            unique.update(s)
        return len(unique)



# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_counters_from_config() -> dict[str, "LineCounter"]:
    """
    Instantiate one ``LineCounter`` per camera entry in ``config.CAMERAS``.

    Returns
    -------
    dict[str, LineCounter]
        Mapping of ``camera_id`` → ``LineCounter`` instance.
    """
    counters: dict[str, LineCounter] = {}
    for cam in config.CAMERAS:
        cid = cam["camera_id"]
        counters[cid] = LineCounter(
            camera_id = cid,
            line_y    = float(cam.get("line_y",    0.5)),
            direction = str(  cam.get("direction", "both")),
        )
        logger.info(
            "Created LineCounter: camera_id=%s  line_y=%.2f  direction=%s",
            cid,
            counters[cid].line_y,
            counters[cid].direction,
        )
    return counters
