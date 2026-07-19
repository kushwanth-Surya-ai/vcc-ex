"""
gst_capture.py -- Native GStreamer RTSP capture using PyGObject (gi) bindings.

Bypasses OpenCV's capture layer entirely. Falls back gracefully if GStreamer
or PyGObject is not installed.

Usage::

    from gst_capture import GStreamerCapture, GST_AVAILABLE

    if GST_AVAILABLE:
        cap = GStreamerCapture(rtsp_url)
        if cap.start():
            ret, frame = cap.read()  # (bool, np.ndarray) same as cv2.VideoCapture
            cap.release()
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attempt to import GStreamer bindings — mark availability
# ---------------------------------------------------------------------------

GST_AVAILABLE = False
_GST_VERSION_STRING = "N/A"

try:
    import os
    import sys
    
    # Check if disabled via environment variable VCC_DISABLE_GST
    if os.getenv("VCC_DISABLE_GST", "false").lower() == "true":
        logger.info("GStreamer disabled via VCC_DISABLE_GST in environment.")
        raise ValueError("GStreamer disabled via VCC_DISABLE_GST")

    # Locate GStreamer folders and dependency folders on Windows
    gst_root = r"C:\Users\Charan Galla\AppData\Local\Programs\gstreamer\1.0\msvc_x86_64"
    gst_bin = os.path.join(gst_root, "bin")
    gst_typelibs = os.path.join(gst_root, "lib", "girepository-1.0")
    
    # Local project bin directory containing GObject/GLib DLLs
    detection_dir = os.path.dirname(os.path.abspath(__file__))
    local_bin = os.path.join(detection_dir, "bin")
    
    if os.path.isdir(gst_bin):
        os.environ["PATH"] = gst_bin + os.pathsep + local_bin + os.pathsep + os.environ.get("PATH", "")
        os.environ["GI_TYPELIB_PATH"] = gst_typelibs
        
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(gst_bin)
                os.add_dll_directory(local_bin)
            except Exception:
                pass

    import gi                                       # type: ignore[import]
    gi.require_version("Gst", "1.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gst, GLib             # type: ignore[import]

    Gst.init(None)
    _GST_VERSION_STRING = Gst.version_string()
    GST_AVAILABLE = True
    logger.info("GStreamer Python bindings loaded: %s", _GST_VERSION_STRING)

except (ImportError, ValueError) as exc:
    logger.warning(
        "GStreamer Python bindings (PyGObject / gi) not available: %s. "
        "Native GStreamer capture will be disabled; FFMPEG fallback will be used.",
        exc,
    )


def gst_version_string() -> str:
    """Return the GStreamer version string, or 'N/A' if unavailable."""
    return _GST_VERSION_STRING


# ---------------------------------------------------------------------------
# GStreamerCapture class
# ---------------------------------------------------------------------------

class GStreamerCapture:
    """
    Native GStreamer RTSP/file capture that exposes a ``cv2.VideoCapture``
    compatible ``.read()`` interface.

    Parameters
    ----------
    source : str
        RTSP URL (``rtsp://...``) or file path.
    latency : int
        RTSP jitter-buffer latency in ms (default 100).
    """

    def __init__(self, source: str, latency: int = 100) -> None:
        if not GST_AVAILABLE:
            raise RuntimeError("GStreamer is not available on this system")

        self._source  = source
        self._latency = latency

        # Thread-safe frame buffer — maxsize=1 ensures we always get the
        # latest frame without backlog
        self._frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=2)

        # GLib main loop and its thread
        self._loop:       Optional[GLib.MainLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # GStreamer pipeline
        self._pipeline = None
        self._appsink  = None

        # State
        self._running   = False
        self._frame_count = 0
        self._width     = 0
        self._height    = 0

    # ----- Pipeline construction ------------------------------------------

    def _build_pipeline_string(self) -> str:
        """
        Build the GStreamer pipeline string.

        Uses ``decodebin`` for auto-negotiation rather than hardcoding
        H.264/H.265 specific elements — this handles codec discovery
        dynamically without needing to probe the camera beforehand.
        """
        src = self._source

        if src.startswith("rtsp://"):
            # RTSP source with decodebin for automatic codec negotiation
            pipeline_str = (
                f'rtspsrc location="{src}" latency={self._latency} '
                f'protocols=tcp ! decodebin ! videoconvert ! '
                f'video/x-raw,format=BGR ! '
                f'appsink name=sink emit-signals=True sync=False '
                f'max-buffers=1 drop=True'
            )
        elif src.endswith(('.mp4', '.avi', '.mkv', '.mov')):
            # File source
            pipeline_str = (
                f'filesrc location="{src}" ! decodebin ! videoconvert ! '
                f'video/x-raw,format=BGR ! '
                f'appsink name=sink emit-signals=True sync=False '
                f'max-buffers=1 drop=True'
            )
        else:
            # Fallback — try as URI
            pipeline_str = (
                f'uridecodebin uri="{src}" ! videoconvert ! '
                f'video/x-raw,format=BGR ! '
                f'appsink name=sink emit-signals=True sync=False '
                f'max-buffers=1 drop=True'
            )

        return pipeline_str

    # ----- Bus message handler --------------------------------------------

    def _on_bus_message(self, bus, message) -> bool:
        """Handle GStreamer bus messages — this is the ONLY place where
        GStreamer reports errors, they don't raise Python exceptions."""
        msg_type = message.type

        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(
                "[GStreamer] ERROR from %s: %s (debug: %s)",
                message.src.get_name(), err.message, debug,
            )
            self._running = False
            if self._loop and self._loop.is_running():
                self._loop.quit()

        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            logger.warning(
                "[GStreamer] WARNING from %s: %s (debug: %s)",
                message.src.get_name(), warn.message, debug,
            )

        elif msg_type == Gst.MessageType.EOS:
            logger.info("[GStreamer] End of stream received.")
            self._running = False
            if self._loop and self._loop.is_running():
                self._loop.quit()

        elif msg_type == Gst.MessageType.STATE_CHANGED:
            if message.src == self._pipeline:
                old, new, pending = message.parse_state_changed()
                logger.info(
                    "[GStreamer] Pipeline state: %s -> %s (pending: %s)",
                    old.value_nick.upper(),
                    new.value_nick.upper(),
                    pending.value_nick.upper(),
                )

        return True  # keep watching

    # ----- appsink new-sample callback ------------------------------------

    def _on_new_sample(self, appsink) -> Gst.FlowReturn:
        """
        Called on GStreamer's internal thread when a new frame is available.

        Pulls the buffer, extracts width/height from negotiated caps, and
        copies the raw data into a numpy array. Hands it off to the main
        thread via ``self._frame_queue``.
        """
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        caps = sample.get_caps()
        structure = caps.get_structure(0)

        # Read width/height from negotiated caps — handles mid-stream
        # renegotiation (some cameras do this on reconnect)
        _, width  = structure.get_int("width")
        _, height = structure.get_int("height")

        buf = sample.get_buffer()
        result, map_info = buf.map(Gst.MapFlags.READ)
        if not result:
            return Gst.FlowReturn.OK

        try:
            # Convert raw buffer to numpy array (BGR format as per caps filter)
            frame = np.frombuffer(map_info.data, dtype=np.uint8).copy()
            frame = frame.reshape((height, width, 3))

            self._width  = width
            self._height = height
            self._frame_count += 1

            if self._frame_count % 100 == 0:
                logger.info(
                    "[GStreamer] Frame #%d received, shape=%dx%d",
                    self._frame_count, width, height,
                )

            # Thread-safe handoff — drop oldest frame if queue is full
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self._frame_queue.put_nowait(frame)

        finally:
            buf.unmap(map_info)

        return Gst.FlowReturn.OK

    # ----- GLib MainLoop thread -------------------------------------------

    def _run_main_loop(self) -> None:
        """Run GLib.MainLoop on a daemon thread."""
        try:
            self._loop = GLib.MainLoop()
            self._loop.run()
        except Exception as exc:
            logger.error("[GStreamer] MainLoop crashed: %s", exc)
        finally:
            self._running = False

    # ----- Public API (cv2.VideoCapture compatible) -----------------------

    def start(self, timeout_sec: float = 20.0) -> bool:
        """
        Build and start the GStreamer pipeline.

        Returns ``True`` if the pipeline reaches ``PLAYING`` state within
        *timeout_sec* seconds.
        """
        pipeline_str = self._build_pipeline_string()
        logger.info("[GStreamer] Pipeline: %s", pipeline_str)

        try:
            self._pipeline = Gst.parse_launch(pipeline_str)
        except GLib.Error as exc:
            logger.error("[GStreamer] Pipeline parse failed: %s", exc)
            return False

        # Get the appsink and connect the new-sample signal
        self._appsink = self._pipeline.get_by_name("sink")
        if self._appsink is None:
            logger.error("[GStreamer] Could not find 'sink' element in pipeline")
            return False

        self._appsink.connect("new-sample", self._on_new_sample)

        # Watch bus for errors, warnings, state changes
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        # Start the GLib main loop on a daemon thread
        self._loop_thread = threading.Thread(
            target=self._run_main_loop, daemon=True, name="gst-mainloop"
        )
        self._loop_thread.start()

        # Set pipeline to PLAYING
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.error("[GStreamer] Failed to set pipeline to PLAYING state")
            self.release()
            return False

        # Wait for the pipeline to actually reach PLAYING (or fail)
        timeout_ns = int(timeout_sec * 1e9)
        state_ret, current, pending = self._pipeline.get_state(timeout_ns)

        if state_ret == Gst.StateChangeReturn.SUCCESS:
            logger.info(
                "[GStreamer] Pipeline reached PLAYING state successfully"
            )
            self._running = True
            return True
        elif state_ret == Gst.StateChangeReturn.ASYNC:
            # Still transitioning — give it a bit more time by checking
            # if we start receiving frames
            logger.info("[GStreamer] Pipeline state change is async, waiting for frames...")
            try:
                self._frame_queue.get(timeout=timeout_sec)
                # Put the frame back
                self._frame_queue.put_nowait(
                    self._frame_queue.get_nowait()
                    if not self._frame_queue.empty()
                    else np.zeros((1, 1, 3), dtype=np.uint8)
                )
                self._running = True
                logger.info("[GStreamer] Frames are flowing, pipeline is operational")
                return True
            except queue.Empty:
                logger.error("[GStreamer] Timeout: no frames received within %ss", timeout_sec)
                self.release()
                return False
        else:
            logger.error("[GStreamer] Pipeline failed to reach PLAYING: %s", state_ret)
            self.release()
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Read one frame — same interface as ``cv2.VideoCapture.read()``.

        Returns ``(True, frame)`` on success, ``(False, None)`` on failure.
        """
        if not self._running:
            return False, None

        try:
            frame = self._frame_queue.get(timeout=2.0)
            return True, frame
        except queue.Empty:
            return False, None

    def isOpened(self) -> bool:
        """Return True if the pipeline is running and producing frames."""
        return self._running

    def release(self) -> None:
        """
        Gracefully shut down the GStreamer pipeline.

        Sets pipeline state to NULL explicitly — skipping this can leave
        the RTSP session open on the camera side and block reconnection
        until the camera's own timeout expires.
        """
        self._running = False

        if self._pipeline is not None:
            logger.info("[GStreamer] Shutting down pipeline -> NULL state")
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        if self._loop is not None and self._loop.is_running():
            self._loop.quit()
            self._loop = None

        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5.0)
            self._loop_thread = None

        # Drain the frame queue
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

        logger.info("[GStreamer] Pipeline released. Total frames captured: %d", self._frame_count)

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def resolution(self) -> Tuple[int, int]:
        return self._width, self._height
