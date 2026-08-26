"""
Duplicate File Detection & Management Subsystem
================================================
Provides low-memory content hashing (streaming SHA-256), persistent searchable
hash indexing, background non-blocking hash workers, duplicate grouping, and safe deletion.
"""

import os
import sys
import time
import json
import asyncio
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Set, Tuple, Any, Union

from utils.logger import Logger

logger = Logger(__name__)

CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HASH_INDEX_PATH = CACHE_DIR / "hash_index.json"


@dataclass
class DuplicateIndexEntry:
    sha256: str
    size: int
    filename: str
    file_id: int  # Telegram message/file ID
    file_uuid: str  # Internal unique identifier in NewDriveData
    folder_id: str = ""
    folder_path: str = ""
    display_path: str = ""
    upload_date: str = ""
    created_at: str = ""
    modified_at: str = ""
    last_indexed: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DuplicateIndexEntry":
        return cls(
            sha256=data.get("sha256", ""),
            size=int(data.get("size", 0)),
            filename=data.get("filename", ""),
            file_id=int(data.get("file_id", 0)),
            file_uuid=data.get("file_uuid", ""),
            folder_id=data.get("folder_id", ""),
            folder_path=data.get("folder_path", ""),
            display_path=data.get("display_path", ""),
            upload_date=data.get("upload_date", ""),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            last_indexed=float(data.get("last_indexed", time.time())),
        )


@dataclass
class DuplicateGroup:
    sha256: str
    size: int
    total_copies: int
    recoverable_bytes: int
    retained_file: Dict[str, Any]
    duplicate_files: List[Dict[str, Any]]
    all_files: List[Dict[str, Any]]


