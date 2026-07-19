"""
routers/videos.py - Upload a traffic video and have the existing detection
pipeline process it.

The central idea, and the reason this router is small: an uploaded video is not
a new kind of thing. It becomes a **Camera row whose rtsp_url is the absolute
path to the stored file**. The detection supervisor (start_detection.py) already
polls the cameras table every five seconds and starts a pipeline for anything it
has not seen, and OpenCV opens a file path exactly as happily as it opens an
RTSP URL. So an upload inherits overlays, MJPEG streaming, line counting and
event ingestion for free, and appears in Live View next to the live cameras with
no special-casing anywhere downstream.

What this module owns is therefore only the *edges* of that idea:

  * accepting the bytes safely (streamed to disk, size-capped, extension-checked)
  * creating the Camera row and a default counting line so the video starts
    counting without the user configuring anything
  * projecting camera rows back out in video-shaped form (list + report)
  * letting the detection process report that its single pass finished
  * deleting the row and the file together

Endpoints are Bearer-authenticated like the rest of the API, with one deliberate
exception: POST /{id}/complete is called by the detection process, which holds
the service API key and no user JWT, so it uses require_api_key - the same
mechanism POST /api/events already uses.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import log_action
from auth import require_api_key, require_bearer_token
from database import get_db
# date_trunc is the dialect-dispatched version (native on PostgreSQL, strftime
# on SQLite). Importing it registers the compiler hooks.
from db_dialect import REPO_ROOT, VALID_TRUNC_UNITS, date_trunc
from models import Camera, CountingLine, Event, Location
from schemas import VideoCompleteRequest, VideoRead, VideoReport, VideoTimelinePoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])

#: Spelled as an int rather than a starlette constant: the name for 413 changed
#: (REQUEST_ENTITY_TOO_LARGE -> CONTENT_TOO_LARGE) and the old one now emits a
#: deprecation warning, so neither name is safe across the versions this runs on.
HTTP_413_TOO_LARGE = 413

# ---------------------------------------------------------------------------
# Storage layout & limits
# ---------------------------------------------------------------------------

#: Uploads live at the REPO ROOT, not under backend/. The detection process runs
#: with cwd=repo-root and the backend with cwd=backend/, so anchoring on
#: REPO_ROOT (the same constant db_dialect uses to resolve the SQLite path) is
#: what guarantees both processes mean the same directory. The paths we store in
#: the database are absolute anyway, but a shared root keeps the traversal guard
#: and any manual cleanup unambiguous.
VIDEO_DIR = REPO_ROOT / "uploads" / "videos"

ALLOWED_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm"})

#: Read at call time rather than import time so tests (and operators) can change
#: the cap without reimporting the module.
DEFAULT_MAX_UPLOAD_MB = 500

#: 1 MiB. Large enough that a 500 MB upload is ~500 awaits, small enough that no
#: single read pins a meaningful amount of memory.
CHUNK_SIZE = 1024 * 1024


def _max_upload_bytes() -> int:
    raw = os.getenv("VCC_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB))
    try:
        mb = float(raw)
    except ValueError:
        logger.warning("VCC_MAX_UPLOAD_MB=%r is not a number; using default", raw)
        mb = DEFAULT_MAX_UPLOAD_MB
    return int(mb * 1024 * 1024)


def _extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def _stored_path_for(camera: Camera) -> Optional[Path]:
    """The on-disk file for an upload camera, or None if it is not one of ours.

    Returns None rather than raising when the stored path resolves outside
    VIDEO_DIR. rtsp_url is a free-text column that also holds real RTSP URLs, so
    "not inside the upload directory" is an expected case, not an attack
    signature - but it is also exactly the case where a delete must not touch
    the filesystem. Callers treat None as "there is no file to remove".
    """
    if not camera.rtsp_url:
        return None
    try:
        candidate = Path(camera.rtsp_url).resolve()
        root = VIDEO_DIR.resolve()
    except (OSError, ValueError):
        return None
    # is_relative_to() on resolved paths is the traversal guard: a stored value
    # like "../../etc/passwd" or a symlink pointing out of the tree collapses to
    # an absolute path that fails this check.
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _to_video_read(camera: Camera, event_count: int = 0) -> VideoRead:
    return VideoRead(
        id=camera.id,
        name=camera.name,
        location_id=camera.location_id,
        video_filename=camera.video_filename,
        video_size_bytes=camera.video_size_bytes,
        processing_status=camera.processing_status,
        source_type=camera.source_type or "upload",
        status=camera.status,
        rtsp_url=camera.rtsp_url,
        uploaded_at=camera.uploaded_at,
        processed_at=camera.processed_at,
        event_count=event_count,
    )


async def _default_location_id(db: AsyncSession) -> int:
    """Location 1 if it exists, else the lowest-id location.

    An upload has no inherent place in the world, but Camera.location_id is NOT
    NULL with a RESTRICT foreign key, so it needs a real row. Startup seeds
    'Default Junction' with id 1; the fallback covers a database where that seed
    was renumbered or removed.
    """
    if await db.get(Location, 1) is not None:
        return 1
    first = (await db.execute(select(Location.id).order_by(Location.id).limit(1))).scalar()
    if first is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No location exists to attach the uploaded video to. Create a location first.",
        )
    return first


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=VideoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a traffic video for processing",
)
async def upload_video(
    request: Request,
    file: UploadFile = File(..., description="Video file (.mp4 .avi .mov .mkv .webm)"),
    name: Optional[str] = Form(None, description="Display name; defaults to the filename"),
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> VideoRead:
    """Store an uploaded video and register it as a camera for the detection pipeline.

    The file is streamed to disk a chunk at a time through aiofiles: never read
    whole into memory, and never written with a blocking call on the event loop.
    """
    original_name = os.path.basename(file.filename or "")
    ext = _extension_of(original_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported file type %r. Allowed extensions: %s"
                % (ext or original_name, ", ".join(sorted(ALLOWED_EXTENSIONS)))
            ),
        )

    max_bytes = _max_upload_bytes()

    # Fast path: reject on the declared length before touching the disk. This is
    # advisory only - Content-Length covers the whole multipart envelope, and a
    # client can lie or omit it - so the streaming check below remains the
    # authoritative one.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(
            status_code=HTTP_413_TOO_LARGE,
            detail=f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB.",
        )

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # uuid4 hex, not the user's filename: two people uploading "traffic.mp4"
    # must not collide, and a generated name cannot carry path separators or
    # other filesystem-hostile characters. The original name is kept in the
    # video_filename column for display.
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = VIDEO_DIR / stored_name

    total = 0
    try:
        async with aiofiles.open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=HTTP_413_TOO_LARGE,
                        detail=(
                            "File exceeds the maximum upload size of "
                            f"{max_bytes // (1024 * 1024)} MB."
                        ),
                    )
                await out.write(chunk)

        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )
    except HTTPException:
        _unlink_quietly(stored_path)
        raise
    except Exception as exc:  # disk full, permissions, client disconnect
        _unlink_quietly(stored_path)
        logger.exception("Failed to store uploaded video %r", original_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not store the uploaded file: {exc}",
        )
    finally:
        await file.close()

    # Deliberately NOT a codec check. cv2 is not installed in the backend
    # environment, and a decoder probe faked with header sniffing would be worse
    # than none: it would reject valid containers and pass corrupt ones. What we
    # can honestly assert is that the file is non-empty and carries a video
    # extension. Whether the bytes actually decode is answered by the detection
    # process, which owns the decoder and reports back via /complete with
    # processing_status='failed'.

    location_id = await _default_location_id(db)

    camera = Camera(
        name=(name or "").strip() or original_name,
        location_id=location_id,
        lane_count=1,
        # The absolute path IS the source. This is the whole integration.
        rtsp_url=str(stored_path),
        status="active",
        source_type="upload",
        processing_status="pending",
        video_filename=original_name,
        video_size_bytes=total,
        uploaded_at=datetime.now(timezone.utc),
    )
    db.add(camera)

    try:
        await db.flush()

        # A default counting line, so the upload starts counting the moment the
        # pipeline picks it up. Without one the video would be detected and
        # streamed but count nothing, and the user would have to open the line
        # editor before any report had numbers in it. Horizontal across the
        # middle of the frame in normalised coordinates.
        db.add(
            CountingLine(
                camera_id=camera.id,
                name="Main Line",
                x1=0.0,
                y1=0.5,
                x2=1.0,
                y2=0.5,
                lane_id=1,
                direction="both",
                color="#00d4ff",
            )
        )
        await db.commit()
        await db.refresh(camera)
    except Exception:
        await db.rollback()
        # The row is gone, so the bytes are orphaned; remove them too.
        _unlink_quietly(stored_path)
        logger.exception("Failed to register uploaded video %r", original_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not register the uploaded video.",
        )

    await log_action(
        db,
        token.get("sub", "unknown"),
        "VIDEO_UPLOADED",
        f"Uploaded '{original_name}' ({total} bytes) as camera ID {camera.id}",
    )

    return _to_video_read(camera, event_count=0)


def _unlink_quietly(path: Path) -> None:
    """Best-effort delete used on failure paths, where raising would mask the real error."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path, exc)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[VideoRead],
    summary="List uploaded videos (newest first)",
)
async def list_videos(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> list[VideoRead]:
    event_count = (
        select(func.count(Event.id))
        .where(Event.camera_id == Camera.id)
        .correlate(Camera)
        .scalar_subquery()
    )

    rows = (
        await db.execute(
            select(Camera, event_count.label("event_count"))
            .where(Camera.source_type == "upload")
            # uploaded_at is the intended sort key, but it is NULL for any row
            # created before the column existed. Descending id is a stable
            # tiebreaker that keeps those rows in a sensible place instead of
            # letting NULL ordering differ between SQLite and PostgreSQL.
            .order_by(Camera.id.desc())
        )
    ).all()

    return [_to_video_read(cam, count) for cam, count in rows]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


async def _get_upload_camera(db: AsyncSession, camera_id: int) -> Camera:
    camera = await db.get(Camera, camera_id)
    if camera is None or (camera.source_type or "live") != "upload":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Uploaded video not found",
        )
    return camera


