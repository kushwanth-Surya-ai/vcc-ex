import asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
import sys
import os

# Add backend directory to sys.path so we can import models
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
from models import Camera, CameraStatus

async def main():
    engine = create_async_engine('postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/vcc_db')
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as db:
        try:
            cam = Camera(name='Test3', location_id=1, lane_count=1, rtsp_url='rtsp://test', status=CameraStatus.inactive)
            db.add(cam)
            await db.commit()
            print('SUCCESS')
        except Exception as e:
            print('DB EXCEPTION:', repr(e))
            import traceback
            traceback.print_exc()
        
asyncio.run(main())
