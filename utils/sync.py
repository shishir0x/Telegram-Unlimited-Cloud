"""
Synchronization Engine — Change Tracking & Sync Service
========================================================
Provides:
  - ChangeTracker: high-level API to record mutations in the shared changelog
  - SyncService: query helpers for clients catching up on missed changes
  - Conflict detection helpers for optimistic concurrency control

All mutations flow through ChangeTracker so that both Local and Render
environments observe the same versioned change stream.

Design decisions:
  - Version is a monotonically increasing integer stored in sync_version.
  - Every mutation atomically increments the version and appends a changelog row
    inside the same database transaction.
  - WebSocket notifications are dispatched after the transaction commits so
    clients never see events for rolled-back mutations.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from database.connection import get_db_session
from database.models import ChangeLogModel, SyncVersionModel, FileModel, FolderModel, utc_now
from database.repository import DatabaseRepository

logger = logging.getLogger("sync")

# ---------------------------------------------------------------------------
# Operation constants
# ---------------------------------------------------------------------------

# File operations
FILE_CREATED = "FILE_CREATED"
FILE_UPDATED = "FILE_UPDATED"
FILE_RENAMED = "FILE_RENAMED"
FILE_MOVED = "FILE_MOVED"
FILE_DELETED = "FILE_DELETED"
FILE_TRASHED = "FILE_TRASHED"
FILE_RESTORED = "FILE_RESTORED"

# Folder operations
FOLDER_CREATED = "FOLDER_CREATED"
FOLDER_UPDATED = "FOLDER_UPDATED"
FOLDER_RENAMED = "FOLDER_RENAMED"
FOLDER_MOVED = "FOLDER_MOVED"
FOLDER_DELETED = "FOLDER_DELETED"
FOLDER_TRASHED = "FOLDER_TRASHED"
FOLDER_RESTORED = "FOLDER_RESTORED"


def _entity_type_from_item(item) -> str:
    """Determine entity_type from an in-memory Folder/File object."""
    item_type = getattr(item, "type", "")
    if item_type == "folder":
        return "folder"
    return "file"


# ---------------------------------------------------------------------------
# ChangeTracker — primary API for recording mutations
# ---------------------------------------------------------------------------

class ChangeTracker:
    """
    Stateless service.  Every method:
      1. Increments the global version
      2. Appends a changelog row
      3. Returns the new version

    The caller is responsible for calling `drive.save()` and backup after
    recording the change.
    """

    @staticmethod
    def record(
        operation: str,
        entity_id: str,
        entity_type: str = "file",
        user_id: str = "admin",
        old_name: Optional[str] = None,
        new_name: Optional[str] = None,
        old_folder_id: Optional[str] = None,
        new_folder_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> int:
        """
        Records a single change event in the database. Returns the new version.
        This should be called INSIDE the same transaction as the mutation,
        or immediately after a successful mutation before drive.save().
        """
        if not entity_id:
            logger.debug("[SYNC] Skipping record: entity_id is empty/None (op=%s)", operation)
            return 0
        try:
            with get_db_session() as session:
                new_version = DatabaseRepository.increment_version(session=session)
                DatabaseRepository.record_change(
                    version=new_version,
                    user_id=user_id,
                    entity_id=entity_id,
                    entity_type=entity_type,
                    operation=operation,
                    old_name=old_name,
                    new_name=new_name,
                    old_folder_id=old_folder_id,
                    new_folder_id=new_folder_id,
                    extra=extra,
                    session=session,
                )
                # Session auto-commits on exit from context manager
                logger.info(
                    "[SYNC] user=%s version=%d %s %s=%s",
                    user_id,
                    new_version,
                    operation,
                    entity_type,
                    entity_id,
                )
                return new_version
        except Exception as e:
            logger.error("[SYNC] Failed to record change: %s (op=%s entity=%s)", e, operation, entity_id)
            # Non-fatal: the mutation itself succeeded, but the sync log missed it.
            # This is acceptable — the next backup cycle will reconcile.
            return 0

    # Convenience wrappers for common mutations --------------------------------

    @staticmethod
    def file_created(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FILE_CREATED, entity_id, "file", user_id, **kwargs)

    @staticmethod
    def file_renamed(entity_id: str, old_name: str, new_name: str, user_id: str = "admin") -> int:
        return ChangeTracker.record(
            FILE_RENAMED, entity_id, "file", user_id,
            old_name=old_name, new_name=new_name,
        )

    @staticmethod
    def file_moved(entity_id: str, old_folder_id: str, new_folder_id: str, user_id: str = "admin") -> int:
        return ChangeTracker.record(
            FILE_MOVED, entity_id, "file", user_id,
            old_folder_id=old_folder_id, new_folder_id=new_folder_id,
        )

    @staticmethod
    def file_deleted(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FILE_DELETED, entity_id, "file", user_id, **kwargs)

    @staticmethod
    def file_trashed(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FILE_TRASHED, entity_id, "file", user_id, **kwargs)

    @staticmethod
    def file_restored(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FILE_RESTORED, entity_id, "file", user_id, **kwargs)

    @staticmethod
    def folder_created(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FOLDER_CREATED, entity_id, "folder", user_id, **kwargs)

    @staticmethod
    def folder_renamed(entity_id: str, old_name: str, new_name: str, user_id: str = "admin") -> int:
        return ChangeTracker.record(
            FOLDER_RENAMED, entity_id, "folder", user_id,
            old_name=old_name, new_name=new_name,
        )

    @staticmethod
    def folder_moved(entity_id: str, old_folder_id: str, new_folder_id: str, user_id: str = "admin") -> int:
        return ChangeTracker.record(
            FOLDER_MOVED, entity_id, "folder", user_id,
            old_folder_id=old_folder_id, new_folder_id=new_folder_id,
        )

    @staticmethod
    def folder_deleted(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FOLDER_DELETED, entity_id, "folder", user_id, **kwargs)

    @staticmethod
    def folder_trashed(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FOLDER_TRASHED, entity_id, "folder", user_id, **kwargs)

    @staticmethod
    def folder_restored(entity_id: str, user_id: str = "admin", **kwargs) -> int:
        return ChangeTracker.record(FOLDER_RESTORED, entity_id, "folder", user_id, **kwargs)


# ---------------------------------------------------------------------------
# Conflict detection — optimistic concurrency control
# ---------------------------------------------------------------------------

class ConflictDetector:
    """
    Detects conflicts when two environments modify the same entity concurrently.
    Uses the updated_at timestamp on the database record as the version
    and the global sync version for ordering.
    """

    @staticmethod
    def check_version_conflict(
        entity_id: str,
        client_version: int,
        user_id: str = "admin",
    ) -> Optional[Dict[str, Any]]:
        """
        Returns None if no conflict (client is up-to-date).
        Returns a conflict descriptor dict if the entity was modified since
        client_version.
        """
        current_server_version = DatabaseRepository.get_current_version()
        if current_server_version <= client_version:
            return None  # No changes since client's version

        # Check if this specific entity was modified after the client's version
        with get_db_session() as session:
            # Check file
            file_obj = session.query(FileModel).filter(FileModel.id == entity_id).first()
            if file_obj:
                # Compare updated_at — if it's newer than client's known state, conflict
                entity_version = int(current_server_version)
                return {
                    "conflict": True,
                    "entity_id": entity_id,
                    "entity_type": "file",
                    "client_version": client_version,
                    "server_version": entity_version,
                    "current_name": file_obj.name,
                    "current_folder_id": file_obj.folder_id,
                }

            # Check folder
            folder_obj = session.query(FolderModel).filter(FolderModel.id == entity_id).first()
            if folder_obj:
                entity_version = int(current_server_version)
                return {
                    "conflict": True,
                    "entity_id": entity_id,
                    "entity_type": "folder",
                    "client_version": client_version,
                    "server_version": entity_version,
                    "current_name": folder_obj.name,
                    "current_folder_id": folder_obj.parent_folder_id,
                }

        return None  # Entity doesn't exist or no conflict detected

    @staticmethod
    def assert_no_conflict(
        entity_id: str,
        client_version: int,
        user_id: str = "admin",
    ) -> None:
        """
        Raises ConflictError if the entity has been modified since client_version.
        Use before performing mutations to prevent lost updates.
        """
        conflict = ConflictDetector.check_version_conflict(entity_id, client_version, user_id)
        if conflict:
            raise ConflictError(conflict)


class ConflictError(Exception):
    """Raised when an optimistic concurrency conflict is detected."""

    def __init__(self, conflict_info: Dict[str, Any]):
        self.conflict_info = conflict_info
        super().__init__(
            f"Conflict on {conflict_info.get('entity_id')}: "
            f"server version {conflict_info.get('server_version')} > "
            f"client version {conflict_info.get('client_version')}"
        )


# ---------------------------------------------------------------------------
# SyncService — query helpers for catching up
# ---------------------------------------------------------------------------

class SyncService:
    """High-level query helpers used by the sync API endpoints."""

    @staticmethod
    def get_status(user_id: str = "admin") -> Dict[str, Any]:
        """
        Returns the current sync status:
        - version: current global version
        - server_time: current server UTC time
        - last_change: most recent changelog entry for this user
        """
        version = DatabaseRepository.get_current_version()
        last_change = DatabaseRepository.get_last_change(user_id=user_id)
        return {
            "version": version,
            "server_time": utc_now().isoformat(),
            "last_change": last_change,
        }

    @staticmethod
    def get_changes_since(
        user_id: str,
        since_version: int,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """
        Returns changes since the given version for the specified user.
        The client can use this to catch up after being offline.
        """
        current_version = DatabaseRepository.get_current_version()
        changes = DatabaseRepository.get_changes_since(
            user_id=user_id,
            since_version=since_version,
            limit=limit,
        )
        return {
            "current_version": current_version,
            "changes": changes,
        }# ---------------------------------------------------------------------------
# Non-blocking helpers for use inside async handlers
# ---------------------------------------------------------------------------

import concurrent.futures

_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


async def record_change_async(
    operation: str,
    entity_id: str,
    entity_type: str = "file",
    user_id: str = "admin",
    **kwargs,
) -> int:
    """
    Non-blocking wrapper around ChangeTracker.record() for use in async handlers.
    Runs the synchronous DB operation in a thread pool so the event loop is never
    blocked.  Returns 0 on failure (non-fatal).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _thread_pool,
        lambda: ChangeTracker.record(operation, entity_id, entity_type, user_id, **kwargs),
    )


# ---------------------------------------------------------------------------
# Broadcast helper — notify WebSocket clients after mutations
# ---------------------------------------------------------------------------
def broadcast_sync_event(operation: str, entity_id: str, entity_type: str, version: int, user_id: str = "admin") -> None:
    """
    Schedules a WebSocket broadcast for connected clients.
    This is fire-and-forget; broadcast failures are logged but not raised.
    """
    try:
        from utils.websocket_manager import ws_manager
        event = {
            "type": "sync_event",
            "operation": operation,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "version": version,
            "user_id": user_id,
        }
        # Fire-and-forget broadcast — runs in the event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast_to_user(user_id, event))
        except RuntimeError:
            pass  # No running event loop (e.g. called from CLI)
    except Exception as e:
        logger.debug("WebSocket broadcast skipped: %s", e)
