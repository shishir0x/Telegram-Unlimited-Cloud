"""
Synchronization API Routes
===========================
Endpoints:
  GET  /api/sync/status          — current version, server time, last change
  GET  /api/sync/changes         — changes since a given version
  WS   /ws/sync                  — real-time push notifications

All sync endpoints enforce authentication via the existing session cookie
(require_auth dependency from utils.auth).
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from utils.auth import require_auth, Session, validate_session, get_client_ip, SESSION_COOKIE_NAME
from utils.sync import SyncService, broadcast_sync_event

logger = logging.getLogger("sync")

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def sync_status(request: Request, _auth: Session = Depends(require_auth)):
    """
    Returns the current synchronization status:
    - version: current global sync version (monotonic counter)
    - server_time: current server UTC timestamp
    - last_change: most recent changelog entry for this user
    """
    # Single-admin system: always use "admin" as the user_id
    status = SyncService.get_status(user_id="admin")
    return JSONResponse({
        "status": "ok",
        **status,
    })


@router.get("/changes")
async def sync_changes(
    request: Request,
    since: int = Query(0, description="Version number to get changes since"),
    limit: int = Query(500, ge=1, le=2000, description="Max changes to return"),
    _auth: Session = Depends(require_auth),
):
    """
    Returns all changes with version > since for the authenticated user.
    The client uses this to catch up after being offline or reconnecting.

    Response:
    {
        "current_version": 127,
        "changes": [
            {
                "change_id": 1,
                "version": 124,
                "entity_type": "file",
                "entity_id": "ABC123",
                "operation": "FILE_CREATED",
                "created_at": "..."
            },
            ...
        ]
    }
    """
    # Single-admin system: always use "admin" as the user_id
    result = SyncService.get_changes_since(
        user_id="admin",
        since_version=since,
        limit=limit,
    )
    return JSONResponse({
        "status": "ok",
        **result,
    })


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------

async def _authenticate_ws(websocket: WebSocket) -> Optional[str]:
    """
    Authenticates a WebSocket connection using the session cookie.
    Returns user_id on success, None on failure.
    """
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        # Also try query parameter fallback for WebSocket clients
        token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=4001, reason="No session token provided")
        return None

    ip = websocket.client.host if websocket.client else "unknown"
    session = validate_session(token, ip=ip)
    if not session:
        await websocket.close(code=4003, reason="Invalid or expired session")
        return None

    # Single-admin system: always return "admin"
    return "admin"


@router.websocket("/ws")
async def websocket_sync(websocket: WebSocket):
    """
    WebSocket endpoint for real-time sync notifications.

    Authentication: session cookie or query param ?token=...
    Protocol:
      - Server sends: {"type": "sync_event", "operation": "...", "entity_id": "...", ...}
      - Client can send: {"type": "ping"} for keepalive
      - Server responds: {"type": "pong"}
    """
    from utils.websocket_manager import ws_manager

    user_id = await _authenticate_ws(websocket)
    if not user_id:
        return

    await ws_manager.connect(websocket, user_id)

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    break
                continue

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "check_version":
                # Client asks "is there anything newer than version X?"
                client_version = msg.get("version", 0)
                from database.repository import DatabaseRepository
                current = DatabaseRepository.get_current_version()
                await websocket.send_json({
                    "type": "version_check",
                    "current_version": current,
                    "has_changes": current > client_version,
                })

            # Unknown message types are silently ignored

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("[WS] Connection error for user=%s: %s", user_id, e)
    finally:
        await ws_manager.disconnect(websocket, user_id)


# ---------------------------------------------------------------------------
# Utility: register routes on the FastAPI app
# ---------------------------------------------------------------------------

def register_sync_routes(app):
    """Registers the sync API router and WebSocket endpoint on the FastAPI app."""
    app.include_router(router)
    logger.info("[SYNC] Sync API routes registered (/api/sync/status, /api/sync/changes, /ws/sync)")