class DuplicateManager:
    _instance: Optional["DuplicateManager"] = None

    def __init__(self, index_path: Optional[Path] = None):
        self.index_path = index_path or HASH_INDEX_PATH
        # Map: file_uuid -> DuplicateIndexEntry
        self.entries: Dict[str, DuplicateIndexEntry] = {}
        
        # Concurrency & background scanning control
        self._lock = asyncio.Lock()
        self._scan_task: Optional[asyncio.Task] = None
        self._is_scanning: bool = False
        self._scan_stats = {
            "state": "idle",  # idle | scanning | completed | failed
            "total_files": 0,
            "processed_files": 0,
            "hashed_files": 0,
            "skipped_cached": 0,
            "failed_files": 0,
            "start_time": 0.0,
            "elapsed_seconds": 0.0,
            "current_filename": "",
            "last_error": None,
        }
        self.load_index()

    @classmethod
    def get_instance(cls) -> "DuplicateManager":
        if cls._instance is None:
            cls._instance = DuplicateManager()
        return cls._instance

    def load_index(self) -> None:
        """Loads persistent hash index from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        raw_entries = data.get("entries", {})
                        self.entries = {
                            k: DuplicateIndexEntry.from_dict(v)
                            for k, v in raw_entries.items()
                            if isinstance(v, dict)
                        }
                        logger.info(f"Loaded {len(self.entries)} entries from duplicate hash index.")
            except Exception as e:
                logger.warning(f"Error loading duplicate hash index ({e}). Starting fresh index.")
                self.entries = {}

    def save_index(self) -> None:
        """Atomically saves hash index to disk."""
        try:
            tmp_path = self.index_path.with_suffix(".tmp")
            data = {
                "version": "1.0",
                "updated_at": time.time(),
                "total_entries": len(self.entries),
                "entries": {k: v.to_dict() for k, v in self.entries.items()}
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.index_path)
        except Exception as e:
            logger.error(f"Failed to save duplicate hash index: {e}")

    # --------------------------------------------------------------------------
    # Low-Memory Streaming Hasher
    # --------------------------------------------------------------------------

    @staticmethod
    def calculate_file_sha256(file_path: Union[Path, str], chunk_size: int = 65536) -> Optional[str]:
        """
        Calculates SHA-256 for a local file in 64KB chunks.
        Memory consumption remains virtually zero regardless of file size (e.g. 4GB).
        """
        fp = Path(file_path)
        if not fp.exists() or not fp.is_file():
            return None
        try:
            h = hashlib.sha256()
            with open(fp, "rb") as f:
                while chunk := f.read(chunk_size):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Streaming hash calculation error for local file '{file_path}': {e}")
            return None

    @staticmethod
    def calculate_bytes_sha256(data: Union[bytes, bytearray]) -> str:
        """Calculates SHA-256 for in-memory buffer."""
        h = hashlib.sha256()
        h.update(data)
        return h.hexdigest()

    async def stream_hash_telegram_file(
        self, channel_id: int, message_id: int, file_size: int, chunk_size: int = 524288
    ) -> Optional[str]:
        """
        Streams a remote file from Telegram storage channel in 512KB chunks,
        feeding each chunk to SHA-256 without buffering the entire file in RAM.
        """
        if not channel_id or not message_id or file_size <= 0:
            return None

        from utils.clients import get_client, multi_clients, premium_clients
        from utils.streamer.custom_dl import ByteStreamer
        from pyrogram.file_id import FileId

        # Candidate client selection
        primary_client = get_client()
        candidates = [primary_client]
        for c in list(multi_clients.values()) + list(premium_clients.values()):
            if c not in candidates:
                candidates.append(c)

        for client in candidates:
            try:
                streamer = ByteStreamer(client)
                file_props: FileId = await streamer.get_file_properties(channel_id, message_id)
                if not file_props:
                    continue

                part_count = (file_size + chunk_size - 1) // chunk_size
                h = hashlib.sha256()

                # Stream chunks incrementally
                async for chunk in streamer.yield_file(
                    file_id=file_props,
                    offset=0,
                    first_part_cut=0,
                    last_part_cut=file_size % chunk_size or chunk_size,
                    part_count=part_count,
                    chunk_size=chunk_size,
                ):
                    if chunk:
                        h.update(chunk)
                        # Small yield to event loop to keep server responsive
                        await asyncio.sleep(0)

                return h.hexdigest()
            except Exception as e:
                logger.warning(f"Telegram streaming hash attempt failed for msg {message_id} on client: {e}")
                continue

        return None

    # --------------------------------------------------------------------------
    # Background Indexing & Tree Scanner
    # --------------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Returns real-time scan and duplicate summary status."""
        stats = dict(self._scan_stats)
        if self._is_scanning and stats["start_time"] > 0:
            stats["elapsed_seconds"] = round(time.time() - stats["start_time"], 1)
        
        # Calculate live duplicate counts from current entries
        groups, total_rec = self._compute_groups_from_entries()
        stats["total_indexed_entries"] = len(self.entries)
        stats["duplicate_groups_count"] = len(groups)
        stats["total_recoverable_bytes"] = total_rec
        return stats

    def start_background_scan(self) -> bool:
        """Starts asynchronous background hash scanning if not already running."""
        if self._is_scanning:
            return False

        self._scan_task = asyncio.create_task(self._scan_drive_tree_task())
        return True

    async def _scan_drive_tree_task(self) -> None:
        """Background worker that discovers and hashes unhashed files."""
        async with self._lock:
            self._is_scanning = True
            self._scan_stats.update({
                "state": "scanning",
                "start_time": time.time(),
                "processed_files": 0,
                "hashed_files": 0,
                "skipped_cached": 0,
                "failed_files": 0,
                "last_error": None,
            })

            try:
                from utils.directoryHandler import ensure_drive_data
                import config

                drive = ensure_drive_data()
                if not drive or not hasattr(drive, "contents") or "/" not in drive.contents:
                    self._scan_stats["state"] = "failed"
                    self._scan_stats["last_error"] = "Drive data not available"
                    return

                # Collect all active non-trashed files
                collected_files: List[Tuple[Any, str, str]] = []  # (file_obj, folder_id, display_path)

                def traverse(folder_obj, current_path: str = "/"):
                    if getattr(folder_obj, "trash", False):
                        return
                    contents = getattr(folder_obj, "contents", {})
                    if not isinstance(contents, dict):
                        return
                    for item_id, item in contents.items():
                        if getattr(item, "trash", False):
                            continue
                        item_type = getattr(item, "type", "")
                        name = getattr(item, "name", "Unnamed")
                        item_display = f"{current_path.rstrip('/')}/{name}"
                        if item_type == "folder":
                            traverse(item, item_display)
                        elif item_type == "file":
                            collected_files.append((item, getattr(folder_obj, "id", "root"), item_display))

                traverse(drive.contents["/"], "/")
                self._scan_stats["total_files"] = len(collected_files)

                # Clean up deleted files from index
                active_uuids = {getattr(f[0], "id", "") for f in collected_files}
                stale_uuids = [u for u in self.entries.keys() if u not in active_uuids]
                for su in stale_uuids:
                    self.entries.pop(su, None)

                # Process each file
                for file_obj, folder_id, display_path in collected_files:
                    file_uuid = getattr(file_obj, "id", "")
                    file_name = getattr(file_obj, "name", "")
                    file_size = int(getattr(file_obj, "size", 0) or 0)
                    msg_id = int(getattr(file_obj, "file_id", 0) or 0)
                    upload_date = str(getattr(file_obj, "upload_date", "") or "")
                    created_at = str(getattr(file_obj, "created_at", "") or "")
                    modified_at = str(getattr(file_obj, "modified_at", "") or "")

                    self._scan_stats["processed_files"] += 1
                    self._scan_stats["current_filename"] = file_name

                    # 1. Check if hash already exists in file_obj or cache
                    existing_hash = getattr(file_obj, "sha256", None)
                    if not existing_hash and file_uuid in self.entries:
                        cached_entry = self.entries[file_uuid]
                        if cached_entry.size == file_size and cached_entry.sha256:
                            existing_hash = cached_entry.sha256
                            file_obj.sha256 = existing_hash

                    if existing_hash:
                        # Update index record without re-hashing
                        self.entries[file_uuid] = DuplicateIndexEntry(
                            sha256=existing_hash,
                            size=file_size,
                            filename=file_name,
                            file_id=msg_id,
                            file_uuid=file_uuid,
                            folder_id=folder_id,
                            folder_path=getattr(file_obj, "path", "/"),
                            display_path=display_path,
                            upload_date=upload_date,
                            created_at=created_at,
                            modified_at=modified_at,
                        )
                        self._scan_stats["skipped_cached"] += 1
                        continue

                    # 2. Skip empty files (0 bytes hash is deterministic)
                    if file_size == 0:
                        empty_sha = hashlib.sha256(b"").hexdigest()
                        file_obj.sha256 = empty_sha
                        self.entries[file_uuid] = DuplicateIndexEntry(
                            sha256=empty_sha,
                            size=0,
                            filename=file_name,
                            file_id=msg_id,
                            file_uuid=file_uuid,
                            folder_id=folder_id,
                            folder_path=getattr(file_obj, "path", "/"),
                            display_path=display_path,
                            upload_date=upload_date,
                        )
                        self._scan_stats["hashed_files"] += 1
                        continue

                    # 3. Perform streaming hash
                    computed_hash = None
                    # If file has a local path stored or exists in temp/cache
                    local_candidate = getattr(file_obj, "local_path", None)
                    if local_candidate and os.path.exists(local_candidate):
                        computed_hash = self.calculate_file_sha256(local_candidate)
                    elif msg_id and config.STORAGE_CHANNEL:
                        # Remote Telegram streaming hash
                        computed_hash = await self.stream_hash_telegram_file(
                            channel_id=config.STORAGE_CHANNEL,
                            message_id=msg_id,
                            file_size=file_size,
                        )

                    if computed_hash:
                        file_obj.sha256 = computed_hash
                        self.entries[file_uuid] = DuplicateIndexEntry(
                            sha256=computed_hash,
                            size=file_size,
                            filename=file_name,
                            file_id=msg_id,
                            file_uuid=file_uuid,
                            folder_id=folder_id,
                            folder_path=getattr(file_obj, "path", "/"),
                            display_path=display_path,
                            upload_date=upload_date,
                            created_at=created_at,
                            modified_at=modified_at,
                        )
                        self._scan_stats["hashed_files"] += 1
                    else:
                        self._scan_stats["failed_files"] += 1

                    # Polite background throttling so normal server traffic is unhindered
                    await asyncio.sleep(0.02)

                self.save_index()
                drive.save()
                self._scan_stats["state"] = "completed"
                logger.info(
                    f"Duplicate scan completed: {self._scan_stats['hashed_files']} newly hashed, "
                    f"{self._scan_stats['skipped_cached']} cached."
                )
            except Exception as e:
                self._scan_stats["state"] = "failed"
                self._scan_stats["last_error"] = str(e)
                logger.error(f"Duplicate background scan error: {e}")
            finally:
                self._is_scanning = False

    # --------------------------------------------------------------------------
    # Duplicate Detection & Grouping Engine
    # --------------------------------------------------------------------------

    def _compute_groups_from_entries(self) -> Tuple[List[DuplicateGroup], int]:
        """Groups indexed entries by (sha256, size). Designates original/retained file."""
        # Key: (sha256, size) -> List[DuplicateIndexEntry]
        buckets: Dict[Tuple[str, int], List[DuplicateIndexEntry]] = {}
        for entry in self.entries.values():
            if not entry.sha256 or entry.size <= 0:
                continue
            key = (entry.sha256, entry.size)
            buckets.setdefault(key, []).append(entry)

        groups: List[DuplicateGroup] = []
        total_recoverable = 0

        for (sha, sz), item_list in buckets.items():
            if len(item_list) <= 1:
                continue

            # Sort items to pick the single Retained (Original) file:
            # 1. Oldest upload_date / created_at (earliest created)
            # 2. Shortest display_path (root-most)
            def sort_key(e: DuplicateIndexEntry):
                d_str = e.upload_date or e.created_at or "9999-99-99"
                return (d_str, len(e.display_path), e.display_path)

            sorted_items = sorted(item_list, key=sort_key)
            retained_entry = sorted_items[0]
            duplicate_entries = sorted_items[1:]

            recoverable_bytes = sz * len(duplicate_entries)
            total_recoverable += recoverable_bytes

            retained_dict = retained_entry.to_dict()
            retained_dict["is_retained"] = True

            dup_dicts = []
            for d in duplicate_entries:
                d_dict = d.to_dict()
                d_dict["is_retained"] = False
                dup_dicts.append(d_dict)

            all_dicts = [retained_dict] + dup_dicts

            groups.append(
                DuplicateGroup(
                    sha256=sha,
                    size=sz,
                    total_copies=len(item_list),
                    recoverable_bytes=recoverable_bytes,
                    retained_file=retained_dict,
                    duplicate_files=dup_dicts,
                    all_files=all_dicts,
                )
            )

        # Sort groups by highest recoverable bytes first
        groups.sort(key=lambda g: (g.recoverable_bytes, g.size), reverse=True)
        return groups, total_recoverable

    def get_duplicate_groups(
        self,
        query: str = "",
        mime_category: Optional[str] = None,
        sort_by: str = "recoverable_size",
    ) -> Dict[str, Any]:
        """
        Returns filtered and categorized duplicate groups for UI presentation.
        """
        all_groups, total_recoverable = self._compute_groups_from_entries()
        
        filtered_groups = []
        q_lower = query.strip().lower()

        for g in all_groups:
            # Search query filter (matches any file name or folder in the group)
            if q_lower:
                match_any = any(
                    q_lower in f.get("filename", "").lower() or q_lower in f.get("display_path", "").lower()
                    for f in g.all_files
                )
                if not match_any and q_lower not in g.sha256.lower():
                    continue

            # MIME / extension category filter
            if mime_category and mime_category != "all":
                from utils.properties import MIME_CATEGORY_EXTENSIONS
                allowed_exts = MIME_CATEGORY_EXTENSIONS.get(mime_category.lower(), set())
                match_cat = any(
                    Path(f.get("filename", "")).suffix.lower() in allowed_exts
                    for f in g.all_files
                )
                if not match_cat:
                    continue

            filtered_groups.append(g)

        # Sorting options
        if sort_by == "name":
            filtered_groups.sort(key=lambda g: g.retained_file.get("filename", "").lower())
        elif sort_by == "copies":
            filtered_groups.sort(key=lambda g: g.total_copies, reverse=True)
        elif sort_by == "file_size":
            filtered_groups.sort(key=lambda g: g.size, reverse=True)
        else:  # recoverable_size
            filtered_groups.sort(key=lambda g: g.recoverable_bytes, reverse=True)

        return {
            "status": "ok",
            "total_groups": len(filtered_groups),
            "duplicate_groups_count": len(filtered_groups),
            "total_duplicates": sum(len(g.duplicate_files) for g in filtered_groups),
            "total_recoverable_bytes": total_recoverable,
            "groups": [
                {
                    "sha256": g.sha256,
                    "size": g.size,
                    "file_size": g.size,
                    "total_copies": g.total_copies,
                    "copies_count": g.total_copies,
                    "recoverable_bytes": g.recoverable_bytes,
                    "retained_file": g.retained_file,
                    "duplicate_files": g.duplicate_files,
                    "all_files": g.all_files,
                    "files": g.all_files,
                }
                for g in filtered_groups
            ],
            "scan_status": self.get_status(),
        }

    # --------------------------------------------------------------------------
    # Safe & Explicit Duplicate Deletion
    # --------------------------------------------------------------------------

    def delete_duplicates(
        self, target_file_uuids: List[str], soft_delete: bool = True
    ) -> Dict[str, Any]:
        """
        Explicitly removes or trashes selected duplicate files while strictly enforcing
        the retention safety invariant (cannot delete all copies of a duplicate group).
        """
        if not target_file_uuids:
            return {"status": "error", "message": "No file IDs provided for deletion."}

        target_set = set(target_file_uuids)

        # Safety Check: Verify that for every duplicate group represented,
        # at least ONE copy remains unselected!
        all_groups, _ = self._compute_groups_from_entries()
        for g in all_groups:
            group_uuids = {f.get("file_uuid") for f in g.all_files}
            selected_in_group = group_uuids.intersection(target_set)
            if len(selected_in_group) == len(group_uuids) and len(group_uuids) > 0:
                # User tried to delete 100% of copies in this group
                raise ValueError(
                    f"Retention Safety Violation: Cannot delete all {len(group_uuids)} copies of "
                    f"'{g.retained_file.get('filename')}' (SHA: {g.sha256[:8]}...). At least one copy must be preserved."
                )

        from utils.directoryHandler import ensure_drive_data
        drive = ensure_drive_data()
        if not drive or not hasattr(drive, "contents") or "/" not in drive.contents:
            return {"status": "error", "message": "Drive metadata unavailable."}

        deleted_count = 0
        freed_bytes = 0
        deleted_details = []

        # Find and remove or trash each file
        for uuid in target_set:
            entry = self.entries.get(uuid)
            file_found = False

            # Walk tree to locate file node
            def walk_and_delete(folder_obj):
                nonlocal deleted_count, freed_bytes, file_found
                contents = getattr(folder_obj, "contents", {})
                if not isinstance(contents, dict):
                    return

                # If file is directly in this folder
                if uuid in contents:
                    f_item = contents[uuid]
                    if getattr(f_item, "type", "") == "file":
                        file_sz = int(getattr(f_item, "size", 0) or 0)
                        f_name = getattr(f_item, "name", "Unnamed")
                        
                        if soft_delete:
                            # Soft-delete to trash
                            f_item.trash = True
                            f_item.trashed_at = time.strftime("%Y-%m-%d %H:%M:%S")
                            try:
                                from utils.properties import ActivityTracker
                                ActivityTracker.record_activity(f_item, "trashed")
                            except Exception:
                                pass
                        else:
                            # Permanent delete
                            del contents[uuid]
                            if uuid in drive.used_ids:
                                drive.used_ids.remove(uuid)

                        deleted_count += 1
                        freed_bytes += file_sz
                        file_found = True
                        deleted_details.append({"uuid": uuid, "name": f_name, "size": file_sz})
                        return

                for item in list(contents.values()):
                    if getattr(item, "type", "") == "folder":
                        walk_and_delete(item)
                        if file_found:
                            return

            walk_and_delete(drive.contents["/"])

            # Remove from hash index
            self.entries.pop(uuid, None)

        # Invalidate folder stats cache & save drive data
        try:
            from utils.properties import FolderStatsCalculator
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass

        drive.save()
        self.save_index()

        logger.info(
            f"Duplicate deletion: {deleted_count} files ({freed_bytes} bytes) "
            f"{'trashed' if soft_delete else 'permanently deleted'}."
        )

        return {
            "status": "ok",
            "message": f"Successfully {'moved to trash' if soft_delete else 'deleted'} {deleted_count} duplicate files.",
            "deleted_count": deleted_count,
            "freed_bytes": freed_bytes,
            "deleted_files": deleted_details,
            "scan_status": self.get_status(),
        }


# Global singleton instance
duplicate_manager = DuplicateManager.get_instance()


def calculate_file_sha256(file_path: Union[Path, str], chunk_size: int = 65536) -> Optional[str]:
    """Module-level helper to calculate SHA-256 for a local file in chunks."""
    return DuplicateManager.calculate_file_sha256(file_path, chunk_size)


async def stream_hash_telegram_file(
    channel_id: int, message_id: int, file_size: int, chunk_size: int = 524288
) -> Optional[str]:
    """Module-level helper to stream SHA-256 for a Telegram file."""
    return await duplicate_manager.stream_hash_telegram_file(channel_id, message_id, file_size, chunk_size)

