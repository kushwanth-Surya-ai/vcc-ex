"""
seed_demo.py — Synthetic event seeder for the VCC backend.

Generates 7 days of realistic traffic events across 3 locations (4 lanes
each) and POSTs them to the backend API using the SERVICE_API_KEY.

Usage::

    # Using default settings from .env / environment
    python seed_demo.py

    # Preview events without posting
    python seed_demo.py --dry-run

    # Custom URL
    VCC_API_URL=http://my-server:8000 python seed_demo.py

Vehicle class distribution
--------------------------
car          50 %
motorcycle   25 %
bus          10 %
truck        10 %
bicycle       5 %

Hourly weight table
-------------------
Each hour has a relative weight.  Peak hours 08-09 and 17-18 carry the
highest traffic; 02-04 is near-zero.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

import config


# ---------------------------------------------------------------------------
# Seeder configuration
# ---------------------------------------------------------------------------

LOCATIONS: list[dict[str, Any]] = [
    {
        "location_id":   "loc_mg_road",
        "location_name": "MG Road Junction",
        "base_volume":   120,   # total vehicles per day (before lane/hour split)
    },
    {
        "location_id":   "loc_airport_rd",
        "location_name": "Airport Road",
        "base_volume":   90,
    },
    {
        "location_id":   "loc_city_centre",
        "location_name": "City Centre",
        "base_volume":   150,
    },
]

LANE_IDS: list[int] = [1, 2, 3, 4]

CROSSING_DIRS: list[str] = ["down", "up"]

VEHICLE_CLASSES: list[str] = ["car", "motorcycle", "bus", "truck", "bicycle"]
CLASS_WEIGHTS:   list[float] = [0.50,  0.25,        0.10,  0.10,   0.05]

# Relative traffic volume per hour (index = hour, 0-23).
HOURLY_WEIGHTS: list[float] = [
    0.10,  # 00
    0.07,  # 01
    0.05,  # 02
    0.04,  # 03
    0.04,  # 04
    0.06,  # 05
    0.15,  # 06 — morning ramp-up
    0.55,  # 07
    1.00,  # 08 — MORNING PEAK
    0.95,  # 09
    0.70,  # 10
    0.55,  # 11
    0.60,  # 12
    0.55,  # 13
    0.50,  # 14
    0.55,  # 15
    0.70,  # 16 — evening ramp-up
    1.00,  # 17 — EVENING PEAK
    0.95,  # 18
    0.75,  # 19
    0.55,  # 20
    0.40,  # 21
    0.25,  # 22
    0.15,  # 23
]

DAYS: int = 7
_TOTAL_HOURLY_WEIGHT: float = sum(HOURLY_WEIGHTS)


# ---------------------------------------------------------------------------
# Event generator
# ---------------------------------------------------------------------------

def _random_ts(day: datetime, hour: int) -> datetime:
    """Return a uniformly random second within *hour* of *day* (UTC)."""
    return day.replace(
        hour       = hour,
        minute     = random.randint(0, 59),
        second     = random.randint(0, 59),
        microsecond = 0,
        tzinfo     = timezone.utc,
    )


def generate_events() -> list[dict[str, Any]]:
    """
    Build the complete list of synthetic crossing events for ``DAYS`` days.

    Returns
    -------
    list[dict]
        Each dict is ready to be sent as JSON to ``POST /api/events``.
        The list is sorted chronologically.
    """
    start_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=DAYS - 1)

    events: list[dict[str, Any]] = []

    for day_offset in range(DAYS):
        current_day = start_day + timedelta(days=day_offset)

        for loc in LOCATIONS:
            for lane_id in LANE_IDS:
                # Lane factor: busier lanes skew slightly higher
                lane_factor = 0.70 + 0.10 * (lane_id - 1)   # 0.70 → 1.00

                for hour, hw in enumerate(HOURLY_WEIGHTS):
                    # Expected events this hour for this location+lane
                    hourly_base = loc["base_volume"] / _TOTAL_HOURLY_WEIGHT * hw
                    count = max(
                        0,
                        int(hourly_base * lane_factor + random.gauss(0, 2)),
                    )

                    for _ in range(count):
                        ts      = _random_ts(current_day, hour)
                        veh_cls = random.choices(
                            VEHICLE_CLASSES, weights=CLASS_WEIGHTS, k=1
                        )[0]
                        conf    = round(random.uniform(0.70, 0.99), 4)
                        cdir    = random.choice(CROSSING_DIRS)

                        loc_slug  = loc["location_id"].replace("loc_", "")
                        camera_id = f"cam_{loc_slug}_l{lane_id}"

                        events.append(
                            {
                                "camera_id":     camera_id,
                                "location_id":   loc["location_id"],
                                "location":      loc["location_name"],
                                "lane_id":       lane_id,
                                "vehicle_class": veh_cls,
                                "confidence":    conf,
                                "crossing_dir":  cdir,
                                "timestamp":     ts.isoformat(),
                                "track_id":      random.randint(1, 99_999),
                            }
                        )

    events.sort(key=lambda e: e["timestamp"])
    return events


# ---------------------------------------------------------------------------
# Chunked iteration
# ---------------------------------------------------------------------------

def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

def seed(
    *,
    batch_size:    int   = 50,
    delay_between: float = 0.05,
    dry_run:       bool  = False,
) -> None:
    """
    Generate events and POST them to the backend API.

    Parameters
    ----------
    batch_size : int
        Number of events posted before a progress line is printed.
    delay_between : float
        Seconds to sleep between batches (throttle).
    dry_run : bool
        If True, generate and display a sample but do not POST.
    """
    print("VCC Demo Seeder")
    print("=" * 64)
    print(f"  API URL      : {config.API_BASE_URL}")
    print(f"  Duration     : {DAYS} days")
    print(f"  Locations    : {len(LOCATIONS)}")
    print(f"  Lanes/loc    : {len(LANE_IDS)}")
    print(f"  Vehicle types: {len(VEHICLE_CLASSES)}")
    print(f"  Dry run      : {dry_run}")
    print("=" * 64)

    events = generate_events()
    total  = len(events)
    print(f"  Events generated : {total:,}")
    print()

    if dry_run:
        print("Sample events (first 5):")
        for ev in events[:5]:
            print("  ", ev)
        print(f"\nDry run complete — {total:,} events NOT posted.")
        return

    url     = f"{config.API_BASE_URL}/api/events"
    headers = {"X-API-Key": config.SERVICE_API_KEY}

    posted  = 0
    failed  = 0
    t0      = time.monotonic()

    with httpx.Client(timeout=10.0) as client:
        for batch in _chunks(events, batch_size):
            for ev in batch:
                try:
                    resp = client.post(url, json=ev, headers=headers)
                    resp.raise_for_status()
                    posted += 1
                except httpx.HTTPStatusError as exc:
                    failed += 1
                    print(
                        f"\n  [WARN] HTTP {exc.response.status_code} — "
                        f"{ev['camera_id']} @ {ev['timestamp']}: "
                        f"{exc.response.text[:80]}",
                        file=sys.stderr,
                    )
                except httpx.RequestError as exc:
                    failed += 1
                    print(f"\n  [ERROR] Network: {exc}", file=sys.stderr)

            done    = posted + failed
            elapsed = time.monotonic() - t0
            pct     = done / total * 100
            rate    = done / elapsed if elapsed > 0 else 0
            print(
                f"  {done:>7,}/{total:,}  ({pct:5.1f}%)  "
                f"posted={posted:,}  failed={failed:,}  "
                f"rate={rate:.0f} ev/s   ",
                end="\r",
                flush=True,
            )

            if delay_between > 0:
                time.sleep(delay_between)

    elapsed_total = time.monotonic() - t0
    print()  # end the \r line
    print()
    print("=" * 64)
    print(f"  Done in {elapsed_total:.1f}s")
    print(f"  Posted : {posted:,}")
    print(f"  Failed : {failed:,}")
    print("=" * 64)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VCC demo data seeder")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate events but do not POST them to the API.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Events per progress-report batch (default: 50).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        metavar="S",
        help="Seconds to sleep between batches (default: 0.05).",
    )
    args = parser.parse_args()
    seed(
        batch_size    = args.batch_size,
        delay_between = args.delay,
        dry_run       = args.dry_run,
    )
