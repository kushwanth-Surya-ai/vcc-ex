"""
tracker.py — Async YOLO + ByteTrack inference and event posting.

Architecture
------------
* ``load_model()``      — loads the primary model; falls back to FALLBACK_MODEL
                          with a ``logging.warning`` if primary is unavailable.
* ``run_camera()``      — async task per camera: grabs frames, runs .track(),
                          feeds the LineCounter, posts events to the backend,
                          and pushes annotated frames to a per-camera Queue.
* ``main()``            — gathers all camera tasks concurrently.

Run directly::

    python tracker.py
"""

from __future__ import annotations

import asyncio
import os
import logging

import time
from typing import Any

import cv2
import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()                          # load .env before importing config

import config
from counter import CrossingEvent, LineCounter, create_counters_from_config
from gst_capture import GStreamerCapture, GST_AVAILABLE, gst_version_string

logger = logging.getLogger(__name__)
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

def load_model() -> Any:
    """
    Load a YOLO model from ``config.MODEL_PATH``.

    If that path cannot be loaded (file not found, corrupted, etc.) a
    ``logging.warning`` is emitted and the function retries with
    ``config.FALLBACK_MODEL``.  If that also fails the exception propagates.

    Returns
    -------
    ultralytics.YOLO
        Loaded model instance.
    """
    from ultralytics import YOLO

    primary = config.MODEL_PATH
    try:
        model = YOLO(primary)
        logger.info("Model loaded: %s", primary)
        return model
    except Exception as exc:
        logger.warning(
            "Could not load primary model '%s' (%s). "
            "Falling back to '%s'.",
            primary,
            exc,
            config.FALLBACK_MODEL,
        )
        model = YOLO(config.FALLBACK_MODEL)
        logger.info("Fallback model loaded: %s", config.FALLBACK_MODEL)
        return model


# ---------------------------------------------------------------------------
# Frame annotation helpers
# ---------------------------------------------------------------------------

def _is_network_source(src: str) -> bool:
    """True for stream URLs (rtsp/http/udp/…), False for local file paths."""
    return "://" in src


def _hex_to_bgr(hex_str: str) -> tuple[int, int, int]:
    """Convert a '#RRGGBB' string to an OpenCV BGR tuple."""
    try:
        hex_str = hex_str.lstrip("#")
        return tuple(int(hex_str[i:i+2], 16) for i in (4, 2, 0))  # BGR order
    except Exception:
        return (255, 212, 0)  # Fallback to cyan (#00d4ff)


def _draw_lines(frame: np.ndarray, counter: LineCounter) -> None:
    """Draw all virtual counting lines on *frame* in-place."""
    h, w = frame.shape[:2]
    for line in counter.lines:
        try:
            x1, y1 = int(line["x1"] * w), int(line["y1"] * h)
            x2, y2 = int(line["x2"] * w), int(line["y2"] * h)
            color = _hex_to_bgr(line.get("color", "#00d4ff"))
            
            # Draw line segment
            cv2.line(frame, (x1, y1), (x2, y2), color, config.LINE_THICKNESS)
            # Draw small circles at endpoints
            cv2.circle(frame, (x1, y1), 5, (0, 0, 255), -1) # Red start A
            cv2.circle(frame, (x2, y2), 5, (0, 255, 0), -1) # Green end B
            
            # Draw line name label near midpoint
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            cv2.putText(
                frame,
                line["name"],
                (mx + 10, my - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA
            )
        except Exception:
            pass


def _draw_track(
    frame:     np.ndarray,
    box:       tuple[float, float, float, float],
    track_id:  int,
    label:     str,
    direction: str | None,
) -> None:
    """Draw a bounding box and label for a single tracked vehicle."""
    x1, y1, x2, y2 = (int(v) for v in box)

    colour = (
        config.COLOUR_BOX_DOWN if direction == "down"
        else config.COLOUR_BOX_UP if direction == "up"
        else config.COLOUR_BOX_NONE
    )

    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, config.BOX_THICKNESS)

    text = f"#{track_id} {label}"
    (tw, th), _ = cv2.getTextSize(
        text, cv2.FONT_HERSHEY_SIMPLEX, config.FONT_SCALE, 1
    )
    cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), colour, -1)
    cv2.putText(
        frame, text,
        (x1 + 2, y1 - 4),
        cv2.FONT_HERSHEY_SIMPLEX,
        config.FONT_SCALE,
        config.COLOUR_TEXT,
        1,
        cv2.LINE_AA,
    )


