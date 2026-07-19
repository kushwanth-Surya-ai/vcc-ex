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
# -------------------------------------------------------------------def _hex_to_bgr(hex_str: str) -> tuple[int, int, int]:
    try:
        hex_str = hex_str.lstrip("#")
        return tuple(int(hex_str[i:i+2], 16) for i in (4, 2, 0)) # BGR order
    except Exception:
        return (255, 212, 0) # Fallback to cyan


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
        down_cnt = len(counter.counted_down_per_line.get(lid, set()))
        up_cnt = len(counter.counted_up_per_line.get(lid, set()))
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
    def __init__(self, source_parsed: int | str):
        self.source_parsed = source_parsed
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|buffer_size;10240000|max_delay;500000"
        self.cap = cv2.VideoCapture(source_parsed, cv2.CAP_FFMPEG)
        self.latest_frame = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._update_loop, daemon=True)
        self.thread.start()

    def isOpened(self) -> bool:
        with self.lock:
            return self.cap is not None and self.cap.isOpened()

    def get(self, prop_id: int) -> float:
        with self.lock:
            if self.cap is not None and self.cap.isOpened():
                return self.cap.get(prop_id)
            return 0.0

    def _update_loop(self) -> None:
        while self.running:
            with self.lock:
                if not self.running or self.cap is None or not self.cap.isOpened():
                    time.sleep(0.05)
                    continue
                try:
                    ret, frame = self.cap.read()
                except Exception:
                    ret, frame = False, None

            if ret and frame is not None:
                with self.lock:
                    self.latest_frame = frame
                    self.ret = True
            else:
                time.sleep(0.01)

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self.lock:
            if self.latest_frame is not None:
                return self.ret, self.latest_frame.copy()
            return False, None

    def release(self) -> None:
        self.running = False
        with self.lock:
            if self.cap is not None:
                try:
                    if self.cap.isOpened():
                        self.cap.release()
                except Exception:
                    pass
                self.cap = None



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
                None, lambda: ThreadedRTSPCapture(source_parsed)
            )
            if not cap.isOpened():
                logger.error(
                    "[%s] Cannot open source '%s'. Camera task exiting.",
                    camera_id, source,
                )
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
        
        # CPU Optimization: target FPS pacing
        target_fps = float(os.getenv("VCC_TARGET_FPS", "10.0"))
        target_delay = 1.0 / target_fps if target_fps > 0 else 0

        try:
            while True:
                start_time = asyncio.get_event_loop().time()



                ret, frame = await loop.run_in_executor(None, cap.read)

                if not ret:
                    # If we reached the end of a video file, loop back
                    if not using_native_gst and str(source_parsed).endswith(('.mp4', '.avi', '.mkv')):
                        if hasattr(cap, "set"):
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue

                    logger.warning("[%s] Frame read failed -- retrying in 1 s.", camera_id)
                    await asyncio.sleep(1.0)
                    cap.release()

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
                            cap = await loop.run_in_executor(None, lambda: ThreadedRTSPCapture(source_parsed))
                            using_native_gst = False
                    else:
                        cap = await loop.run_in_executor(None, lambda: ThreadedRTSPCapture(source_parsed))
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
            cap.release()
            logger.info("[%s] Capture released.", camera_id)


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
