import asyncio
import os
import time
import json
import random
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, asdict
from urllib.parse import unquote_plus

from utils.logger import Logger

logger = Logger(__name__)

DATA_DIR = Path("./data")
CACHE_DIR = Path("./cache")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TRANSFERS_PERSISTENCE_FILE = DATA_DIR / "transfers.json"


class TransferState(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TransferType(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass
class TransferProgressSample:
    timestamp: float
    bytes_transferred: int


@dataclass
class TransferItem:
    id: str
    type: TransferType
    filename: str
    size: int
    transferred: int = 0
    percentage: float = 0.0
    speed: float = 0.0
    speed_formatted: str = "0 B/s"
    eta: Optional[int] = None
    eta_formatted: str = "--"
    start_time: float = field(default_factory=time.time)
    completion_time: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 5
    error_reason: Optional[str] = None
    state: TransferState = TransferState.QUEUED
    target_path: str = "/"
    relative_path: Optional[str] = None
    batch_id: Optional[str] = None
    source_url: Optional[str] = None
    conflict_mode: str = "keep_both"
    temp_file_path: Optional[str] = None
    single_threaded: bool = False

    # Runtime internal references (not serialized to disk)
    _samples: List[TransferProgressSample] = field(default_factory=list, repr=False)
    _cancel_event: Optional[asyncio.Event] = field(default=None, repr=False)
    _client_ref: Dict[str, Any] = field(default_factory=lambda: {"client": None}, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _flood_expires: Optional[float] = field(default=None, repr=False)

    @property
    def cooldown_remaining(self) -> float:
        if hasattr(self, "_flood_expires") and self._flood_expires:
            return max(0.0, self._flood_expires - time.monotonic())
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, TransferType) else str(self.type),
            "filename": self.filename,
            "size": self.size,
            "transferred": self.transferred,
            "percentage": round(self.percentage, 2),
            "speed": round(self.speed, 2),
            "speed_formatted": self.speed_formatted,
            "eta": self.eta,
            "eta_formatted": self.eta_formatted,
            "start_time": self.start_time,
            "completion_time": self.completion_time,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_reason": self.error_reason,
            "cooldown_remaining": round(self.cooldown_remaining, 1),
            "state": self.state.value if isinstance(self.state, TransferState) else str(self.state),
            "target_path": self.target_path,
            "relative_path": self.relative_path,
            "batch_id": self.batch_id,
            "source_url": self.source_url,
            "conflict_mode": self.conflict_mode,
            "temp_file_path": self.temp_file_path,
            "single_threaded": self.single_threaded,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransferItem":
        item_type = TransferType(data.get("type", "upload"))
        item_state = TransferState(data.get("state", "queued"))
        return cls(
            id=str(data.get("id")),
            type=item_type,
            filename=str(data.get("filename", "unknown")),
            size=int(data.get("size", 0)),
            transferred=int(data.get("transferred", 0)),
            percentage=float(data.get("percentage", 0.0)),
            speed=float(data.get("speed", 0.0)),
            speed_formatted=str(data.get("speed_formatted", "0 B/s")),
            eta=data.get("eta"),
            eta_formatted=str(data.get("eta_formatted", "--")),
            start_time=float(data.get("start_time", time.time())),
            completion_time=data.get("completion_time"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 5)),
            error_reason=data.get("error_reason"),
            state=item_state,
            target_path=str(data.get("target_path", "/")),
            relative_path=data.get("relative_path"),
            batch_id=data.get("batch_id"),
            source_url=data.get("source_url"),
            conflict_mode=str(data.get("conflict_mode", "keep_both")),
            temp_file_path=data.get("temp_file_path"),
            single_threaded=bool(data.get("single_threaded", False)),
        )

    def update_progress(self, current_bytes: int, total_bytes: Optional[int] = None):
        if total_bytes and total_bytes > 0:
            self.size = total_bytes
        self.transferred = current_bytes

        if self.size > 0:
            self.percentage = min(100.0, max(0.0, (self.transferred / self.size) * 100.0))
        else:
            self.percentage = 0.0

        now = time.monotonic()
        if not hasattr(self, "_samples") or self._samples is None:
            self._samples = []

        self._samples.append(TransferProgressSample(now, current_bytes))

        # Keep rolling window of last 4 seconds
        cutoff = now - 4.0
        self._samples = [s for s in self._samples if s.timestamp >= cutoff]

        if len(self._samples) >= 2:
            first_sample = self._samples[0]
            last_sample = self._samples[-1]
            time_delta = last_sample.timestamp - first_sample.timestamp
            bytes_delta = last_sample.bytes_transferred - first_sample.bytes_transferred

            if time_delta > 0.1 and bytes_delta >= 0:
                calc_speed = bytes_delta / time_delta
                # Smooth speed using exponential moving average
                self.speed = 0.7 * calc_speed + 0.3 * self.speed if self.speed > 0 else calc_speed
            else:
                self.speed = 0.0
        else:
            self.speed = 0.0

        self.speed_formatted = self._format_bytes(self.speed) + "/s"

        if self.speed > 1024 and self.size > self.transferred:
            remaining_bytes = self.size - self.transferred
            remaining_seconds = int(remaining_bytes / self.speed)
            self.eta = remaining_seconds
            self.eta_formatted = self._format_duration(remaining_seconds)
        else:
            self.eta = None
            self.eta_formatted = "--" if self.state in (TransferState.UPLOADING, TransferState.DOWNLOADING) else ""

    @staticmethod
    def _format_bytes(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            m = seconds // 60
            s = seconds % 60
            return f"{m}m {s:02d}s"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}h {m:02d}m"


class TransferStore:
    """Thread-safe & async-safe persistent store for transfers."""

    def __init__(self, filepath: Optional[Union[str, Path]] = None):
        self.filepath = Path(filepath) if filepath else TRANSFERS_PERSISTENCE_FILE
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.transfers: Dict[str, TransferItem] = {}
        self._lock = asyncio.Lock()
        self._dirty = False
        self._load()

    def _load(self):
        if not self.filepath.exists():
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    items = data.get("transfers", [])
                    for raw in items:
                        try:
                            item = TransferItem.from_dict(raw)
                            self.transfers[item.id] = item
                        except Exception as e:
                            logger.warning(f"Failed to deserialize transfer item: {e}")
            logger.info(f"Loaded {len(self.transfers)} transfer items from {self.filepath}.")
        except Exception as e:
            logger.error(f"Failed to read transfers persistence file: {e}")
            try:
                # Backup corrupted file so it does not block future runs
                corrupt_backup = self.filepath.with_suffix(f".corrupt_{int(time.time())}")
                if self.filepath.exists():
                    self.filepath.rename(corrupt_backup)
                    logger.info(f"Moved corrupted transfers file to {corrupt_backup}")
            except Exception as ren_e:
                logger.debug(f"Could not rename corrupted transfers file: {ren_e}")
            self.transfers = {}

    async def save(self, force: bool = False):
        async with self._lock:
            if not self._dirty and not force:
                return
            try:
                # Keep active items + latest 200 completed/failed/cancelled items
                active_items = [t for t in self.transfers.values() if t.state in (
                    TransferState.QUEUED, TransferState.PREPARING, TransferState.UPLOADING,
                    TransferState.DOWNLOADING, TransferState.RETRYING
                )]
                inactive_items = [t for t in self.transfers.values() if t.state not in (
                    TransferState.QUEUED, TransferState.PREPARING, TransferState.UPLOADING,
                    TransferState.DOWNLOADING, TransferState.RETRYING
                )]
                inactive_items.sort(key=lambda x: x.completion_time or x.start_time, reverse=True)
                trimmed_inactive = inactive_items[:200]

                to_save = [t.to_dict() for t in (active_items + trimmed_inactive)]
                
                temp_file = self.filepath.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump({"transfers": to_save, "updated_at": time.time()}, f, indent=2)
                
                # Atomic replace
                temp_file.replace(self.filepath)
                self._dirty = False
            except Exception as e:
                logger.error(f"Failed to persist transfers: {e}")

    def mark_dirty(self):
        self._dirty = True


class TransferManager:
    """
    Production-Grade Transfer Manager for Telegram-Unlimited-Cloud.
    Manages bounded concurrency, resilient retries with exponential backoff,
    cancellation tokens, background worker loops, and restart recovery.
    """

    _instance: Optional["TransferManager"] = None

    def __new__(cls, *args, **kwargs):
        if kwargs.get("is_singleton", True) and cls._instance is not None:
            return cls._instance
        inst = super(TransferManager, cls).__new__(cls)
        inst._initialized = False
        if kwargs.get("is_singleton", True):
            cls._instance = inst
        return inst

    def __init__(
        self,
        store_path: Optional[Union[str, Path]] = None,
        max_concurrent_uploads: int = 3,
        max_concurrent_downloads: int = 3,
        is_singleton: bool = True,
    ):
        if self._initialized:
            return
        self._initialized = True

        self.max_concurrent_uploads = max_concurrent_uploads
        self.max_concurrent_downloads = max_concurrent_downloads

        self.store = TransferStore(filepath=store_path)
        self.upload_queue: asyncio.Queue[str] = asyncio.Queue()
        self.download_queue: asyncio.Queue[str] = asyncio.Queue()

        self.upload_semaphore = asyncio.Semaphore(max_concurrent_uploads)
        self.download_semaphore = asyncio.Semaphore(max_concurrent_downloads)

        self._workers: List[asyncio.Task] = []
        self._save_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self):
        """Initializes workers, recovers interrupted jobs, and starts periodic persistence."""
        async with self._lock:
            if self._running:
                return
            self._running = True

            logger.info("Starting Transfer Manager subsystem...")
            await self.recover_interrupted_transfers()

            # Start worker pools
            for i in range(self.max_concurrent_uploads):
                self._workers.append(asyncio.create_task(self._upload_worker(i + 1)))

            for i in range(self.max_concurrent_downloads):
                self._workers.append(asyncio.create_task(self._download_worker(i + 1)))

            # Start periodic persistence loop (flushes dirty state every 3s)
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Transfer Manager started with active worker pools.")

    async def shutdown(self):
        """Gracefully shuts down workers and persists state."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            logger.info("Shutting down Transfer Manager...")

            for worker in self._workers:
                worker.cancel()

            if self._save_task:
                self._save_task.cancel()

            # Final persistence flush
            await self.store.save(force=True)
            logger.info("Transfer Manager shut down cleanly.")

    async def recover_interrupted_transfers(self):
        """
        Scans persisted transfers on startup. Any transfer that was interrupted mid-flight
        (preparing, uploading, downloading, retrying) is either safely re-queued or marked failed.
        """
        for item in self.store.transfers.values():
            if item.state in (TransferState.PREPARING, TransferState.UPLOADING, TransferState.RETRYING):
                # For uploads: check if temp file is still valid on disk
                if item.temp_file_path and os.path.isfile(item.temp_file_path):
                    logger.info(f"Restart Recovery: Re-queueing interrupted upload {item.id} ({item.filename})")
                    item.state = TransferState.QUEUED
                    item.error_reason = "Recovered after server restart"
                    item._cancel_event = asyncio.Event()
                    await self.upload_queue.put(item.id)
                else:
                    logger.warning(f"Restart Recovery: Temp file missing for upload {item.id}, marking failed.")
                    item.state = TransferState.FAILED
                    item.error_reason = "Interrupted by server restart (temp file lost)"
                    item.completion_time = time.time()
                self.store.mark_dirty()

            elif item.state in (TransferState.DOWNLOADING, TransferState.PREPARING):
                # For remote downloads: re-queue to download from source URL
                if item.source_url:
                    logger.info(f"Restart Recovery: Re-queueing interrupted download {item.id} ({item.filename})")
                    item.state = TransferState.QUEUED
                    item.transferred = 0
                    item.percentage = 0.0
                    item.error_reason = "Recovered after server restart"
                    item._cancel_event = asyncio.Event()
                    await self.download_queue.put(item.id)
                else:
                    item.state = TransferState.FAILED
                    item.error_reason = "Interrupted by server restart"
                    item.completion_time = time.time()
                self.store.mark_dirty()

        await self.store.save(force=True)

    async def _periodic_save_loop(self):
        while self._running:
            try:
                await asyncio.sleep(3.0)
                await self.store.save()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Periodic transfer store save note: {e}")

    # -----------------------------------------------------------------------
    # Public Queue API
    # -----------------------------------------------------------------------

    async def queue_upload(
        self,
        file_path: str,
        id: str,
        target_path: str,
        filename: str,
        file_size: Optional[int] = None,
        conflict: str = "keep_both",
        relative_path: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> TransferItem:
        """Queues a local file for Telegram cloud upload with deduplication and state tracking."""
        if file_size is None:
            try:
                file_size = os.path.getsize(file_path) if (file_path and os.path.isfile(file_path)) else 0
            except Exception:
                file_size = 0

        async with self._lock:
            # Check for duplicate job
            if id in self.store.transfers:
                existing = self.store.transfers[id]
                if existing.state in (TransferState.QUEUED, TransferState.PREPARING, TransferState.UPLOADING, TransferState.RETRYING):
                    logger.info(f"Duplicate upload job ignored for ID: {id}")
                    return existing

            item = TransferItem(
                id=id,
                type=TransferType.UPLOAD,
                filename=filename,
                size=file_size,
                target_path=target_path,
                relative_path=relative_path,
                batch_id=batch_id,
                conflict_mode=conflict,
                temp_file_path=str(file_path),
                state=TransferState.QUEUED,
                start_time=time.time(),
            )
            item._cancel_event = asyncio.Event()
            self.store.transfers[id] = item
            self.store.mark_dirty()

        await self.upload_queue.put(id)
        logger.info(f"Transfer Manager: Queued upload {id} ({filename}, {file_size} bytes, target: {target_path})")
        return item

    async def queue_download(
        self,
        url: str,
        id: str,
        target_path: str,
        filename: str,
        single_threaded: bool = False,
    ) -> TransferItem:
        """Queues a remote URL for download and subsequent drive storage."""
        async with self._lock:
            if id in self.store.transfers:
                existing = self.store.transfers[id]
                if existing.state in (TransferState.QUEUED, TransferState.PREPARING, TransferState.DOWNLOADING, TransferState.RETRYING):
                    logger.info(f"Duplicate download job ignored for ID: {id}")
                    return existing

            item = TransferItem(
                id=id,
                type=TransferType.DOWNLOAD,
                filename=filename,
                size=0,
                target_path=target_path,
                source_url=url,
                single_threaded=single_threaded,
                state=TransferState.QUEUED,
                start_time=time.time(),
            )
            item._cancel_event = asyncio.Event()
            self.store.transfers[id] = item
            self.store.mark_dirty()

        await self.download_queue.put(id)
        logger.info(f"Transfer Manager: Queued download {id} ({url})")
        return item

    async def cancel_transfer(self, transfer_id: str) -> bool:
        """Cancels an active or queued transfer immediately and cleans up temporary files."""
        item = self.store.transfers.get(transfer_id)
        if not item:
            return False

        logger.info(f"Transfer Manager: Cancelling transfer {transfer_id} (current state: {item.state})")

        # Set cancellation flag
        if hasattr(item, "_cancel_event") and item._cancel_event:
            item._cancel_event.set()

        # Signal Pyrogram / TechZDL STOP flags for backward compatibility
        from utils.uploader import STOP_TRANSMISSION
        from utils.downloader import STOP_DOWNLOAD
        if transfer_id not in STOP_TRANSMISSION:
            STOP_TRANSMISSION.append(transfer_id)
        if transfer_id not in STOP_DOWNLOAD:
            STOP_DOWNLOAD.append(transfer_id)

        # Stop active Pyrogram client if available
        if hasattr(item, "_client_ref") and item._client_ref and item._client_ref.get("client"):
            try:
                item._client_ref["client"].stop_transmission()
            except Exception:
                pass

        item.state = TransferState.CANCELLED
        item.error_reason = "Cancelled by user"
        item.completion_time = time.time()
        item.speed = 0.0
        item.speed_formatted = "0 B/s"
        item.eta = None
        item.eta_formatted = ""

        # Clean temp file
        self._cleanup_temp_file(item)
        self.store.mark_dirty()
        await self.store.save()
        return True

    async def retry_transfer(self, transfer_id: str) -> Optional[TransferItem]:
        """Manually retries a failed or cancelled transfer."""
        item = self.store.transfers.get(transfer_id)
        if not item:
            return None

        if item.state in (TransferState.QUEUED, TransferState.PREPARING, TransferState.UPLOADING, TransferState.DOWNLOADING):
            logger.info(f"Transfer {transfer_id} is already in active/queued state.")
            return item

        logger.info(f"Transfer Manager: Retrying transfer {transfer_id} ({item.filename})")
        item.state = TransferState.QUEUED
        item.retry_count = 0
        item.error_reason = None
        item.transferred = 0
        item.percentage = 0.0
        item.speed = 0.0
        item.speed_formatted = "0 B/s"
        item.eta = None
        item.eta_formatted = "--"
        item.completion_time = None
        item.start_time = time.time()
        item._cancel_event = asyncio.Event()

        # Remove from STOP lists
        from utils.uploader import STOP_TRANSMISSION
        from utils.downloader import STOP_DOWNLOAD
        if transfer_id in STOP_TRANSMISSION:
            try:
                STOP_TRANSMISSION.remove(transfer_id)
            except ValueError:
                pass
        if transfer_id in STOP_DOWNLOAD:
            try:
                STOP_DOWNLOAD.remove(transfer_id)
            except ValueError:
                pass

        if item.type == TransferType.UPLOAD:
            if not item.temp_file_path or not os.path.isfile(item.temp_file_path):
                item.state = TransferState.FAILED
                item.error_reason = "Original temporary upload file is no longer available on disk"
                item.completion_time = time.time()
                self.store.mark_dirty()
                return item
            await self.upload_queue.put(transfer_id)
        else:
            await self.download_queue.put(transfer_id)

        self.store.mark_dirty()
        await self.store.save()
        return item

    async def remove_transfer(self, transfer_id: str) -> bool:
        """Removes a finished, failed, or cancelled transfer from the history."""
        async with self._lock:
            item = self.store.transfers.get(transfer_id)
            if not item:
                return False
            if item.state in (TransferState.UPLOADING, TransferState.DOWNLOADING, TransferState.PREPARING):
                await self.cancel_transfer(transfer_id)
            self._cleanup_temp_file(item)
            self.store.transfers.pop(transfer_id, None)
            self.store.mark_dirty()
        await self.store.save()
        return True

    async def clear_finished_transfers(self) -> int:
        """Removes all completed, cancelled, and failed transfers from history."""
        async with self._lock:
            to_remove = [
                tid for tid, t in self.store.transfers.items()
                if t.state in (TransferState.COMPLETED, TransferState.CANCELLED, TransferState.FAILED)
            ]
            for tid in to_remove:
                item = self.store.transfers.pop(tid, None)
                if item:
                    self._cleanup_temp_file(item)
            if to_remove:
                self.store.mark_dirty()
        await self.store.save()
        return len(to_remove)

    def get_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        item = self.store.transfers.get(transfer_id)
        return item.to_dict() if item else None

    def get_all_transfers(self, filter_type: Optional[str] = None, filter_state: Optional[str] = None) -> Dict[str, Any]:
        """Returns all transfers along with real-time aggregate statistics."""
        items = list(self.store.transfers.values())

        if filter_type:
            items = [i for i in items if (i.type.value if isinstance(i.type, TransferType) else str(i.type)) == filter_type]
        if filter_state:
            items = [i for i in items if (i.state.value if isinstance(i.state, TransferState) else str(i.state)) == filter_state]

        # Sort: active first, then newest start_time
        items.sort(key=lambda x: (
            0 if x.state in (TransferState.PREPARING, TransferState.UPLOADING, TransferState.DOWNLOADING, TransferState.RETRYING) else 1,
            -x.start_time
        ))

        active_uploads = sum(1 for t in self.store.transfers.values() if t.type == TransferType.UPLOAD and t.state in (TransferState.QUEUED, TransferState.PREPARING, TransferState.UPLOADING, TransferState.RETRYING))
        active_downloads = sum(1 for t in self.store.transfers.values() if t.type == TransferType.DOWNLOAD and t.state in (TransferState.QUEUED, TransferState.PREPARING, TransferState.DOWNLOADING, TransferState.RETRYING))
        total_upload_speed = sum(t.speed for t in self.store.transfers.values() if t.type == TransferType.UPLOAD and t.state == TransferState.UPLOADING)
        total_download_speed = sum(t.speed for t in self.store.transfers.values() if t.type == TransferType.DOWNLOAD and t.state == TransferState.DOWNLOADING)

        return {
            "status": "ok",
            "transfers": [item.to_dict() for item in items],
            "stats": {
                "total_count": len(self.store.transfers),
                "active_uploads": active_uploads,
                "active_downloads": active_downloads,
                "total_active": active_uploads + active_downloads,
                "upload_speed": total_upload_speed,
                "upload_speed_formatted": TransferItem._format_bytes(total_upload_speed) + "/s",
                "download_speed": total_download_speed,
                "download_speed_formatted": TransferItem._format_bytes(total_download_speed) + "/s",
            }
        }

    # -----------------------------------------------------------------------
    # Background Workers & Processing
    # -----------------------------------------------------------------------

    async def _upload_worker(self, worker_id: int):
        logger.info(f"Upload worker #{worker_id} online.")
        while self._running:
            try:
                transfer_id = await self.upload_queue.get()
                item = self.store.transfers.get(transfer_id)
                if not item:
                    self.upload_queue.task_done()
                    continue

                if item.state == TransferState.CANCELLED:
                    self.upload_queue.task_done()
                    continue

                async with self.upload_semaphore:
                    await self._process_upload(item)

                self.upload_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Upload worker #{worker_id} exception: {e}")
                await asyncio.sleep(1)

    async def _download_worker(self, worker_id: int):
        logger.info(f"Download worker #{worker_id} online.")
        while self._running:
            try:
                transfer_id = await self.download_queue.get()
                item = self.store.transfers.get(transfer_id)
                if not item:
                    self.download_queue.task_done()
                    continue

                if item.state == TransferState.CANCELLED:
                    self.download_queue.task_done()
                    continue

                async with self.download_semaphore:
                    await self._process_download(item)

                self.download_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Download worker #{worker_id} exception: {e}")
                await asyncio.sleep(1)

    async def _process_upload(self, item: TransferItem):
        from config import STORAGE_CHANNEL
        from pyrogram.errors import FloodWait
        from utils.clients import get_client
        from utils.uploader import (
            _pick_flood_safe_client,
            _wait_for_any_client,
            _client_key,
            STOP_TRANSMISSION,
            PROGRESS_CACHE,
        )
        from utils import tg_gate
        from utils.directoryHandler import ensure_drive_data, backup_drive_data

        drive = ensure_drive_data()
        item.state = TransferState.PREPARING
        self.store.mark_dirty()

        file_path = item.temp_file_path
        if not file_path or not os.path.isfile(file_path):
            item.state = TransferState.FAILED
            item.error_reason = "Upload file not found on disk"
            item.completion_time = time.time()
            self.store.mark_dirty()
            return

        file_size = os.path.getsize(file_path)
        item.size = file_size
        premium_required = file_size > 1.98 * 1024 * 1024 * 1024

        async def pyrogram_progress(current, total):
            if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()):
                if hasattr(item, "_client_ref") and item._client_ref.get("client"):
                    item._client_ref["client"].stop_transmission()
                return

            item.state = TransferState.UPLOADING
            item.update_progress(current, total)
            # Sync with legacy progress cache for backward compatibility
            PROGRESS_CACHE[item.id] = ("running", current, total, item.filename)

        item.state = TransferState.UPLOADING
        self.store.mark_dirty()

        # Resilient loop with exponential backoff
        while item.retry_count <= item.max_retries and self._running:
            if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()) or item.id in STOP_TRANSMISSION:
                item.state = TransferState.CANCELLED
                item.error_reason = "Cancelled by user"
                item.completion_time = time.time()
                self._cleanup_temp_file(item)
                self.store.mark_dirty()
                return

            client = _pick_flood_safe_client(premium_required)
            while client is None and self._running:
                await _wait_for_any_client()
                if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()):
                    item.state = TransferState.CANCELLED
                    self._cleanup_temp_file(item)
                    self.store.mark_dirty()
                    return
                client = _pick_flood_safe_client(premium_required)

            if not client:
                item.state = TransferState.FAILED
                item.error_reason = "No Telegram clients connected or available"
                item.completion_time = time.time()
                self.store.mark_dirty()
                return

            item._client_ref["client"] = client

            try:
                async with tg_gate.send_slot():
                    if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()):
                        item.state = TransferState.CANCELLED
                        self._cleanup_temp_file(item)
                        self.store.mark_dirty()
                        return

                    message = await client.send_document(
                        STORAGE_CHANNEL,
                        file_path,
                        progress=pyrogram_progress,
                        disable_notification=True,
                    )

                if message is None:
                    item.state = TransferState.CANCELLED
                    item.error_reason = "Cancelled by user"
                    item.completion_time = time.time()
                    self._cleanup_temp_file(item)
                    self.store.mark_dirty()
                    return

                tg_gate.note_success()
                actual_size = (
                    message.photo or message.document or message.video or message.audio or message.sticker
                ).file_size

                clean_filename = unquote_plus(item.filename)
                new_item_id = drive.new_file(
                    item.target_path, clean_filename, message.id, actual_size, conflict=item.conflict_mode
                )

                # Rich properties extraction
                try:
                    from utils.properties import MetadataWorker
                    new_file_obj = drive.find_item_by_id(new_item_id)
                    if new_file_obj:
                        MetadataWorker._process_item(new_file_obj, file_path)
                        drive.save()
                except Exception as meta_err:
                    logger.debug(f"Post-upload metadata extraction note: {meta_err}")

                # Trigger backup
                asyncio.create_task(backup_drive_data(loop=False))

                # Success State
                item.state = TransferState.COMPLETED
                item.transferred = actual_size
                item.size = actual_size
                item.percentage = 100.0
                item.speed = 0.0
                item.speed_formatted = "0 B/s"
                item.eta = None
                item.eta_formatted = ""
                item.completion_time = time.time()
                PROGRESS_CACHE[item.id] = ("completed", actual_size, actual_size, item.filename)

                # Generate thumbnail cache if media
                try:
                    ext = item.filename.rsplit(".", 1)[-1].lower() if "." in item.filename else ""
                    if ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "tiff"]:
                        from PIL import Image
                        thumb_cache_dir = Path("./cache/thumbs")
                        thumb_cache_dir.mkdir(parents=True, exist_ok=True)
                        with Image.open(str(file_path)) as img:
                            img = img.convert("RGB")
                            img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                            img.save(thumb_cache_dir / f"{message.id}.jpg", format="JPEG", quality=75, optimize=True)
                except Exception as thumb_err:
                    logger.debug(f"Thumbnail pre-generation note: {thumb_err}")

                # Clean temporary file
                self._cleanup_temp_file(item)
                self.store.mark_dirty()
                await self.store.save()
                logger.info(f"Transfer Manager: Successfully uploaded {item.id} ({item.filename})")
                return

            except FloodWait as fw:
                wait_time = float(fw.value)
                c_key = _client_key(client)
                has_alt = (_pick_flood_safe_client(premium_required) is not None)
                logger.warning(
                    f"Telegram FloodWait {wait_time:.0f}s on upload {item.id} (Attempt {item.retry_count + 1}/{item.max_retries}). "
                    f"Alternative bot available: {has_alt}"
                )
                tg_gate.note_flood(c_key, wait_time, has_alternatives=has_alt)
                item.state = TransferState.RETRYING
                item.error_reason = f"Telegram rate-limit (FloodWait {int(wait_time)}s)"
                item._flood_expires = time.monotonic() + wait_time
                item.retry_count += 1
                PROGRESS_CACHE[item.id] = ("waiting", 0, item.size, item.filename)
                self.store.mark_dirty()
                if item.retry_count > item.max_retries:
                    item.state = TransferState.FAILED
                    item.error_reason = "Exhausted max retries due to persistent FloodWait"
                    item.completion_time = time.time()
                    self._cleanup_temp_file(item)
                    self.store.mark_dirty()
                    return
                if has_alt:
                    # Switch immediately to available alternative client
                    continue
                await asyncio.sleep(min(wait_time + 0.5, 60.0))

            except Exception as exc:
                item.retry_count += 1
                logger.warning(f"Upload {item.id} error (Attempt {item.retry_count}/{item.max_retries}): {exc}")
                if item.retry_count > item.max_retries:
                    item.state = TransferState.FAILED
                    item.error_reason = str(exc)
                    item.completion_time = time.time()
                    self._cleanup_temp_file(item)
                    PROGRESS_CACHE[item.id] = ("error", 0, item.size, item.filename)
                    self.store.mark_dirty()
                    await self.store.save()
                    return

                item.state = TransferState.RETRYING
                item.error_reason = str(exc)
                self.store.mark_dirty()
                # Exponential backoff with jitter
                backoff = min(60.0, (2.0 ** (item.retry_count - 1)) * 2.0 + random.uniform(0.5, 2.0))
                await asyncio.sleep(backoff)

    async def _process_download(self, item: TransferItem):
        from techzdl import TechZDL
        from utils.downloader import validate_download_url, STOP_DOWNLOAD, DOWNLOAD_PROGRESS

        item.state = TransferState.PREPARING
        self.store.mark_dirty()

        try:
            clean_url = validate_download_url(item.source_url or "")
        except Exception as e:
            item.state = TransferState.FAILED
            item.error_reason = f"Invalid URL: {e}"
            item.completion_time = time.time()
            self.store.mark_dirty()
            return

        async def dl_progress_callback(status, current, total, tid):
            if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()):
                return
            item.state = TransferState.DOWNLOADING
            item.update_progress(current, total)
            DOWNLOAD_PROGRESS[item.id] = (status, current, total)

        while item.retry_count <= item.max_retries and self._running:
            if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()) or item.id in STOP_DOWNLOAD:
                item.state = TransferState.CANCELLED
                item.error_reason = "Cancelled by user"
                item.completion_time = time.time()
                self._cleanup_temp_file(item)
                self.store.mark_dirty()
                return

            try:
                item.state = TransferState.DOWNLOADING
                downloader = TechZDL(
                    clean_url,
                    output_dir=CACHE_DIR,
                    debug=False,
                    progress_callback=dl_progress_callback,
                    progress_args=(item.id,),
                    max_retries=3,
                    single_threaded=item.single_threaded,
                )

                await downloader.start(in_background=True)
                await asyncio.sleep(2)

                while downloader.is_running and self._running:
                    if item.state == TransferState.CANCELLED or (item._cancel_event and item._cancel_event.is_set()) or item.id in STOP_DOWNLOAD:
                        await downloader.stop()
                        item.state = TransferState.CANCELLED
                        item.error_reason = "Cancelled by user"
                        item.completion_time = time.time()
                        self._cleanup_temp_file(item)
                        self.store.mark_dirty()
                        return
                    await asyncio.sleep(1)

                if downloader.download_success is False:
                    raise downloader.download_error or RuntimeError("Remote download failed")

                output_path = str(downloader.output_path)
                item.temp_file_path = output_path
                item.size = downloader.total_size
                item.transferred = downloader.total_size
                item.percentage = 100.0
                item.state = TransferState.COMPLETED
                item.completion_time = time.time()
                DOWNLOAD_PROGRESS[item.id] = ("completed", downloader.total_size, downloader.total_size)
                self.store.mark_dirty()
                await self.store.save()

                logger.info(f"Transfer Manager: Download complete for {item.id} -> Queuing for Cloud Upload")

                # Chain to Upload
                upload_id = f"up_{item.id}"
                await self.queue_upload(
                    file_path=output_path,
                    id=upload_id,
                    target_path=item.target_path,
                    filename=item.filename,
                    file_size=downloader.total_size,
                    conflict="keep_both"
                )
                return

            except Exception as e:
                item.retry_count += 1
                logger.warning(f"Download {item.id} attempt {item.retry_count}/{item.max_retries} failed: {e}")
                if item.retry_count > item.max_retries:
                    item.state = TransferState.FAILED
                    item.error_reason = str(e)
                    item.completion_time = time.time()
                    DOWNLOAD_PROGRESS[item.id] = ("error", 0, item.size)
                    self._cleanup_temp_file(item)
                    self.store.mark_dirty()
                    await self.store.save()
                    return

                item.state = TransferState.RETRYING
                item.error_reason = str(e)
                self.store.mark_dirty()
                backoff = min(60.0, (2.0 ** (item.retry_count - 1)) * 2.0 + random.uniform(0.5, 2.0))
                await asyncio.sleep(backoff)

    @staticmethod
    def _cleanup_temp_file(item: TransferItem):
        if item.temp_file_path:
            try:
                p = Path(item.temp_file_path)
                if p.exists() and p.is_file():
                    p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"Temp file cleanup note for {item.id}: {e}")


# Singleton instance
transfer_manager = TransferManager()