def _draw_counters(
    frame:   np.ndarray,
    counter: LineCounter,
) -> None:
    """Overlay down/up counts on the top-left corner of *frame* per line."""
    y_offset = 30
    for line in counter.lines:
        lid = line["id"]
        # Must not use len(counted_*_per_line[lid]): those sets evict retired track
        # ids, so their size falls as traffic clears. line_totals() is monotonic.
        down_cnt, up_cnt = counter.line_totals(lid)
        txt = f"{line['name']}: DOWN {down_cnt} | UP {up_cnt}"
        color = _hex_to_bgr(line.get("color", "#00d4ff"))
        cv2.putText(
            frame, txt,
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
        y_offset += 24




# ---------------------------------------------------------------------------
# HTTP posting helper
# ---------------------------------------------------------------------------

#: Consecutive failed reads before an uploaded video is declared finished.
#: A single failed decode mid-file must not truncate the run.
EOF_CONFIRM_READS = 5


async def _report_video_complete(
    client: httpx.AsyncClient,
    camera_id: str,
    status: str,
    detail: str | None = None,
) -> None:
    """
    Tell the backend an uploaded video finished processing.

    Best-effort: a failure here leaves the job showing as still processing, which
    is recoverable and far better than crashing the camera task after the events
    have already been recorded.
    """
    url = f"{config.API_BASE_URL}/api/videos/{camera_id}/complete"
    payload: dict[str, Any] = {"status": status}
    if detail:
        payload["detail"] = detail
    try:
        response = await client.post(
            url,
            json=payload,
            headers={"X-API-Key": config.SERVICE_API_KEY},
            timeout=5.0,
        )
        response.raise_for_status()
        logger.info("[%s] Reported video status '%s' to backend.", camera_id, status)
    except Exception as exc:
        logger.warning(
            "[%s] Could not report video completion (%s): %s", camera_id, status, exc
        )


async def _post_event(
    client: httpx.AsyncClient,
    event:  CrossingEvent,
    cam:    dict[str, Any],
) -> None:
    """
    POST a single ``CrossingEvent`` to the backend API.

    Headers
    -------
    X-API-Key : ``config.SERVICE_API_KEY``
    """
    url     = f"{config.API_BASE_URL}/api/events"
    payload = {
        "camera_id":     int(event.camera_id),
        "location_id":   int(cam.get("location_id", 1)),
        "lane_id":       int(getattr(event, "lane_id", cam.get("lane_id", 1))),
        "vehicle_class": event.vehicle_class,
        "confidence":    round(event.confidence, 4),
        "crossing_dir":  event.direction,
        "timestamp":     event.timestamp.isoformat(),
        "track_id":      event.track_id,
    }

    headers = {"X-API-Key": config.SERVICE_API_KEY}

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=5.0)
        response.raise_for_status()
        logger.debug("Event posted: track=%d dir=%s", event.track_id, event.direction)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Backend rejected event (HTTP %d): %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.warning("Could not reach backend: %s", exc)


# ---------------------------------------------------------------------------
# Thin wrapper so each detection index behaves like a track object
# ---------------------------------------------------------------------------

class _BoxWrapper:
    """Thin shim that exposes a single-index slice of an ultralytics Boxes."""

    __slots__ = ("_boxes", "_idx")

    def __init__(self, boxes: Any, idx: int) -> None:
        self._boxes = boxes
        self._idx   = idx

    @property
    def id(self) -> Any:
        ids = self._boxes.id
        if ids is None:
            return None
        return ids[self._idx]

    @property
    def xyxy(self) -> Any:
        return self._boxes.xyxy[self._idx]

    @property
    def cls(self) -> Any:
        return self._boxes.cls[self._idx]

    @property
    def conf(self) -> Any:
        return self._boxes.conf[self._idx]


# ---------------------------------------------------------------------------
# Dedicated Threaded RTSP Capture to eliminate socket buffer overflow
# ---------------------------------------------------------------------------

import threading

