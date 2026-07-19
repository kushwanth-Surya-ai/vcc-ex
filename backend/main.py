"""
main.py - FastAPI application entry point for the VCC backend.

Startup sequence:
  1. Validate all required environment variables (raises RuntimeError if any missing).
  2. Start APScheduler for materialized-view refresh.
  3. Mount CORS, SlowAPI middleware, and all routers.
  4. Expose a WebSocket endpoint at /ws with first-message JWT auth.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ---------------------------------------------------------------------------
# Required environment variable validation
# ---------------------------------------------------------------------------

_REQUIRED_ENV_VARS = (
    "DATABASE_URL",
    "JWT_SECRET",
    "SERVICE_API_KEY",
    "ALLOWED_ORIGINS",
)


def _validate_env() -> None:
    """Raise RuntimeError immediately if any required env var is missing."""
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Please copy .env.example to .env and fill in all values."
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: validate env, start scheduler, clean up on exit."""
    _validate_env()
    logger.info("Environment validated. Starting VCC backend...")

    # Auto-create tables (e.g. audit_logs and login_logs) on startup
    from database import engine
    from models import Base
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS latitude FLOAT"))
            await conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS longitude FLOAT"))
            await conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE cameras ADD COLUMN IF NOT EXISTS counting_line VARCHAR(255)"))
            logger.info("Database columns upgraded successfully.")
        except Exception as e:
            logger.warning("Could not execute alter table upgrade: %s", e)

        # Migrate old counting_line values to the new counting_lines table
        try:
            res = await conn.execute(text("SELECT id, name, counting_line FROM cameras WHERE counting_line IS NOT NULL"))
            rows = res.all()
            for row in rows:
                cam_id, cam_name, line_str = row
                # Check if this camera already has lines in counting_lines
                check_res = await conn.execute(
                    text("SELECT 1 FROM counting_lines WHERE camera_id = :cid"),
                    {"cid": cam_id}
                )
                if not check_res.first():
                    # Parse line coords "x1,y1,x2,y2"
                    parts = line_str.split(",")
                    if len(parts) == 4:
                        try:
                            x1, y1, x2, y2 = map(float, parts)
                            await conn.execute(
                                text(
                                    "INSERT INTO counting_lines (camera_id, name, x1, y1, x2, y2, lane_id, direction, color) "
                                    "VALUES (:cid, :name, :x1, :y1, :x2, :y2, 1, 'both', '#00d4ff')"
                                ),
                                {
                                    "cid": cam_id,
                                    "name": f"{cam_name} Line 1",
                                    "x1": x1,
                                    "y1": y1,
                                    "x2": x2,
                                    "y2": y2
                                }
                            )
                            logger.info("Migrated old counting_line for camera %s to counting_lines table", cam_id)
                        except ValueError:
                            pass
        except Exception as e:
            logger.warning("Could not migrate old counting_line values: %s", e)

            
        # Seed default settings
        try:
            res = await conn.execute(text("SELECT 1 FROM system_settings WHERE key = 'confidence_threshold'"))
            if not res.first():
                default_conf = os.getenv("VCC_CONF", "0.45")
                await conn.execute(
                    text("INSERT INTO system_settings (key, value) VALUES ('confidence_threshold', :val)"),
                    {"val": default_conf}
                )
                logger.info("Seeded default confidence_threshold: %s", default_conf)
        except Exception as e:
            logger.warning("Could not seed default settings: %s", e)

        # Seed default location if empty
        try:
            res = await conn.execute(text("SELECT 1 FROM locations LIMIT 1"))
            if not res.first():
                await conn.execute(
                    text("INSERT INTO locations (id, name, latitude, longitude) VALUES (1, 'Default Junction', 12.9716, 77.5946)")
                )
                try:
                    await conn.execute(text("SELECT setval('locations_id_seq', 1)"))
                except Exception:
                    pass
                logger.info("Seeded default location 'Default Junction' with ID 1")
        except Exception as e:
            logger.warning("Could not seed default location: %s", e)

        # Seed default admin user if empty
        try:
            res = await conn.execute(text("SELECT 1 FROM users LIMIT 1"))
            if not res.first():
                from auth import hash_password
                hashed_pw = hash_password("Admin1234!")
                await conn.execute(
                    text("INSERT INTO users (email, hashed_password, role) VALUES ('admin@vcc.local', :pw, 'admin')"),
                    {"pw": hashed_pw}
                )
                logger.info("Seeded default admin user 'admin@vcc.local'")
        except Exception as e:
            logger.warning("Could not seed default admin user: %s", e)
    logger.info("Database tables initialized.")

    # Import scheduler here (after env validation) to avoid import-time errors
    from scheduler import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("APScheduler started (MV refresh every %s min)", os.getenv("MV_REFRESH_INTERVAL_MINUTES", "5"))

    yield  # Application runs here

    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped.")

    # Close SQLAlchemy engine connection pool
    from database import engine
    await engine.dispose()
    logger.info("Database engine disposed.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Vehicle Counting & Classification API",
    version="1.0.0",
    description=(
        "Backend API for the VCC real-time vehicle counting and classification system. "
        "Provides event ingestion, analytics, alerting, and WebSocket streaming."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

from auth import limiter  # noqa: E402 (import after app definition is fine)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SlowAPI middleware must be added AFTER CORSMiddleware
app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from routers import health, analytics, settings, counting_lines  # noqa: E402
from routers import auth as auth_router  # noqa: E402
from routers import cameras, events, locations, users  # noqa: E402
from routers import alerts as alerts_router  # noqa: E402

app.include_router(health.router)
app.include_router(auth_router.router)
app.include_router(events.router)
app.include_router(cameras.router)
app.include_router(locations.router)
app.include_router(analytics.router)
app.include_router(alerts_router.router)
app.include_router(users.router)
app.include_router(settings.router)
app.include_router(counting_lines.router)



@app.get("/", include_in_schema=False)
def read_root():
    """Redirect users who accidentally hit the backend URL directly to the frontend."""
    return RedirectResponse(url="http://localhost:5173")

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

from websocket import manager  # noqa: E402


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Authenticated WebSocket endpoint for real-time event streaming.

    Auth protocol:
      1. Server sends {"type": "auth_required"}
      2. Client sends {"type": "auth", "token": "<access_jwt>"}
      3. If valid: connection registered; events/alerts streamed.
      4. If invalid or timeout (5 s): close(1008).
    """
    await websocket.accept()

    user_email = await manager.authenticate_ws(websocket)
    if user_email is None:
        # authenticate_ws already called websocket.close(1008)
        return

    await manager.connect(websocket, user_email)
    try:
        while True:
            # Keep the connection alive; client messages are ignored
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("WebSocket error for %s: %s", user_email, exc)
        manager.disconnect(websocket)
