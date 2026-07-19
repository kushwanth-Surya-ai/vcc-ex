"""
Lifecycle tests for the isolated training subprocess (training_process.py).

Uses a trivial fake worker script instead of real YOLO, so these run without
ultralytics/torch. Covers: start -> progress -> complete, unexpected death ->
failed with stderr tail, and cancel -> terminated + cancelled status.
"""
from __future__ import annotations

import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import training_process as tp  # noqa: E402


def _write_fake_worker(tmp_path, body: str) -> str:
    """Create a stand-in for training_worker.py accepting the same CLI flags."""
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(f"""
        import argparse, json, sys, time
        p = argparse.ArgumentParser()
        p.add_argument("--epochs", type=int)
        p.add_argument("--batch", type=int)
        p.add_argument("--data")
        p.add_argument("--output")
        p.add_argument("--work-dir")
        args = p.parse_args()

        def emit(**kw):
            sys.stdout.write("@@VCC " + json.dumps(kw) + "\\n")
            sys.stdout.flush()

        {body}
    """))
    return str(script)


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test gets a clean TrainingState and the real worker path restored."""
    original_script = tp.WORKER_SCRIPT
    tp._state = tp.TrainingState()
    yield
    proc = tp._state.process
    if proc is not None and proc.poll() is None:
        tp.terminate_process(proc, grace=1)
    tp.WORKER_SCRIPT = original_script


def _start(tmp_path, body: str, epochs: int = 2, output_name: str = "model_v1.pt"):
    tp.WORKER_SCRIPT = _write_fake_worker(tmp_path, body)
    output = str(tmp_path / output_name)
    with tp._state._lock:
        tp._state.status = "training"
        tp._state.total_epochs = epochs
        tp._state.process = tp.spawn_training(
            epochs=epochs,
            batch_size=4,
            data_yaml_path=str(tmp_path / "data.yaml"),
            new_model_path=output,
            versioned_name=output_name,
        )
    return output


def _wait_for_status(*expected: str, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with tp._state._lock:
            status = tp._state.status
        if status in expected:
            return status
        time.sleep(0.05)
    with tp._state._lock:
        pytest.fail(f"timed out waiting for {expected}; status={tp._state.status} logs={tp._state.logs}")


def test_successful_run_streams_progress_and_completes(tmp_path):
    """Happy path: epoch events update state, weights land, status -> complete."""
    output = _start(tmp_path, """
        emit(event="start", base_model="fake.pt", device="cpu", imgsz=480, workers=2)
        print("plain log line from worker")
        for i in range(1, args.epochs + 1):
            emit(event="epoch", epoch=i, total=args.epochs, loss=1.0 / i)
        open(args.output, "wb").write(b"fake-weights")
        emit(event="complete", output=args.output)
        sys.exit(0)
    """)

    assert _wait_for_status("complete", "failed") == "complete"

    with tp._state._lock:
        assert tp._state.current_epoch == 2
        assert tp._state.metrics["loss"] == pytest.approx(0.5)
        assert tp._state.new_model_name == "model_v1.pt"
        assert tp._state.process is None
        logs = "\n".join(tp._state.logs)

    assert os.path.exists(output)
    # Structured events are rendered, and plain stdout is forwarded verbatim.
    assert "Epoch 1/2 completed. Loss: 1.0000" in logs
    assert "Epoch 2/2 completed. Loss: 0.5000" in logs
    assert "plain log line from worker" in logs
    assert "Initializing fake.pt training on device='cpu'" in logs
    assert "SUCCESS: Training complete!" in logs


def test_unexpected_death_marks_failed_with_stderr_tail(tmp_path):
    """Non-zero exit -> failed, and the stderr tail is surfaced to the user."""
    _start(tmp_path, """
        sys.stderr.write("CUDA out of memory\\n")
        sys.stderr.flush()
        sys.exit(3)
    """)

    assert _wait_for_status("failed", "complete") == "failed"

    with tp._state._lock:
        logs = "\n".join(tp._state.logs)
        assert tp._state.process is None
        assert tp._state.new_model_name is None

    assert "CUDA out of memory" in logs
    assert "ERROR: Training failed:" in logs


def test_exit_zero_without_weights_is_still_failed(tmp_path):
    """A clean exit that produced no weights must not be reported as success."""
    _start(tmp_path, """
        sys.exit(0)
    """)
    assert _wait_for_status("failed", "complete") == "failed"


def test_cancel_terminates_subprocess_and_settles_cancelled(tmp_path):
    """Cancel actually kills the child and the state settles on 'cancelled'."""
    _start(tmp_path, """
        emit(event="start", base_model="fake.pt", device="cpu", imgsz=480, workers=2)
        time.sleep(120)
    """)

    # Wait until the child is really up before cancelling.
    deadline = time.time() + 10
    while time.time() < deadline:
        with tp._state._lock:
            if any("Initializing" in line for line in tp._state.logs):
                break
        time.sleep(0.05)

    with tp._state._lock:
        proc = tp._state.process
        tp._state.cancel_requested = True
        tp._state.status = "cancelled"
    assert proc is not None and proc.poll() is None

    tp.terminate_process(proc, grace=5)

    assert proc.poll() is not None, "subprocess should be dead after terminate"
    assert _wait_for_status("cancelled") == "cancelled"

    with tp._state._lock:
        assert tp._state.process is None
        assert "CANCELLED: Training cancelled by user." in tp._state.logs


def test_kill_escalation_for_sigterm_resistant_child(tmp_path):
    """A child that ignores SIGTERM is escalated to SIGKILL after the grace period."""
    _start(tmp_path, """
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        emit(event="start", base_model="stubborn.pt", device="cpu", imgsz=480, workers=1)
        time.sleep(120)
    """)

    deadline = time.time() + 10
    while time.time() < deadline:
        with tp._state._lock:
            if any("Initializing" in line for line in tp._state.logs):
                break
        time.sleep(0.05)

    with tp._state._lock:
        proc = tp._state.process
        tp._state.cancel_requested = True
    assert proc is not None and proc.poll() is None

    started = time.time()
    tp.terminate_process(proc, grace=1)
    elapsed = time.time() - started

    assert proc.poll() is not None, "SIGTERM-ignoring child should have been killed"
    assert elapsed >= 1, "should have waited out the grace period before killing"


def test_shutdown_training_cleans_up_running_process(tmp_path):
    """A process still running at shutdown is terminated."""
    _start(tmp_path, """
        emit(event="start", base_model="fake.pt", device="cpu", imgsz=480, workers=1)
        time.sleep(120)
    """)

    deadline = time.time() + 10
    while time.time() < deadline:
        with tp._state._lock:
            if any("Initializing" in line for line in tp._state.logs):
                break
        time.sleep(0.05)

    with tp._state._lock:
        proc = tp._state.process
    assert proc is not None

    tp.shutdown_training(grace=5)
    assert proc.poll() is not None
    assert _wait_for_status("cancelled") == "cancelled"


def test_log_buffer_is_bounded(tmp_path, monkeypatch):
    """Streaming a chatty subprocess must not grow the log list without bound."""
    monkeypatch.setattr(tp, "TRAIN_LOG_LIMIT", 50)
    with tp._state._lock:
        for i in range(500):
            tp.append_log_locked(f"line {i}")
        assert len(tp._state.logs) == 50
        assert tp._state.logs[-1] == "line 499"
