"""
setup_and_run.py - One-command bootstrap + launch for the VCC system.

Run this on a fresh clone:

    python setup_and_run.py

It performs, in order, everything a clone cannot carry because it is
gitignored, then hands off to run_all.py:

  1. .env files          copied from .env.example, secrets generated
  2. backend/venv        created, requirements installed (shared with detection)
  3. database            created + migrated (cwd=backend, matching run_all.py)
  4. frontend deps       npm install
  5. model weights       checked and reported
  6. launch              run_all.py

Every step is idempotent - re-running skips work already done - and every
step fails loudly rather than letting a later stage produce a confusing
error (the failure mode that makes 'No module named uvicorn' look like a
Python problem when it is really a missing venv).
"""
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
VENV = ROOT / "backend" / "venv"
VENV_PY = VENV / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
NPM = "npm.cmd" if IS_WINDOWS else "npm"

# The root and backend templates use different placeholder wording for the same
# two secrets. Both must end up with identical values or detection cannot
# authenticate against the API.
JWT_PLACEHOLDERS = (
    "replace_with_a_long_secure_random_string",
    "replace-with-long-random-secret-min-64-chars",
)
KEY_PLACEHOLDERS = (
    "replace_with_a_long_secure_service_api_key",
    "replace-with-long-random-api-key-min-32-chars",
)


def say(msg):
    print(f"[SETUP] {msg}", flush=True)


def die(msg):
    print(f"\n[SETUP] FAILED: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd, cwd=None, what=""):
    result = subprocess.run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        die(f"{what} failed (exit {result.returncode}). Fix the error above, then re-run this script.")


def step_1_env_files():
    say("Step 1/6: environment files")
    for target, template in (
        (".env", ".env.example"),
        ("backend/.env", "backend/.env.example"),
        ("frontend/.env", "frontend/.env.example"),
    ):
        dst, src = ROOT / target, ROOT / template
        if dst.exists():
            say(f"  {target} already exists, left untouched")
            continue
        if not src.exists():
            die(f"missing template {template}")
        shutil.copyfile(src, dst)
        say(f"  created {target}")

    # One JWT secret and one service key, shared across root and backend.
    jwt_secret = secrets.token_hex(32)
    service_key = secrets.token_hex(32)
    substitutions = {p: jwt_secret for p in JWT_PLACEHOLDERS}
    substitutions.update({p: service_key for p in KEY_PLACEHOLDERS})

    for name in (".env", "backend/.env"):
        path = ROOT / name
        if not path.exists():
            continue
        text = original = path.read_text(encoding="utf-8")
        for placeholder, value in substitutions.items():
            text = text.replace(placeholder, value)
        if text != original:
            path.write_text(text, encoding="utf-8")
            say(f"  generated secrets in {name}")


def step_2_venv():
    say("Step 2/6: backend virtualenv (shared with detection)")
    if not VENV_PY.exists():
        say("  creating backend/venv ...")
        run([sys.executable, "-m", "venv", str(VENV)], what="venv creation")
    if not VENV_PY.exists():
        die(f"expected interpreter at {VENV_PY} but it does not exist")

    probe = subprocess.run(
        [str(VENV_PY), "-c", "import uvicorn, ultralytics"],
        capture_output=True,
    )
    if probe.returncode == 0:
        say("  dependencies already installed")
        return
    say("  installing requirements (pulls torch + ultralytics, takes a few minutes) ...")
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip"], what="pip upgrade")
    run(
        [str(VENV_PY), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")],
        what="dependency install",
    )


def step_3_database():
    # cwd=backend on purpose: run_all.py starts uvicorn with cwd="backend", so the
    # relative sqlite path ./vcc.db resolves to backend/vcc.db. Bootstrapping from
    # anywhere else would create a second, empty database the API never opens.
    say("Step 3/6: database")
    backend = ROOT / "backend"
    run([str(VENV_PY), "create_db.py"], cwd=backend, what="create_db.py")
    run([str(VENV_PY), "-m", "alembic", "upgrade", "head"], cwd=backend, what="alembic migration")
    say("  schema up to date (admin@vcc.local is seeded by the API on first start)")


def step_4_frontend():
    say("Step 4/6: frontend dependencies")
    if (ROOT / "frontend" / "node_modules").is_dir():
        say("  node_modules already present")
        return
    if shutil.which(NPM) is None:
        die("npm not found on PATH. Install Node 20+ and re-run.")
    run([NPM, "install"], cwd=ROOT / "frontend", what="npm install")


def step_5_model_weights():
    say("Step 5/6: model weights")
    primary = os.environ.get("VCC_MODEL_PATH", "yolo26s.pt")
    fallback = os.environ.get("VCC_FALLBACK_MODEL", "yolo11s.pt")
    if (ROOT / primary).exists():
        say(f"  found {primary}")
        return
    # *.pt is gitignored ("distribute out-of-band"), so a clone never has these.
    say(f"  WARNING: {primary} not found in the project root.")
    say(f"  Ultralytics can auto-download {fallback}, but {primary} is not a stock")
    say("  name - copy it in manually or detection will fall back / fail.")


def step_6_launch():
    say("Step 6/6: launching all services")
    print("=" * 70, flush=True)
    # subprocess rather than os.execv: on Windows execv detaches in a way that
    # returns the shell prompt early and breaks run_all.py's Ctrl+C teardown.
    result = subprocess.run([str(VENV_PY), str(ROOT / "run_all.py")], cwd=ROOT)
    sys.exit(result.returncode)


def main():
    print("=" * 70)
    print("VCC ONE-COMMAND SETUP + LAUNCH")
    print("=" * 70)
    os.chdir(ROOT)
    step_1_env_files()
    step_2_venv()
    step_3_database()
    step_4_frontend()
    step_5_model_weights()
    step_6_launch()


if __name__ == "__main__":
    main()
