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


# ---------------------------------------------------------------------------
# Static placeholder frame for cameras not yet streaming
# ---------------------------------------------------------------------------

def _make_placeholder(camera_id: str, w: int = 640, h: int = 360) -> bytes:
    """Return a JPEG-encoded grey placeholder frame with a status message."""
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
    return buf.tobytes()


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
    queue       = frame_queues.get(camera_id)

    logger.info(
        "MJPEG stream started: camera_id=%s  peer=%s",
        camera_id, request.remote,
    )

    try:
        while True:
            frame_bytes: bytes

            if queue is not None:
                try:
                    frame_np = await asyncio.wait_for(queue.get(), timeout=5.0)
                    logger.debug(
                        "Frame received for camera_id=%s  shape=%s",
                        camera_id, frame_np.shape,
                    )
                    _, buf   = cv2.imencode(
                        ".jpg", frame_np,
                        [cv2.IMWRITE_JPEG_QUALITY, 80],
                    )
                    frame_bytes = buf.tobytes()
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

    except (ConnectionResetError, asyncio.CancelledError):
        logger.info("MJPEG stream closed: camera_id=%s", camera_id)

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
    """Return a single JPEG snapshot of the latest frame in the queue without consuming it."""
    camera_id = request.match_info["camera_id"]
    queue = frame_queues.get(camera_id)
    if queue is not None and not queue.empty():
        try:
            # Peek at the most recent frame in the queue's collection deque
            frame_np = queue._queue[-1]
            _, buf = cv2.imencode(".jpg", frame_np, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return web.Response(body=buf.tobytes(), content_type="image/jpeg", headers={"Access-Control-Allow-Origin": "*"})
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