class ThreadedRTSPCapture:
    """Dedicated background thread for OpenCV RTSP VideoCapture.
    Reads frames continuously at full network speed (~25 FPS) into a single-slot buffer.
    Prevents FFmpeg RTSP socket buffer overflow, packet drops, and H.265 reference frame corruption."""
    # Health states reported by :meth:`health`.
    CONNECTING = "connecting"   # no frame decoded yet, still inside the grace window
    OK         = "ok"           # a frame arrived recently enough
    STALLED    = "stalled"      # was/should be streaming, nothing arriving -> reconnect

    def __init__(
        self,
        source_parsed: int | str,
        first_frame_timeout: float | None = None,
        stall_timeout: float | None = None,
        sequential: bool = False,
    ):
        self.source_parsed = source_parsed
        # Live sources (RTSP/webcam) use latest-frame-wins: the newest frame is
        # always the interesting one and stale frames are worthless, so the reader
        # overwrites a single slot and the consumer samples whatever is current.
        #
        # A FILE is the opposite. Every frame is content, and the reader can decode
        # the whole clip in well under a second while inference takes ~200ms per
        # frame -- so latest-wins silently discards almost all of it. Measured on a
        # 90-frame clip: only 6 frames ever reached inference and the vehicle was
        # never counted. Sequential mode applies back-pressure so the reader hands
        # over exactly one frame per consumer read, and nothing is dropped.
        self.sequential = sequential
        # Set == "consumer has taken the frame in the slot, produce the next one".
        self._taken = threading.Event()
        self._taken.set()
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|buffer_size;10240000|max_delay;500000"
        self.cap = cv2.VideoCapture(source_parsed, cv2.CAP_FFMPEG)
        self.latest_frame = None
        self.ret = False
        self.running = True
        # Monotonic sequence number: incremented once per *newly decoded* frame.
        # Consumers compare against the seq they last saw to tell a fresh frame
        # from a repeat of the one they already tracked.
        self.frame_seq = 0
        self._last_read_seq = -1
        self._pending_set: list[tuple[int, float]] = []
        # Liveness bookkeeping.  ``opened_at`` starts the first-frame grace
        # window; ``last_frame_ts`` drives stall detection once streaming began.
        self.opened_at = time.monotonic()
        self.last_frame_ts: float | None = None
        # A camera that is slow to hand over its first frame (H.265 keyframe
        # interval, TCP negotiation, DHCP-slow NVR) must NOT be torn down while
        # it is still negotiating -- that used to livelock the reconnect ladder.
        self.first_frame_timeout = (
            first_frame_timeout
            if first_frame_timeout is not None
            else float(os.getenv("VCC_FIRST_FRAME_TIMEOUT", "20.0"))
        )
        # Once frames HAVE been flowing, silence this long means the stream is
        # dead even though cap.read() may keep failing quietly forever.
        self.stall_timeout = (
            stall_timeout
            if stall_timeout is not None
            else float(os.getenv("VCC_STALL_TIMEOUT", "5.0"))
        )
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._update_loop, daemon=True)
        self.thread.start()

    # -- capture handle access ---------------------------------------------
    # The lock is held ONLY to hand out / swap the capture reference and the
    # frame slot.  No blocking decode ever happens under it.

    def _cap_ref(self) -> Any:
        with self.lock:
            return self.cap

    def isOpened(self) -> bool:
        cap = self._cap_ref()
        try:
            return cap is not None and cap.isOpened()
        except Exception:
            return False

    def get(self, prop_id: int) -> float:
        cap = self._cap_ref()
        try:
            if cap is not None and cap.isOpened():
                return cap.get(prop_id)
        except Exception:
            pass
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        """
        Queue a property change (e.g. ``CAP_PROP_POS_FRAMES`` rewind).

        The change is applied by the reader thread between two reads rather than
        inline: calling ``cap.set()`` from another thread while a decode is in
        flight is not safe with the FFMPEG backend.
        """
        with self.lock:
            if self.cap is None:
                return False
            self._pending_set.append((prop_id, value))
            # A deliberate seek (e.g. rewinding a finished .mp4) interrupts the
            # stream on purpose, so restart the liveness clock instead of
            # letting the gap it creates look like a stall.
            self.opened_at    = time.monotonic()
            self.last_frame_ts = None
        return True

    def _update_loop(self) -> None:
        try:
            while self.running:
                cap = self._cap_ref()
                if cap is None:
                    time.sleep(0.05)
                    continue
                try:
                    if not cap.isOpened():
                        time.sleep(0.05)
                        continue
                except Exception:
                    time.sleep(0.05)
                    continue

                # Apply any queued property changes (rewind, etc.) in-thread.
                with self.lock:
                    pending, self._pending_set = self._pending_set, []
                for prop_id, value in pending:
                    try:
                        cap.set(prop_id, value)
                    except Exception:
                        logger.debug("Capture set(%s, %s) failed", prop_id, value)

                # Sequential (file) mode: do not decode ahead of the consumer.
                # Waiting in slices keeps release() responsive.
                if self.sequential:
                    while self.running and not self._taken.wait(timeout=0.1):
                        pass
                    if not self.running:
                        break

                # ---- BLOCKING network decode happens OUTSIDE the lock -------
                try:
                    ret, frame = cap.read()
                except Exception:
                    ret, frame = False, None

                if ret and frame is not None:
                    with self.lock:
                        if not self.running:
                            break
                        self.latest_frame = frame
                        self.ret = True
                        self.frame_seq += 1
                        self.last_frame_ts = time.monotonic()
                    # Hold here until the consumer has actually taken this frame.
                    # Cleared only on success: a failed read produced nothing, so
                    # the consumer owes no acknowledgement and we must not block.
                    if self.sequential:
                        self._taken.clear()
                else:
                    # ``ret`` must track the CURRENT health of the capture, not
                    # "did we ever succeed".  Leaving it latched True made the
                    # whole reconnect ladder in run_camera() unreachable.
                    with self.lock:
                        self.ret = False
                    time.sleep(0.01)
        finally:
            # The reader thread owns teardown, so a release() that races with an
            # in-flight read() can never free the capture out from under us.
            self._close_cap()

    def read(self) -> tuple[bool, np.ndarray | None]:
        """cv2-compatible read.  Returns the latest frame, fresh or not."""
        ret, frame, _is_new = self.read_with_freshness()
        return ret, frame

    def read_with_freshness(self) -> tuple[bool, np.ndarray | None, bool]:
        """
        Like :meth:`read` but also reports whether the frame is *new*.

        ``is_new`` is False when the reader thread has not decoded anything
        since the previous call — re-running the tracker on such a frame would
        feed ByteTrack a duplicate observation and corrupt its Kalman motion
        model.
        """
        with self.lock:
            if self.latest_frame is None:
                return False, None, False
            is_new = self.frame_seq != self._last_read_seq
            self._last_read_seq = self.frame_seq
            # A single failed cap.read() between two good ones is normal (packet
            # loss, decoder warm-up).  Only sustained silence counts as a
            # failure, so a blip does not trip the reconnect ladder.
            ret = self.ret or self._health_locked() == self.OK
            frame = self.latest_frame.copy()

        # Acknowledge OUTSIDE the lock: this releases the reader thread to decode
        # the next frame, and the reader takes self.lock to store it.  Signalling
        # while holding the lock would hand it a thread that immediately blocks.
        if is_new and self.sequential:
            self._taken.set()

        return ret, frame, is_new

    # -- liveness ----------------------------------------------------------

    def _health_locked(self) -> str:
        """``self.lock`` must be held.  See :meth:`health`."""
        now = time.monotonic()
        if self.last_frame_ts is None:
            # Nothing has ever been decoded.  Be patient until the grace window
            # expires -- tearing the capture down here is what caused a
            # slow-to-start camera to loop forever without ever streaming.
            if (now - self.opened_at) < self.first_frame_timeout:
                return self.CONNECTING
            return self.STALLED
        if (now - self.last_frame_ts) >= self.stall_timeout:
            return self.STALLED
        return self.OK

    def health(self) -> str:
        """
        Current liveness of the capture: ``CONNECTING`` / ``OK`` / ``STALLED``.

        This is what distinguishes "no frame yet, still negotiating" (wait) from
        "frames were flowing and stopped" (reconnect).
        """
        with self.lock:
            return self._health_locked()

    def _close_cap(self) -> None:
        """Idempotently release the underlying capture."""
        with self.lock:
            cap, self.cap = self.cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def release(self) -> None:
        self.running = False
        thread = self.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            # Wait for any in-flight decode to finish; the reader thread then
            # releases the capture itself in its finally block.
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning(
                    "Capture reader thread still blocked in read(); "
                    "deferring release to that thread."
                )
                return
        self._close_cap()


