"""
Tests for LineCounter.update_lines() — applying ROI edits without a pipeline restart.

The behaviour that matters operationally: editing a counting line must not cause
already-counted vehicles to be counted a second time, and must not cause vehicles
to lose their tracking identity.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from counter import LineCounter  # noqa: E402


class FakeTrack:
    """Minimal stand-in for an ultralytics track row."""

    def __init__(self, tid, cx, cy, cls=2, conf=0.9):
        self.id = tid
        self._c = (cx, cy)
        self.cls = cls
        self.conf = conf

    @property
    def xyxy(self):
        cx, cy = self._c
        return [cx - 10, cy - 10, cx + 10, cy + 10]


def _line(lid=1, y=0.5, direction="both", color="#00d4ff", name="L1"):
    return {
        "id": lid, "name": name,
        "x1": 0.0, "y1": y, "x2": 1.0, "y2": y,
        "lane_id": 1, "direction": direction, "color": color,
    }


def _counter(lines):
    return LineCounter(camera_id="cam_test", lines=list(lines))


H, W = 1000, 1000


def _cross(counter, tid, from_y, to_y):
    """Move a track from one side of the frame to the other, returning events."""
    counter.process_tracks([FakeTrack(tid, 500, from_y)], H, W)
    return counter.process_tracks([FakeTrack(tid, 500, to_y)], H, W)


def test_cosmetic_edit_reports_no_change_and_keeps_counts():
    """Recolouring or renaming must not churn state — that restarted the video before."""
    c = _counter([_line(color="#00d4ff", name="L1")])
    assert len(_cross(c, 1, 400, 600)) == 1
    assert 1 in c.counted_down_per_line[1]

    changed = c.update_lines([_line(color="#ff0000", name="Renamed")])

    assert changed is False, "cosmetic-only edit must not report a geometry change"
    assert 1 in c.counted_down_per_line[1], "dedup state must survive a cosmetic edit"
    assert c.lines[0]["color"] == "#ff0000", "new colour should still be stored"
    assert c.lines[0]["name"] == "Renamed"


def test_vehicle_in_frame_is_not_recounted_after_unrelated_line_edit():
    """
    The double-count regression: restarting the pipeline wiped the dedup sets, so a
    vehicle still on screen got counted again. Editing line 2 must not re-count a
    vehicle already counted on line 1.
    """
    c = _counter([_line(1, y=0.5), _line(2, y=0.8)])
    assert len(_cross(c, 7, 400, 600)) == 1          # crosses line 1 only
    assert 7 in c.counted_down_per_line[1]

    c.update_lines([_line(1, y=0.5), _line(2, y=0.85)])   # move only line 2

    assert 7 in c.counted_down_per_line[1], "untouched line must keep its dedup set"
    # Same vehicle continues past the old position; must not be counted again on line 1.
    events = c.process_tracks([FakeTrack(7, 500, 700)], H, W)
    assert not [e for e in events if e.lane_id == 1 and e.track_id == 7], \
        "vehicle already counted on line 1 was counted a second time"


def test_moved_line_does_not_recount_an_already_counted_vehicle():
    """
    Nudging a line must never produce a duplicate event.

    An earlier implementation cleared the dedup set whenever geometry changed, so a
    one-pixel drag made every already-counted vehicle on screen eligible again --
    and each had already been POSTed to /api/events, writing duplicate rows.
    Dedup means "already counted at this line" and survives the line moving.
    """
    c = _counter([_line(1, y=0.5)])
    assert len(_cross(c, 3, 400, 600)) == 1
    assert 3 in c.counted_down_per_line[1]

    c.update_lines([_line(1, y=0.51)])                # barely nudge the line

    assert 3 in c.counted_down_per_line[1], "moved line must retain its dedup set"
    events = _cross(c, 3, 850, 950)
    assert not events, "a nudge re-counted a vehicle that was already counted"


def test_new_vehicles_still_count_at_a_moved_line():
    """The flip side: relocating a line must not disable it for fresh traffic."""
    c = _counter([_line(1, y=0.5)])
    _cross(c, 4, 400, 600)
    c.update_lines([_line(1, y=0.9)])

    events = _cross(c, 99, 850, 950)                  # a vehicle never seen before
    assert len(events) == 1, "moved line stopped counting new vehicles"


def test_tracking_identity_survives_reload():
    """prev_centroids must not be reset, or crossings straddling the edit are lost."""
    c = _counter([_line(1, y=0.5)])
    c.process_tracks([FakeTrack(11, 500, 400)], H, W)   # seed previous position
    assert 11 in c.prev_centroids

    c.update_lines([_line(1, y=0.5), _line(2, y=0.2)])  # add a second line

    assert 11 in c.prev_centroids, "tracker state was discarded by a line edit"
    # The crossing that began before the edit still resolves after it.
    events = c.process_tracks([FakeTrack(11, 500, 600)], H, W)
    assert len(events) == 1, "crossing straddling a config edit was lost"


def test_deleted_line_drops_its_state_only():
    c = _counter([_line(1, y=0.5), _line(2, y=0.8)])
    _cross(c, 5, 400, 600)
    _cross(c, 6, 700, 900)
    assert 5 in c.counted_down_per_line[1]
    assert 6 in c.counted_down_per_line[2]

    c.update_lines([_line(1, y=0.5)])                  # delete line 2

    assert 2 not in c.counted_down_per_line, "deleted line left state behind"
    assert 5 in c.counted_down_per_line[1], "surviving line lost its state"


def test_empty_update_is_refused():
    """A transient empty DB read must not silently disable counting."""
    c = _counter([_line(1, y=0.5)])
    changed = c.update_lines([])
    assert changed is False
    assert len(c.lines) == 1, "existing geometry must be retained"


def test_new_line_gets_initialised_state():
    c = _counter([_line(1, y=0.5)])
    c.update_lines([_line(1, y=0.5), _line(9, y=0.3)])
    assert c.counted_down_per_line[9] == set()
    assert c.counted_up_per_line[9] == set()
    events = _cross(c, 21, 200, 400)
    assert len(events) == 1, "newly added line should count immediately"


def test_totals_are_not_rewritten_by_an_edit():
    """Session tallies represent history; deleting a line must not rewrite it."""
    c = _counter([_line(1, y=0.5)])
    _cross(c, 31, 400, 600)
    before = c.total_down
    assert before == 1
    c.update_lines([_line(1, y=0.9)])
    assert c.total_down == before, "historical total changed on a config edit"


def test_display_tallies_never_decrease_as_tracks_retire():
    """
    Regression guard: dedup sets evict retired ids to bound memory, so their length
    falls as traffic clears. The on-screen overlay must not follow it downward.
    """
    c = _counter([_line(1, y=0.5)])
    c.retire_after_frames = 2

    for tid in (101, 102, 103):
        _cross(c, tid, 400, 600)

    down, up = c.line_totals(1)
    assert down == 3, f"expected 3 counted, got {down}"

    # All vehicles leave the scene; retirement kicks in.
    for _ in range(10):
        c.process_tracks([], H, W)

    assert len(c.counted_down_per_line[1]) < 3, "eviction did not occur; test is vacuous"
    assert c.line_totals(1)[0] == 3, "displayed total regressed as tracks retired"


def test_deleted_line_tally_is_dropped():
    c = _counter([_line(1, y=0.5), _line(2, y=0.8)])
    _cross(c, 201, 400, 600)
    assert c.line_totals(1)[0] == 1
    c.update_lines([_line(2, y=0.8)])
    assert c.line_totals(1) == (0, 0), "deleted line kept a stale tally"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
