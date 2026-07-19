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
* All per-track state (``prev_centroids`` and the dedup sets) is evicted once a
  track has been absent for ``retire_after_frames`` consecutive frames.  This
  bounds memory over long runs and stops a recycled ByteTrack id from inheriting
  the previous vehicle's centroid (which produced phantom counts) or its
  "already counted" flag (which silently suppressed real ones).  The threshold
  is deliberately several frames wide so brief occlusions do not retire a track.
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

    # Number of consecutive frames a track may be absent before it is considered
    # retired.  Until then its centroid and dedup entries are kept, so a track
    # that briefly disappears behind an occlusion resumes exactly where it left
    # off.  Once retired, all of its state is dropped so (a) memory stays bounded
    # and (b) a recycled ByteTrack id starts from a clean slate.
    retire_after_frames: int = 30

    # Internal state
    prev_centroids:  dict[int, tuple[float, float]] = field(default_factory=dict)
    counted_down_per_line: dict[int, set[int]] = field(default_factory=dict)
    counted_up_per_line:   dict[int, set[int]] = field(default_factory=dict)

    # track_id -> consecutive frames not seen (0 while visible)
    _frames_missing: dict[int, int] = field(default_factory=dict, repr=False)
    # Reused across frames to keep the hot path allocation-free
    _seen_this_frame: set[int] = field(default_factory=set, repr=False)

    # Monotonic tallies.  The dedup sets are now evicted as tracks retire, so
    # their sizes can no longer serve as running totals — these can only grow
    # (until reset()) and keep ``total_down`` / ``total_up`` truthful.
    _total_down: int = field(default=0, repr=False)
    _total_up:   int = field(default=0, repr=False)

    # Monotonic per-line tallies for the on-screen overlay.
    #
    # The overlay used to render len(counted_down_per_line[lid]). Once those sets
    # began evicting retired track ids (to bound memory and allow id reuse), that
    # length started falling as traffic cleared -- the displayed count would tick
    # downward toward zero. These tallies only ever increase.
    _tally_down: dict[int, int] = field(default_factory=dict, repr=False)
    _tally_up:   dict[int, int] = field(default_factory=dict, repr=False)

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

    def update_lines(self, new_lines: list[dict]) -> bool:
        """
        Swap in a new set of counting lines *without* disturbing tracker state.

        Previously the only way to apply an edited counting line was to cancel and
        respawn the whole camera task, which reloaded the YOLO model, reopened the
        RTSP connection and discarded ``prev_centroids`` plus every dedup set. That
        last part silently double-counted: any vehicle still in frame at the moment
        of the edit had no record of having been counted, so it was counted again on
        its next crossing.

        This applies the change in place. ``prev_centroids`` and ``_frames_missing``
        are untouched, so in-flight tracks keep their identity and their crossing
        history. Dedup sets are preserved for every surviving line id -- including
        lines whose geometry moved -- and dropped only for lines the user actually
        deleted. A vehicle already counted is therefore never counted twice, whatever
        the edit.

        Returns
        -------
        bool
            True if the geometry differs from what is currently loaded.
        """
        # Compare only the fields that affect counting. Cosmetic edits (name, color)
        # must not be treated as a change worth churning state for -- the old
        # supervisor compared whole dicts, so recolouring a line restarted the video.
        def _geometry(lines: list[dict]) -> dict:
            return {
                ln["id"]: (
                    round(float(ln["x1"]), 9), round(float(ln["y1"]), 9),
                    round(float(ln["x2"]), 9), round(float(ln["y2"]), 9),
                    str(ln.get("direction", "both")),
                    int(ln.get("lane_id", 1)),
                )
                for ln in lines
            }

        if not new_lines:
            # Refuse to blank the geometry: an empty DB read (transient error, or a
            # camera mid-edit with no lines saved yet) would otherwise silently stop
            # all counting. Keep the last known-good configuration.
            logger.warning(
                "cam=%s update_lines called with no lines; keeping existing %d",
                self.camera_id, len(self.lines),
            )
            return False

        old_geom = _geometry(self.lines)
        new_geom = _geometry(new_lines)

        # Cosmetic-only edits: refresh the stored dicts so overlays repaint, but
        # report "unchanged" so callers skip any expensive reaction.
        if old_geom == new_geom:
            self.lines = list(new_lines)
            return False

        moved = {lid for lid in set(new_geom) & set(old_geom)
                 if old_geom[lid] != new_geom[lid]}

        self.lines = list(new_lines)

        # A moved line KEEPS its dedup set.
        #
        # An earlier version cleared it, reasoning that a relocated line is a new
        # measurement point. That was wrong in practice: nudging a line by one pixel
        # made every already-counted vehicle still on screen eligible again, and each
        # one had already been POSTed to /api/events -- so a trivial drag wrote
        # duplicate rows into the events table and permanently skewed reports.
        #
        # Dedup means "this vehicle has already been counted at this line". That
        # holds regardless of how far the line subsequently moves. The cost is that
        # vehicles in frame at the moment of a large move are not re-counted at the
        # new position; they retire within `retire_after_frames` and traffic arriving
        # afterwards counts normally. Missing a few counts for a second or two is
        # recoverable. Duplicate rows in the events table are not.
        for lid in list(self.counted_down_per_line):
            if lid not in new_geom:
                del self.counted_down_per_line[lid]
        for lid in list(self.counted_up_per_line):
            if lid not in new_geom:
                del self.counted_up_per_line[lid]

        for lid in new_geom:
            self.counted_down_per_line.setdefault(lid, set())
            self.counted_up_per_line.setdefault(lid, set())

        # Display tallies are dropped for deleted lines only, so an on-screen count
        # never jumps backward while its line still exists.
        for lid in list(self._tally_down):
            if lid not in new_geom:
                del self._tally_down[lid]
        for lid in list(self._tally_up):
            if lid not in new_geom:
                del self._tally_up[lid]

        logger.info(
            "cam=%s counting lines updated in place: %d line(s), %d moved, "
            "%d added, %d removed (tracker state preserved)",
            self.camera_id, len(new_lines), len(moved),
            len(set(new_geom) - set(old_geom)), len(set(old_geom) - set(new_geom)),
        )
        return True

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

        # Resolve every track's id + centroid exactly once per frame (previously
        # this was recomputed for every line, and again in a second pass).
        current: list[tuple[int, float, float, Any]] = []
        seen = self._seen_this_frame
        seen.clear()
        for track in tracks:
            resolved = self._resolve(track)
            if resolved is None:
                continue
            track_id, cx, cy = resolved
            current.append((track_id, cx, cy, track))
            seen.add(track_id)

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

            for track_id, cx, cy, track in current:
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
                            if not self._counted_anywhere(self.counted_down_per_line, track_id):
                                self._total_down += 1
                            self._tally_down[lid] = self._tally_down.get(lid, 0) + 1
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
                            if not self._counted_anywhere(self.counted_up_per_line, track_id):
                                self._total_up += 1
                            self._tally_up[lid] = self._tally_up.get(lid, 0) + 1
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
        missing = self._frames_missing
        for track_id, cx, cy, _track in current:
            self.prev_centroids[track_id] = (cx, cy)
            missing[track_id] = 0

        # Age out tracks that were not seen this frame.  Note the ordering: every
        # track present above already had its counter reset to 0, so a vehicle
        # that is still being tracked can never be retired here — the "counted
        # exactly once while tracked" guarantee is preserved.  Only after
        # ``retire_after_frames`` consecutive absences is the id considered
        # retired and all of its state dropped, which both bounds memory and lets
        # a recycled id be counted again as the new vehicle it now represents.
        retired: list[int] | None = None
        for track_id, absent in missing.items():
            if track_id in seen:
                continue
            absent += 1
            missing[track_id] = absent
            if absent >= self.retire_after_frames:
                if retired is None:
                    retired = []
                retired.append(track_id)

        if retired is not None:
            for track_id in retired:
                del missing[track_id]
                self.prev_centroids.pop(track_id, None)
                for counted in self.counted_down_per_line.values():
                    counted.discard(track_id)
                for counted in self.counted_up_per_line.values():
                    counted.discard(track_id)

        return events

    @staticmethod
    def _resolve(track: Any) -> tuple[int, float, float] | None:
        """Return ``(track_id, centroid_x, centroid_y)``, or None if unusable."""
        tid_raw = track.id
        if tid_raw is None:
            return None
        try:
            track_id = int(tid_raw.item()) if hasattr(tid_raw, "item") else int(tid_raw)
            box = track.xyxy
            if hasattr(box, "shape") and len(box.shape) == 2:
                box = box[0]
            cx = (float(box[0]) + float(box[2])) / 2.0
            cy = (float(box[1]) + float(box[3])) / 2.0
        except Exception:
            return None
        return track_id, cx, cy

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
        self._frames_missing.clear()
        self._seen_this_frame.clear()
        self._total_down = 0
        self._total_up = 0
        self._tally_down.clear()
        self._tally_up.clear()
        self.counted_down_per_line.clear()
        self.counted_up_per_line.clear()
        for line in self.lines:
            lid = line["id"]
            self.counted_down_per_line[lid] = set()
            self.counted_up_per_line[lid] = set()

    @staticmethod
    def _counted_anywhere(per_line: dict[int, set[int]], track_id: int) -> bool:
        """True if *track_id* is already in any line's dedup set."""
        for s in per_line.values():
            if track_id in s:
                return True
        return False

    def line_totals(self, line_id: int) -> tuple[int, int]:
        """
        Monotonic (down, up) crossing counts for one line, safe for on-screen display.

        These count CROSSINGS OF THIS LINE. ``total_down`` / ``total_up`` count
        DISTINCT VEHICLES across all lines. The two deliberately disagree when a
        camera has more than one line: a vehicle crossing three lines contributes
        3 to the sum of per-line tallies but 1 to the total. Do not compare them or
        expect ``sum(line_totals) == total_down``.
        """
        return self._tally_down.get(line_id, 0), self._tally_up.get(line_id, 0)

    @property
    def total_down(self) -> int:
        return self._total_down

    @property
    def total_up(self) -> int:
        return self._total_up



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
