"""
run_all.py -- Unified launcher script for VCC application components.

Runs:
1. Backend API (FastAPI, Port 8000)
2. Detection Layer (GStreamer + YOLO, Port 8001)
3. Frontend Dev Server (Vite React UI, Port 5173)

Monitors all processes and handles graceful shutdown (Ctrl+C).
"""

import sys
import os
import subprocess
import threading
import time

def log_reader(pipe, prefix, color_code):
    """Reads lines from a subprocess pipe and logs them with a prefix in color."""
    reset = "\033[0m"
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            print(f"{color_code}{prefix}{reset} {line.strip()}")
    except Exception:
        pass

def main():
    # Locate virtualenv python
    venv_python = (
        os.path.join("backend", "venv", "Scripts", "python.exe")
        if os.name == "nt"
        else os.path.join("backend", "venv", "bin", "python")
    )
    if not os.path.isfile(venv_python):
        # Fall back to sys.executable if virtualenv not found
        venv_python = sys.executable

    # Check for GStreamer disable option in backend/.env
    disable_gst = False
    env_path = os.path.join("backend", ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    if line.strip().startswith("VCC_DISABLE_GST"):
                        parts = line.strip().split("=")
                        if len(parts) >= 2:
                            disable_gst = parts[1].strip().lower() == "true"
        except Exception:
            pass

    # Setup GStreamer path configuration for detection subprocess
    gst_root = r"C:\Users\Charan Galla\AppData\Local\Programs\gstreamer\1.0\msvc_x86_64"
    gst_bin = os.path.join(gst_root, "bin")
    gst_typelibs = os.path.join(gst_root, "lib", "girepository-1.0")
    local_bin = os.path.abspath(os.path.join("detection", "bin"))

    # Copy current environment and update paths for child processes
    env = os.environ.copy()
    env["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|buffer_size;10240000|max_delay;500000"
    if not disable_gst and os.path.isdir(gst_bin):
        env["PATH"] = gst_bin + os.pathsep + local_bin + os.pathsep + env.get("PATH", "")
        env["GI_TYPELIB_PATH"] = gst_typelibs



    processes = []

    # -- Color codes --
    cyan = "\033[36m"
    green = "\033[32m"
    yellow = "\033[33m"
    magenta = "\033[35m"
    red = "\033[31m"
    reset = "\033[0m"

    print("=" * 70)
    print(f"{cyan}VCC UNIFIED SERVICE LAUNCHER{reset}")
    print("=" * 70)

    try:
        # 1. Start Backend API
        print(f"{green}[SYSTEM] Starting Backend API (Port 8000)...{reset}")
        backend_proc = subprocess.Popen(
            [venv_python, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd="backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        processes.append(("BACKEND", backend_proc))
        
        threading.Thread(target=log_reader, args=(backend_proc.stdout, "[BACKEND]", green), daemon=True).start()
        threading.Thread(target=log_reader, args=(backend_proc.stderr, "[BACKEND]", green), daemon=True).start()

        # 1b. Start Training Dedicated Server
        print(f"{magenta}[SYSTEM] Starting Training Dedicated Server (Port 8002)...{reset}")
        training_proc = subprocess.Popen(
            [venv_python, "-m", "uvicorn", "training_app:app", "--host", "0.0.0.0", "--port", "8002"],
            cwd="backend",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        processes.append(("TRAINING", training_proc))
        
        threading.Thread(target=log_reader, args=(training_proc.stdout, "[TRAINING]", magenta), daemon=True).start()
        threading.Thread(target=log_reader, args=(training_proc.stderr, "[TRAINING]", magenta), daemon=True).start()

        # Give backend a moment to bind and initialize database/materialized views
        time.sleep(3)


        # 2. Start Detection Layer
        print(f"{yellow}[SYSTEM] Starting Detection Layer (GStreamer + YOLO, Port 8001)...{reset}")
        detection_proc = subprocess.Popen(
            [venv_python, "start_detection.py"],
            cwd=".",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        processes.append(("DETECTION", detection_proc))

        threading.Thread(target=log_reader, args=(detection_proc.stdout, "[DETECTION]", yellow), daemon=True).start()
        threading.Thread(target=log_reader, args=(detection_proc.stderr, "[DETECTION]", yellow), daemon=True).start()

        # 3. Start Frontend Dev Server
        print(f"{cyan}[SYSTEM] Starting Frontend Dev Server (Vite, Port 5173)...{reset}")
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        frontend_proc = subprocess.Popen(
            [npm_cmd, "run", "dev"],
            cwd="frontend",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        processes.append(("FRONTEND", frontend_proc))

        threading.Thread(target=log_reader, args=(frontend_proc.stdout, "[FRONTEND]", cyan), daemon=True).start()
        threading.Thread(target=log_reader, args=(frontend_proc.stderr, "[FRONTEND]", cyan), daemon=True).start()

        print(f"\n{green}[SYSTEM] All services running! Press Ctrl+C to terminate all services.{reset}\n")

        # Monitor loop
        while True:
            for name, proc in processes:
                code = proc.poll()
                if code is not None:
                    print(f"{red}[SYSTEM] {name} process exited unexpectedly with code {code}.{reset}")
                    raise KeyboardInterrupt
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n{red}[SYSTEM] Terminating all services...{reset}")
        for name, proc in processes:
            print(f"  Stopping {name}...")
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
                
        for _, proc in processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        print(f"{green}[SYSTEM] All services cleanly terminated.{reset}")

if __name__ == "__main__":
    main()
