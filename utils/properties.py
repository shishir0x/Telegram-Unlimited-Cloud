"""
Properties & Details System for Files and Folders
==================================================
Comprehensive Google Drive-style metadata extraction, recursive folder statistics,
activity history tracking, and property serialization for Telegram-Unlimited-Cloud.

Design Principles:
    - Never fabricate timestamps: accurately distinguish created, uploaded, modified,
      accessed, downloaded, previewed, and trashed dates.
    - Non-blocking execution: derived metadata and deep recursive folder stats
      are computed efficiently and cached with automated invalidation.
    - Complete privacy & security: internal Telegram bot tokens, session keys,
      and administrative secrets are strictly filtered from property responses.
    - Zero data loss / backward compatible: safely defaults missing attributes on legacy
      File and Folder objects loaded from existing drive.data pickles.
"""

import asyncio
import copy
import hashlib
import io
import json
import mimetypes
import os
import re
import struct
import time
import zipfile
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.logger import Logger

logger = Logger(__name__)

# Cache for expensive folder calculations: folder_id -> (timestamp, stats_dict)
_FOLDER_STATS_CACHE: Dict[str, Tuple[float, dict]] = {}
_FOLDER_STATS_TTL_SECONDS = 60.0  # 1 minute cache TTL before recalculation
_STATS_LOCK = asyncio.Lock()

# Throttle for high-volume activity logging (e.g. repeated previews within 5 mins)
_ACTIVITY_THROTTLE: Dict[str, float] = {}
_ACTIVITY_THROTTLE_WINDOW = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Date and Timestamp Helpers
# ---------------------------------------------------------------------------