def _capture_health(cap: Any) -> str:
    """
    Liveness of *cap* for captures that expose :meth:`ThreadedRTSPCapture.health`.

    Captures without it (``GStreamerCapture``) block on their own frame queue, so
    a falsy read from them already means the pipeline is dead -> ``STALLED``.
    """
    probe = getattr(cap, "health", None)
    if probe is None:
        return ThreadedRTSPCapture.STALLED
    try:
        return probe()
    except Exception:
        return ThreadedRTSPCapture.STALLED


def _release_capture_in_background(cap: Any, camera_id: str) -> None:
    """
    Release *cap* off the event loop, without waiting for it.

    ``release()`` joins the reader thread for up to 5 s.  Calling it inline from
    a ``finally`` block stalled every other camera and the MJPEG streamer for
    that long on each restart.  A plain daemon thread is used rather than
    ``run_in_executor`` because this runs during task cancellation, where
    awaiting anything re-raises ``CancelledError`` immediately and the loop may
    already be shutting down.
    """
    def _do_release() -> None:
        try:
            cap.release()
        except Exception:
            logger.debug("[%s] Capture release raised; ignoring.", camera_id)
        logger.info("[%s] Capture released.", camera_id)

    threading.Thread(
        target=_do_release, name=f"cap-release-{camera_id}", daemon=True
    ).start()



