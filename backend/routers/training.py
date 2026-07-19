from __future__ import annotations
import os
import re
import shutil
import time
import logging
import threading
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import pydantic

from database import get_db
from models import UserRole, Camera
from auth import require_bearer_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["training"])

# ---------------------------------------------------------------------------
# Directories & Constants
# ---------------------------------------------------------------------------
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "training_data")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
LABELS_DIR = os.path.join(BASE_DIR, "labels")
SPLIT_DIR = os.path.join(BASE_DIR, "split")

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LABELS_DIR, exist_ok=True)

STREAM_BASE_URL = os.getenv("STREAM_BASE_URL", "http://localhost:8001")
MIN_LABELED_IMAGES = int(os.getenv("VCC_MIN_LABELED_IMAGES", "5"))
VCC_AUTO_TRAIN_THRESHOLD = int(os.getenv("VCC_AUTO_TRAIN_THRESHOLD", "50"))


# ---------------------------------------------------------------------------
# Global Training State
# ---------------------------------------------------------------------------
class TrainingState:
    def __init__(self):
        self.status = "idle"  # idle | training | complete | failed
        self.current_epoch = 0
        self.total_epochs = 0
        self.metrics: Dict[str, float] = {}
        self.logs: List[str] = []
        self.cancel_requested = False
        self.new_model_name: Optional[str] = None
        self._lock = threading.Lock()

_state = TrainingState()

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class BoundingBox(pydantic.BaseModel):
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float

class LabelData(pydantic.BaseModel):
    boxes: List[BoundingBox]

class ImageInfo(pydantic.BaseModel):
    filename: str
    labeled: bool
    timestamp: float

class TrainingStatus(pydantic.BaseModel):
    status: str
    current_epoch: int
    total_epochs: int
    metrics: Dict[str, float]
    logs: List[str]
    new_model_name: Optional[str] = None

class TrainRequest(pydantic.BaseModel):
    epochs: int = 10
    batch_size: int = 8
    force: bool = False

class LabelClass(pydantic.BaseModel):
    id: int
    name: str
    color: str

DEFAULT_CLASSES = [
    {"id": 0, "name": "car", "color": "border-[#00d4ff] text-[#00d4ff] bg-[#00d4ff]/10"},
    {"id": 1, "name": "motorcycle", "color": "border-[#7c3aed] text-[#7c3aed] bg-[#7c3aed]/10"},
    {"id": 2, "name": "bus", "color": "border-[#10b981] text-[#10b981] bg-[#10b981]/10"},
    {"id": 3, "name": "truck", "color": "border-[#f59e0b] text-[#f59e0b] bg-[#f59e0b]/10"},
    {"id": 4, "name": "bicycle", "color": "border-[#f97316] text-[#f97316] bg-[#f97316]/10"}
]

# ---------------------------------------------------------------------------
# Bounding Box Coordinates Helper
# ---------------------------------------------------------------------------
# Note: YOLO format: class_id x_center y_center width height (all float 0-1)
# Bounding box values are normalized against image width and height.

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/labels", response_model=List[LabelClass], summary="Get active training label classes")
async def get_training_labels(db: AsyncSession = Depends(get_db)):
    """Retrieve active list of custom label classes, falling back to default 5 classes if unset."""
    import json
    from models import SystemSetting
    from sqlalchemy import select as sa_select
    try:
        res = await db.execute(sa_select(SystemSetting).where(SystemSetting.key == "training_labels"))
        row = res.scalar_one_or_none()
        if row:
            return json.loads(row.value)
    except Exception as e:
        logger.error("Failed to read training labels: %s", e)
    return DEFAULT_CLASSES

@router.post("/labels", response_model=List[LabelClass], summary="Update training label classes (Admin Only)")
async def update_training_labels(
    body: List[LabelClass],
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Save the updated list of custom labels. Requires admin role."""
    role = token.get("role")
    if role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can modify training label classes",
        )
    import json
    from models import SystemSetting
    from sqlalchemy import select as sa_select
    
    serialized = json.dumps([c.model_dump() for c in body])
    try:
        res = await db.execute(sa_select(SystemSetting).where(SystemSetting.key == "training_labels"))
        row = res.scalar_one_or_none()
        if row:
            row.value = serialized
        else:
            db.add(SystemSetting(key="training_labels", value=serialized))
        await db.commit()
    except Exception as e:
        logger.error("Failed to update training labels: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
        
    return body

@router.post("/capture", response_model=Dict[str, Any], summary="Capture frame from live camera stream")
async def capture_frame(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Grabs a frame from the live stream. Peeks at the frame without consuming it."""
    # Build URL to streamer snapshot endpoint
    url = f"{STREAM_BASE_URL}/snapshot/{camera_id}"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=5.0)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Camera snapshot endpoint returned status {response.status_code}"
                )
            
            if response.headers.get("X-Placeholder") == "true":
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Camera stream is offline or initializing. No frames available yet."
                )
            
            # Save the frame image
            timestamp = int(time.time())
            filename = f"img_{timestamp}.jpg"
            filepath = os.path.join(IMAGES_DIR, filename)
            
            with open(filepath, "wb") as f:
                f.write(response.content)
                
            return {
                "status": "ok",
                "filename": filename,
                "timestamp": timestamp,
                "message": "Frame captured successfully"
            }
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach detection streamer snapshot endpoint: {exc}"
        )