def get_current_iso_time() -> str:
    """Returns current ISO-8601 formatted UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def format_display_date(date_val: Optional[Union[str, float, datetime]]) -> Optional[str]:
    """Formats raw date or timestamp into a clean human-readable date string."""
    if not date_val:
        return None
    try:
        if isinstance(date_val, (int, float)):
            dt = datetime.fromtimestamp(date_val, tz=timezone.utc)
            return dt.strftime("%B %d, %Y, %I:%M %p")
        
        date_str = str(date_val).strip()
        # Handle ISO format
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%B %d, %Y, %I:%M %p")
        
        # Handle standard %Y-%m-%d %H:%M:%S format
        if len(date_str) >= 19 and date_str[4] == "-" and date_str[7] == "-":
            dt = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%B %d, %Y, %I:%M %p")
        
        return date_str
    except Exception:
        return str(date_val)


def format_relative_date_group(date_val: Optional[Union[str, float, datetime]]) -> str:
    """Groups activity into 'Today', 'Yesterday', or 'Month Day, Year'."""
    if not date_val:
        return "Earlier"
    try:
        dt = None
        if isinstance(date_val, (int, float)):
            dt = datetime.fromtimestamp(date_val)
        else:
            date_str = str(date_val).strip()
            if "T" in date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone()
            elif len(date_str) >= 19 and date_str[4] == "-" and date_str[7] == "-":
                dt = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")

        if dt is None:
            return "Earlier"

        now = datetime.now()
        diff = now.date() - dt.date()
        if diff.days == 0:
            return "Today"
        elif diff.days == 1:
            return "Yesterday"
        elif diff.days < 7:
            return dt.strftime("%A")  # e.g., "Monday"
        elif dt.year == now.year:
            return dt.strftime("%b %d")  # e.g., "Aug 24"
        else:
            return dt.strftime("%b %d, %Y")
    except Exception:
        return "Earlier"


# ---------------------------------------------------------------------------
# Metadata Extraction Engine
# ---------------------------------------------------------------------------

class MetadataExtractor:
    """Extracts derived content properties (dimensions, codecs, pages, archives, hashes)."""

    @staticmethod
    def calculate_bytes_sha256(data: bytes) -> str:
        h = hashlib.sha256()
        h.update(data)
        return h.hexdigest()

    @staticmethod
    def calculate_file_sha256(file_path: Union[Path, str]) -> Optional[str]:
        fp = Path(file_path)
        if not fp.exists() or not fp.is_file():
            return None
        try:
            from utils.extra import is_low_memory_env
            file_size = fp.stat().st_size
            # On low-memory environments (Render 512MB), use fast sample hash for large files (>100MB)
            if is_low_memory_env() and file_size > 100 * 1024 * 1024:
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    h.update(f.read(1024 * 1024))
                    f.seek(max(0, file_size - 1024 * 1024))
                    h.update(f.read(1024 * 1024))
                h.update(str(file_size).encode())
                return f"fast-{h.hexdigest()}"

            h = hashlib.sha256()
            with open(fp, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Error computing sha256 for {file_path}: {e}")
            return None

    @staticmethod
    def extract_image_metadata(data_or_path: Union[bytes, Path, str]) -> Dict[str, Any]:
        """Extracts width, height, format, and color mode using Pillow."""
        meta = {}
        try:
            from PIL import Image
            img = None
            if isinstance(data_or_path, (bytes, bytearray)):
                img = Image.open(io.BytesIO(data_or_path))
            else:
                img = Image.open(data_or_path)
            
            with img:
                meta["width"] = img.width
                meta["height"] = img.height
                meta["format"] = img.format
                meta["mode"] = img.mode
                meta["dimensions"] = f"{img.width} × {img.height}"
        except Exception as e:
            logger.debug(f"Image metadata extraction note: {e}")
        return meta

    @staticmethod
    def extract_pdf_metadata(data_or_path: Union[bytes, Path, str]) -> Dict[str, Any]:
        """Extracts PDF page count using fast pure-Python byte inspection."""
        meta = {}
        try:
            raw_bytes = None
            if isinstance(data_or_path, (bytes, bytearray)):
                raw_bytes = bytes(data_or_path)
            elif isinstance(data_or_path, (str, Path)) and os.path.exists(data_or_path):
                # Read up to first 2MB and last 2MB for fast trailer/header inspection
                size = os.path.getsize(data_or_path)
                with open(data_or_path, "rb") as f:
                    if size <= 4 * 1024 * 1024:
                        raw_bytes = f.read()
                    else:
                        head = f.read(2 * 1024 * 1024)
                        f.seek(max(0, size - 2 * 1024 * 1024))
                        tail = f.read()
                        raw_bytes = head + b"\n...[TRUNCATED]...\n" + tail

            if raw_bytes:
                # 1. Count instances of /Type\s*/Page (excluding /Pages)
                page_matches = re.findall(rb"/Type\s*/Page(?![a-zA-Z])", raw_bytes)
                if page_matches:
                    meta["page_count"] = len(page_matches)
                else:
                    # 2. Check /Count <num> inside /Pages dict
                    count_match = re.search(rb"/Type\s*/Pages.*?/Count\s+(\d+)", raw_bytes, re.DOTALL)
                    if count_match:
                        meta["page_count"] = int(count_match.group(1))
        except Exception as e:
            logger.debug(f"PDF metadata extraction note: {e}")
        return meta

    @staticmethod
    def extract_archive_metadata(data_or_path: Union[bytes, Path, str]) -> Dict[str, Any]:
        """Extracts archive item count and uncompressed size without extracting files."""
        meta = {}
        try:
            if isinstance(data_or_path, (bytes, bytearray)):
                f_obj = io.BytesIO(data_or_path)
                if zipfile.is_zipfile(f_obj):
                    f_obj.seek(0)
                    with zipfile.ZipFile(f_obj, "r") as zf:
                        infolist = zf.infolist()
                        meta["archive_file_count"] = len(infolist)
                        meta["archive_uncompressed_size"] = sum(info.file_size for info in infolist)
                        meta["archive_compressed_size"] = sum(info.compress_size for info in infolist)
            elif isinstance(data_or_path, (str, Path)) and os.path.exists(data_or_path):
                file_path = Path(data_or_path)
                if zipfile.is_zipfile(file_path):
                    with zipfile.ZipFile(file_path, "r") as zf:
                        infolist = zf.infolist()
                        meta["archive_file_count"] = len(infolist)
                        meta["archive_uncompressed_size"] = sum(info.file_size for info in infolist)
                        meta["archive_compressed_size"] = sum(info.compress_size for info in infolist)
                elif tarfile.is_tarfile(file_path):
                    with tarfile.open(file_path, "r:*") as tf:
                        members = tf.getmembers()
                        meta["archive_file_count"] = len(members)
                        meta["archive_uncompressed_size"] = sum(m.size for m in members)
        except Exception as e:
            logger.debug(f"Archive metadata extraction note: {e}")
        return meta

    @staticmethod
    def extract_media_stream_metadata(data_or_path: Union[bytes, Path, str]) -> Dict[str, Any]:
        """Extracts audio/video duration, resolution, codecs, sample rate, bitrate from headers."""
        meta = {}
        try:
            header_bytes = b""
            if isinstance(data_or_path, (bytes, bytearray)):
                header_bytes = bytes(data_or_path[:1048576])  # First 1MB is enough for MP4/MP3/WAV headers
            elif isinstance(data_or_path, (str, Path)) and os.path.exists(data_or_path):
                with open(data_or_path, "rb") as f:
                    header_bytes = f.read(1048576)

            if not header_bytes:
                return meta

            # --- MP4 / MOV Container Parsing ---
            if b"ftyp" in header_bytes[:64] or b"moov" in header_bytes:
                # Search for 'mvhd' atom for timescale and duration
                mvhd_idx = header_bytes.find(b"mvhd")
                if mvhd_idx != -1 and len(header_bytes) >= mvhd_idx + 24:
                    version = header_bytes[mvhd_idx + 4]
                    if version == 0 and len(header_bytes) >= mvhd_idx + 24:
                        timescale = struct.unpack(">I", header_bytes[mvhd_idx + 16:mvhd_idx + 20])[0]
                        duration_units = struct.unpack(">I", header_bytes[mvhd_idx + 20:mvhd_idx + 24])[0]
                        if timescale > 0:
                            duration_secs = duration_units / timescale
                            meta["duration_seconds"] = round(duration_secs, 2)
                            meta["duration"] = format_duration(duration_secs)

                # Search for 'tkhd' atom for video dimensions
                tkhd_idx = header_bytes.find(b"tkhd")
                if tkhd_idx != -1 and len(header_bytes) >= tkhd_idx + 84:
                    # Width and height are 16.16 fixed-point integers at the end of tkhd atom
                    width_raw = struct.unpack(">I", header_bytes[tkhd_idx + 76:tkhd_idx + 80])[0]
                    height_raw = struct.unpack(">I", header_bytes[tkhd_idx + 80:tkhd_idx + 84])[0]
                    width = width_raw >> 16
                    height = height_raw >> 16
                    if 0 < width < 10000 and 0 < height < 10000:
                        meta["width"] = width
                        meta["height"] = height
                        meta["resolution"] = f"{width} × {height}"

                # Search for codec fourcc codes
                if b"avc1" in header_bytes or b"h264" in header_bytes:
                    meta["video_codec"] = "H.264 / AVC"
                elif b"hvc1" in header_bytes or b"hev1" in header_bytes:
                    meta["video_codec"] = "H.265 / HEVC"
                elif b"vp09" in header_bytes:
                    meta["video_codec"] = "VP9"
                elif b"av01" in header_bytes:
                    meta["video_codec"] = "AV1"

                if b"mp4a" in header_bytes:
                    meta["audio_codec"] = "AAC"
                elif b"opus" in header_bytes:
                    meta["audio_codec"] = "Opus"

            # --- WAV Header Parsing ---
            elif header_bytes[:4] == b"RIFF" and header_bytes[8:12] == b"WAVE":
                fmt_idx = header_bytes.find(b"fmt ")
                if fmt_idx != -1 and len(header_bytes) >= fmt_idx + 24:
                    channels = struct.unpack("<H", header_bytes[fmt_idx + 10:fmt_idx + 12])[0]
                    sample_rate = struct.unpack("<I", header_bytes[fmt_idx + 12:fmt_idx + 16])[0]
                    byte_rate = struct.unpack("<I", header_bytes[fmt_idx + 16:fmt_idx + 20])[0]
                    meta["audio_channels"] = channels
                    meta["audio_sample_rate"] = f"{sample_rate} Hz"
                    meta["audio_codec"] = "PCM"
                    if byte_rate > 0:
                        meta["audio_bitrate"] = f"{(byte_rate * 8) // 1000} kbps"
                        data_idx = header_bytes.find(b"data")
                        if data_idx != -1 and len(header_bytes) >= data_idx + 8:
                            data_size = struct.unpack("<I", header_bytes[data_idx + 4:data_idx + 8])[0]
                            duration_secs = data_size / byte_rate
                            meta["duration_seconds"] = round(duration_secs, 2)
                            meta["duration"] = format_duration(duration_secs)

            # --- MP3 Header Parsing ---
            elif header_bytes[:3] == b"ID3" or (len(header_bytes) >= 4 and header_bytes[0] == 0xFF and (header_bytes[1] & 0xE0) == 0xE0):
                meta["audio_codec"] = "MP3 (MPEG Audio)"
                # Find MPEG sync word
                sync_idx = 0
                if header_bytes[:3] == b"ID3" and len(header_bytes) >= 10:
                    # Skip ID3 tag
                    tag_size = ((header_bytes[6] & 0x7F) << 21) | ((header_bytes[7] & 0x7F) << 14) | ((header_bytes[8] & 0x7F) << 7) | (header_bytes[9] & 0x7F)
                    sync_idx = 10 + tag_size

                if len(header_bytes) > sync_idx + 4:
                    for i in range(sync_idx, min(sync_idx + 4096, len(header_bytes) - 4)):
                        if header_bytes[i] == 0xFF and (header_bytes[i + 1] & 0xE0) == 0xE0:
                            header_int = struct.unpack(">I", header_bytes[i:i + 4])[0]
                            bitrate_idx = (header_int >> 12) & 0x0F
                            samplerate_idx = (header_int >> 10) & 0x03
                            
                            bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
                            samplerates = [44100, 48000, 32000, 0]
                            
                            if bitrate_idx < len(bitrates) and bitrates[bitrate_idx] > 0:
                                meta["audio_bitrate"] = f"{bitrates[bitrate_idx]} kbps"
                            if samplerate_idx < len(samplerates) and samplerates[samplerate_idx] > 0:
                                meta["audio_sample_rate"] = f"{samplerates[samplerate_idx]} Hz"
                            break

        except Exception as e:
            logger.debug(f"Media stream header extraction note: {e}")
        return meta

    @classmethod
    def extract_all(cls, file_path: Union[Path, str], mime_type: Optional[str] = None) -> Dict[str, Any]:
        """Extracts all applicable metadata (dimensions, duration, codecs, pages, archives, sha256) for a file."""
        fp = Path(file_path)
        meta: Dict[str, Any] = {}
        if not fp.exists() or not fp.is_file():
            return meta

        # 1. Checksum
        sha = cls.calculate_file_sha256(fp)
        if sha:
            meta["sha256"] = sha

        # 2. MIME-specific parsers
        m = (mime_type or "").lower()
        if not m:
            m = mimetypes.guess_type(fp.name)[0] or ""

        if m.startswith("image/"):
            meta.update(cls.extract_image_metadata(fp))
        elif m == "application/pdf" or fp.name.lower().endswith(".pdf"):
            meta.update(cls.extract_pdf_metadata(fp))
        elif m in ("application/zip", "application/x-tar", "application/gzip", "application/x-gzip") or fp.name.lower().endswith((".zip", ".tar", ".gz", ".tar.gz", ".tgz")):
            meta.update(cls.extract_archive_metadata(fp))
        elif m.startswith("video/") or m.startswith("audio/") or fp.name.lower().endswith((".mp4", ".mkv", ".mov", ".webm", ".avi", ".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg")):
            meta.update(cls.extract_media_stream_headers(fp))

        return meta


def format_duration(seconds: float) -> str:
    """Formats seconds into mm:ss or hh:mm:ss."""
    try:
        s = int(round(seconds))
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
    except Exception:
        return f"{seconds}s"


def format_bytes(size: Union[int, float, None]) -> str:
    """Formats bytes into human readable binary units."""
    if size is None or size == 0:
        return "0 B"
    try:
        size = float(size)
        for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
            if size < 1024.0 or unit == "PB":
                return f"{size:.2f} {unit}".replace(".00 ", " ")
            size /= 1024.0
        return f"{size:.2f} GB"
    except Exception:
        return f"{size} B"


# ---------------------------------------------------------------------------
# Activity Tracking Engine
# ---------------------------------------------------------------------------

class ActivityTracker:
    """Records and formats user and lifecycle events on items."""

    @staticmethod
    def record_activity(
        item: Any,
        action: str,
        actor: str = "Admin (You)",
        details: Optional[dict] = None,
        timestamp: Optional[str] = None
    ) -> None:
        """
        Appends a structured event to an item's activity history.
        Throttles high-frequency read actions (preview, view) to avoid log bloating.
        """
        if not item:
            return

        now_ts = time.time()
        item_id = getattr(item, "id", "unknown")
        throttle_key = f"{item_id}:{action}"

        # Throttle preview/view/open actions within window
        if action in ["previewed", "opened", "viewed", "downloaded"]:
            last_ts = _ACTIVITY_THROTTLE.get(throttle_key, 0.0)
            if now_ts - last_ts < _ACTIVITY_THROTTLE_WINDOW:
                return

            # Bound cache to avoid memory leak under high item volume
            if len(_ACTIVITY_THROTTLE) > 300:
                expired = [k for k, ts in _ACTIVITY_THROTTLE.items() if now_ts - ts > _ACTIVITY_THROTTLE_WINDOW]
                for k in expired:
                    _ACTIVITY_THROTTLE.pop(k, None)
                if len(_ACTIVITY_THROTTLE) > 1000:
                    for old_k in list(_ACTIVITY_THROTTLE.keys())[: len(_ACTIVITY_THROTTLE) - 500]:
                        _ACTIVITY_THROTTLE.pop(old_k, None)

            _ACTIVITY_THROTTLE[throttle_key] = now_ts

        if not hasattr(item, "activity_history") or not isinstance(item.activity_history, list):
            item.activity_history = []

        event = {
            "id": hashlib.md5(f"{item_id}:{action}:{now_ts}".encode()).hexdigest()[:10],
            "action": action,
            "actor": actor,
            "timestamp": timestamp or get_current_iso_time(),
            "details": details or {}
        }

        # Keep latest 50 activities per item
        item.activity_history.insert(0, event)
        if len(item.activity_history) > 50:
            item.activity_history = item.activity_history[:50]

        # Update last accessed / modified timestamp on relevant actions
        if action in ["previewed", "opened", "viewed"]:
            item.viewed_at = event["timestamp"]
            item.accessed_at = event["timestamp"]
        elif action == "downloaded":
            item.downloaded_at = event["timestamp"]
            item.accessed_at = event["timestamp"]
        elif action in ["renamed", "moved", "copied"]:
            item.modified_at = event["timestamp"]
        elif action == "trashed":
            item.trashed_at = event["timestamp"]
        elif action == "restored":
            item.restored_at = event["timestamp"]

    @staticmethod
    def get_timeline(item: Any) -> List[Dict[str, Any]]:
        """Returns structured timeline entries grouped by relative dates."""
        raw_history = getattr(item, "activity_history", None) or []
        if not raw_history:
            # Generate baseline events if history is empty (e.g. uploaded event)
            upload_date = getattr(item, "upload_date", "") or getattr(item, "uploaded_at", "") or get_current_iso_time()
            raw_history = [{
                "id": "init_upload",
                "action": "uploaded",
                "actor": getattr(item, "owner", "Admin (You)") or "Admin (You)",
                "timestamp": upload_date,
                "details": {}
            }]

        timeline = []
        for event in raw_history:
            ts = event.get("timestamp")
            action = event.get("action", "modified")
            actor = event.get("actor", "Admin (You)")
            details = event.get("details", {})

            icon = "clock"
            description = f"{action.capitalize()} by {actor}"

            if action == "uploaded":
                icon = "upload"
                description = f"Uploaded by {actor}"
            elif action == "created":
                icon = "plus"
                description = f"Created by {actor}"
            elif action == "renamed":
                icon = "edit"
                old_n = details.get("old_name")
                description = f"Renamed from '{old_n}'" if old_n else f"Renamed by {actor}"
            elif action == "moved":
                icon = "folder"
                dest_p = details.get("dest_path", "")
                description = f"Moved to {dest_p}" if dest_p else f"Moved by {actor}"
            elif action == "copied":
                icon = "copy"
                src_n = details.get("src_name", "")
                description = f"Copied from '{src_n}'" if src_n else f"Copied by {actor}"
            elif action == "downloaded":
                icon = "download"
                description = f"Downloaded by {actor}"
            elif action in ["previewed", "opened", "viewed"]:
                icon = "eye"
                description = f"Previewed by {actor}"
            elif action == "shared":
                icon = "share"
                description = f"Share link created by {actor}"
            elif action == "share_revoked":
                icon = "lock"
                description = f"Share link revoked by {actor}"
            elif action == "trashed":
                icon = "trash"
                description = f"Moved to trash by {actor}"
            elif action == "restored":
                icon = "refresh"
                description = f"Restored from trash by {actor}"

            timeline.append({
                "id": event.get("id"),
                "action": action,
                "actor": actor,
                "icon": icon,
                "description": description,
                "timestamp": ts,
                "timestamp_formatted": format_display_date(ts),
                "display_date": format_display_date(ts),
                "date_group": format_relative_date_group(ts),
                "date_label": format_relative_date_group(ts),
                "details": details
            })

        return timeline

    @classmethod
    def get_grouped_timeline(cls, item: Any) -> List[Dict[str, Any]]:
        """Returns timeline grouped into date sections."""
        flat = cls.get_timeline(item)
        groups_dict: Dict[str, List[Dict[str, Any]]] = {}
        for ev in flat:
            label = ev.get("date_group") or "History"
            if label not in groups_dict:
                groups_dict[label] = []
            groups_dict[label].append(ev)

        return [{"date_label": label, "events": evs} for label, evs in groups_dict.items()]


# ---------------------------------------------------------------------------
# Folder Recursive Statistics Engine
# ---------------------------------------------------------------------------

class FolderStatsCalculator:
    """Calculates comprehensive recursive statistics for folders with TTL caching."""

    @staticmethod
    def invalidate_cache(folder_id: Optional[str] = None):
        """Invalidates stats cache for a specific folder or all folders."""
        global _FOLDER_STATS_CACHE
        if folder_id:
            _FOLDER_STATS_CACHE.pop(folder_id, None)
        else:
            _FOLDER_STATS_CACHE.clear()

    @staticmethod
    def calculate(folder: Any, drive_root: Optional[Any] = None) -> Dict[str, Any]:
        """
        Recursively calculates folder statistics.
        Returns:
            - total_size (recursive bytes)
            - direct_size (direct child files bytes)
            - total_files (recursive file count)
            - direct_files (direct child file count)
            - total_folders (recursive subfolder count)
            - direct_folders (direct child subfolder count)
            - largest_file: {name, size, id}
            - category_counts: images, videos, audio, documents, spreadsheets, presentations, archives, code, others
            - duplicate_count: count of duplicate file sizes
            - trash_count: trashed child items count
            - calculation_status: 'exact'
        """
        folder_id = getattr(folder, "id", "root")
        now = time.time()

        # Check memory cache
        if folder_id in _FOLDER_STATS_CACHE:
            cache_ts, cached_stats = _FOLDER_STATS_CACHE[folder_id]
            if now - cache_ts < _FOLDER_STATS_TTL_SECONDS:
                stats_copy = copy.deepcopy(cached_stats)
                stats_copy["calculation_status"] = "cached"
                return stats_copy

        total_size = 0
        direct_size = 0
        total_files = 0
        direct_files = 0
        total_folders = 0
        direct_folders = 0
        trash_count = 0

        largest_file = {"name": None, "size": 0, "id": None}
        seen_sizes = set()
        duplicate_count = 0

        category_counts = {
            "images": 0,
            "videos": 0,
            "audio": 0,
            "documents": 0,
            "spreadsheets": 0,
            "presentations": 0,
            "archives": 0,
            "code": 0,
            "others": 0
        }

        from utils.extra import get_file_details

        def classify_file(filename: str) -> str:
            _, category, ext, _ = get_file_details(filename)
            cat_lower = category.lower()
            if "image" in cat_lower:
                return "images"
            elif "video" in cat_lower:
                return "videos"
            elif "audio" in cat_lower:
                return "audio"
            elif "pdf" in cat_lower or "word" in cat_lower or "text" in cat_lower or "document" in cat_lower:
                return "documents"
            elif "spreadsheet" in cat_lower or "excel" in cat_lower:
                return "spreadsheets"
            elif "presentation" in cat_lower or "powerpoint" in cat_lower:
                return "presentations"
            elif "archive" in cat_lower:
                return "archives"
            elif "code" in cat_lower:
                return "code"
            return "others"

        # 1. Direct children inspection
        contents = getattr(folder, "contents", {}) or {}
        for child in contents.values():
            is_trash = getattr(child, "trash", False)
            if is_trash:
                trash_count += 1
                continue
            
            c_type = getattr(child, "type", "file")
            c_size = getattr(child, "size", 0) or 0
            if c_type == "file":
                direct_files += 1
                direct_size += c_size
            elif c_type == "folder":
                direct_folders += 1

        # 2. Recursive traversal
        def traverse(node):
            nonlocal total_size, total_files, total_folders, trash_count, largest_file, duplicate_count
            node_contents = getattr(node, "contents", {}) or {}
            for item in node_contents.values():
                if getattr(item, "trash", False):
                    trash_count += 1
                    continue
                
                item_type = getattr(item, "type", "file")
                raw_size = getattr(item, "size", 0) or 0
                try:
                    item_size = int(float(raw_size))
                except (ValueError, TypeError):
                    item_size = 0
                item_name = getattr(item, "name", "Unnamed")

                if item_type == "file":
                    total_files += 1
                    total_size += item_size
                    cat = classify_file(item_name)
                    category_counts[cat] += 1

                    if item_size > largest_file["size"]:
                        largest_file = {
                            "name": item_name,
                            "size": item_size,
                            "formatted_size": format_bytes(item_size),
                            "id": getattr(item, "id", None)
                        }

                    # Duplicate detection based on size
                    if item_size > 0:
                        if item_size in seen_sizes:
                            duplicate_count += 1
                        else:
                            seen_sizes.add(item_size)

                elif item_type == "folder":
                    total_folders += 1
                    traverse(item)

        traverse(folder)

        stats = {
            "folder_id": folder_id,
            "total_size": total_size,
            "total_size_bytes": total_size,
            "formatted_total_size": format_bytes(total_size),
            "total_size_formatted": format_bytes(total_size),
            "direct_size": direct_size,
            "direct_size_bytes": direct_size,
            "formatted_direct_size": format_bytes(direct_size),
            "total_files": total_files,
            "direct_files": direct_files,
            "total_folders": total_folders,
            "direct_folders": direct_folders,
            "largest_file": largest_file if largest_file["name"] else None,
            "largest_file_name": largest_file["name"] if largest_file["name"] else None,
            "largest_file_size": largest_file["size"] if largest_file["name"] else None,
            "largest_file_size_formatted": largest_file["formatted_size"] if largest_file["name"] else None,
            "category_counts": category_counts,
            "media_breakdown": category_counts,
            "duplicate_count": duplicate_count,
            "trash_count": trash_count,
            "calculation_status": "exact",
            "calculated_at": get_current_iso_time()
        }

        # Cache result (bounded to 300 items to avoid RAM bloat)
        if len(_FOLDER_STATS_CACHE) > 300:
            excess = len(_FOLDER_STATS_CACHE) - 300
            for old_k in list(_FOLDER_STATS_CACHE.keys())[:excess]:
                _FOLDER_STATS_CACHE.pop(old_k, None)
        _FOLDER_STATS_CACHE[folder_id] = (now, stats)
        return stats


# ---------------------------------------------------------------------------
# Properties Serializer and Formatter
# ---------------------------------------------------------------------------

class PropertiesFormatter:
    """Transforms File and Folder objects into rich, sanitized Google Drive properties schemas."""

    @staticmethod
    def get_file_properties(
        file_obj: Any,
        drive_instance: Any,
        is_admin: bool = True,
        request_base_url: str = ""
    ) -> Dict[str, Any]:
        """Builds complete properties schema for a File."""
        from utils.extra import get_file_details
        import config

        file_name = getattr(file_obj, "name", "Unnamed")
        file_id = getattr(file_obj, "id", "")
        tg_file_id = getattr(file_obj, "file_id", None)
        size = getattr(file_obj, "size", 0) or 0
        path = getattr(file_obj, "path", "/")

        mime_type, category, ext, icon = get_file_details(file_name)

        # Dates and timestamps
        upload_date_raw = getattr(file_obj, "upload_date", None) or getattr(file_obj, "uploaded_at", None)
        created_at = getattr(file_obj, "created_at", None)
        modified_at = getattr(file_obj, "modified_at", None) or upload_date_raw
        accessed_at = getattr(file_obj, "accessed_at", None) or getattr(file_obj, "viewed_at", None) or upload_date_raw
        downloaded_at = getattr(file_obj, "downloaded_at", None)
        viewed_at = getattr(file_obj, "viewed_at", None)
        trashed_at = getattr(file_obj, "trashed_at", None)
        restored_at = getattr(file_obj, "restored_at", None)

        # Content metadata
        meta_extra = getattr(file_obj, "metadata_extra", {}) or {}
        sha256_hash = getattr(file_obj, "sha256", None) or meta_extra.get("sha256")
        
        # Check thumbnail status and size
        thumb_size = 0
        thumb_available = False
        thumb_path = Path(f"./cache/thumbs/{tg_file_id}.jpg") if tg_file_id else None
        if thumb_path and thumb_path.exists():
            thumb_available = True
            try:
                thumb_size = thumb_path.stat().st_size
            except Exception:
                pass

        # Build full logical path and breadcrumbs
        full_logical_path = f"{path.rstrip('/')}/{file_name}".replace("//", "/")
        breadcrumbs = drive_instance.get_breadcrumbs(path) if drive_instance else []

        # Sharing status
        from utils.shareManager import get_active_shares_for_target
        item_full_id_path = (path.rstrip("/") + "/" + file_id).replace("//", "/")
        active_shares = get_active_shares_for_target(item_full_id_path)
        is_shared = len(active_shares) > 0
        share_info = None
        if is_shared:
            s = active_shares[0]
            share_info = {
                "token": s.get("token"),
                "has_password": s.get("has_password", False),
                "allow_download": s.get("allow_download", True),
                "expires_at": s.get("expires_at"),
                "expires_display": format_display_date(s.get("expires_at")) if s.get("expires_at") else "Never",
                "active_shares_count": len(active_shares)
            }

        # Activity timeline
        timeline = ActivityTracker.get_timeline(file_obj)

        properties = {
            "type": "file",
            "basic": {
                "id": file_id,
                "name": file_name,
                "extension": ext.lstrip("."),
                "mime_type": mime_type,
                "category": category,
                "icon": icon,
                "size_bytes": size,
                "formatted_size": format_bytes(size),
                "parent_path": path,
                "full_path": full_logical_path,
                "breadcrumbs": breadcrumbs,
                "tags": getattr(file_obj, "tags", []) or []
            },
            "timestamps": {
                "uploaded_at": upload_date_raw,
                "uploaded_display": format_display_date(upload_date_raw),
                "created_at": created_at,
                "created_display": format_display_date(created_at) if created_at else None,
                "modified_at": modified_at,
                "modified_display": format_display_date(modified_at),
                "accessed_at": accessed_at,
                "accessed_display": format_display_date(accessed_at),
                "downloaded_at": downloaded_at,
                "downloaded_display": format_display_date(downloaded_at) if downloaded_at else None,
                "viewed_at": viewed_at,
                "viewed_display": format_display_date(viewed_at) if viewed_at else None,
                "trashed_at": trashed_at,
                "trashed_display": format_display_date(trashed_at) if trashed_at else None,
                "restored_at": restored_at,
                "restored_display": format_display_date(restored_at) if restored_at else None
            },
            "content": {
                "sha256": sha256_hash,
                "checksum_status": "verified" if sha256_hash else "unverified",
                "width": meta_extra.get("width"),
                "height": meta_extra.get("height"),
                "dimensions": meta_extra.get("dimensions") or (f"{meta_extra['width']} × {meta_extra['height']}" if meta_extra.get("width") and meta_extra.get("height") else None),
                "duration": meta_extra.get("duration"),
                "duration_seconds": meta_extra.get("duration_seconds"),
                "resolution": meta_extra.get("resolution"),
                "video_codec": meta_extra.get("video_codec"),
                "audio_codec": meta_extra.get("audio_codec"),
                "audio_bitrate": meta_extra.get("audio_bitrate"),
                "audio_sample_rate": meta_extra.get("audio_sample_rate"),
                "page_count": meta_extra.get("page_count"),
                "archive_file_count": meta_extra.get("archive_file_count"),
                "archive_uncompressed_size": meta_extra.get("archive_uncompressed_size"),
                "formatted_archive_uncompressed_size": format_bytes(meta_extra.get("archive_uncompressed_size")) if meta_extra.get("archive_uncompressed_size") is not None else None,
                "is_duplicate": bool(meta_extra.get("is_duplicate", False))
            },
            "checksums": {
                "sha256": sha256_hash,
                "status": "verified" if sha256_hash else "unverified"
            },
            "storage": {
                "logical_size_bytes": size,
                "formatted_logical_size": format_bytes(size),
                "telegram_available": True,
                "telegram_channel_id": config.STORAGE_CHANNEL if is_admin else None,
                "thumbnail_available": thumb_available,
                "thumbnail_size_bytes": thumb_size,
                "formatted_thumbnail_size": format_bytes(thumb_size) if thumb_available else None,
                "integrity_status": "ok",
                "storage_backend": "Telegram Cloud Datacenter"
            },
            "sharing": {
                "owner": getattr(file_obj, "owner", "Admin (You)") or "Admin (You)",
                "is_shared": is_shared,
                "share_status": "Shared via Link" if is_shared else "Private (Only You)",
                "share_info": share_info
            },
            "activity": timeline
        }

        # Advanced / Admin section (Strictly filtered, no secrets)
        if is_admin:
            properties["advanced"] = {
                "internal_id": file_id,
                "telegram_message_id": tg_file_id,
                "telegram_channel": f"Storage Channel ID: {config.STORAGE_CHANNEL}" if config.STORAGE_CHANNEL else "Unconfigured",
                "metadata_version": "2.0",
                "processing_status": meta_extra.get("processing_status", "ready")
            }

        return properties

    @staticmethod
    def get_folder_properties(
        folder_obj: Any,
        drive_instance: Any,
        is_admin: bool = True,
        request_base_url: str = ""
    ) -> Dict[str, Any]:
        """Builds complete properties schema for a Folder."""
        from utils.shareManager import get_active_shares_for_target
        import config

        folder_name = getattr(folder_obj, "name", "Unnamed")
        folder_id = getattr(folder_obj, "id", "root")
        path = getattr(folder_obj, "path", "/")

        upload_date_raw = getattr(folder_obj, "upload_date", None) or getattr(folder_obj, "uploaded_at", None)
        created_at = getattr(folder_obj, "created_at", None)
        modified_at = getattr(folder_obj, "modified_at", None) or upload_date_raw
        accessed_at = getattr(folder_obj, "accessed_at", None) or upload_date_raw
        trashed_at = getattr(folder_obj, "trashed_at", None)
        restored_at = getattr(folder_obj, "restored_at", None)

        full_logical_path = f"{path.rstrip('/')}/{folder_name}".replace("//", "/") if folder_name != "/" else "/"
        breadcrumbs = drive_instance.get_breadcrumbs(path if folder_id == "root" else f"{path.rstrip('/')}/{folder_id}") if drive_instance else []

        # Calculate recursive statistics
        stats = FolderStatsCalculator.calculate(folder_obj, drive_instance.contents.get("/") if drive_instance else None)

        # Sharing status
        item_full_id_path = (path.rstrip("/") + "/" + folder_id).replace("//", "/")
        active_shares = get_active_shares_for_target(item_full_id_path)
        is_shared = len(active_shares) > 0
        share_info = None
        if is_shared:
            s = active_shares[0]
            share_info = {
                "token": s.get("token"),
                "has_password": s.get("has_password", False),
                "allow_download": s.get("allow_download", True),
                "expires_at": s.get("expires_at"),
                "expires_display": format_display_date(s.get("expires_at")) if s.get("expires_at") else "Never",
                "active_shares_count": len(active_shares)
            }

        timeline = ActivityTracker.get_timeline(folder_obj)

        properties = {
            "type": "folder",
            "basic": {
                "id": folder_id,
                "name": folder_name if folder_name != "/" else "My Drive (Root)",
                "category": "Folder",
                "icon": "folder",
                "parent_path": path,
                "full_path": full_logical_path,
                "breadcrumbs": breadcrumbs,
                "tags": getattr(folder_obj, "tags", []) or []
            },
            "timestamps": {
                "created_at": created_at,
                "created_display": format_display_date(created_at) if created_at else None,
                "uploaded_at": upload_date_raw,
                "uploaded_display": format_display_date(upload_date_raw),
                "modified_at": modified_at,
                "modified_display": format_display_date(modified_at),
                "accessed_at": accessed_at,
                "accessed_display": format_display_date(accessed_at),
                "trashed_at": trashed_at,
                "trashed_display": format_display_date(trashed_at) if trashed_at else None,
                "restored_at": restored_at,
                "restored_display": format_display_date(restored_at) if restored_at else None
            },
            "statistics": stats,
            "folder_stats": stats,
            "storage": {
                "total_recursive_size_bytes": stats["total_size"],
                "formatted_total_size": stats["formatted_total_size"],
                "direct_size_bytes": stats["direct_size"],
                "formatted_direct_size": stats["formatted_direct_size"],
                "storage_backend": "Virtual Folder Hierarchy / Telegram Storage"
            },
            "sharing": {
                "owner": getattr(folder_obj, "owner", "Admin (You)") or "Admin (You)",
                "is_shared": is_shared,
                "share_status": "Shared via Link" if is_shared else "Private (Only You)",
                "share_info": share_info
            },
            "activity": timeline
        }

        if is_admin:
            properties["advanced"] = {
                "internal_id": folder_id,
                "metadata_version": "2.0",
                "storage_channel": f"Channel: {config.STORAGE_CHANNEL}" if config.STORAGE_CHANNEL else "Unconfigured"
            }

        return properties


# ---------------------------------------------------------------------------
# Background Metadata Enrichment Worker
# ---------------------------------------------------------------------------

class MetadataWorker:
    """Asynchronously enriches file properties in background without blocking transfers."""

    _queue: asyncio.Queue = asyncio.Queue()
    _running: bool = False

    @classmethod
    async def start(cls):
        if cls._running:
            return
        cls._running = True
        asyncio.create_task(cls._worker_loop())
        logger.info("Metadata Enrichment Background Worker started.")

    @classmethod
    def enqueue(cls, file_obj: Any, local_path_or_bytes: Optional[Union[bytes, Path, str]] = None):
        """Enqueues a file for background metadata extraction."""
        cls._queue.put_nowait((file_obj, local_path_or_bytes))

    @classmethod
    async def _worker_loop(cls):
        while True:
            try:
                file_obj, data_source = await cls._queue.get()
                cls._process_item(file_obj, data_source)
                cls._queue.task_done()
            except Exception as e:
                logger.debug(f"Metadata worker exception: {e}")
                await asyncio.sleep(1)

    @classmethod
    def _process_item(cls, file_obj: Any, data_source: Optional[Union[bytes, Path, str]] = None):
        if not file_obj:
            return

        if not hasattr(file_obj, "metadata_extra") or not isinstance(file_obj.metadata_extra, dict):
            file_obj.metadata_extra = {}

        # If we have local bytes or a local file, extract metadata directly
        if data_source is not None:
            if isinstance(data_source, (bytes, bytearray)):
                if not getattr(file_obj, "sha256", None):
                    file_obj.sha256 = MetadataExtractor.calculate_bytes_sha256(data_source)
                
                # Image metadata
                img_meta = MetadataExtractor.extract_image_metadata(data_source)
                file_obj.metadata_extra.update(img_meta)

                # PDF metadata
                pdf_meta = MetadataExtractor.extract_pdf_metadata(data_source)
                file_obj.metadata_extra.update(pdf_meta)

                # Archive metadata
                arch_meta = MetadataExtractor.extract_archive_metadata(data_source)
                file_obj.metadata_extra.update(arch_meta)

                # Media stream metadata
                media_meta = MetadataExtractor.extract_media_stream_metadata(data_source)
                file_obj.metadata_extra.update(media_meta)

            elif isinstance(data_source, (str, Path)) and os.path.exists(data_source):
                if not getattr(file_obj, "sha256", None):
                    file_obj.sha256 = MetadataExtractor.calculate_file_sha256(data_source)
                
                img_meta = MetadataExtractor.extract_image_metadata(data_source)
                file_obj.metadata_extra.update(img_meta)

                pdf_meta = MetadataExtractor.extract_pdf_metadata(data_source)
                file_obj.metadata_extra.update(pdf_meta)

                arch_meta = MetadataExtractor.extract_archive_metadata(data_source)
                file_obj.metadata_extra.update(arch_meta)

                media_meta = MetadataExtractor.extract_media_stream_metadata(data_source)
                file_obj.metadata_extra.update(media_meta)

        file_obj.metadata_extra["processing_status"] = "complete"
        from utils.extra import clean_memory
        clean_memory()