# ---------------------------------------------------------------------------
# Per-camera async task
# ---------------------------------------------------------------------------

async def run_camera(
    camera_config: dict[str, Any],
    counter:       LineCounter,
    frame_queues:  dict[str, asyncio.Queue],
) -> None:
    """
    Continuous inference loop for a single camera.

    Reads frames from ``camera_config['source']``, runs ByteTrack via
    ``.track()``, updates the ``LineCounter``, posts any new events to the
    backend API, annotates frames, and pushes them to the shared
    ``frame_queues[camera_id]`` for the MJPEG streamer.

    Parameters
    ----------
    camera_config :
        A dict from ``config.CAMERAS``.
    counter :
        The ``LineCounter`` instance for this camera.
    frame_queues :
        Shared mapping of camera_id → asyncio.Queue (max ``config.FRAME_BUFFER_SIZE``).
    """
    camera_id = camera_config["camera_id"]
    source    = camera_config["source"]

    # source may be "0", "1", … (device index strings) or a URL / path
    try:
        source_parsed: int | str = int(source)
    except (ValueError, TypeError):
        source_parsed = source

    # A local file is finite content, not a live feed: every frame matters, so the
    # capture must hand them over one-for-one instead of dropping whatever the
    # consumer was too slow to sample. Network URLs and device indices stay on the
    # latest-frame-wins path, where dropping stale frames is the correct behaviour.
    is_file_source = isinstance(source_parsed, str) and not _is_network_source(source_parsed)
    if is_file_source:
        logger.info("[%s] File source detected — using sequential capture (no frame drops).", camera_id)

    # An uploaded video is a finite job: process it once and report a result.
    # Looping it (the behaviour a live/demo file source wants) would keep
    # re-counting the same vehicles and inflate the report without bound.
    single_pass = str(camera_config.get("source_type") or "live") == "upload"
    eof_streak = 0
    if single_pass:
        logger.info("[%s] Uploaded video — single-pass mode, will finish at EOF.", camera_id)

    logger.info("[%s] Opening source: %s", camera_id, source_parsed)

    model = load_model()

    async with httpx.AsyncClient() as http_client:
        loop = asyncio.get_running_loop()

        # ---- Dual-path capture: native GStreamer preferred, FFMPEG fallback
        cap = None
        using_native_gst = False

        if GST_AVAILABLE and isinstance(source_parsed, str):
            logger.info(
                "[%s] Attempting NATIVE GStreamer capture (%s)",
                camera_id, gst_version_string(),
            )
            gst_cap = GStreamerCapture(source_parsed)
            started = await loop.run_in_executor(None, gst_cap.start)
            if started:
                cap = gst_cap
                using_native_gst = True
                logger.info(
                    "[%s] Using NATIVE GStreamer capture -- pipeline active",
                    camera_id,
                )
            else:
                logger.warning(
                    "[%s] Native GStreamer pipeline failed to reach PLAYING. "
                    "Falling back to FFMPEG capture.",
                    camera_id,
                )
                gst_cap.release()
        elif not GST_AVAILABLE:
            logger.info(
                "[%s] GStreamer unavailable, falling back to FFMPEG capture",
                camera_id,
            )

        if cap is None:
            # FFMPEG / OpenCV fallback with dedicated background frame reader thread
            logger.info("[%s] Opening source using Threaded RTSP Capture (TCP Transport)...", camera_id)
            cap = await loop.run_in_executor(
                None, lambda: ThreadedRTSPCapture(source_parsed, sequential=is_file_source)
            )
            if not cap.isOpened():
                logger.error(
                    "[%s] Cannot open source '%s'. Camera task exiting.",
                    camera_id, source,
                )
                # An uploaded video that cannot be opened is never going to
                # succeed on a retry. Without this the supervisor sees the task
                # exit, respawns it 5 s later and the row reads 'processing'
                # forever -- a silent infinite loop instead of a visible failure.
                if single_pass:
                    await _report_video_complete(http_client, camera_id, "failed")
                return

        # Inspect stream codec
        try:
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec_str = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]).strip().lower()
            if codec_str in ["hevc", "h265", "265h"]:
                logger.info("[%s] Stream Codec: H.265 (HEVC) detected. Decoding via lossless TCP stream.", camera_id)
            elif codec_str in ["avc1", "h264", "264h"]:
                logger.info("[%s] Stream Codec: H.264 (AVC) detected. Decoding via lossless TCP stream.", camera_id)
            else:
                logger.info("[%s] Stream Codec: %s (FOURCC %d) detected. Decoding via lossless TCP stream.", camera_id, codec_str or "Unknown", fourcc)
        except Exception:
            pass

        logger.info("[%s] Capture open. Starting inference loop.", camera_id)

        # Announce that an uploaded video has actually started decoding. Without
        # this it would sit at 'pending' until EOF and then jump straight to
        # 'completed' -- and the UI only offers the live annotated preview while a
        # job reads 'processing', so the user would never see it work.
        if single_pass:
            await _report_video_complete(http_client, camera_id, "processing")
        
        # CPU Optimization: target FPS pacing
        target_fps = float(os.getenv("VCC_TARGET_FPS", "10.0"))
        target_delay = 1.0 / target_fps if target_fps > 0 else 0

        # Reconnect backoff: 1 s doubling to RECONNECT_MAX_BACKOFF.  A camera
        # that is permanently dead must not spin at 1 Hz forever, nor flood the
        # log with one warning per attempt.
        RECONNECT_BASE_BACKOFF = 1.0
        RECONNECT_MAX_BACKOFF  = 30.0
        backoff          = RECONNECT_BASE_BACKOFF
        fail_streak      = 0
        connecting_logged = False

        try:
            while True:
                start_time = asyncio.get_event_loop().time()

                reader = getattr(cap, "read_with_freshness", None)
                if reader is not None:
                    ret, frame, is_new = await loop.run_in_executor(None, reader)
                else:
                    # GStreamerCapture.read() blocks on its own frame queue, so
                    # every successful read is new by construction.
                    ret, frame = await loop.run_in_executor(None, cap.read)
                    is_new = ret

                if not ret:
                    health = _capture_health(cap)

                    if health == ThreadedRTSPCapture.CONNECTING:
                        # No frame has EVER arrived and the grace window is still
                        # open.  Releasing the capture here would tear down a
                        # connection that is mid-negotiation, and the doubling
                        # backoff would then keep a slow camera off-air forever.
                        if not connecting_logged:
                            logger.info(
                                "[%s] Waiting for first frame from source...",
                                camera_id,
                            )
                            connecting_logged = True
                        await asyncio.sleep(0.2)
                        continue

                    # End of a video file.
                    #
                    # Keyed off is_file_source rather than an extension whitelist:
                    # the old check listed only .mp4/.avi/.mkv, so an uploaded .mov
                    # or .webm fell through to the reconnect ladder and was treated
                    # as a dead camera.
                    # Native GStreamer needs an extra test to get here. Its read()
                    # returns (False, None) both at real EOS and on a transient
                    # queue timeout, and only EOS clears isOpened() -- so without
                    # that check a merely slow decoder would be mistaken for the
                    # end of the clip. Restricted to single_pass because a looping
                    # demo source cannot rewind a dead pipeline with cap.set(); it
                    # relies on the reconnect ladder below to reopen and replay.
                    gst_upload_eos = using_native_gst and single_pass and not cap.isOpened()
                    if is_file_source and (not using_native_gst or gst_upload_eos):
                        eof_streak += 1
                        # Require a few consecutive failures before declaring the
                        # file finished, so one transient decode hiccup mid-clip
                        # cannot truncate the run and publish a short count.
                        if single_pass and eof_streak >= EOF_CONFIRM_READS:
                            logger.info(
                                "[%s] End of uploaded video — single pass complete. "
                                "Counted %d down / %d up.",
                                camera_id, counter.total_down, counter.total_up,
                            )
                            await _report_video_complete(http_client, camera_id, "completed")
                            return

                        if not single_pass:
                            # Live/demo file source: loop so the feed keeps playing.
                            if hasattr(cap, "set"):
                                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        # Always yield — without this the rewind path is a hot
                        # busy-loop that starves the event loop.
                        await asyncio.sleep(0.05)
                        continue

                    fail_streak += 1
                    # Log every attempt for the first few, then decreasingly
                    # often, so a dead camera does not flood the log.
                    if fail_streak <= 3 or fail_streak % 10 == 0:
                        logger.warning(
                            "[%s] Frame read failed (attempt %d) -- retrying in %.1f s.",
                            camera_id, fail_streak, backoff,
                        )
                    else:
                        logger.debug(
                            "[%s] Frame read failed (attempt %d) -- retrying in %.1f s.",
                            camera_id, fail_streak, backoff,
                        )

                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, RECONNECT_MAX_BACKOFF)

                    await loop.run_in_executor(None, cap.release)

                    # Reconnect using the same path that originally worked
                    if using_native_gst:
                        gst_cap = GStreamerCapture(source_parsed)
                        started = await loop.run_in_executor(None, gst_cap.start)
                        if started:
                            cap = gst_cap
                            logger.info("[%s] Reconnected via native GStreamer.", camera_id)
                        else:
                            gst_cap.release()
                            logger.warning("[%s] GStreamer reconnect failed, trying FFMPEG.", camera_id)
                            cap = await loop.run_in_executor(None, lambda: ThreadedRTSPCapture(source_parsed, sequential=is_file_source))
                            using_native_gst = False
                    else:
                        cap = await loop.run_in_executor(None, lambda: ThreadedRTSPCapture(source_parsed, sequential=is_file_source))
                    # The replacement capture gets its own first-frame grace
                    # window, so let it announce itself again.
                    connecting_logged = False
                    continue

                # Recovered — reset the backoff ladder.
                if fail_streak:
                    logger.info("[%s] Capture recovered after %d failed attempts.", camera_id, fail_streak)
                    fail_streak = 0
                    backoff     = RECONNECT_BASE_BACKOFF
                # A good frame means we are not at EOF, so the confirmation streak
                # must restart — otherwise scattered hiccups across a long video
                # would accumulate and end the job early.
                eof_streak = 0
                connecting_logged = False

                if not is_new:
                    # Inference is outrunning capture.  Re-tracking the same
                    # frame would feed ByteTrack a duplicate observation, so
                    # yield briefly and wait for a genuinely new frame instead.
                    await asyncio.sleep(0.005)
                    continue

                frame_h = frame.shape[0]

                # ---- run tracker ----------------------------------------
                # Offload the blocking YOLO call to the default thread-pool
                # executor so we don't starve the event loop.
                loop    = asyncio.get_running_loop()
                results = await loop.run_in_executor(
                    None,
                    lambda f=frame: model.track(
                        f,
                        persist    = True,
                        tracker    = config.TRACKER,
                        conf       = config.CONF_THRESHOLD,
                        iou        = config.IOU_THRESHOLD,
                        classes    = list(config.VEHICLE_CLASS_MAP.keys()),
                        verbose    = False,
                    ),
                )

                # ---- collect tracks -------------------------------------
                tracks: list[Any] = []
                if results and results[0].boxes is not None:
                    boxes = results[0].boxes
                    for i in range(len(boxes)):
                        tracks.append(_BoxWrapper(boxes, i))

                # ---- count crossings ------------------------------------
                events = counter.process_tracks(tracks, frame_h, frame_w=frame.shape[1])

                # ---- post events to backend (fire-and-forget) -----------
                for ev in events:
                    asyncio.ensure_future(
                        _post_event(http_client, ev, camera_config)
                    )

                # ---- annotate frame -------------------------------------
                annotated = frame.copy()
                _draw_lines(annotated, counter)
                _draw_counters(annotated, counter)

                for t in tracks:
                    try:
                        tid_raw = t.id
                        if tid_raw is None:
                            continue
                        tid     = int(tid_raw.item() if hasattr(tid_raw, "item") else tid_raw)
                        box     = t.xyxy
                        if hasattr(box, "shape") and len(box.shape) == 2:
                            box = box[0]
                        cls_raw = t.cls
                        cls_id  = int(cls_raw.item() if hasattr(cls_raw, "item") else cls_raw)
                        label   = config.VEHICLE_CLASS_MAP.get(cls_id, "vehicle")
                        last_dir: str | None = None
                        
                        # Check if this track was counted in any of the lines
                        for lid in counter.counted_down_per_line:
                            if tid in counter.counted_down_per_line[lid]:
                                last_dir = "down"
                                break
                        if not last_dir:
                            for lid in counter.counted_up_per_line:
                                if tid in counter.counted_up_per_line[lid]:
                                    last_dir = "up"
                                    break
                                    
                        _draw_track(
                            annotated,
                            (float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                            tid, label, last_dir,
                        )

                    except Exception:
                        pass

                # ---- push annotated frame to queue ----------------------
                q = frame_queues.get(camera_id)
                if q is not None:
                    if q.full():
                        try:
                            q.get_nowait()   # drop oldest frame
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        q.put_nowait(annotated)
                    except asyncio.QueueFull:
                        pass



                # Sleep to maintain target FPS and yield CPU
                if target_delay > 0:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    sleep_time = target_delay - elapsed
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                    else:
                        await asyncio.sleep(0.005) # minimal yield to keep event loop responsive
                else:
                    await asyncio.sleep(0.005)


        finally:
            # Non-blocking: release() joins the reader thread for up to 5 s and
            # this runs on the event loop during task cancellation.
            _release_capture_in_background(cap, camera_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def poll_settings_config() -> None:
    """
    Background task that polls GET /api/settings/config every 10 seconds.
    Updates config.CONF_THRESHOLD in-place.
    """
    url = f"{config.API_BASE_URL}/api/settings/config"
    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    new_val = float(data["confidence_threshold"])
                    if config.CONF_THRESHOLD != new_val:
                        logger.info("Updating confidence threshold dynamically: %s -> %s", config.CONF_THRESHOLD, new_val)
                        config.CONF_THRESHOLD = new_val
        except Exception as exc:
            logger.warning("Failed to poll dynamic settings configuration (using last-known-good %s): %s", config.CONF_THRESHOLD, exc)
        await asyncio.sleep(10.0)


async def main() -> None:
    """Spin up one coroutine per configured camera and settings poll task."""
    counters     = create_counters_from_config()
    frame_queues = {
        cam["camera_id"]: asyncio.Queue(maxsize=config.FRAME_BUFFER_SIZE)
        for cam in config.CAMERAS
    }

    tasks = [
        asyncio.create_task(
            run_camera(cam, counters[cam["camera_id"]], frame_queues),
            name=f"camera-{cam['camera_id']}",
        )
        for cam in config.CAMERAS
    ]
    # Add settings config poll task
    tasks.append(asyncio.create_task(poll_settings_config(), name="settings-poll"))

    logger.info("Tracker started — %d tasks.", len(tasks))
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
