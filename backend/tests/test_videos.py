"""
tests/test_videos.py - Video upload -> camera -> report lifecycle.

The feature under test has one load-bearing idea: an uploaded video becomes a
Camera row whose rtsp_url is the absolute path to the stored file, so the
existing detection supervisor picks it up on its normal poll. Most of these
tests therefore assert on the *camera row and its counting line*, not on some
separate video entity - if those are wrong, the detection pipeline silently
never processes the upload.

VIDEO_DIR is redirected to a tmp directory for the whole module so the suite
never writes into the repo's real uploads/ tree.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Environment bootstrap (must be set BEFORE importing app modules)
# ---------------------------------------------------------------------------

_TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "vcc_test_videos.db")
os.environ["DATABASE_URL"] = (
    os.getenv("VCC_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_TEST_DB_FILE}"
)
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256-algorithm-padding-ok")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
os.environ.setdefault("SERVICE_API_KEY", "test-api-key-that-is-long-enough-32chars!")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")

from auth import hash_password  # noqa: E402
from database import Base, get_db  # noqa: E402
from db_dialect import (  # noqa: E402
    apply_camera_upgrades,
    create_analytics_views,
    create_engine_from_url,
)
from main import app  # noqa: E402
from models import Camera, CountingLine, Event, Location, User  # noqa: E402
from routers import videos as videos_router  # noqa: E402

TEST_DATABASE_URL: str = os.environ["DATABASE_URL"]

test_engine = create_engine_from_url(TEST_DATABASE_URL)
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)

LOC_NAME = "Video Test Junction"
USER_EMAIL = "video_admin@vcc.local"

# A few hundred KB of the real demo clip if it is available, otherwise arbitrary
# bytes. The endpoint deliberately does not decode the file (cv2 is not
# installed in the backend environment), so the content only has to be non-empty
# - but using real MP4 bytes keeps the test honest about size handling.
_SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "demo_traffic.mp4"
VIDEO_BYTES = _SAMPLE.read_bytes()[:200_000] if _SAMPLE.is_file() else os.urandom(200_000)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def redirect_upload_dir() -> AsyncGenerator[Path, None]:
    """Keep test uploads out of the repo's real uploads/ directory."""
    with tempfile.TemporaryDirectory(prefix="vcc-test-uploads-") as tmp:
        original = videos_router.VIDEO_DIR
        videos_router.VIDEO_DIR = Path(tmp) / "videos"
        yield videos_router.VIDEO_DIR
        videos_router.VIDEO_DIR = original


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # The test database file is reused across runs and create_all() will not
        # add columns to a table it already found; bring it up to date the same
        # way the application does at startup.
        await apply_camera_upgrades(conn)
        await create_analytics_views(conn)

    async with TestSessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE email = :e"), {"e": USER_EMAIL})
        await session.execute(
            text("DELETE FROM cameras WHERE location_id IN "
                 "(SELECT id FROM locations WHERE name = :n)"),
            {"n": LOC_NAME},
        )
        await session.execute(text("DELETE FROM locations WHERE name = :n"), {"n": LOC_NAME})
        await session.commit()

    async with TestSessionLocal() as session:
        session.add(Location(name=LOC_NAME, latitude=12.97, longitude=77.59))
        session.add(
            User(email=USER_EMAIL, hashed_password=hash_password("Admin1234!"), role="admin")
        )
        await session.commit()

    yield

    async with TestSessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE email = :e"), {"e": USER_EMAIL})
        await session.execute(
            text("DELETE FROM cameras WHERE location_id IN "
                 "(SELECT id FROM locations WHERE name = :n)"),
            {"n": LOC_NAME},
        )
        await session.execute(text("DELETE FROM locations WHERE name = :n"), {"n": LOC_NAME})
        await session.commit()
    await test_engine.dispose()


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    if getattr(getattr(app.state, "limiter", None), "_storage", None):
        app.state.limiter._storage.reset()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def auth_headers(client: AsyncClient) -> dict:
    resp = await client.post(
        "/auth/login", json={"email": USER_EMAIL, "password": "Admin1234!"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def api_key_headers() -> dict:
    return {"X-API-Key": os.environ["SERVICE_API_KEY"]}


async def _upload(
    client: AsyncClient,
    auth_headers: dict,
    filename: str = "traffic.mp4",
    content: bytes | None = None,
    name: str | None = None,
):
    data = {"name": name} if name else None
    return await client.post(
        "/api/videos/upload",
        headers=auth_headers,
        files={"file": (filename, VIDEO_BYTES if content is None else content, "video/mp4")},
        data=data,
    )


@pytest_asyncio.fixture()
async def uploaded(client: AsyncClient, auth_headers: dict):
    """An uploaded video, cleaned up afterwards."""
    resp = await _upload(client, auth_headers, name="Fixture Clip")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    yield body
    await client.delete(f"/api/videos/{body['id']}", headers=auth_headers)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_creates_camera_row_pointing_at_the_file(uploaded, client, auth_headers):
    """The integration contract: rtsp_url is the absolute path to the stored file."""
    stored = Path(uploaded["rtsp_url"])
    assert stored.is_absolute()
    assert stored.is_file()
    assert stored.read_bytes() == VIDEO_BYTES

    resp = await client.get(f"/api/cameras/{uploaded['id']}", headers=auth_headers)
    assert resp.status_code == 200
    cam = resp.json()
    assert cam["rtsp_url"] == str(stored)
    assert cam["source_type"] == "upload"
    assert cam["status"] == "active"


@pytest.mark.asyncio
async def test_upload_records_metadata(uploaded):
    assert uploaded["source_type"] == "upload"
    assert uploaded["processing_status"] == "pending"
    assert uploaded["video_filename"] == "traffic.mp4"
    assert uploaded["video_size_bytes"] == len(VIDEO_BYTES)
    assert uploaded["uploaded_at"] is not None
    assert uploaded["processed_at"] is None
    assert uploaded["name"] == "Fixture Clip"


@pytest.mark.asyncio
async def test_upload_without_name_uses_original_filename(client, auth_headers):
    resp = await _upload(client, auth_headers, filename="rush_hour.mov")
    assert resp.status_code == 201
    assert resp.json()["name"] == "rush_hour.mov"
    await client.delete(f"/api/videos/{resp.json()['id']}", headers=auth_headers)


@pytest.mark.asyncio
async def test_upload_stored_filename_is_collision_proof(client, auth_headers):
    """Two uploads of the same filename must not overwrite each other."""
    a = await _upload(client, auth_headers, filename="same.mp4")
    b = await _upload(client, auth_headers, filename="same.mp4")
    assert a.status_code == b.status_code == 201
    pa, pb = Path(a.json()["rtsp_url"]), Path(b.json()["rtsp_url"])
    assert pa != pb
    assert pa.is_file() and pb.is_file()
    # The display name is still the original for both.
    assert a.json()["video_filename"] == b.json()["video_filename"] == "same.mp4"
    for r in (a, b):
        await client.delete(f"/api/videos/{r.json()['id']}", headers=auth_headers)


@pytest.mark.asyncio
async def test_upload_creates_default_counting_line(uploaded):
    """Without this the video streams but counts nothing, and the report is empty."""
    async with TestSessionLocal() as session:
        lines = (
            await session.execute(
                select(CountingLine).where(CountingLine.camera_id == uploaded["id"])
            )
        ).scalars().all()

    assert len(lines) == 1
    line = lines[0]
    assert (line.x1, line.y1, line.x2, line.y2) == (0.0, 0.5, 1.0, 0.5)
    assert line.lane_id == 1
    assert line.direction == "both"
    assert line.color == "#00d4ff"
    assert line.name == "Main Line"


@pytest.mark.parametrize("ext", [".mp4", ".avi", ".mov", ".mkv", ".webm", ".MP4", ".MoV"])
@pytest.mark.asyncio
async def test_upload_accepts_allowed_extensions_case_insensitively(client, auth_headers, ext):
    resp = await _upload(client, auth_headers, filename=f"clip{ext}")
    assert resp.status_code == 201, resp.text
    await client.delete(f"/api/videos/{resp.json()['id']}", headers=auth_headers)


@pytest.mark.parametrize("filename", ["notes.txt", "archive.zip", "clip.mp4.exe", "noext"])
@pytest.mark.asyncio
async def test_upload_rejects_disallowed_extensions_400(client, auth_headers, filename):
    resp = await _upload(client, auth_headers, filename=filename, content=b"x" * 100)
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_empty_file_400(client, auth_headers):
    resp = await _upload(client, auth_headers, filename="empty.mp4", content=b"")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejects_oversize_413(client, auth_headers, monkeypatch):
    monkeypatch.setenv("VCC_MAX_UPLOAD_MB", "1")
    before = set(videos_router.VIDEO_DIR.glob("*")) if videos_router.VIDEO_DIR.exists() else set()

    resp = await _upload(client, auth_headers, filename="big.mp4", content=b"\0" * (3 * 1024 * 1024))
    assert resp.status_code == 413

    after = set(videos_router.VIDEO_DIR.glob("*")) if videos_router.VIDEO_DIR.exists() else set()
    assert before == after, "a rejected oversize upload must not leave a partial file behind"


@pytest.mark.asyncio
async def test_upload_requires_auth(client):
    resp = await client.post(
        "/api/videos/upload", files={"file": ("clip.mp4", b"abc", "video/mp4")}
    )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_videos_returns_uploads_newest_first(client, auth_headers):
    first = await _upload(client, auth_headers, filename="one.mp4")
    second = await _upload(client, auth_headers, filename="two.mp4")

    resp = await client.get("/api/videos", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    ids = [v["id"] for v in items]
    assert ids == sorted(ids, reverse=True)
    assert ids.index(second.json()["id"]) < ids.index(first.json()["id"])
    assert all(v["source_type"] == "upload" for v in items)
    for key in ("video_filename", "video_size_bytes", "processing_status",
                "processed_at", "uploaded_at", "event_count"):
        assert key in items[0]

    for r in (first, second):
        await client.delete(f"/api/videos/{r.json()['id']}", headers=auth_headers)


@pytest.mark.asyncio
async def test_list_videos_excludes_live_cameras(client, auth_headers, uploaded):
    async with TestSessionLocal() as session:
        loc_id = (await session.execute(select(Location.id).where(Location.name == LOC_NAME))).scalar_one()
        cam = Camera(name="A Live Camera", location_id=loc_id, rtsp_url="rtsp://cam/live")
        session.add(cam)
        await session.commit()
        live_id = cam.id

    resp = await client.get("/api/videos", headers=auth_headers)
    ids = [v["id"] for v in resp.json()]
    assert uploaded["id"] in ids
    assert live_id not in ids


@pytest.mark.asyncio
async def test_list_videos_reports_event_count(client, auth_headers, uploaded):
    await _seed_events(uploaded, [("car", "down", 0), ("bus", "up", 5)])
    resp = await client.get("/api/videos", headers=auth_headers)
    row = next(v for v in resp.json() if v["id"] == uploaded["id"])
    assert row["event_count"] == 2


# ---------------------------------------------------------------------------
# Cameras source_type filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cameras_source_type_filter(client, auth_headers, uploaded):
    uploads = await client.get("/api/cameras?source_type=upload", headers=auth_headers)
    assert uploaded["id"] in [c["id"] for c in uploads.json()["items"]]

    live = await client.get("/api/cameras?source_type=live", headers=auth_headers)
    assert uploaded["id"] not in [c["id"] for c in live.json()["items"]]
    assert all(c["source_type"] == "live" for c in live.json()["items"])


@pytest.mark.asyncio
async def test_cameras_without_filter_is_unchanged(client, auth_headers, uploaded):
    """The existing response shape and default behaviour must not regress."""
    resp = await client.get("/api/cameras", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert {"total", "limit", "offset", "items"} <= set(body)
    assert uploaded["id"] in [c["id"] for c in body["items"]]


@pytest.mark.asyncio
async def test_cameras_rejects_unknown_source_type(client, auth_headers):
    resp = await client.get("/api/cameras?source_type=bogus", headers=auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


async def _seed_events(uploaded: dict, specs: list[tuple[str, str, int]]) -> datetime:
    """Insert events directly; specs are (vehicle_class, crossing_dir, +seconds)."""
    base = datetime.now(timezone.utc).replace(microsecond=0)
    async with TestSessionLocal() as session:
        loc_id = (
            await session.execute(select(Location.id).where(Location.name == LOC_NAME))
        ).scalar_one()
        for vehicle_class, direction, offset in specs:
            session.add(
                Event(
                    camera_id=uploaded["id"],
                    location_id=loc_id,
                    lane_id=1,
                    vehicle_class=vehicle_class,
                    confidence=0.9,
                    crossing_dir=direction,
                    timestamp=base + timedelta(seconds=offset),
                )
            )
        await session.commit()
    return base


@pytest.mark.asyncio
async def test_report_aggregates_by_class_and_direction(client, auth_headers, uploaded):
    await _seed_events(
        uploaded,
        [("car", "down", 0), ("car", "down", 1), ("car", "up", 2),
         ("truck", "up", 3), ("bus", "down", 4)],
    )

    resp = await client.get(f"/api/videos/{uploaded['id']}/report", headers=auth_headers)
    assert resp.status_code == 200
    report = resp.json()

    assert report["total_vehicles"] == 5
    assert report["by_class"] == {"car": 3, "truck": 1, "bus": 1}
    assert report["by_direction"] == {"down": 3, "up": 2}
    assert report["first_event_at"] is not None
    assert report["last_event_at"] >= report["first_event_at"]
    assert report["processing_status"] == "pending"


@pytest.mark.asyncio
async def test_report_direction_keys_always_present(client, auth_headers, uploaded):
    """A client rendering a down/up comparison should not have to null-check."""
    await _seed_events(uploaded, [("car", "down", 0)])
    resp = await client.get(f"/api/videos/{uploaded['id']}/report", headers=auth_headers)
    assert resp.json()["by_direction"] == {"down": 1, "up": 0}


@pytest.mark.asyncio
async def test_report_timeline_buckets_are_dialect_aware(client, auth_headers, uploaded):
    """date_trunc() comes from db_dialect - raw date_trunc would be a SQLite syntax error."""
    await _seed_events(
        uploaded,
        [("car", "down", 0), ("car", "down", 1),      # minute 0
         ("car", "down", 65),                          # minute 1
         ("car", "down", 130), ("bus", "up", 131)],    # minute 2
    )

    resp = await client.get(
        f"/api/videos/{uploaded['id']}/report?interval=minute", headers=auth_headers
    )
    timeline = resp.json()["timeline"]
    assert [p["count"] for p in timeline] == [2, 1, 2]
    assert [p["ts"] for p in timeline] == sorted(p["ts"] for p in timeline)

    hourly = await client.get(
        f"/api/videos/{uploaded['id']}/report?interval=hour", headers=auth_headers
    )
    assert sum(p["count"] for p in hourly.json()["timeline"]) == 5
    assert len(hourly.json()["timeline"]) <= len(timeline)


@pytest.mark.asyncio
async def test_report_empty_video_is_all_zeroes(client, auth_headers, uploaded):
    resp = await client.get(f"/api/videos/{uploaded['id']}/report", headers=auth_headers)
    report = resp.json()
    assert report["total_vehicles"] == 0
    assert report["by_class"] == {}
    assert report["timeline"] == []
    assert report["first_event_at"] is None


@pytest.mark.asyncio
async def test_report_rejects_injected_interval(client, auth_headers, uploaded):
    """The unit is inlined into SQL by db_dialect, so it must be whitelisted."""
    resp = await client.get(
        f"/api/videos/{uploaded['id']}/report?interval=hour'); DROP TABLE events;--",
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_report_404_for_live_camera(client, auth_headers):
    async with TestSessionLocal() as session:
        loc_id = (await session.execute(select(Location.id).where(Location.name == LOC_NAME))).scalar_one()
        cam = Camera(name="Live Only", location_id=loc_id, rtsp_url="rtsp://cam/2")
        session.add(cam)
        await session.commit()
        live_id = cam.id

    resp = await client.get(f"/api/videos/{live_id}/report", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_report_404_for_missing_camera(client, auth_headers):
    resp = await client.get("/api/videos/99999999/report", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /complete (service auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_sets_status_and_timestamp(client, api_key_headers, uploaded):
    resp = await client.post(
        f"/api/videos/{uploaded['id']}/complete",
        headers=api_key_headers,
        json={"status": "completed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["processing_status"] == "completed"
    assert body["processed_at"] is not None


@pytest.mark.asyncio
async def test_complete_is_idempotent(client, api_key_headers, uploaded):
    payload = {"status": "completed"}
    first = await client.post(
        f"/api/videos/{uploaded['id']}/complete", headers=api_key_headers, json=payload
    )
    second = await client.post(
        f"/api/videos/{uploaded['id']}/complete", headers=api_key_headers, json=payload
    )
    assert first.status_code == second.status_code == 200
    assert second.json()["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_complete_failed_deactivates_the_camera(client, api_key_headers, uploaded):
    """A file the decoder cannot open must stop being retried and stop looking healthy."""
    resp = await client.post(
        f"/api/videos/{uploaded['id']}/complete",
        headers=api_key_headers,
        json={"status": "failed", "detail": "could not open video stream"},
    )
    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "failed"
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_complete_rejects_bearer_token(client, auth_headers, uploaded):
    """This endpoint is service-authenticated; a user JWT is not a substitute."""
    resp = await client.post(
        f"/api/videos/{uploaded['id']}/complete", headers=auth_headers, json={"status": "completed"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_complete_rejects_missing_and_wrong_api_key(client, uploaded):
    no_key = await client.post(
        f"/api/videos/{uploaded['id']}/complete", json={"status": "completed"}
    )
    assert no_key.status_code == 403

    bad_key = await client.post(
        f"/api/videos/{uploaded['id']}/complete",
        headers={"X-API-Key": "not-the-key"},
        json={"status": "completed"},
    )
    assert bad_key.status_code == 403


@pytest.mark.asyncio
async def test_complete_rejects_invalid_status(client, api_key_headers, uploaded):
    resp = await client.post(
        f"/api/videos/{uploaded['id']}/complete",
        headers=api_key_headers,
        json={"status": "halfway"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_complete_404_for_live_camera(client, api_key_headers):
    async with TestSessionLocal() as session:
        loc_id = (await session.execute(select(Location.id).where(Location.name == LOC_NAME))).scalar_one()
        cam = Camera(name="Live Complete", location_id=loc_id, rtsp_url="rtsp://cam/3")
        session.add(cam)
        await session.commit()
        live_id = cam.id

    resp = await client.post(
        f"/api/videos/{live_id}/complete", headers=api_key_headers, json={"status": "completed"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Download & delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_returns_the_original_bytes(client, auth_headers, uploaded):
    resp = await client.get(f"/api/videos/{uploaded['id']}/download", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.content == VIDEO_BYTES


@pytest.mark.asyncio
async def test_delete_removes_row_file_and_children(client, auth_headers):
    resp = await _upload(client, auth_headers, filename="doomed.mp4")
    camera_id = resp.json()["id"]
    stored = Path(resp.json()["rtsp_url"])
    await _seed_events({"id": camera_id}, [("car", "down", 0), ("bus", "up", 1)])
    assert stored.is_file()

    deleted = await client.delete(f"/api/videos/{camera_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert not stored.exists(), "the stored file must be removed from disk"

    async with TestSessionLocal() as session:
        assert await session.get(Camera, camera_id) is None
        lines = (
            await session.execute(select(CountingLine).where(CountingLine.camera_id == camera_id))
        ).scalars().all()
        events = (
            await session.execute(select(Event).where(Event.camera_id == camera_id))
        ).scalars().all()
    assert lines == [] and events == []


@pytest.mark.asyncio
async def test_delete_does_not_touch_files_outside_upload_dir(client, auth_headers, tmp_path):
    """rtsp_url is free text and also holds real RTSP URLs - deleting must be scoped."""
    victim = tmp_path / "important.txt"
    victim.write_text("must survive")

    async with TestSessionLocal() as session:
        loc_id = (await session.execute(select(Location.id).where(Location.name == LOC_NAME))).scalar_one()
        cam = Camera(
            name="Hostile Path",
            location_id=loc_id,
            rtsp_url=str(victim),
            source_type="upload",
            processing_status="completed",
        )
        session.add(cam)
        await session.commit()
        camera_id = cam.id

    resp = await client.delete(f"/api/videos/{camera_id}", headers=auth_headers)
    assert resp.status_code == 204
    assert victim.exists(), "a path outside uploads/videos must never be unlinked"


@pytest.mark.asyncio
async def test_delete_404_for_live_camera(client, auth_headers):
    async with TestSessionLocal() as session:
        loc_id = (await session.execute(select(Location.id).where(Location.name == LOC_NAME))).scalar_one()
        cam = Camera(name="Live Delete", location_id=loc_id, rtsp_url="rtsp://cam/4")
        session.add(cam)
        await session.commit()
        live_id = cam.id

    resp = await client.delete(f"/api/videos/{live_id}", headers=auth_headers)
    assert resp.status_code == 404

    async with TestSessionLocal() as session:
        assert await session.get(Camera, live_id) is not None


@pytest.mark.asyncio
async def test_video_endpoints_require_auth(client, uploaded):
    for method, path in (
        ("get", "/api/videos"),
        ("get", f"/api/videos/{uploaded['id']}/report"),
        ("get", f"/api/videos/{uploaded['id']}/download"),
        ("delete", f"/api/videos/{uploaded['id']}"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code in (401, 403), f"{method.upper()} {path} was not protected"
