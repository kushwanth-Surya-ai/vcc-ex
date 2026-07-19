"""
training_worker.py - Isolated YOLO training entrypoint.

Run as a SUBPROCESS by ``routers/training.py`` so that the heavyweight
ultralytics/torch workload never competes with the ASGI server for CPU, GPU or
the GIL, and so that a run can be genuinely cancelled (a thread stuck inside
``model.train()`` cannot be killed; a process can).

Usage::

    python -m training_worker --epochs 10 --batch 8 \
        --data /abs/path/data.yaml --output /abs/path/yolo11s_custom_v1.pt \
        --work-dir /abs/path/work

Protocol
--------
* Ordinary stdout/stderr is forwarded verbatim into the parent's log buffer and
  shows up in the Training Studio log terminal.
* Structured progress is emitted as single-line JSON prefixed with
  ``training_paths.EVENT_PREFIX``.
* Exit code 0 == success (weights written to ``--output``); non-zero == failure,
  with the reason on stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback

# Allow both `python -m training_worker` and `python backend/training_worker.py`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from training_paths import EVENT_PREFIX, TRAIN_BASE_MODEL, TRAIN_IMGSZ  # noqa: E402


def emit(**payload: object) -> None:
    """Write one structured event line for the parent process to parse."""
    sys.stdout.write(EVENT_PREFIX + json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated YOLO training worker")
    parser.add_argument("--epochs", type=int, required=True)
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--data", required=True, help="Absolute path to data.yaml")
    parser.add_argument("--output", required=True, help="Absolute destination path for best.pt")
    parser.add_argument("--work-dir", required=True, help="Explicit CWD for the run; runs/ is created here")
    parser.add_argument("--imgsz", type=int, default=TRAIN_IMGSZ)
    parser.add_argument("--base-model", default=TRAIN_BASE_MODEL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # CWD is set EXPLICITLY rather than inherited-and-assumed, so ultralytics'
    # runs/ tree is always created in a directory we own and may safely delete.
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)
    os.chdir(work_dir)

    runs_root = os.path.join(work_dir, "runs")
    project_dir = os.path.join(runs_root, "detect")

    try:
        from ultralytics import YOLO
        import torch

        # Determine optimal compute device & worker threads
        device = 0 if torch.cuda.is_available() else "cpu"
        num_workers = min(4, max(1, (os.cpu_count() or 2) // 2))

        emit(
            event="start",
            base_model=args.base_model,
            device=str(device),
            imgsz=args.imgsz,
            workers=num_workers,
            total_epochs=args.epochs,
        )

        model = YOLO(args.base_model)

        def on_train_epoch_end(trainer):
            try:
                loss = float(trainer.loss.item() if hasattr(trainer.loss, "item") else trainer.loss)
            except Exception:
                loss = 0.0
            emit(event="epoch", epoch=int(trainer.epoch) + 1, total=args.epochs, loss=loss)

        model.add_callback("on_train_epoch_end", on_train_epoch_end)

        model.train(
            data=args.data,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            workers=num_workers,
            plots=False,
            verbose=False,
            # Pin the output location instead of relying on the process CWD.
            project=project_dir,
            name="train",
            exist_ok=True,
        )

        best_weights = os.path.join(project_dir, "train", "weights", "best.pt")
        if not os.path.exists(best_weights):
            raise FileNotFoundError("Could not locate trained weights best.pt file")

        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        shutil.copy(best_weights, args.output)
        emit(event="complete", output=args.output)
        return 0

    except KeyboardInterrupt:
        sys.stderr.write("Training interrupted.\n")
        return 130
    except Exception as exc:  # noqa: BLE001 - surfaced to the parent via stderr
        sys.stderr.write(f"Training failed: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        # Scoped cleanup: only ever removes the runs/ tree inside OUR work dir.
        try:
            if os.path.isdir(runs_root):
                # Step out before deleting so the CWD is never a dangling dir.
                os.chdir(work_dir)
                shutil.rmtree(runs_root, ignore_errors=True)
        except Exception:
            pass
        sys.stdout.flush()
        sys.stderr.flush()


if __name__ == "__main__":
    sys.exit(main())
