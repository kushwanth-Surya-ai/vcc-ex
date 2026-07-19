"""
websocket.py – WebSocket connection manager with JWT auth handshake.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from auth import JWT_ALGORITHM, JWT_SECRET

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages authenticated WebSocket connections."""

    def __init__(self) -> None:
        self._connections: Dict[WebSocket, str] = {}

    async def authenticate_ws(self, websocket: WebSocket) -> Optional[str]:
        """First-message JWT auth handshake.

        1. Server sends  {"type": "auth_required"}
        2. Client must reply within 5 s with {"type": "auth", "token": "<jwt>"}
        3. Token is verified; on failure close(1008) and return None.
        """
        await websocket.send_json({"type": "auth_required"})

        try:
            raw = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("WebSocket auth timeout - closing 1008")
            await websocket.close(code=1008)
            return None
        except WebSocketDisconnect:
            logger.info("Client disconnected during WS auth handshake")
            return None
        except Exception as exc:
            logger.error("Unexpected WS auth error: %s", exc)
            await websocket.close(code=1008)
            return None

        if not isinstance(raw, dict) or raw.get("type") != "auth":
            logger.warning("WS auth: unexpected message %s", raw)
            await websocket.close(code=1008)
            return None

        token: str = raw.get("token", "")
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            if payload.get("type") != "access":
                raise JWTError("wrong token type")
            email: Optional[str] = payload.get("sub")
            if not email:
                raise JWTError("missing sub claim")
        except JWTError as exc:
            logger.warning("WS JWT verification failed: %s", exc)
            await websocket.close(code=1008)
            return None

        await websocket.send_json({"type": "auth_ok"})
        return email

    async def connect(self, websocket: WebSocket, user_email: str) -> None:
        """Register an authenticated WebSocket."""
        self._connections[websocket] = user_email
        logger.info("WS connected: %s (total=%d)", user_email, len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Deregister a WebSocket."""
        email = self._connections.pop(websocket, "<unknown>")
        logger.info("WS disconnected: %s (total=%d)", email, len(self._connections))

    async def broadcast(self, message: dict) -> None:
        """Send message to every connected client; prune dead connections."""
        dead: list[WebSocket] = []
        for ws in list(self._connections.keys()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


# Singleton shared across the application
manager = ConnectionManager()
