"""
training_app.py - Standalone FastAPI application dedicated to YOLO model training (runs on Port 8002).
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting VCC Training Dedicated Server on Port 8002...")
    yield
    # Kill any in-flight training subprocess before we go down.
    from routers.training import shutdown_training
    shutdown_training()
    # Close SQLAlchemy engine connection pool
    from database import engine
    await engine.dispose()
    logger.info("Training Server database engine disposed.")


app = FastAPI(
    title="VCC Dedicated Training Service API",
    version="1.0.0",
    description="Dedicated microservice for YOLO training operations, isolated from live feed pipeline.",
    lifespan=lifespan,
)

# CORS middleware
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the training router
from routers import training
app.include_router(training.router)
