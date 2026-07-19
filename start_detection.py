import asyncio
import sys
import asyncpg
from dotenv import load_dotenv
import os

# Load backend/.env to get DATABASE_URL
load_dotenv("backend/.env")

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgres://")

sys.path.append('detection')
import config
from tracker import run_camera
from counter import LineCounter
from streamer import start_server

async def fetch_cameras():
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Fetch counting lines
        lines_rows = await conn.fetch(
            "SELECT id, camera_id, name, x1, y1, x2, y2, lane_id, direction, color FROM counting_lines"
        )
        cam_lines_map = {}
        for lr in lines_rows:
            cid = str(lr["camera_id"])
            if cid not in cam_lines_map:
                cam_lines_map[cid] = []
            cam_lines_map[cid].append({
                "id": lr["id"],
                "name": lr["name"],
                "x1": lr["x1"], "y1": lr["y1"], "x2": lr["x2"], "y2": lr["y2"],
                "lane_id": lr["lane_id"],
                "direction": lr["direction"],
                "color": lr["color"]
            })

        # Fetch cameras
        rows = await conn.fetch("SELECT id, name, rtsp_url, location_id, counting_line FROM cameras")
        cams = []
        for r in rows:
            url = r["rtsp_url"]
            if not url:
                url = "0" # fallback to webcam if no url
            cid_str = str(r["id"])
            cams.append({
                "camera_id": cid_str,
                "name": r["name"],
                "source": url,
                "location_id": r["location_id"],
                "lane_id": int(os.getenv("VCC_DEFAULT_LANE_ID", "1")),
                "direction": os.getenv("VCC_DEFAULT_DIRECTION", "both"),
                "line_y": float(os.getenv("VCC_DEFAULT_LINE_Y", "0.5")),
                "counting_line": r["counting_line"],
                "counting_lines": cam_lines_map.get(cid_str, [])
            })
        await conn.close()
        return cams
    except Exception as e:
        print(f"[SYSTEM] Database error fetching cameras: {e}")
        return []

async def monitor_cameras_loop(active_tasks, queues):
    print("[SYSTEM] Starting dynamic camera coordinator loop...")
    while True:
        try:
            cameras = await fetch_cameras()
            db_cameras_map = {cam["camera_id"]: cam for cam in cameras}

            # 1. Stop tasks for cameras that were deleted, modified, or crashed
            for camera_id in list(active_tasks.keys()):
                current_task = active_tasks[camera_id]
                if current_task.done():
                    exc = None
                    try:
                        exc = current_task.exception()
                    except asyncio.CancelledError:
                        pass
                    if exc:
                        print(f"[SYSTEM] Camera {camera_id} task crashed with exception: {exc}")
                    else:
                        print(f"[SYSTEM] Camera {camera_id} task exited.")
                    active_tasks.pop(camera_id)
                    queues.pop(camera_id, None)
                    continue

                if camera_id not in db_cameras_map:
                    print(f"[SYSTEM] Camera {camera_id} deleted. Stopping pipeline...")
                    task = active_tasks.pop(camera_id)
                    task.cancel()
                    queues.pop(camera_id, None)
                else:
                    # Check if source or configuration changed
                    running_config = current_task.vcc_config
                    db_config = db_cameras_map[camera_id]
                    if (
                        running_config["source"] != db_config["source"] or 
                        running_config.get("counting_line") != db_config.get("counting_line") or
                        running_config.get("counting_lines") != db_config.get("counting_lines")
                    ):
                        print(f"[SYSTEM] Camera {camera_id} configuration changed. Restarting pipeline...")
                        task = active_tasks.pop(camera_id)
                        task.cancel()
                        queues.pop(camera_id, None)


            # 2. Start tasks for new cameras
            for camera_id, cam in db_cameras_map.items():
                if camera_id not in active_tasks:
                    print(f"[SYSTEM] New camera {camera_id} ({cam['name']}) detected. Starting native pipeline...")
                    
                    # Create queue and counter
                    queues[camera_id] = asyncio.Queue(maxsize=config.FRAME_BUFFER_SIZE)
                    counter = LineCounter(
                        camera_id=camera_id,
                        line_y=cam["line_y"],
                        direction=cam["direction"],
                        counting_line=cam.get("counting_line"),
                        lines=cam.get("counting_lines", [])
                    )
                    
                    # Spawn task
                    task = asyncio.create_task(
                        run_camera(cam, counter, queues),
                        name=f"camera-{camera_id}"
                    )
                    task.vcc_config = cam  # Attach config to task for change-detection
                    active_tasks[camera_id] = task



        except Exception as e:
            print(f"[SYSTEM] Error in camera coordinator loop: {e}")
            
        await asyncio.sleep(5)

async def run():
    try:
        active_tasks = {}
        queues = {}
        
        # Start the HTTP streamer server
        print("[SYSTEM] Starting MJPEG Streamer Server (Port 8001)...")
        streamer_task = asyncio.create_task(start_server(queues))
        
        # Run the dynamic coordinator
        await monitor_cameras_loop(active_tasks, queues)
    except Exception as e:
        print(f"[SYSTEM] Critical error in detection main process: {e}")

if __name__ == "__main__":
    asyncio.run(run())
