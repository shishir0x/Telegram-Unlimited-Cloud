"""
WebSocket Connection Manager
=============================
Manages active WebSocket connections per user and broadcasts sync events
to all connected browser clients belonging to the affected user.

When a mutation happens on either Local or Render:
  1. The mutation writes to the shared database (changelog + version bump).
  2. After commit, the server broadcasts a lightweight sync event via WebSocket.
  3. Connected clients receive the event and call GET /api/sync/changes?since=...
     to fetch the actual change details.

If WebSocket is unavailable (e.g. proxy doesn't support it), clients fall back
to polling /api/sync/status every 5-10 seconds.

Thread-safety: asyncio is single-threaded; dict operations are safe.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Set, Optional, Any

from fastapi import WebSocket

logger = logging.getLogger("websocket")


class WebSocketManager:
    """Singleton-style manager for per-user WebSocket connections."""

    def __init__(self):
        # user_id -> set of active WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Per-connection metadata for diagnostics
        self._conn_meta: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        if user_id not in self._connections:
            self._connections[user_id] = set()
        self._connections[user_id].add(websocket)
        self._conn_meta[websocket] = {
            "user_id": user_id,
            "connected_at": time.time(),
            "messages_sent": 0,
        }
        logger.info(
            "[WS] Client connected: user=%s total=%d",
            user_id,
            self.count_connections(),
        )

    async def disconnect(self, websocket: WebSocket, user_id: str) -> None:
        """Remove a WebSocket connection."""
        if user_id in self._connections:
            self._connections[user_id].discard(websocket)
            if not self._connections[user_id]:
                del self._connections[user_id]
        self._conn_meta.pop(websocket, None)
        logger.info(
            "[WS] Client disconnected: user=%s total=%d",
            user_id,
            self.count_connections(),
        )

    async def broadcast_to_user(self, user_id: str, event: Dict[str, Any]) -> int:
        """
        Sends a JSON event to all connected clients for the given user.
        Returns the number of clients successfully notified.
        Dead connections are automatically pruned.
        """
        conns = self._connections.get(user_id, set()).copy()
        if not conns:
            return 0

        payload = json.dumps(event, default=str)
        notified = 0
        dead: list[WebSocket] = []

        for ws in conns:
            try:
                await ws.send_text(payload)
                meta = self._conn_meta.get(ws)
                if meta:
                    meta["messages_sent"] = meta.get("messages_sent", 0) + 1
                notified += 1
            except Exception:
                dead.append(ws)

        # Prune dead connections
        for ws in dead:
            await self.disconnect(ws, user_id)

        return notified

    async def broadcast_all(self, event: Dict[str, Any]) -> int:
        """Broadcast to ALL connected clients (used for system-wide events)."""
        total = 0
        for user_id in list(self._connections.keys()):
            total += await self.broadcast_to_user(user_id, event)
        return total

    def count_connections(self) -> int:
        """Total number of active WebSocket connections."""
        return sum(len(conns) for conns in self._connections.values())

    def get_connected_users(self) -> list[str]:
        """Returns list of user IDs with at least one active connection."""
        return [uid for uid, conns in self._connections.items() if conns]

    def get_user_connection_count(self, user_id: str) -> int:
        """Returns number of active connections for a specific user."""
        return len(self._connections.get(user_id, set()))


# Module-level singleton
ws_manager = WebSocketManager()