async def _capture_single(camera_id: str) -> Dict[str, Any]:
    """Internal helper: capture one frame from given camera_id. Returns result dict or error dict."""
    url = f"{STREAM_BASE_URL}/snapshot/{camera_id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=5.0)
            if response.status_code != 200:
                return {"camera_id": camera_id, "status": "error", "detail": f"Snapshot returned {response.status_code}"}
            if response.headers.get("X-Placeholder") == "true":
                return {"camera_id": camera_id, "status": "offline", "detail": "Camera stream offline"}
            timestamp = int(time.time())
            filename = f"img_{timestamp}_{camera_id}.jpg"
            filepath = os.path.join(IMAGES_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(response.content)
            return {"camera_id": camera_id, "status": "ok", "filename": filename}
    except httpx.RequestError as exc:
        return {"camera_id": camera_id, "status": "error", "detail": str(exc)}


@router.post("/auto-capture", response_model=Dict[str, Any], summary="Auto-capture frames from all cameras")
async def auto_capture(
    camera_id: Optional[str] = Query(None, description="Specific camera ID; if omitted, captures from all cameras"),
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Automatically capture one frame from all cameras (or a specific one).
    Called by the frontend on a timed interval for hands-free dataset collection."""
    from sqlalchemy import select as sa_select
    
    if camera_id:
        camera_ids = [str(camera_id)]
    else:
        # Fetch all camera IDs from DB — completely isolated from training logic
        result = await db.execute(sa_select(Camera.id))
        camera_ids = [str(row[0]) for row in result.all()]
    
    if not camera_ids:
        return {"status": "ok", "captured": 0, "results": [], "message": "No cameras found"}
    
    results = []
    for cid in camera_ids:
        result = await _capture_single(cid)
        results.append(result)
    
    captured = sum(1 for r in results if r["status"] == "ok")
    return {
        "status": "ok",
        "captured": captured,
        "total": len(camera_ids),
        "results": results,
        "message": f"Auto-captured {captured}/{len(camera_ids)} frames successfully"
    }

@router.get("/images", response_model=List[ImageInfo], summary="List all captured training images")
async def list_images(token: dict = Depends(require_bearer_token)):
    """Return a list of all captured images and their label status."""
    images = []
    for f in os.listdir(IMAGES_DIR):
        if f.endswith(".jpg"):
            base = os.path.splitext(f)[0]
            label_file = os.path.join(LABELS_DIR, f"{base}.txt")
            labeled = os.path.exists(label_file) and os.path.getsize(label_file) > 0
            filepath = os.path.join(IMAGES_DIR, f)
            images.append(ImageInfo(
                filename=f,
                labeled=labeled,
                timestamp=os.path.getmtime(filepath)
            ))
            
    # Sort by timestamp descending
    images.sort(key=lambda x: x.timestamp, reverse=True)
    return images


@router.delete("/images/{filename}", response_model=Dict[str, Any], summary="Delete a training image and its label")
async def delete_image(
    filename: str,
    token: dict = Depends(require_bearer_token),
):
    """Delete a captured training image and its corresponding label file."""
    validate_filename(filename)
    filepath = os.path.join(IMAGES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    os.remove(filepath)
    # Also remove label if exists
    base = os.path.splitext(filename)[0]
    label_path = os.path.join(LABELS_DIR, f"{base}.txt")
    if os.path.exists(label_path):
        os.remove(label_path)
    return {"status": "ok", "message": f"Deleted {filename}"}


@router.delete("/images", response_model=Dict[str, Any], summary="Delete all training images and labels")
async def delete_all_images(
    token: dict = Depends(require_bearer_token),
):
    """Delete ALL captured training images and their labels. Admin only."""
    role = token.get("role")
    if role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can delete all training images"
        )
    deleted = 0
    for f in os.listdir(IMAGES_DIR):
        if f.endswith(".jpg"):
            os.remove(os.path.join(IMAGES_DIR, f))
            deleted += 1
    for f in os.listdir(LABELS_DIR):
        if f.endswith(".txt"):
            os.remove(os.path.join(LABELS_DIR, f))
    return {"status": "ok", "deleted": deleted, "message": f"Deleted {deleted} images and all labels"}


def validate_filename(filename: str) -> str:
    """Strictly validate filename against img_<timestamp>.jpg or img_<timestamp>_<cameraid>.jpg patterns."""
    if not re.match(r"^img_\d+(_\d+)?\.jpg$", filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename format. Expected img_<timestamp>.jpg or img_<timestamp>_<cameraid>.jpg"
        )
    return filename

@router.get("/images/{filename}", summary="Get captured image file")
async def get_image(filename: str):
    """Serve the raw JPG image file. Strictly validated to prevent traversal."""
    validate_filename(filename)
    filepath = os.path.join(IMAGES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found")
    return FileResponse(filepath)

@router.get("/images/{filename}/label", response_model=LabelData, summary="Get bounding box annotations")
async def get_label(filename: str, token: dict = Depends(require_bearer_token)):
    """Serve annotations for an image. Strictly validated."""
    validate_filename(filename)
    base = os.path.splitext(filename)[0]
    label_path = os.path.join(LABELS_DIR, f"{base}.txt")
    
    boxes = []
    if os.path.exists(label_path):
        try:
            with open(label_path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 5:
                        boxes.append(BoundingBox(
                            class_id=int(parts[0]),
                            x_center=float(parts[1]),
                            y_center=float(parts[2]),
                            width=float(parts[3]),
                            height=float(parts[4])
                        ))
        except Exception as e:
            logger.error("Failed to read label txt file: %s", e)
            
    return LabelData(boxes=boxes)

@router.post("/images/{filename}/label", response_model=Dict[str, Any], summary="Save bounding box annotations")
async def save_label(
    filename: str,
    body: LabelData,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Save bounding box annotations to label text file. Strictly validated."""
    validate_filename(filename)
    base = os.path.splitext(filename)[0]
    label_path = os.path.join(LABELS_DIR, f"{base}.txt")
    
    try:
        with open(label_path, "w") as f:
            for box in body.boxes:
                # YOLO format: class x_center y_center width height
                f.write(f"{box.class_id} {box.x_center:.6f} {box.y_center:.6f} {box.width:.6f} {box.height:.6f}\n")
                
        # ---- Automatic training check ----------------------------------------
        all_images = [file for file in os.listdir(IMAGES_DIR) if file.endswith(".jpg")]
        labeled_count = 0
        for f in all_images:
            b = os.path.splitext(f)[0]
            lbl = os.path.join(LABELS_DIR, f"{b}.txt")
            if os.path.exists(lbl) and os.path.getsize(lbl) > 0:
                labeled_count += 1
                
        if labeled_count >= VCC_AUTO_TRAIN_THRESHOLD:
            # Check concurrency
            is_idle = False
            with _state._lock:
                if _state.status != "training":
                    is_idle = True
            
            if is_idle:
                logger.info("Labeled images threshold reached (%d/%d). Auto-triggering model training...", labeled_count, VCC_AUTO_TRAIN_THRESHOLD)
                # Run the helper function asynchronously (fire-and-forget)
                asyncio.create_task(_trigger_training_job_impl(db, epochs=10, batch_size=8, force=True, triggered_by="auto_trigger"))

        return {"status": "ok", "message": "Annotations saved successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write label file: {e}"
        )


# ---------------------------------------------------------------------------
# Training Engine thread
# ---------------------------------------------------------------------------
def run_yolo_train(epochs: int, batch_size: int, data_yaml_path: str, new_model_path: str, versioned_name: str):
    from ultralytics import YOLO
    import torch
    
    try:
        # Load lightweight base model for fast CPU/GPU fine-tuning
        base_model_name = os.getenv("VCC_TRAIN_BASE_MODEL", "yolo11n.pt")
        model = YOLO(base_model_name)
        
        # Add custom callbacks to report stats & handle cancellation
        def on_train_epoch_end(trainer):
            with _state._lock:
                _state.current_epoch = trainer.epoch + 1
                # Grab metrics
                loss = float(trainer.loss.item() if hasattr(trainer.loss, "item") else trainer.loss)
                _state.metrics = {"loss": loss}
                _state.logs.append(f"Epoch {_state.current_epoch}/{_state.total_epochs} completed. Loss: {loss:.4f}")
                
        def on_train_batch_end(trainer):
            if _state.cancel_requested:
                raise RuntimeError("TRAINING_CANCELLED")
                
        model.add_callback("on_train_epoch_end", on_train_epoch_end)
        model.add_callback("on_train_batch_end", on_train_batch_end)
        
        # Determine optimal compute device & worker threads
        device = 0 if torch.cuda.is_available() else "cpu"
        num_workers = min(4, max(1, (os.cpu_count() or 2) // 2))
        imgsz = int(os.getenv("VCC_TRAIN_IMGSZ", "480"))
        
        _state.logs.append(f"Initializing {base_model_name} training on device='{device}' (imgsz={imgsz}, workers={num_workers})...")
        
        # Fast Train
        model.train(
            data=data_yaml_path,
            epochs=epochs,
            imgsz=imgsz,
            batch=batch_size,
            device=device,
            workers=num_workers,
            plots=False,
            verbose=False
        )
        
        # Copy weights
        best_weights = os.path.join("runs", "detect", "train", "weights", "best.pt")
        if os.path.exists(best_weights):
            shutil.copy(best_weights, new_model_path)
            _state.status = "complete"
            _state.new_model_name = versioned_name
            _state.logs.append(f"SUCCESS: Training complete! New model saved to: {versioned_name}")
        else:
            raise FileNotFoundError("Could not locate trained weights best.pt file")
            
    except Exception as e:
        if "TRAINING_CANCELLED" in str(e):
            _state.status = "cancelled"
            _state.logs.append("CANCELLED: Training cancelled by user.")
        else:
            _state.status = "failed"
            _state.logs.append(f"ERROR: Training failed: {e}")
            logger.error("YOLO Training failed: %s", e)
    finally:
        # Cleanup runs directory safely
        try:
            if os.path.exists("runs"):
                shutil.rmtree("runs", ignore_errors=True)
        except Exception:
            pass


async def _trigger_training_job_impl(
    db: AsyncSession,
    epochs: int,
    batch_size: int,
    force: bool,
    triggered_by: str,
) -> Dict[str, Any]:
    """Internal helper to execute dataset split preparation and launch the YOLO training thread."""
    # 1. Concurrency Check
    with _state._lock:
        if _state.status == "training":
            return {"status": "error", "message": "A training job is already running."}
            
    # 2. Count labeled images
    all_images = [f for f in os.listdir(IMAGES_DIR) if f.endswith(".jpg")]
    labeled_count = 0
    labeled_pairs = []
    
    for f in all_images:
        base = os.path.splitext(f)[0]
        lbl = f"{base}.txt"
        lbl_path = os.path.join(LABELS_DIR, lbl)
        if os.path.exists(lbl_path) and os.path.getsize(lbl_path) > 0:
            labeled_count += 1
            labeled_pairs.append((f, lbl))
            
    if labeled_count < MIN_LABELED_IMAGES:
        return {
            "status": "error",
            "message": f"Insufficient labeled images. Found {labeled_count}, but a minimum of {MIN_LABELED_IMAGES} is required."
        }
        
    # 3. GPU check
    try:
        import torch
        cuda_in_use = torch.cuda.is_available() and torch.cuda.memory_allocated() > 0
    except ImportError:
        cuda_in_use = False
        
    if cuda_in_use and not force:
        return {
            "status": "error",
            "message": "Live inference is currently using the GPU — stop cameras or wait before training, or pass force=true to override."
        }
        
    # 4. Prepare Split Directories
    shutil.rmtree(SPLIT_DIR, ignore_errors=True)
    
    split_images_train = os.path.join(SPLIT_DIR, "images", "train")
    split_images_val = os.path.join(SPLIT_DIR, "images", "val")
    split_labels_train = os.path.join(SPLIT_DIR, "labels", "train")
    split_labels_val = os.path.join(SPLIT_DIR, "labels", "val")
    
    os.makedirs(split_images_train, exist_ok=True)
    os.makedirs(split_images_val, exist_ok=True)
    os.makedirs(split_labels_train, exist_ok=True)
    os.makedirs(split_labels_val, exist_ok=True)
    
    # Split 80/20 train/val
    split_idx = int(len(labeled_pairs) * 0.8)
    # Ensure at least 1 image in validation
    if split_idx == len(labeled_pairs):
        split_idx = max(0, len(labeled_pairs) - 1)
        
    train_set = labeled_pairs[:split_idx]
    val_set = labeled_pairs[split_idx:]
    
    for img, lbl in train_set:
        shutil.copy(os.path.join(IMAGES_DIR, img), os.path.join(split_images_train, img))
        shutil.copy(os.path.join(LABELS_DIR, lbl), os.path.join(split_labels_train, lbl))
        
    for img, lbl in val_set:
        shutil.copy(os.path.join(IMAGES_DIR, img), os.path.join(split_images_val, img))
        shutil.copy(os.path.join(LABELS_DIR, lbl), os.path.join(split_labels_val, lbl))
        
    # Create data.yaml dynamically using DB custom labels list
    import json
    from models import SystemSetting
    from sqlalchemy import select as sa_select

    labels_list = DEFAULT_CLASSES
    try:
        res = await db.execute(sa_select(SystemSetting).where(SystemSetting.key == "training_labels"))
        row = res.scalar_one_or_none()
        if row:
            labels_list = json.loads(row.value)
    except Exception as e:
        logger.warning("Could not read dynamic labels from db during training split: %s", e)

    data_yaml = os.path.join(SPLIT_DIR, "data.yaml")
    split_dir_formatted = SPLIT_DIR.replace('\\', '/')
    with open(data_yaml, "w") as f:
        f.write(f"path: {split_dir_formatted}\n")
        f.write("train: images/train\n")
        f.write("val: images/val\n")
        f.write("names:\n")
        for lbl in labels_list:
            f.write(f"  {lbl['id']}: {lbl['name']}\n")

    # 5. Determine next versioned model file name
    v = 1
    # Check current directory files
    detection_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "detection")
    while os.path.exists(os.path.join(detection_dir, f"yolo11s_custom_v{v}.pt")):
        v += 1
    versioned_name = f"yolo11s_custom_v{v}.pt"
    new_model_path = os.path.join(detection_dir, versioned_name)

    # 6. Initialize State & Start Training Thread
    with _state._lock:
        _state.status = "training"
        _state.current_epoch = 0
        _state.total_epochs = epochs
        _state.metrics = {}
        _state.logs = ["Starting dataset preparation...", f"Total train files: {len(train_set)}, val files: {len(val_set)}"]
        _state.cancel_requested = False
        _state.new_model_name = None
        
        thread = threading.Thread(
            target=run_yolo_train,
            args=(epochs, batch_size, data_yaml, new_model_path, versioned_name)
        )
        _state.thread = thread
        thread.start()
        
    # Log audit event
    from audit import log_action
    await log_action(
        db,
        triggered_by,
        "TRAINING_STARTED",
        f"Model training started. Targets {epochs} epochs."
    )
    return {"status": "training", "message": "Training started successfully"}


@router.post("/train", response_model=Dict[str, Any], summary="Trigger custom YOLO training run (Admin Only)")
async def start_training(
    body: TrainRequest,
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Start custom model training. Requires admin role."""
    role = token.get("role")
    if role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can trigger model training",
        )
        
    res = await _trigger_training_job_impl(
        db=db,
        epochs=body.epochs,
        batch_size=body.batch_size,
        force=body.force,
        triggered_by=token.get("sub", "admin")
    )
    
    if res.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("message")
        )
        
    return res


@router.get("/status", response_model=TrainingStatus, summary="Get current training status")
async def get_training_status(token: dict = Depends(require_bearer_token)):
    """Return status of background training task."""
    with _state._lock:
        return TrainingStatus(
            status=_state.status,
            current_epoch=_state.current_epoch,
            total_epochs=_state.total_epochs,
            metrics=_state.metrics,
            logs=_state.logs,
            new_model_name=_state.new_model_name
        )

@router.post("/cancel", response_model=Dict[str, Any], summary="Cancel in-progress training job")
async def cancel_training(
    db: AsyncSession = Depends(get_db),
    token: dict = Depends(require_bearer_token),
):
    """Cancel in-progress training. Requires admin role."""
    role = token.get("role")
    if role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can cancel training",
        )
        
    with _state._lock:
        if _state.status != "training":
            return {"status": "ok", "message": "No active training job found to cancel"}
            
        _state.cancel_requested = True
        _state.status = "cancelled"
        _state.logs.append("Cancellation requested by administrator. Aborting...")
        
    # Log audit event safely without raising 500 errors if audit table commit fails
    try:
        from audit import log_action
        await log_action(
            db,
            token.get("sub", "admin"),
            "TRAINING_CANCELLED",
            "A training job was manually aborted by administrator."
        )
    except Exception as e:
        logger.warning("Could not record audit log for training cancellation: %s", e)
    
    return {"status": "ok", "message": "Cancellation command sent"}

