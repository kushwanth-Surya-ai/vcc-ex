"""
training_process.py - Lifecycle manager for the isolated training subprocess.

Owns the module-global :class:`TrainingState` and everything needed to spawn,
stream, cancel and reap the ``training_worker.py`` child process.

Deliberately stdlib-only (no fastapi / sqlalchemy / ultralytics) so that the
lifecycle is unit-testable against a trivial fake training script, and so the
parent process pays no ML import cost.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from typing import Dict, List, Optional

from training_paths import (
    BACKEND_DIR,
    EVENT_PREFIX,
    TRAIN_CANCEL_GRACE_SECONDS,
    TRAIN_LOG_LIMIT,
    TRAIN_WORK_DIR,
)

logger = logging.getLogger(__name__)

#: Path to the isolated training entrypoint executed as a subprocess.
WORKER_SCRIPT = os.path.join(BACKEND_DIR, "training_worker.py")


class TrainingState:
    def __init__(self):
        self.status = "idle"  # idle | training | complete | failed | cancelled
        self.current_epoch = 0
        self.total_epochs = 0
        self.metrics: Dict[str, float] = {}
        self.logs: List[str] = []
        self.cancel_requested = False
        self.new_model_name: Optional[str] = None
        # Subprocess bookkeeping. The parent owns all state; the child only
        # streams stdout/stderr back to us.
        self.process: Optional[subprocess.Popen] = None
        self.stderr_tail: deque = deque(maxlen=40)
        self._lock = threading.Lock()


_state = TrainingState()


# ---------------------------------------------------------------------------
# Log buffer
# ---------------------------------------------------------------------------
def append_log_locked(line: str) -> None:
    """Append one log line. Caller MUST hold ``_state._lock``."""
    _state.logs.append(line)
    if len(_state.logs) > TRAIN_LOG_LIMIT:
        # Keep the tail — the UI terminal is a live view, not an archive.
        del _state.logs[: len(_state.logs) - TRAIN_LOG_LIMIT]


def append_log(line: str) -> None:
    with _state._lock:
        append_log_locked(line)


# ---------------------------------------------------------------------------
# Child -> parent event handling
# ---------------------------------------------------------------------------
def _handle_event(payload: str) -> None:
    """Apply a structured ``@@VCC {...}`` event emitted by the worker."""
    try:
        event = json.loads(payload)
    except ValueError:
        append_log(payload)
        return

    kind = event.get("event")
    with _state._lock:
        if kind == "start":
            append_log_locked(
                "Initializing {model} training on device='{device}' "
                "(imgsz={imgsz}, workers={workers})...".format(
                    model=event.get("base_model"),
                    device=event.get("device"),
                    imgsz=event.get("imgsz"),
                    workers=event.get("workers"),
                )
            )
        elif kind == "epoch":
            _state.current_epoch = int(event.get("epoch", _state.current_epoch))
            total = int(event.get("total") or _state.total_epochs)
            loss = float(event.get("loss", 0.0))
            _state.metrics = {"loss": loss}
            append_log_locked(
                f"Epoch {_state.current_epoch}/{total} completed. Loss: {loss:.4f}"
            )
        elif kind == "complete":
            append_log_locked("Weights exported. Finalizing...")
        else:
            append_log_locked(payload)


def _pump_stream(stream, is_stderr: bool) -> None:
    """Reader thread: stream one subprocess pipe into the shared state."""
    try:
        for raw in iter(stream.readline, ""):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if not is_stderr and line.startswith(EVENT_PREFIX):
                _handle_event(line[len(EVENT_PREFIX):])
                continue
            with _state._lock:
                if is_stderr:
                    _state.stderr_tail.append(line)
                    append_log_locked(f"STDERR: {line}")
                else:
                    append_log_locked(line)
    except Exception as e:  # pragma: no cover - pipe torn down mid-read
        logger.debug("Training log pump ended: %s", e)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _await_process(
    proc: subprocess.Popen,
    versioned_name: str,
    output_path: str,
    pumps: List[threading.Thread],
) -> None:
    """Waiter thread: reap the subprocess and settle the terminal status."""
    returncode = proc.wait()
    for t in pumps:
        t.join(timeout=5)

    with _state._lock:
        if _state.process is not proc:
            # A newer run superseded this one; don't clobber its state.
            return
        _state.process = None
        cancelled = _state.cancel_requested

        if cancelled:
            _state.status = "cancelled"
            append_log_locked("CANCELLED: Training cancelled by user.")
        elif returncode == 0 and os.path.exists(output_path):
            _state.status = "complete"
            _state.new_model_name = versioned_name
            append_log_locked(
                f"SUCCESS: Training complete! New model saved to: {output_path}"
            )
        else:
            _state.status = "failed"
            tail = "; ".join(_state.stderr_tail) or f"worker exited with code {returncode}"
            append_log_locked(f"ERROR: Training failed: {tail}")
            logger.error("YOLO training subprocess failed (rc=%s): %s", returncode, tail)


# ---------------------------------------------------------------------------
# Spawn / terminate
# ---------------------------------------------------------------------------
def spawn_training(
    epochs: int,
    batch_size: int,
    data_yaml_path: str,
    new_model_path: str,
    versioned_name: str,
) -> subprocess.Popen:
    """Launch the training worker as an isolated subprocess with an explicit CWD."""
    os.makedirs(TRAIN_WORK_DIR, exist_ok=True)
    output_path = os.path.abspath(new_model_path)

    cmd = [
        sys.executable,
        "-u",  # unbuffered, so the live log terminal stays live
        WORKER_SCRIPT,
        "--epochs", str(epochs),
        "--batch", str(batch_size),
        "--data", os.path.abspath(data_yaml_path),
        "--output", output_path,
        "--work-dir", TRAIN_WORK_DIR,
    ]

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # Ensure `import training_paths` resolves regardless of how the parent started.
    env["PYTHONPATH"] = os.pathsep.join(
        [BACKEND_DIR] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )

    proc = subprocess.Popen(
        cmd,
        cwd=TRAIN_WORK_DIR,  # explicit, never inherited-and-assumed
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=env,
    )

    pumps = [
        threading.Thread(target=_pump_stream, args=(proc.stdout, False), daemon=True),
        threading.Thread(target=_pump_stream, args=(proc.stderr, True), daemon=True),
    ]
    for t in pumps:
        t.start()

    threading.Thread(
        target=_await_process,
        args=(proc, versioned_name, output_path, pumps),
        daemon=True,
    ).start()

    return proc


def terminate_process(
    proc: subprocess.Popen, grace: float = TRAIN_CANCEL_GRACE_SECONDS
) -> None:
    """Terminate, then hard-kill after ``grace`` seconds if still alive."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception as e:
        logger.warning("Could not terminate training subprocess: %s", e)
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        logger.warning("Training subprocess ignored SIGTERM; killing (pid=%s)", proc.pid)
    except Exception:
        return
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception as e:
        logger.error("Could not kill training subprocess: %s", e)


def shutdown_training(grace: float = 5.0) -> None:
    """Kill any in-flight training subprocess. Called on application shutdown."""
    with _state._lock:
        proc = _state.process
        if proc is not None:
            _state.cancel_requested = True
    if proc is not None:
        logger.info("Shutting down: terminating training subprocess (pid=%s)", proc.pid)
        terminate_process(proc, grace=grace)


atexit.register(shutdown_training)