@router.get(
    "/{camera_id}/report",
    response_model=VideoReport,
    summary="Analysis report for an uploaded video",
)
async def get_video_report(
    camera_id: int,
    interval: str = Query(
        "minute",
        description="Timeline bucket size (second, minute, hour, day, week, month, year)",
    ),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> VideoReport:
    if interval not in VALID_TRUNC_UNITS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="interval must be one of: %s" % ", ".join(sorted(VALID_TRUNC_UNITS)),
        )

    camera = await _get_upload_camera(db, camera_id)

    by_class_rows = (
        await db.execute(
            select(Event.vehicle_class, func.count(Event.id))
            .where(Event.camera_id == camera_id)
            .group_by(Event.vehicle_class)
        )
    ).all()
    by_class = {cls: count for cls, count in by_class_rows}

    dir_rows = (
        await db.execute(
            select(Event.crossing_dir, func.count(Event.id))
            .where(Event.camera_id == camera_id)
            .group_by(Event.crossing_dir)
        )
    ).all()
    # Seed the two directions the counter emits so the client can render both
    # halves of a comparison without a null check, then fold in anything else
    # actually present (legacy rows use in/out).
    by_direction: dict[str, int] = {"down": 0, "up": 0}
    for direction, count in dir_rows:
        by_direction[direction] = by_direction.get(direction, 0) + count

    bounds = (
        await db.execute(
            select(func.min(Event.timestamp), func.max(Event.timestamp)).where(
                Event.camera_id == camera_id
            )
        )
    ).first()
    first_event_at, last_event_at = bounds if bounds else (None, None)

    # Dialect-aware truncation from db_dialect - no raw date_trunc() here, which
    # would be a syntax error on SQLite.
    bucket = date_trunc(interval, Event.timestamp).label("ts")
    timeline_rows = (
        await db.execute(
            select(bucket, func.count(Event.id))
            .where(Event.camera_id == camera_id)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()

    return VideoReport(
        camera_id=camera.id,
        name=camera.name,
        video_filename=camera.video_filename,
        processing_status=camera.processing_status,
        processed_at=camera.processed_at,
        uploaded_at=camera.uploaded_at,
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        total_vehicles=sum(by_class.values()),
        by_class=by_class,
        by_direction=by_direction,
        timeline=[VideoTimelinePoint(ts=ts, count=count) for ts, count in timeline_rows],
    )


# ---------------------------------------------------------------------------
# Completion callback (service auth, not user auth)
# ---------------------------------------------------------------------------


@router.post(
    "/{camera_id}/complete",
    response_model=VideoRead,
    summary="Mark an uploaded video's processing pass finished (API-key auth)",
    dependencies=[Depends(require_api_key)],
)
async def complete_video(
    camera_id: int,
    body: VideoCompleteRequest,
    db: AsyncSession = Depends(get_db),
) -> VideoRead:
    """Called by the detection process when it finishes one pass over the file.

    Authenticated with X-API-Key rather than a Bearer token: the caller is a
    background service with no user session, exactly like POST /api/events.

    Idempotent - a repeated call (the supervisor restarting, a retry after a
    dropped response) simply rewrites the same terminal state.
    """
    camera = await _get_upload_camera(db, camera_id)

    camera.processing_status = body.status
    camera.processed_at = datetime.now(timezone.utc)
    if body.status == "failed":
        # A file that cannot be decoded must not be retried forever by the
        # supervisor's 5-second poll, and it should not sit in Live View
        # claiming to be a healthy camera.
        camera.status = "inactive"
        logger.warning(
            "Video processing failed for camera %s: %s", camera_id, body.detail or "no detail"
        )

    await db.commit()
    await db.refresh(camera)

    await log_action(
        db,
        "detection-service",
        "VIDEO_PROCESSING_COMPLETE",
        f"Camera ID {camera_id} -> {body.status}"
        + (f": {body.detail}" if body.detail else ""),
    )

    count = (
        await db.execute(select(func.count(Event.id)).where(Event.camera_id == camera_id))
    ).scalar_one()
    return _to_video_read(camera, count)


# ---------------------------------------------------------------------------
# Download & delete
# ---------------------------------------------------------------------------


@router.get(
    "/{camera_id}/download",
    summary="Download the original uploaded file",
)
async def download_video(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_bearer_token),
) -> FileResponse:
    camera = await _get_upload_camera(db, camera_id)
    path = _stored_path_for(camera)
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored file is missing",
        )
    return FileResponse(
        path,
        filename=camera.video_filename or path.name,
        media_type="application/octet-stream",
    )


@router.delete(
    "/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an uploaded video, its camera row, and its stored file",
)
async def delete_video(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
) -> None:
    camera = await _get_upload_camera(db, camera_id)
    name = camera.name

    # Resolve the path BEFORE the row is gone; refuse to unlink anything that
    # does not resolve inside uploads/videos/.
    path = _stored_path_for(camera)

    # counting_lines and events go with it via ON DELETE CASCADE (enforced on
    # SQLite too - db_dialect sets PRAGMA foreign_keys=ON).
    await db.delete(camera)
    await db.commit()

    if path is not None:
        _unlink_quietly(path)

    await log_action(
        db,
        token.get("sub", "unknown"),
        "VIDEO_DELETED",
        f"Deleted uploaded video '{name}' (camera ID {camera_id})",
    )
