from sqlalchemy.ext.asyncio import AsyncSession
from models import LoginLog, AuditLog
from datetime import datetime, timezone

async def log_login(db: AsyncSession, email: str, ip_address: str, success: bool) -> None:
    log = LoginLog(
        email=email,
        ip_address=ip_address or "unknown",
        success=success,
        timestamp=datetime.now(timezone.utc)
    )
    db.add(log)
    await db.commit()

async def log_action(db: AsyncSession, email: str, action: str, details: str = None) -> None:
    log = AuditLog(
        email=email,
        action=action,
        details=details,
        timestamp=datetime.now(timezone.utc)
    )
    db.add(log)
    await db.commit()
