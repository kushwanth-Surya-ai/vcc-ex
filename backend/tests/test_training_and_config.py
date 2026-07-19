from __future__ import annotations
import os
import sys
import asyncio

# Seed Env variables before imports
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/vcc_db")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256-algorithm-padding-ok")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("SERVICE_API_KEY", "test-api-key-that-is-long-enough-32chars!")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import create_access_token, hash_password
from database import Base, get_db
from main import app
from training_app import app as training_app
from models import User, UserRole

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture(autouse=True)
async def setup_test_users():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestSessionLocal() as session:
        from sqlalchemy import select
        # Create an operator user if not exists
        res = await session.execute(select(User).where(User.email == "operator@vcc.local"))
        if not res.scalar_one_or_none():
            op_user = User(
                email="operator@vcc.local",
                hashed_password=hash_password("Operator1234!"),
                role=UserRole.operator.value
            )
            session.add(op_user)
            
        # Create an admin user if not exists
        res = await session.execute(select(User).where(User.email == "admin_test@vcc.local"))
        if not res.scalar_one_or_none():
            admin_user = User(
                email="admin_test@vcc.local",
                hashed_password=hash_password("Admin1234!"),
                role=UserRole.admin.value
            )
            session.add(admin_user)
        await session.commit()
    await test_engine.dispose()

@pytest_asyncio.fixture()
async def client() -> AsyncClient:
    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    await test_engine.dispose()

@pytest_asyncio.fixture()
async def training_client() -> AsyncClient:
    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session
    training_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=training_app), base_url="http://test") as ac:
        yield ac
    training_app.dependency_overrides.clear()
    await test_engine.dispose()

@pytest_asyncio.fixture()
async def admin_headers(client: AsyncClient) -> dict:
    resp = await client.post("/auth/login", json={"email": "admin_test@vcc.local", "password": "Admin1234!"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest_asyncio.fixture()
async def op_headers(client: AsyncClient) -> dict:
    resp = await client.post("/auth/login", json={"email": "operator@vcc.local", "password": "Operator1234!"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dynamic_config_get_post(client: AsyncClient, admin_headers: dict, op_headers: dict) -> None:
    # 1. Get default config
    resp = await client.get("/api/settings/config")
    assert resp.status_code == 200
    assert "confidence_threshold" in resp.json()
    
    # 2. Operator post should fail with 403
    resp = await client.post("/api/settings/config", json={"confidence_threshold": 0.65}, headers=op_headers)
    assert resp.status_code == 403
    
    # 3. Admin post should succeed and update threshold
    resp = await client.post("/api/settings/config", json={"confidence_threshold": 0.65}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["confidence_threshold"] == 0.65
    
    # 4. Get config should return new threshold
    resp = await client.get("/api/settings/config")
    assert resp.status_code == 200
    assert resp.json()["confidence_threshold"] == 0.65

@pytest.mark.asyncio
async def test_path_traversal_gating(training_client: AsyncClient, admin_headers: dict) -> None:
    # 1. Invalid filenames should return 400 Bad Request or 404 Not Found (safely blocked)
    invalid_paths = [
        "img_..%2F..%2F.env",
        "img_123.jpg%2Flabels",
        "random_filename.jpg",
        "img_abc.jpg",
        "%2Fetc%2Fpasswd",
        "C:%5CWindows%5Csystem32%5Ccmd.exe"
    ]
    for filename in invalid_paths:
        resp = await training_client.get(f"/api/training/images/{filename}", headers=admin_headers)
        assert resp.status_code in (400, 404)
        
        resp = await training_client.get(f"/api/training/images/{filename}/label", headers=admin_headers)
        assert resp.status_code in (400, 404)
        
        resp = await training_client.post(f"/api/training/images/{filename}/label", json={"boxes": []}, headers=admin_headers)
        assert resp.status_code in (400, 404)

@pytest.mark.asyncio
async def test_training_gating_and_validation(training_client: AsyncClient, admin_headers: dict, op_headers: dict) -> None:
    # 1. Non-admin should be rejected with 403
    resp = await training_client.post("/api/training/train", json={"epochs": 10}, headers=op_headers)
    assert resp.status_code == 403
    
    # 2. Admin should get 400 because labeled image count is below minimum (0)
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("routers.training.IMAGES_DIR", tmp_dir), \
             patch("routers.training.LABELS_DIR", tmp_dir):
            resp = await training_client.post("/api/training/train", json={"epochs": 10}, headers=admin_headers)
            assert resp.status_code == 400
            assert "Insufficient labeled images" in resp.json()["detail"]
