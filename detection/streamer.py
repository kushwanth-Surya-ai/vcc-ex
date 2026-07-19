"""
streamer.py — MJPEG streaming server for the VCC detection layer.

Serves live annotated camera frames over HTTP as a multipart/x-mixed-replace
MJPEG stream.  Each camera gets its own endpoint::

    GET http://localhost:8001/stream/cam_001
    GET http://localhost:8001/stream/cam_002
    GET http://localhost:8001/stream/cam_003

Frames are produced by ``tracker.py`` and pushed into a shared
``dict[str, asyncio.Queue]`` (max ``config.FRAME_BUFFER_SIZE``).

Run alongside the tracker::

    python streamer.py          # starts HTTP server
    python tracker.py           # starts inference (separate terminal)

Or integrate both in one process::

    python -c "
    import asyncio, config
    from tracker import main as tracker_main
    from streamer import start_server
    from counter import create_counters_from_config

    async def run():
        queues = {cam['camera_id']: asyncio.Queue(maxsize=config.FRAME_BUFFER_SIZE)
                  for cam in config.CAMERAS}
        await asyncio.gather(tracker_main(), start_server(queues))

    asyncio.run(run())
    "
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import cv2
import numpy as np
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

import config

logger = logging.getLogger(__name__)
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ---------------------------------------------------------------------------
# MJPEG boundary constant
# ---------------------------------------------------------------------------
_BOUNDARY = b"--VCCFrame"
_CRLF     = b"\r\n"

#: Max age of a broadcaster's cached JPEG before /snapshot ignores it and reads
#: the live queue instead.
_SNAPSHOT_MAX_AGE = 2.0

# ---------------------------------------------------------------------------
# aiohttp application keys (AppKey where available, plain str on aiohttp < 3.9)
# ---------------------------------------------------------------------------
try:
    _BROADCASTERS: Any = web.AppKey("vcc_broadcasters", dict)
    _FRAME_QUEUES: Any = web.AppKey("vcc_frame_queues", dict)
except AttributeError:                      # pragma: no cover - older aiohttp
    _BROADCASTERS = "vcc_broadcasters"
    _FRAME_QUEUES = "vcc_frame_queues"


# ---------------------------------------------------------------------------
# Static placeholder frame for cameras not yet streaming
# ---------------------------------------------------------------------------

_PLACEHOLDER_CACHE: dict[str, bytes] = {}


def _make_placeholder(camera_id: str, w: int = 640, h: int = 360) -> bytes:
    """Return a JPEG-encoded grey placeholder frame with a status message."""
    cached = _PLACEHOLDER_CACHE.get(camera_id)
    if cached is not None:
        return cached

    img = np.full((h, w, 3), 40, dtype=np.uint8)
    cv2.putText(
        img,
        f"Waiting for camera: {camera_id}",
        (20, h // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (200, 200, 200),
        2,
        cv2.LINE_AA,
    )
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    data = buf.tobytes()
    _PLACEHOLDER_CACHE[camera_id] = data
    return data


# ---------------------------------------------------------------------------
# JPEG encoding (executed off the event loop)
# ---------------------------------------------------------------------------

def _encode_jpeg(frame_np: np.ndarray, quality: int = 80) -> bytes | None:
    """Encode *frame_np* to JPEG bytes.  Runs in a worker thread, never inline."""
    ok, buf = cv2.imencode(".jpg", frame_np, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    return buf.tobytes()


# ---------------------------------------------------------------------------
# Per-camera broadcaster: ONE queue consumer, ONE encode, N viewers
# ---------------------------------------------------------------------------

class _CameraBroadcaster:
    """
    Fan-out hub for a single camera.

    Exactly one task per camera consumes ``frame_queues[camera_id]`` and encodes
    each frame to JPEG once (in the default executor).  The resulting immutable
    ``bytes`` object is then handed to every currently-connected viewer, so N
    viewers cost ONE encode and no viewer steals another viewer's frames.

    Each subscriber owns a one-slot queue.  A viewer that has not picked up its
    previous frame simply loses it — a slow client can never stall the
    broadcaster or any other client.

    The broadcaster deliberately holds the *frame_queues registry*, not a queue
    object.  ``start_detection.monitor_cameras_loop`` pops and re-creates a
    camera's queue on every source change, crash or delete; a captured queue
    object would leave this task awaiting an orphan forever and every viewer of
    that camera stuck on the placeholder until the process restarted.
    """

    #: How long to wait on one queue before re-resolving it from the registry.
    QUEUE_REFRESH_INTERVAL = 1.0

    def __init__(
        self,
        camera_id:    str,
        frame_queues: dict[str, asyncio.Queue],
    ) -> None:
        self.camera_id    = camera_id
        self.frame_queues = frame_queues
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.latest_jpeg: bytes | None = None
        self.latest_jpeg_ts: float = 0.0
        self._task: asyncio.Task | None = None

    @property
    def is_live(self) -> bool:
        """True while the broadcast task is actually consuming the queue."""
        return self._task is not None and not self._task.done()

    # -- subscription management -------------------------------------------

    def subscribe(self) -> asyncio.Queue[bytes]:
        """Register a new viewer and (re)start the broadcast task if needed."""
        sub: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self.subscribers.add(sub)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name=f"mjpeg-broadcast-{self.camera_id}"
            )
            logger.debug("Broadcaster started: camera_id=%s", self.camera_id)
        return sub

    def unsubscribe(self, sub: asyncio.Queue[bytes]) -> None:
        """Drop a viewer; stop the broadcast task once the last one leaves."""
        self.subscribers.discard(sub)
        if not self.subscribers:
            self.stop()

    def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            logger.debug("Broadcaster stopped: camera_id=%s", self.camera_id)

    # -- broadcast loop -----------------------------------------------------

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                # Re-resolve every iteration: the pipeline may have been
                # restarted and the queue swapped out underneath us.
                queue = self.frame_queues.get(self.camera_id)
                if queue is None:
                    # Pipeline is down (camera deleted or restarting).  Viewers
                    # fall back to the placeholder; pick the queue up when the
                    # coordinator re-creates it.
                    await asyncio.sleep(self.QUEUE_REFRESH_INTERVAL)
                    continue

                try:
                    frame_np = await asyncio.wait_for(
                        queue.get(), timeout=self.QUEUE_REFRESH_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Bounded wait, so a queue replaced while we were blocked is
                    # noticed on the next pass instead of never.
                    continue

                # Offload the (CPU-bound, ~ms) encode so the event loop — which
                # also runs the counting logic — is never blocked by it.
                jpeg = await loop.run_in_executor(None, _encode_jpeg, frame_np, 80)
                if jpeg is None:
                    logger.warning("JPEG encode failed: camera_id=%s", self.camera_id)
                    continue
                self.latest_jpeg    = jpeg
                self.latest_jpeg_ts = time.monotonic()
                self._publish(jpeg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Broadcaster crashed: camera_id=%s", self.camera_id)

    def _publish(self, jpeg: bytes) -> None:
        """Hand the shared JPEG to every viewer, dropping frames for laggards."""
        for sub in list(self.subscribers):
            if sub.full():
                try:
                    sub.get_nowait()      # this client is behind — drop its stale frame
                except asyncio.QueueEmpty:
                    pass
            try:
                sub.put_nowait(jpeg)
            except asyncio.QueueFull:
                pass


def _get_broadcaster(
    request:   web.Request,
    camera_id: str,
) -> _CameraBroadcaster | None:
    """Return (creating on demand) the broadcaster for *camera_id*, or None."""
    registry: dict[str, _CameraBroadcaster] = request.app[_BROADCASTERS]
    frame_queues: dict[str, asyncio.Queue] = request.app[_FRAME_QUEUES]
    bcast = registry.get(camera_id)
    if bcast is None:
        if camera_id not in frame_queues:
            return None
        # Hand over the registry, not the queue: see _CameraBroadcaster.
        bcast = _CameraBroadcaster(camera_id, frame_queues)
        registry[camera_id] = bcast
    return bcast


# ---------------------------------------------------------------------------
# MJPEG stream handler
# ---------------------------------------------------------------------------

async def _mjpeg_handler(
    request:      web.Request,
    frame_queues: dict[str, asyncio.Queue],
) -> web.StreamResponse:
    """
    Stream annotated frames from ``frame_queues[camera_id]`` as MJPEG.

    The response uses ``multipart/x-mixed-replace`` so browsers and
    OpenCV ``VideoCapture`` clients can consume it directly without any
    additional client-side logic.

    Viewers do NOT consume the camera queue themselves — they subscribe to the
    camera's ``_CameraBroadcaster``, so any number of tabs can watch the same
    camera at full frame rate.
    """
    camera_id = request.match_info["camera_id"]

    response = web.StreamResponse(
        status  = 200,
        headers = {
            "Content-Type":  "multipart/x-mixed-replace; boundary=VCCFrame",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma":        "no-cache",
            "Connection":    "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    placeholder = _make_placeholder(camera_id)
    bcast       = _get_broadcaster(request, camera_id)
    sub         = bcast.subscribe() if bcast is not None else None

    logger.info(
        "MJPEG stream started: camera_id=%s  peer=%s  viewers=%d",
        camera_id, request.remote,
        len(bcast.subscribers) if bcast is not None else 0,
    )

    try:
        while True:
            frame_bytes: bytes

            if sub is not None:
                try:
                    # Already-encoded JPEG, shared with every other viewer.
                    frame_bytes = await asyncio.wait_for(sub.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    # No new frame yet — send placeholder to keep connection alive
                    frame_bytes = placeholder
            else:
                # Unknown camera_id — serve placeholder
                frame_bytes = placeholder
                await asyncio.sleep(0.5)

            # Compose the MJPEG part
            part_header = (
                _BOUNDARY + _CRLF
                + b"Content-Type: image/jpeg" + _CRLF
                + b"Content-Length: " + str(len(frame_bytes)).encode() + _CRLF
                + _CRLF
            )
            await response.write(part_header + frame_bytes + _CRLF)

    except ConnectionResetError:
        logger.info("MJPEG stream closed: camera_id=%s", camera_id)
    except asyncio.CancelledError:
        # Must propagate so start_detection.py's task.cancel() still works.
        logger.info("MJPEG stream cancelled: camera_id=%s", camera_id)
        raise
    finally:
        if bcast is not None and sub is not None:
            bcast.unsubscribe(sub)

    return response


# ---------------------------------------------------------------------------
# Health & index endpoints
# ---------------------------------------------------------------------------

async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "vcc-streamer"})


async def _index_handler(
    request:      web.Request,
    frame_queues: dict[str, asyncio.Queue],
) -> web.Response:
    """Return a simple HTML page listing all active camera stream links."""
    links = "\n".join(
        f'  <li><a href="/stream/{cid}" target="_blank">/stream/{cid}</a></li>'
        for cid in frame_queues
    )
    html = (
        "<!DOCTYPE html><html><head><title>VCC Live Streams</title></head>"
        f"<body><h1>VCC Live Camera Streams</h1><ul>\n{links}\n</ul></body></html>"
    )
    return web.Response(text=html, content_type="text/html")


async def _snapshot_handler(
    request:      web.Request,
    frame_queues: dict[str, asyncio.Queue],
) -> web.Response:
    """Return a single JPEG snapshot of the most recent frame for *camera_id*."""
    camera_id = request.match_info["camera_id"]
    queue = frame_queues.get(camera_id)

    # Preferred source: the frame the broadcaster encoded most recently.  Costs
    # nothing and never touches the camera queue.
    #
    # Only usable while the broadcaster is LIVE and its cached frame is recent.
    # stop() leaves the broadcaster (and its last JPEG) in the registry, so an
    # unguarded preference here served one frozen frame forever, for the rest of
    # the process lifetime, to every /snapshot caller.
    registry: dict[str, _CameraBroadcaster] = request.app[_BROADCASTERS]
    bcast = registry.get(camera_id)
    if (
        bcast is not None
        and bcast.latest_jpeg is not None
        and bcast.is_live
        and (time.monotonic() - bcast.latest_jpeg_ts) <= _SNAPSHOT_MAX_AGE
    ):
        return web.Response(
            body=bcast.latest_jpeg,
            content_type="image/jpeg",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if queue is not None and not queue.empty():
        try:
            # No broadcaster running (nobody is streaming this camera).  Take the
            # newest queued frame through the public API and put it straight
            # back, so we neither reach into asyncio.Queue internals nor leave
            # the queue emptier than we found it.  Older frames are stale by
            # definition — the producer already drops them under back-pressure.
            frame_np = None
            while True:
                try:
                    frame_np = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            if frame_np is None:
                raise RuntimeError("queue emptied concurrently")
            try:
                queue.put_nowait(frame_np)
            except asyncio.QueueFull:      # pragma: no cover - queue was drained
                pass

            loop = asyncio.get_running_loop()
            body = await loop.run_in_executor(None, _encode_jpeg, frame_np, 90)
            if body is None:
                raise RuntimeError("JPEG encode failed")
            return web.Response(body=body, content_type="image/jpeg", headers={"Access-Control-Allow-Origin": "*"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Snapshot error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)
    else:
        try:
            buf = _make_placeholder(camera_id)
            return web.Response(
                body=buf,
                content_type="image/jpeg",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "X-Placeholder": "true"
                }
            )
        except Exception as exc:
            logger.error("Snapshot placeholder error: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def build_app(frame_queues: dict[str, asyncio.Queue]) -> web.Application:
    """
    Construct and return the configured aiohttp ``Application``.

    Parameters
    ----------
    frame_queues :
        Shared mapping of ``camera_id`` → ``asyncio.Queue`` populated by
        the tracker inference loop.
    """
    app = web.Application()
    app[_FRAME_QUEUES] = frame_queues
    app[_BROADCASTERS] = {}

    async def _shutdown_broadcasters(app: web.Application) -> None:
        for bcast in app[_BROADCASTERS].values():
            bcast.stop()

    app.on_cleanup.append(_shutdown_broadcasters)

    app.router.add_get("/health", _health_handler)
    app.router.add_get(
        "/",
        lambda req: _index_handler(req, frame_queues),
    )
    app.router.add_get(
        "/stream/{camera_id}",
        lambda req: _mjpeg_handler(req, frame_queues),
    )
    app.router.add_get(
        "/snapshot/{camera_id}",
        lambda req: _snapshot_handler(req, frame_queues),
    )
    return app


# ---------------------------------------------------------------------------
# Server coroutine
# ---------------------------------------------------------------------------

async def start_server(
    frame_queues: dict[str, asyncio.Queue] | None = None,
    host:  str       = "0.0.0.0",
    port:  int | None = None,
) -> None:
    """
    Start the MJPEG HTTP server and block until cancelled.

    Parameters
    ----------
    frame_queues :
        Per-camera asyncio queues.  When ``None``, empty queues are created
        for every camera listed in ``config.CAMERAS`` (useful for standalone
        testing).
    host : str
        Bind address (default ``"0.0.0.0"``).
    port : int | None
        TCP port; falls back to ``config.STREAM_PORT``
        (env ``VCC_STREAM_PORT``, default 8001).
    """
    if port is None:
        port = config.STREAM_PORT

    if frame_queues is None:
        frame_queues = {
            cam["camera_id"]: asyncio.Queue(maxsize=config.FRAME_BUFFER_SIZE)
            for cam in config.CAMERAS
        }

    app    = build_app(frame_queues)
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info(
        "MJPEG streamer listening on http://%s:%d  |  cameras: %s",
        host, port, ", ".join(frame_queues.keys()),
    )

    try:
        # Run forever until the task is cancelled or the process exits
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        logger.info("MJPEG streamer stopped.")


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(start_server())
