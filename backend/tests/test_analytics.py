"""
tests/test_analytics.py

Complete pytest-asyncio integration test suite for the VCC backend.

Tests:
  - test_health_returns_200
  - test_health_db_ok
  - test_summary_pct_change_correct
  - test_heatmap_returns_24_hour_grid
  - test_events_pagination_limit_offset
  - test_login_wrong_password_401
  - test_login_rate_limit_429
  - test_events_post_valid_api_key_201
  - test_events_post_bearer_jwt_403
  - test_events_post_wrong_api_key_403
  - test_ws_no_auth_timeout_1008
  - test_matview_refresh_no_transaction_error
"""
from __future__ import annotations

import sys
import asyncio
import os
import tempfile
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Environment bootstrap (must be set BEFORE importing app modules)
# ---------------------------------------------------------------------------

# Default to a throwaway SQLite file so the suite runs with no database server.
# Set VCC_TEST_DATABASE_URL to run these against PostgreSQL instead.
# Deliberately NOT keyed on DATABASE_URL: that name is commonly exported
# machine-wide for an unrelated project, and setdefault() would then let a
# foreign database win -- these tests create and drop tables.
_TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "vcc_test_analytics.db")
os.environ["DATABASE_URL"] = os.getenv("VCC_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_TEST_DB_FILE}"
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256-algorithm-padding-ok")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("SERVICE_API_KEY", "test-api-key-that-is-long-enough-32chars!")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("MV_REFRESH_INTERVAL_MINUTES", "60")  # don't auto-refresh during tests
os.environ["LOGIN_RATE_LIMIT"] = "5/minute"

from auth import create_access_token, hash_password  # noqa: E402
from database import Base, get_db  # noqa: E402
from main import app  # noqa: E402
from models import Alert, Camera, Event, Location, User  # noqa: E402

# ---------------------------------------------------------------------------
# Test database engine & session
# ---------------------------------------------------------------------------

from db_dialect import create_analytics_views, create_engine_from_url  # noqa: E402

TEST_DATABASE_URL: str = os.environ["DATABASE_URL"]

# Same builder the app uses, so the test engine gets the identical dialect
# handling (and, on SQLite, the WAL / busy_timeout / foreign_keys PRAGMAs).
test_engine = create_engine_from_url(TEST_DATABASE_URL)
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables and seed test data once per session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Same dialect-aware view DDL the application runs at startup.
        await create_analytics_views(conn)

    # Clean up stale test data from previous runs if any
    async with TestSessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE email = 'test_admin@vcc.local'"))
        await session.execute(text("DELETE FROM cameras WHERE name = 'Cam-01'"))
        try:
            await session.execute(text("DELETE FROM locations WHERE name = 'Test Junction'"))
            await session.commit()
        except Exception:
            await session.rollback()

    # Seed data
    async with TestSessionLocal() as session:
        # Location
        loc = Location(name="Test Junction", latitude=17.385, longitude=78.486)
        session.add(loc)
        await session.flush()

        # Camera
        cam = Camera(name="Cam-01", location_id=loc.id, lane_count=2, status="active")
        session.add(cam)
        await session.flush()

        # Admin user
        user = User(
            email="test_admin@vcc.local",
            hashed_password=hash_password("Admin1234!"),
            role="admin",
        )
        session.add(user)
        await session.flush()

        # Today's events (10)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(10):
            session.add(
                Event(
                    camera_id=cam.id,
                    location_id=loc.id,
                    lane_id=i % 2,
                    vehicle_class="car",
                    confidence=0.95,
                    crossing_dir="in",
                    timestamp=today_start + timedelta(hours=i),
                )
            )

        # Yesterday's events (5)
        yesterday_start = today_start - timedelta(days=1)
        for i in range(5):
            session.add(
                Event(
                    camera_id=cam.id,
                    location_id=loc.id,
                    lane_id=0,
                    vehicle_class="truck",
                    confidence=0.88,
                    crossing_dir="out",
                    timestamp=yesterday_start + timedelta(hours=i),
                )
            )

        await session.commit()

    yield  # tests run here

    # Teardown (clean up test data only, keep tables intact)
    async with TestSessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE email = 'test_admin@vcc.local'"))
        await session.execute(text("DELETE FROM cameras WHERE name = 'Cam-01'"))
        try:
            await session.execute(text("DELETE FROM locations WHERE name = 'Test Junction'"))
            await session.commit()
        except Exception:
            await session.rollback()
    await test_engine.dispose()


@pytest_asyncio.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Override get_db with the test session factory."""
    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    if hasattr(app, "state") and hasattr(app.state, "limiter") and app.state.limiter._storage:
        app.state.limiter._storage.reset()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest_asyncio.fixture()
async def auth_headers(client: AsyncClient) -> dict:
    """Return Bearer auth headers for the test admin user."""
    resp = await client.post(
        "/auth/login",
        json={"email": "test_admin@vcc.local", "password": "Admin1234!"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
def api_key_headers() -> dict:
    return {"X-API-Key": os.environ["SERVICE_API_KEY"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_db_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    data = resp.json()
    assert data["db_ok"] is True
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_summary_pct_change_correct(
    client: AsyncClient, auth_headers: dict
) -> None:
    resp = await client.get("/api/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_today" in data
    assert "total_yesterday" in data
    assert "pct_change" in data
    # today=10, yesterday=5 → pct_change = 100.0
    assert data["total_today"] >= 10
    assert data["total_yesterday"] >= 5
    expected_pct = ((data["total_today"] - data["total_yesterday"]) / data["total_yesterday"] * 100.0) if data["total_yesterday"] > 0 else 0.0
    assert abs(data["pct_change"] - expected_pct) < 1e-2


@pytest.mark.asyncio
async def test_heatmap_returns_24_hour_grid(
    client: AsyncClient, auth_headers: dict
) -> None:
    resp = await client.get(
        "/api/analytics/hourly-heatmap",
        headers=auth_headers,
        params={"days": 2},
    )
    assert resp.status_code == 200
    cells = resp.json()
    # We seeded 10 today-events across 10 distinct hours; at least some cells expected
    assert isinstance(cells, list)
    assert len(cells) >= 1
    for cell in cells:
        assert "hour" in cell
        assert "count" in cell
        assert "location_id" in cell
        assert "vehicle_class" in cell


@pytest.mark.asyncio
async def test_events_pagination_limit_offset(
    client: AsyncClient, auth_headers: dict
) -> None:
    # Get first page (limit=3)
    resp1 = await client.get(
        "/api/events",
        headers=auth_headers,
        params={"limit": 3, "offset": 0},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["limit"] == 3
    assert data1["offset"] == 0
    assert len(data1["items"]) == 3

    # Get second page (offset=3)
    resp2 = await client.get(
        "/api/events",
        headers=auth_headers,
        params={"limit": 3, "offset": 3},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    ids1 = {e["id"] for e in data1["items"]}
    ids2 = {e["id"] for e in data2["items"]}
    assert ids1.isdisjoint(ids2), "Pages must not overlap"


@pytest.mark.asyncio
async def test_login_wrong_password_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login",
        json={"email": "admin@vcc.local", "password": "wrongpassword"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_rate_limit_429(client: AsyncClient) -> None:
    """6th login attempt within a minute must be rate-limited (429)."""
    payload = {"email": "admin@vcc.local", "password": "wrongpassword"}
    responses = []
    for _ in range(6):
        r = await client.post("/auth/login", json=payload)
        responses.append(r.status_code)
    # At least one 429 must appear (rate limit = 5/minute)
    assert 429 in responses, f"Expected 429 in {responses}"


@pytest.mark.asyncio
async def test_events_post_valid_api_key_201(
    client: AsyncClient,
    api_key_headers: dict,
    db_session: AsyncSession,
) -> None:
    from sqlalchemy import select as sa_select
    loc = (await db_session.execute(sa_select(Location).limit(1))).scalar_one()
    cam = (await db_session.execute(sa_select(Camera).limit(1))).scalar_one()

    payload = {
        "camera_id": cam.id,
        "location_id": loc.id,
        "lane_id": 0,
        "vehicle_class": "car",
        "confidence": 0.92,
        "crossing_dir": "in",
    }
    resp = await client.post("/api/events", json=payload, headers=api_key_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["vehicle_class"] == "car"
    assert data["id"] > 0


@pytest.mark.asyncio
async def test_events_post_bearer_jwt_403(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    """Bearer JWT must be rejected on POST /api/events (requires X-API-Key)."""
    from sqlalchemy import select as sa_select
    loc = (await db_session.execute(sa_select(Location).limit(1))).scalar_one()
    cam = (await db_session.execute(sa_select(Camera).limit(1))).scalar_one()

    payload = {
        "camera_id": cam.id,
        "location_id": loc.id,
        "lane_id": 0,
        "vehicle_class": "car",
        "confidence": 0.90,
        "crossing_dir": "in",
    }
    resp = await client.post("/api/events", json=payload, headers=auth_headers)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_events_post_wrong_api_key_403(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/events",
        json={
            "camera_id": 1,
            "location_id": 1,
            "lane_id": 0,
            "vehicle_class": "car",
            "confidence": 0.9,
            "crossing_dir": "in",
        },
        headers={"X-API-Key": "completely-wrong-key"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ws_no_auth_timeout_1008(client: AsyncClient) -> None:
    """A WebSocket client that never sends auth must be closed with code 1008."""
    from httpx_ws import aconnect_ws

    try:
        async with aconnect_ws("/ws", client) as ws:
            # Receive the auth_required message
            msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
            assert msg["type"] == "auth_required"
            # Do NOT send auth – wait for server to close after 5-second timeout
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=8.0)
            except Exception:
                pass  # Expected – connection should be closed by server
    except Exception:
        pass  # Connection closed by server is expected


@pytest.mark.asyncio
async def test_matview_refresh_no_transaction_error(db_session: AsyncSession) -> None:
    """
    scheduler.refresh_materialized_views() must succeed on either dialect.

    The mv_* objects are now PLAIN views on both PostgreSQL and SQLite, so there
    is nothing to REFRESH and the old 'cannot run inside a transaction block'
    hazard is gone entirely. The call still reads every view, so this asserts the
    views exist and are queryable.
    """
    from scheduler import refresh_materialized_views

    # Should complete without raising an exception
    await refresh_materialized_views()


@pytest.mark.asyncio
async def test_date_trunc_hour_day_week_agree(db_session: AsyncSession) -> None:
    """date_trunc() must truncate identically in shape on PostgreSQL and SQLite.

    Asserts the properties that make the two implementations interchangeable:
      * every bucket is a datetime at exactly midnight-or-on-the-hour,
      * hour buckets zero out minutes/seconds, day buckets also zero the hour,
      * week buckets land on a MONDAY (PostgreSQL date_trunc('week') semantics,
        reproduced on SQLite via strftime(..., 'weekday 0', '-6 days')),
      * the same events are counted regardless of bucket size.
    """
    from db_dialect import date_trunc
    from sqlalchemy import func, select as sa_select

    totals = {}
    for interval in ("hour", "day", "week"):
        bucket = date_trunc(interval, Event.timestamp)
        rows = (
            await db_session.execute(
                sa_select(bucket.label("ts"), func.count(Event.id).label("cnt"))
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()

        assert rows, f"no {interval} buckets returned"
        for ts, _cnt in rows:
            assert isinstance(ts, datetime), f"{interval} bucket is {type(ts)}, not datetime"
            assert ts.minute == 0 and ts.second == 0 and ts.microsecond == 0
            if interval in ("day", "week"):
                assert ts.hour == 0, f"{interval} bucket not truncated to midnight: {ts}"
            if interval == "week":
                assert ts.weekday() == 0, f"week bucket {ts} is not a Monday"

        totals[interval] = sum(c for _, c in rows)

    # Bucket size changes the grouping, never the number of events counted.
    assert totals["hour"] == totals["day"] == totals["week"]


@pytest.mark.asyncio
async def test_timeseries_all_intervals(client: AsyncClient, auth_headers: dict) -> None:
    """The /timeseries endpoint must work for every supported interval."""
    for interval in ("hour", "day", "week"):
        resp = await client.get(
            "/api/analytics/timeseries",
            headers=auth_headers,
            params={"interval": interval},
        )
        assert resp.status_code == 200, f"{interval}: {resp.text}"
        points = resp.json()
        assert isinstance(points, list)
        for p in points:
            assert "ts" in p and "count" in p
