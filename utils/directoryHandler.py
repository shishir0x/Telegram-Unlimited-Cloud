from pathlib import Path
from typing import Union, Optional, Dict, Any, List, Tuple
import config, dill
import shutil
import hashlib
import json
import secrets
from pyrogram.types import InputMediaDocument, Message
from pyrogram.errors import FloodWait
import os, random, string, asyncio, time
from utils.logger import Logger
from datetime import datetime, timezone

logger = Logger(__name__)

cache_dir = Path("./cache")
cache_dir.mkdir(parents=True, exist_ok=True)
drive_cache_path = cache_dir / "drive.data"
drive_backup_path = cache_dir / "drive.data.bak"
drive_checksum_path = cache_dir / "drive.data.sha256"
drive_json_mirror_path = cache_dir / "tgdrive_backup.json"


def sanitize_name(name: str) -> str:
    """Sanitizes file and folder names against path traversal, control chars, and illegal symbols."""
    if not name:
        return "Unnamed"
    # Remove null bytes, newlines, and illegal path characters
    clean = str(name).replace("\x00", "").replace("\r", "").replace("\n", "").replace("/", "_").replace("\\", "_").strip()
    clean = clean.replace("..", "_")
    # Truncate extremely long names to 255 chars
    return clean[:255] if clean else "Unnamed"


def calculate_file_sha256(file_path: Union[Path, str]) -> str:
    """Calculates SHA256 digest of a local file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_file_checksum(file_path: Union[Path, str], checksum_or_path: Union[Path, str]) -> bool:
    """Verifies that the file matches its recorded SHA256 checksum or checksum file."""
    fp = Path(file_path)
    if not fp.exists():
        return False
    try:
        if isinstance(checksum_or_path, str) and len(checksum_or_path.strip()) == 64 and not os.path.exists(checksum_or_path):
            expected = checksum_or_path.strip()
        else:
            cp = Path(checksum_or_path)
            if not cp.exists():
                return False
            expected = cp.read_text(encoding="utf-8").strip()
        actual = calculate_file_sha256(fp)
        return expected.lower() == actual.lower()
    except Exception as e:
        logger.warning(f"Error checking file checksum: {e}")
        return False


def _count_total_drive_items(data) -> int:
    """Recursively counts all files and folders across all directories in drive contents."""
    if not data or not hasattr(data, "contents") or not isinstance(data.contents, dict):
        return 0
    total = 0
    for folder in data.contents.values():
        if hasattr(folder, "contents") and isinstance(folder.contents, dict):
            total += len(folder.contents)
    return total


def load_drive_data_from_file(file_path: Union[Path, str]) -> Optional["NewDriveData"]:
    """
    Robustly deserializes drive data supporting dill, standard pickle with cross-version encodings,
    and JSON export fallbacks across different Python/OS versions.
    """
    fp = Path(file_path)
    if not fp.exists() or fp.stat().st_size == 0:
        return None

    # 1. Try dill.load
    try:
        with open(fp, "rb") as f:
            obj = dill.load(f)
            if hasattr(obj, "contents") and "/" in obj.contents:
                return obj
    except Exception as e:
        logger.debug(f"dill.load skipped for {fp.name}: {e}")

    # 2. Try standard pickle.load with multiple encodings
    for enc in [None, "latin1", "bytes"]:
        try:
            with open(fp, "rb") as f:
                import pickle
                obj = pickle.load(f, encoding=enc) if enc else pickle.load(f)
                if hasattr(obj, "contents") and "/" in obj.contents:
                    return obj
        except Exception:
            pass

    # 3. Try reading as JSON
    try:
        content = fp.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict) and "contents" in data:
            return NewDriveData.from_dict(data)
    except Exception:
        pass

    return None


def ensure_drive_data(force_reload: bool = False):
    global DRIVE_DATA
    if DRIVE_DATA is None or force_reload:
        loaded = False
        # 1. Try loading primary drive.data with multi-format loader
        if drive_cache_path.exists():
            DRIVE_DATA = load_drive_data_from_file(drive_cache_path)
            if DRIVE_DATA is not None:
                loaded = True
                logger.info("Successfully loaded primary drive.data.")
                if drive_checksum_path.exists():
                    try:
                        expected = drive_checksum_path.read_text(encoding="utf-8").strip()
                        actual = calculate_file_sha256(drive_cache_path)
                        if expected.lower() != actual.lower():
                            logger.warning("drive.data SHA256 checksum mismatch (possible unclean shutdown).")
                    except Exception as chk_e:
                        logger.debug(f"Checksum verification skipped: {chk_e}")

        # 2. Try loading backup drive.data.bak if primary failed
        if not loaded and drive_backup_path.exists():
            DRIVE_DATA = load_drive_data_from_file(drive_backup_path)
            if DRIVE_DATA is not None:
                loaded = True
                logger.info("Successfully recovered drive data from drive.data.bak!")
                DRIVE_DATA.save()

        # 3. Try loading JSON mirror if binary failed
        if not loaded and drive_json_mirror_path.exists():
            DRIVE_DATA = load_drive_data_from_file(drive_json_mirror_path)
            if DRIVE_DATA is not None:
                loaded = True
                logger.info("Successfully recovered drive data from JSON mirror!")
                DRIVE_DATA.save()

        # 4. Initialize fresh root if no data exists
        if not loaded:
            logger.info("Initializing new drive data structure.")
            DRIVE_DATA = NewDriveData({"/": Folder("/", "/")}, [])
            DRIVE_DATA.last_modified = 0.0
            DRIVE_DATA.save()
            DRIVE_DATA.isUpdated = False
            DRIVE_DATA.last_modified = 0.0

    return DRIVE_DATA


def getRandomID():
    drive = ensure_drive_data()
    while True:
        id = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not drive:
            return id
        if id not in drive.used_ids:
            drive.used_ids.append(id)
            return id


def get_current_utc_time():
    return datetime.now(timezone.utc).strftime("Date - %Y-%m-%d | Time - %H:%M:%S")


class Folder:
    def __init__(self, name: str, path: str) -> None:
        self.name = sanitize_name(name) if name != "/" else "/"
        self.contents = {}
        if name == "/":
            self.id = "root"
        else:
            self.id = getRandomID()
        self.type = "folder"
        self.trash = False
        self.tags = []
        self.path = ("/" + path.strip("/") + "/").replace("//", "/")
        self.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.uploaded_at = self.upload_date
        self.modified_at = self.upload_date
        self.accessed_at = self.upload_date
        self.trashed_at = None
        self.restored_at = None
        self.owner = "Admin (You)"
        self.auth_hashes = []
        self.activity_history = []
        try:
            from utils.properties import ActivityTracker
            ActivityTracker.record_activity(self, "created" if self.id != "root" else "initialized")
        except Exception:
            pass

    def to_dict(self) -> dict:
        d = {
            "id": getattr(self, "id", "root"),
            "name": getattr(self, "name", "/"),
            "type": "folder",
            "trash": getattr(self, "trash", False),
            "tags": getattr(self, "tags", []),
            "path": getattr(self, "path", "/"),
            "upload_date": getattr(self, "upload_date", ""),
            "created_at": getattr(self, "created_at", ""),
            "uploaded_at": getattr(self, "uploaded_at", ""),
            "modified_at": getattr(self, "modified_at", ""),
            "accessed_at": getattr(self, "accessed_at", ""),
            "trashed_at": getattr(self, "trashed_at", None),
            "restored_at": getattr(self, "restored_at", None),
            "owner": getattr(self, "owner", "Admin (You)"),
            "auth_hashes": getattr(self, "auth_hashes", []),
            "activity_history": getattr(self, "activity_history", []),
            "contents": {}
        }
        if hasattr(self, "contents") and isinstance(self.contents, dict):
            for k, v in self.contents.items():
                if hasattr(v, "to_dict"):
                    d["contents"][k] = v.to_dict()
                elif isinstance(v, dict):
                    d["contents"][k] = v
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Folder":
        folder = cls.__new__(cls)
        folder.id = data.get("id", "root" if data.get("name") == "/" else "root")
        folder.name = data.get("name", "/")
        folder.type = "folder"
        folder.trash = bool(data.get("trash", False))
        folder.tags = list(data.get("tags", []))
        folder.path = data.get("path", "/")
        folder.upload_date = data.get("upload_date", "")
        folder.created_at = data.get("created_at", "")
        folder.uploaded_at = data.get("uploaded_at", folder.upload_date)
        folder.modified_at = data.get("modified_at", folder.upload_date)
        folder.accessed_at = data.get("accessed_at", folder.upload_date)
        folder.trashed_at = data.get("trashed_at")
        folder.restored_at = data.get("restored_at")
        folder.owner = data.get("owner", "Admin (You)")
        folder.auth_hashes = list(data.get("auth_hashes", []))
        folder.activity_history = list(data.get("activity_history", []))
        folder.contents = {}

        raw_contents = data.get("contents", {})
        if isinstance(raw_contents, dict):
            for k, child_data in raw_contents.items():
                if isinstance(child_data, dict):
                    ctype = child_data.get("type", "file")
                    if ctype == "folder":
                        folder.contents[k] = Folder.from_dict(child_data)
                    else:
                        folder.contents[k] = File.from_dict(child_data)
                elif hasattr(child_data, "type"):
                    folder.contents[k] = child_data
        return folder


class File:
    def __init__(
        self,
        name: str,
        file_id: int,
        size: int,
        path: str,
        created_at: Optional[str] = None
    ) -> None:
        self.name = sanitize_name(name)
        self.file_id = file_id
        self.id = getRandomID()
        self.size = size
        self.type = "file"
        self.trash = False
        self.tags = []
        self.path = path[:-1] if path[-1] == "/" else path
        self.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.created_at = created_at  # Unfabricated: None unless supplied by client metadata
        self.uploaded_at = self.upload_date
        self.modified_at = self.upload_date
        self.accessed_at = self.upload_date
        self.downloaded_at = None
        self.viewed_at = None
        self.trashed_at = None
        self.restored_at = None
        self.owner = "Admin (You)"
        self.sha256 = None
        self.metadata_extra = {}
        self.activity_history = []
        try:
            from utils.properties import ActivityTracker
            ActivityTracker.record_activity(self, "uploaded")
        except Exception:
            pass

    def to_dict(self) -> dict:
        return {
            "id": getattr(self, "id", ""),
            "name": getattr(self, "name", ""),
            "file_id": getattr(self, "file_id", 0),
            "size": getattr(self, "size", 0),
            "type": "file",
            "trash": getattr(self, "trash", False),
            "tags": getattr(self, "tags", []),
            "path": getattr(self, "path", "/"),
            "upload_date": getattr(self, "upload_date", ""),
            "created_at": getattr(self, "created_at", None),
            "uploaded_at": getattr(self, "uploaded_at", ""),
            "modified_at": getattr(self, "modified_at", ""),
            "accessed_at": getattr(self, "accessed_at", ""),
            "downloaded_at": getattr(self, "downloaded_at", None),
            "viewed_at": getattr(self, "viewed_at", None),
            "trashed_at": getattr(self, "trashed_at", None),
            "restored_at": getattr(self, "restored_at", None),
            "owner": getattr(self, "owner", "Admin (You)"),
            "sha256": getattr(self, "sha256", None),
            "metadata_extra": getattr(self, "metadata_extra", {}),
            "activity_history": getattr(self, "activity_history", [])
        }

    @classmethod
    def from_dict(cls, data: dict) -> "File":
        file = cls.__new__(cls)
        file.id = data.get("id", "")
        file.name = data.get("name", "Unnamed")
        file.file_id = int(data.get("file_id", 0))
        file.size = int(data.get("size", 0))
        file.type = "file"
        file.trash = bool(data.get("trash", False))
        file.tags = list(data.get("tags", []))
        file.path = data.get("path", "/")
        file.upload_date = data.get("upload_date", "")
        file.created_at = data.get("created_at")
        file.uploaded_at = data.get("uploaded_at", file.upload_date)
        file.modified_at = data.get("modified_at", file.upload_date)
        file.accessed_at = data.get("accessed_at", file.upload_date)
        file.downloaded_at = data.get("downloaded_at")
        file.viewed_at = data.get("viewed_at")
        file.trashed_at = data.get("trashed_at")
        file.restored_at = data.get("restored_at")
        file.owner = data.get("owner", "Admin (You)")
        file.sha256 = data.get("sha256")
        file.metadata_extra = dict(data.get("metadata_extra", {}))
        file.activity_history = list(data.get("activity_history", []))
        return file


class NewDriveData:
    def __init__(self, contents: dict, used_ids: list) -> None:
        self.contents = contents
        self.used_ids = used_ids
        self.isUpdated = False
        # Epoch seconds of the last local mutation. Embedded in every Telegram backup
        # so a stale remote snapshot can never overwrite newer local metadata on pull.
        self.last_modified = time.time()

    def to_dict(self) -> dict:
        d = {
            "used_ids": list(getattr(self, "used_ids", [])),
            "last_modified": float(getattr(self, "last_modified", 0.0)),
            "contents": {}
        }
        if hasattr(self, "contents") and isinstance(self.contents, dict):
            for k, v in self.contents.items():
                if hasattr(v, "to_dict"):
                    d["contents"][k] = v.to_dict()
                elif isinstance(v, dict):
                    d["contents"][k] = v
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "NewDriveData":
        used_ids = list(data.get("used_ids", []))
        raw_contents = data.get("contents", {})
        contents = {}
        if isinstance(raw_contents, dict):
            for k, v in raw_contents.items():
                if isinstance(v, dict):
                    ctype = v.get("type", "folder")
                    if ctype == "folder" or k == "/":
                        contents[k] = Folder.from_dict(v)
                    else:
                        contents[k] = File.from_dict(v)
                else:
                    contents[k] = v
        if "/" not in contents:
            contents["/"] = Folder("/", "/")
        drive = cls(contents, used_ids)
        drive.last_modified = float(data.get("last_modified", 0.0) or 0.0)
        drive.isUpdated = False
        return drive

    def save(self) -> None:
        """Atomically saves drive data to disk with automatic .bak backup copy, SHA256 checksum, and JSON mirror."""
        # Freshness stamp: bumped on every mutation (all mutators funnel through save()).
        self.last_modified = time.time()
        tmp_path = drive_cache_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "wb") as f:
                dill.dump(self, f, protocol=4)
            # Create/update backup before replacing primary
            if drive_cache_path.exists():
                try:
                    shutil.copy2(drive_cache_path, drive_backup_path)
                except Exception:
                    pass
            try:
                os.replace(tmp_path, drive_cache_path)
            except (PermissionError, OSError):
                # Windows atomic replacement fallback if target is momentarily locked
                time.sleep(0.05)
                try:
                    os.replace(tmp_path, drive_cache_path)
                except Exception:
                    shutil.copy2(tmp_path, drive_cache_path)
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
            
            # Compute and save SHA256 checksum
            try:
                chk = calculate_file_sha256(drive_cache_path)
                drive_checksum_path.write_text(chk, encoding="utf-8")
            except Exception as chk_e:
                logger.warning(f"Failed to write SHA256 checksum: {chk_e}")

            # Export JSON metadata mirror
            try:
                dict_snapshot = self.to_dict()
                json_tmp = drive_json_mirror_path.with_suffix(".tmp")
                json_tmp.write_text(json.dumps(dict_snapshot, indent=2, default=str), encoding="utf-8")
                os.replace(json_tmp, drive_json_mirror_path)
            except Exception as json_e:
                logger.debug(f"JSON mirror export skipped: {json_e}")

            self.isUpdated = True
            logger.info("Drive data saved successfully with SHA256 checksum & backup.")
        except Exception as e:
            logger.error(f"Failed to save drive data: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _ensure_folder_chain(self, segments: List[str]) -> Folder:
        """
        Walks a list of ID-path segments, creating any missing folders along the way
        (mkdir -p semantics). Preserves each requested segment as the folder id so
        ID-based paths used by clients remain stable across restarts and restores.
        """
        node: Folder = self.contents["/"]
        created_any = False
        for seg in segments:
            child = node.contents.get(seg)
            if (
                child is not None
                and getattr(child, "type", "") == "folder"
                and not getattr(child, "trash", False)
            ):
                node = child
                continue

            if getattr(node, "id", "") == "root":
                parent_loc = "/"
            else:
                parent_loc = f"{getattr(node, 'path', '').strip('/')}/{node.id}"

            folder = Folder(seg, parent_loc or "/")
            if folder.id != seg:
                if seg not in self.used_ids:
                    self.used_ids.append(seg)
                folder.id = seg
            node.contents[folder.id] = folder
            created_any = True
            logger.warning(f"Healed missing folder '{seg}' inside '{parent_loc}' (auto mkdir -p).")
            node = folder

        if created_any:
            self.save()
        return node

    def new_folder(self, path: str, name: str) -> None:
        clean_name = sanitize_name(name)
        logger.info(f"Creating new folder '{clean_name}' in path '{path}'.")

        folder = Folder(clean_name, path)
        directory_folder: Folder = self.contents["/"]
        if path and path != "/":
            directory_folder = self._ensure_folder_chain(
                [p for p in path.strip("/").split("/") if p]
            )
        directory_folder.contents[folder.id] = folder

        try:
            from utils.properties import FolderStatsCalculator
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass

        self.save()
        return folder.path + folder.id

    def resolve_or_create_folder_hierarchy(self, base_path: str, relative_folder_path: str) -> str:
        """
        Recursively resolves or creates nested subfolders by human names under base_path (mkdir -p semantics).
        Sanitizes path components to prevent directory traversal and collision issues.
        Returns the canonical destination folder path (e.g. '/PARENT_ID/CHILD_ID' or '/').
        """
        if not relative_folder_path or not relative_folder_path.strip():
            return base_path if base_path else "/"

        # Normalize base_node
        if not base_path or base_path == "/":
            current_node: Folder = self.contents["/"]
        else:
            dir_res = self.get_directory(base_path, is_admin=True)
            if isinstance(dir_res, tuple):
                current_node = dir_res[0]
            elif dir_res is not None:
                current_node = dir_res
            else:
                current_node = self._ensure_folder_chain([p for p in base_path.strip("/").split("/") if p])

        # Normalize and sanitize relative path segments
        rel_clean = relative_folder_path.replace("\\", "/").strip("/")
        segments = [p for p in rel_clean.split("/") if p and p not in (".", "..")]
        if not segments:
            if getattr(current_node, "id", "") == "root":
                return "/"
            return (getattr(current_node, "path", "/").rstrip("/") + "/" + current_node.id).replace("//", "/")

        created_any = False
        for seg in segments:
            clean_seg = sanitize_name(seg)
            if not clean_seg or clean_seg == "/":
                continue

            found_child = None
            if hasattr(current_node, "contents") and isinstance(current_node.contents, dict):
                if clean_seg in current_node.contents and getattr(current_node.contents[clean_seg], "type", "") == "folder" and not getattr(current_node.contents[clean_seg], "trash", False):
                    found_child = current_node.contents[clean_seg]
                else:
                    seg_lower = clean_seg.lower()
                    for child in current_node.contents.values():
                        if getattr(child, "type", "") == "folder" and not getattr(child, "trash", False):
                            if getattr(child, "name", "").lower() == seg_lower:
                                found_child = child
                                break

            if found_child is not None:
                current_node = found_child
            else:
                if getattr(current_node, "id", "") == "root":
                    parent_loc = "/"
                else:
                    parent_loc = (getattr(current_node, "path", "/").rstrip("/") + "/" + current_node.id).replace("//", "/")
                new_f = Folder(clean_seg, parent_loc)
                current_node.contents[new_f.id] = new_f
                created_any = True
                logger.info(f"Auto-created folder '{clean_seg}' (ID: {new_f.id}) under '{parent_loc}' for folder upload tree.")
                current_node = new_f

        if created_any:
            try:
                from utils.properties import FolderStatsCalculator
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()

        if getattr(current_node, "id", "") == "root":
            return "/"
        return (getattr(current_node, "path", "/").rstrip("/") + "/" + current_node.id).replace("//", "/")

    def ensure_folder_tree(self, base_path: str, folder_paths: List[str]) -> List[str]:
        """Ensures multiple nested folders exist under base_path, preserving empty folders."""
        results = []
        for fp in folder_paths:
            if fp and fp.strip():
                resolved = self.resolve_or_create_folder_hierarchy(base_path, fp)
                results.append(resolved)
        return results

    def new_file(self, path: str, name: str, file_id: int, size: int, conflict: str = "keep_both", created_at: Optional[str] = None) -> str:
        clean_name = sanitize_name(name)
        logger.info(f"Creating new file '{clean_name}' in path '{path}' (conflict mode: {conflict}).")

        if path == "/" or not path:
            directory_folder: Folder = self.contents["/"]
        else:
            paths = [p for p in path.strip("/").split("/") if p]
            directory_folder = self._ensure_folder_chain(paths)

        # Check for existing file with identical name in destination folder
        existing_file = None
        if hasattr(directory_folder, "contents"):
            for item in directory_folder.contents.values():
                if getattr(item, "type", "") == "file" and getattr(item, "name", "").lower() == clean_name.lower():
                    existing_file = item
                    break

        if existing_file and conflict == "replace":
            logger.info(f"Replacing existing file '{existing_file.name}' with new file_id {file_id}.")
            existing_file.file_id = file_id
            existing_file.size = size
            existing_file.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing_file.uploaded_at = existing_file.upload_date
            existing_file.modified_at = existing_file.upload_date
            try:
                from utils.properties import ActivityTracker, FolderStatsCalculator
                ActivityTracker.record_activity(existing_file, "uploaded")
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()
            return existing_file.id

        if existing_file and conflict == "keep_both":
            # Auto-increment name e.g. "report (1).pdf"
            base_name, ext = (clean_name.rsplit(".", 1)[0], "." + clean_name.rsplit(".", 1)[1]) if "." in clean_name else (clean_name, "")
            counter = 1
            existing_names = {getattr(item, "name", "").lower() for item in directory_folder.contents.values() if getattr(item, "type", "") == "file"}
            new_candidate = f"{base_name} ({counter}){ext}"
            while new_candidate.lower() in existing_names:
                counter += 1
                new_candidate = f"{base_name} ({counter}){ext}"
            clean_name = new_candidate
            logger.info(f"Renamed collision to '{clean_name}'.")

        file = File(clean_name, file_id, size, path, created_at=created_at)
        directory_folder.contents[file.id] = file

        try:
            from utils.properties import FolderStatsCalculator
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass

        self.save()
        return file.id

    def find_item_by_id(self, item_id: str):
        """Finds any file or folder by its random ID or 'root' across the entire drive."""
        if not item_id or item_id in ["root", "/"]:
            return self.contents.get("/")

        def traverse(folder):
            if hasattr(folder, "contents"):
                if item_id in folder.contents:
                    return folder.contents[item_id]
                for child in folder.contents.values():
                    if getattr(child, "type", "") == "folder":
                        res = traverse(child)
                        if res:
                            return res
            return None

        return traverse(self.contents.get("/"))

    def get_directory(
        self, path: str, is_admin: bool = True, auth: str = None
    ):
        clean_path = ("/" + (path or "").replace("/share_", "").replace("share_", "").strip("/")).replace("//", "/")
        folder_data: Folder = self.contents["/"]
        auth_success = False
        auth_home_path = None

        if auth and hasattr(folder_data, "auth_hashes") and auth in folder_data.auth_hashes:
            auth_success = True
            auth_home_path = "/"

        if clean_path and clean_path != "/":
            paths = [p for p in clean_path.strip("/").split("/") if p]
            for p in paths:
                if not hasattr(folder_data, "contents"):
                    logger.warning(f"Folder '{p}' not found in '{clean_path}'.")
                    return None

                # Check 1: Direct ID key match
                if p in folder_data.contents and getattr(folder_data.contents[p], "type", "") == "folder":
                    folder_data = folder_data.contents[p]
                elif p in folder_data.contents:
                    folder_data = folder_data.contents[p]
                else:
                    # Check 2: Match by folder name (case-insensitive)
                    matched_child = None
                    p_lower = p.lower()
                    for item in folder_data.contents.values():
                        if getattr(item, "type", "") == "folder":
                            if getattr(item, "name", "").lower() == p_lower:
                                matched_child = item
                                break
                    if matched_child:
                        folder_data = matched_child
                    else:
                        logger.warning(f"Folder '{p}' not found in '{clean_path}'.")
                        return None

                if auth and hasattr(folder_data, "auth_hashes") and folder_data.auth_hashes:
                    auth_str = str(auth)
                    if any(secrets.compare_digest(auth_str, str(h)) for h in folder_data.auth_hashes):
                        auth_success = True
                        auth_home_path = (
                            "/" + folder_data.path.strip("/") + "/" + folder_data.id
                        ).replace("//", "/")

        if not is_admin and not auth_success:
            logger.warning(f"Unauthorized access attempt to path '{clean_path}'.")
            return None

        if auth_success:
            logger.info(f"Authorization successful for path '{clean_path}'.")
            return folder_data, auth_home_path

        return folder_data

    def get_folder_auth(self, path: str) -> str:
        auth = getRandomID()
        clean = ("/" + (path or "").replace("/share_", "").replace("share_", "").strip("/")).replace("//", "/")
        folder_data: Folder = self.contents["/"]

        if clean and clean != "/":
            paths = [p for p in clean.strip("/").split("/") if p]
            for p in paths:
                if hasattr(folder_data, "contents"):
                    if p in folder_data.contents:
                        folder_data = folder_data.contents[p]
                    else:
                        p_lower = p.lower()
                        for item in folder_data.contents.values():
                            if getattr(item, "type", "") == "folder" and getattr(item, "name", "").lower() == p_lower:
                                folder_data = item
                                break

        if not hasattr(folder_data, "auth_hashes"):
            folder_data.auth_hashes = []
        folder_data.auth_hashes.append(auth)
        self.save()
        logger.info(f"Authorization hash generated for path '{clean}'.")
        return auth

    def get_human_path(self, item: Union[Folder, File]) -> str:
        """
        Computes the human-readable path of any file or folder object,
        e.g. 'C_Drive/Users/shishir0x/Pictures/photo.jpg'
        """
        parts = []
        curr_path = getattr(item, "path", "")
        folder_ids = [p for p in str(curr_path).strip("/").split("/") if p]
        folder = self.contents.get("/")
        for fid in folder_ids:
            if hasattr(folder, "contents") and fid in folder.contents:
                f = folder.contents[fid]
                parts.append(getattr(f, "name", fid))
                folder = f
            else:
                break
        parts.append(getattr(item, "name", ""))
        return "/".join(parts)

    def resolve_local_file_path(self, item: Union[Folder, File, str]) -> Optional[str]:
        """
        Resolves an item (or path) to a physical local file on disk if it exists.
        Handles:
          - Windows drives: 'C_Drive/...' -> 'C:\\...', 'D_Drive/...' -> 'D:\\...'
          - Relative workspace files: './downloads/...', 'cache/...'
          - Absolute paths: '/C_Drive/...' -> 'C:\\...'
        Returns canonical absolute path string if existing file, else None.
        """
        if isinstance(item, str):
            try:
                item_obj = self.get_file(item)
                if item_obj:
                    item = item_obj
            except Exception:
                pass

        if hasattr(item, "name"):
            hp = self.get_human_path(item)
        elif isinstance(item, str):
            hp = item.replace("\\", "/").strip("/")
        else:
            return None

        # 0. Check if item has an explicit device path or local path attached
        if hasattr(item, "device") and item.device:
            dev = str(item.device)
            if os.path.isfile(dev):
                return os.path.abspath(dev)
            cand = os.path.join(dev, getattr(item, "name", ""))
            if os.path.isfile(cand):
                return os.path.abspath(cand)

        # 1. Check Windows Drive patterns: C_Drive/..., D_Drive/..., etc.
        parts = hp.split("/", 1)
        if len(parts) == 2 and parts[0].endswith("_Drive") and len(parts[0]) == 7:
            letter = parts[0][0].upper()
            candidate = f"{letter}:\\" + parts[1].replace("/", "\\")
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        # 2. Check if the path directly exists on disk (relative or absolute)
        if os.path.isfile(hp):
            return os.path.abspath(hp)

        # 3. Check within downloads or workspace
        for base in [Path("."), Path("downloads"), Path("cache")]:
            candidate = base / hp
            if candidate.is_file():
                return str(candidate.resolve())

        return None

    def get_file(self, path: str, is_admin: bool = True) -> File:
        import urllib.parse
        raw_clean = (path or "").replace("/share_", "").replace("share_", "").strip("/")
        clean = urllib.parse.unquote(raw_clean)
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id_or_name = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id_or_name = clean

        folder_data = self.get_directory(folder_path, is_admin=True)
        if folder_data:
            if isinstance(folder_data, tuple):
                folder_data = folder_data[0]
            if hasattr(folder_data, "contents"):
                if file_id_or_name in folder_data.contents:
                    return folder_data.contents[file_id_or_name]
                file_id_or_name_lower = file_id_or_name.lower()
                for item in folder_data.contents.values():
                    if getattr(item, "type", "") == "file":
                        if getattr(item, "name", "") == file_id_or_name or getattr(item, "name", "").lower() == file_id_or_name_lower:
                            return item

        # Also try with raw_clean if different
        if raw_clean != clean and "/" in raw_clean:
            raw_folder_path = "/" + "/".join(raw_clean.split("/")[:-1])
            raw_file_id_or_name = raw_clean.split("/")[-1]
            raw_folder_data = self.get_directory(raw_folder_path, is_admin=True)
            if raw_folder_data:
                if isinstance(raw_folder_data, tuple):
                    raw_folder_data = raw_folder_data[0]
                if hasattr(raw_folder_data, "contents") and raw_file_id_or_name in raw_folder_data.contents:
                    return raw_folder_data.contents[raw_file_id_or_name]

        # Recursive fallback search by file_id or name.
        # SECURITY: skipped for non-admin (share-visitor) requests — otherwise a
        # valid token for folder A could fetch ANY same-named file drive-wide.
        if not is_admin:
            raise KeyError(f"File not found: {path}")

        file_id_or_name_lower = file_id_or_name.lower()

        def find_file(folder):
            if hasattr(folder, "contents"):
                if file_id_or_name in folder.contents and getattr(folder.contents[file_id_or_name], "type", None) == "file":
                    return folder.contents[file_id_or_name]
                for item in folder.contents.values():
                    if getattr(item, "type", None) == "file" and (getattr(item, "name", None) == file_id_or_name or getattr(item, "name", "").lower() == file_id_or_name_lower):
                        return item
                for child in folder.contents.values():
                    if getattr(child, "type", None) == "folder":
                        res = find_file(child)
                        if res:
                            return res
            return None

        found = find_file(self.contents.get("/"))
        if found:
            return found
        raise KeyError(f"File not found: {path}")

    def rename_file_folder(self, path: str, new_name: str) -> None:
        clean = path.strip("/")
        clean_new_name = sanitize_name(new_name)
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id = clean
        folder_data = self.get_directory(folder_path)
        target_item = None
        if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
            target_item = folder_data.contents[file_id]
        else:
            target_item = self.find_item_by_id(file_id)

        if target_item:
            old_name = getattr(target_item, "name", "")
            target_item.name = clean_new_name
            target_item.modified_at = datetime.now(timezone.utc).isoformat()
            try:
                from utils.properties import ActivityTracker, FolderStatsCalculator
                ActivityTracker.record_activity(target_item, "renamed", details={"old_name": old_name, "new_name": clean_new_name})
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()
            logger.info(f"Item at path '{path}' renamed from '{old_name}' to '{clean_new_name}'.")

    def trash_file_folder(self, path: str, trash: bool) -> None:
        action = "Trashing" if trash else "Restoring"
        clean = path.strip("/")
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id = clean
        folder_data = self.get_directory(folder_path)
        target_item = None
        if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
            target_item = folder_data.contents[file_id]
        else:
            target_item = self.find_item_by_id(file_id)

        if target_item:
            target_item.trash = trash
            if trash:
                target_item.trashed_at = datetime.now(timezone.utc).isoformat()
            else:
                target_item.restored_at = datetime.now(timezone.utc).isoformat()
            try:
                from utils.properties import ActivityTracker, FolderStatsCalculator
                ActivityTracker.record_activity(target_item, "trashed" if trash else "restored")
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()
            logger.info(f"Item at path '{path}' {action.lower()} successfully.")

    def get_trashed_files_folders(self):
        root_dir = self.get_directory("/")
        trash_data = {}

        def traverse_directory(folder):
            if hasattr(folder, "contents"):
                for item in folder.contents.values():
                    if item.type == "folder":
                        if item.trash:
                            trash_data[item.id] = item
                        else:
                            traverse_directory(item)
                    elif item.type == "file":
                        if item.trash:
                            trash_data[item.id] = item

        traverse_directory(root_dir)
        return trash_data

    def get_recent_files(self, limit: int = 50) -> dict:
        root_dir = self.get_directory("/")
        all_files = []

        def traverse(folder):
            if hasattr(folder, "contents"):
                for item in folder.contents.values():
                    if getattr(item, "trash", False):
                        continue
                    if getattr(item, "type", "") == "file":
                        all_files.append(item)
                    elif getattr(item, "type", "") == "folder":
                        traverse(item)

        traverse(root_dir)
        all_files.sort(key=lambda x: str(getattr(x, "upload_date", "")), reverse=True)
        recent_dict = {}
        for f in all_files[:limit]:
            recent_dict[f.id] = f
        return recent_dict

    def add_tag(self, path: str, tag: str) -> list[str]:
        clean_tag = tag.strip()
        if not clean_tag:
            return []

        clean = path.strip("/")
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id = clean

        target_item = None
        folder_data = self.get_directory(folder_path)
        if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
            target_item = folder_data.contents[file_id]
        else:
            def find_item(folder):
                if hasattr(folder, "contents"):
                    if file_id in folder.contents:
                        return folder.contents[file_id]
                    for child in folder.contents.values():
                        if getattr(child, "type", "") == "folder":
                            res = find_item(child)
                            if res:
                                return res
                return None
            target_item = find_item(self.contents.get("/"))

        if not target_item:
            return []

        if not hasattr(target_item, "tags") or not isinstance(target_item.tags, list):
            target_item.tags = []

        if clean_tag not in target_item.tags:
            target_item.tags.append(clean_tag)
            self.save()
            logger.info(f"Added tag '{clean_tag}' to item at '{path}'.")

        return target_item.tags

    def remove_tag(self, path: str, tag: str) -> list[str]:
        clean_tag = tag.strip()
        clean = path.strip("/")
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id = clean

        target_item = None
        folder_data = self.get_directory(folder_path)
        if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
            target_item = folder_data.contents[file_id]
        else:
            def find_item(folder):
                if hasattr(folder, "contents"):
                    if file_id in folder.contents:
                        return folder.contents[file_id]
                    for child in folder.contents.values():
                        if getattr(child, "type", "") == "folder":
                            res = find_item(child)
                            if res:
                                return res
                return None
            target_item = find_item(self.contents.get("/"))

        if not target_item or not hasattr(target_item, "tags") or not isinstance(target_item.tags, list):
            return []

        if clean_tag in target_item.tags:
            target_item.tags.remove(clean_tag)
            self.save()
            logger.info(f"Removed tag '{clean_tag}' from item at '{path}'.")

        return target_item.tags

    def get_tagged_items(self, tag: str) -> dict:
        root_dir = self.get_directory("/")
        tagged_dict = {}
        target_tag = tag.strip().lower()

        def traverse(folder):
            if hasattr(folder, "contents"):
                for item in folder.contents.values():
                    if getattr(item, "trash", False):
                        continue
                    item_tags = [t.lower() for t in getattr(item, "tags", []) if isinstance(t, str)]
                    if target_tag in item_tags:
                        tagged_dict[item.id] = item
                    if getattr(item, "type", "") == "folder":
                        traverse(item)

        traverse(root_dir)
        return tagged_dict

    @staticmethod
    def _collect_file_ids(item) -> list[int]:
        ids = []
        if getattr(item, "type", None) == "file":
            fid = getattr(item, "file_id", None)
            if fid:
                try:
                    ids.append(int(fid))
                except (ValueError, TypeError):
                    pass
        elif hasattr(item, "contents"):
            for child in list(item.contents.values()):
                ids.extend(NewDriveData._collect_file_ids(child))
        return ids

    def delete_file_folder(self, path: str) -> list[int]:
        clean = path.strip("/")
        if "/" in clean:
            folder_path = "/" + "/".join(clean.split("/")[:-1])
            file_id = clean.split("/")[-1]
        else:
            folder_path = "/"
            file_id = clean

        deleted_msg_ids = []
        folder_data = self.get_directory(folder_path)
        if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
            deleted_item = folder_data.contents.pop(file_id)
            deleted_msg_ids.extend(self._collect_file_ids(deleted_item))
            try:
                from utils.properties import FolderStatsCalculator
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()
            logger.info(f"Item at path '{path}' deleted successfully. Collected {len(deleted_msg_ids)} Telegram msg IDs.")
            return deleted_msg_ids

        # Fallback global search to permanently delete
        def search_and_delete(folder):
            if hasattr(folder, "contents"):
                if file_id in folder.contents:
                    deleted_item = folder.contents.pop(file_id)
                    deleted_msg_ids.extend(self._collect_file_ids(deleted_item))
                    return True
                for child in list(folder.contents.values()):
                    if child.type == "folder":
                        if search_and_delete(child):
                            return True
            return False

        if search_and_delete(self.contents.get("/")):
            try:
                from utils.properties import FolderStatsCalculator
                FolderStatsCalculator.invalidate_cache()
            except Exception:
                pass
            self.save()
            logger.info(f"Item with ID '{file_id}' deleted via fallback search. Collected {len(deleted_msg_ids)} Telegram msg IDs.")

        return deleted_msg_ids

    def bulk_delete(self, paths: list[str]) -> list[int]:
        all_deleted_ids = []
        for path in paths:
            clean = path.strip("/")
            if "/" in clean:
                folder_path = "/" + "/".join(clean.split("/")[:-1])
                file_id = clean.split("/")[-1]
            else:
                folder_path = "/"
                file_id = clean

            folder_data = self.get_directory(folder_path)
            if folder_data and hasattr(folder_data, "contents") and file_id in folder_data.contents:
                deleted_item = folder_data.contents.pop(file_id)
                all_deleted_ids.extend(self._collect_file_ids(deleted_item))
            else:
                # Fallback search
                def search_and_pop(folder):
                    if hasattr(folder, "contents"):
                        if file_id in folder.contents:
                            deleted_item = folder.contents.pop(file_id)
                            all_deleted_ids.extend(self._collect_file_ids(deleted_item))
                            return True
                        for child in list(folder.contents.values()):
                            if child.type == "folder":
                                if search_and_pop(child):
                                    return True
                    return False
                search_and_pop(self.contents.get("/"))

        try:
            from utils.properties import FolderStatsCalculator
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass
        self.save()
        logger.info(f"Bulk deleted {len(paths)} item(s). Collected {len(all_deleted_ids)} Telegram msg IDs.")
        return all_deleted_ids

    def _find_folder_by_id(self, folder_id: str):
        def traverse(folder):
            if hasattr(folder, "contents"):
                if folder_id in folder.contents and folder.contents[folder_id].type == "folder":
                    return folder.contents[folder_id]
                for child in folder.contents.values():
                    if child.type == "folder":
                        res = traverse(child)
                        if res:
                            return res
            return None
        return traverse(self.contents.get("/"))

    def get_breadcrumbs(self, path: str) -> list:
        crumbs = [{"name": "My Drive", "path": "/", "id": "root"}]
        if path == "/" or not path or path == "redirect":
            return crumbs
        if path.startswith("/trash") or path == "trash":
            return [{"name": "Trash", "path": "/trash", "id": "trash"}]
        if path.startswith("/recent") or path == "recent":
            return [{"name": "My Drive", "path": "/", "id": "root"}, {"name": "Recent", "path": "/recent", "id": "recent"}]
        if path.startswith("/tags/") or path.startswith("tags/"):
            tag_name = path.replace("/tags/", "").replace("tags/", "").strip("/")
            import urllib.parse
            tag_decoded = urllib.parse.unquote(tag_name)
            return [{"name": "My Drive", "path": "/", "id": "root"}, {"name": f"Tag: {tag_decoded}", "path": path, "id": "tags"}]
        if "/search_" in path or path.startswith("search_") or path.startswith("/search"):
            q = path.split("_", 1)[1] if "_" in path else ""
            import urllib.parse
            q_decoded = urllib.parse.unquote(q)
            return [
                {"name": "My Drive", "path": "/", "id": "root"},
                {"name": f'Search: "{q_decoded}"', "path": path, "id": "search"}
            ]


        # Strip share prefix & any accidental query parameters
        clean = path.replace("/share_", "").replace("share_", "").strip("/")
        if "&" in clean:
            clean = clean.split("&")[0].strip("/")
        if not clean:
            return crumbs

        parts = [p for p in clean.split("/") if p]
        curr = self.contents.get("/")
        acc_path = ""
        is_share = path.startswith("/share_") or path.startswith("share_")

        for part in parts:
            matched_child = None
            if curr and hasattr(curr, "contents"):
                if part in curr.contents and getattr(curr.contents[part], "type", "") == "folder":
                    matched_child = curr.contents[part]
                elif part in curr.contents:
                    matched_child = curr.contents[part]
                else:
                    part_lower = part.lower()
                    for item in curr.contents.values():
                        if getattr(item, "type", "") == "folder" and getattr(item, "name", "").lower() == part_lower:
                            matched_child = item
                            break

            if matched_child:
                acc_path += f"/{matched_child.id}"
                target_path = f"/share_{acc_path.strip('/')}" if is_share else acc_path
                crumbs.append({"name": matched_child.name, "path": target_path, "id": matched_child.id})
                curr = matched_child
            else:
                found = self._find_folder_by_id(part)
                if found:
                    acc_path += f"/{found.id}"
                    target_path = f"/share_{acc_path.strip('/')}" if is_share else acc_path
                    crumbs.append({"name": found.name, "path": target_path, "id": found.id})
                    curr = found
                else:
                    acc_path += f"/{part}"
                    target_path = f"/share_{acc_path.strip('/')}" if is_share else acc_path
                    crumbs.append({"name": part, "path": target_path, "id": part})
                    curr = None

        return crumbs

    def move_file_folder(self, src_path: str, dest_folder_path: str) -> None:
        src_path = ("/" + src_path.strip("/")).replace("//", "/")
        dest_folder_path = ("/" + dest_folder_path.strip("/")).replace("//", "/")

        if len(src_path.strip("/").split("/")) > 1:
            src_parent_path = "/" + "/".join(src_path.strip("/").split("/")[:-1])
            src_item_id = src_path.strip("/").split("/")[-1]
        else:
            src_parent_path = "/"
            src_item_id = src_path.strip("/")

        # Cannot move into the same parent folder
        if src_parent_path == dest_folder_path:
            logger.info(f"Item '{src_item_id}' is already in destination '{dest_folder_path}'.")
            return

        # Prevent moving a folder into itself or its subfolders
        if dest_folder_path == src_path or dest_folder_path.startswith(src_path + "/"):
            raise ValueError("Cannot move a folder into itself or a subfolder.")

        src_folder = self.get_directory(src_parent_path)
        dest_folder = self.get_directory(dest_folder_path)

        if not dest_folder:
            raise KeyError(f"Destination folder not found: {dest_folder_path}")

        item = None
        if src_folder and hasattr(src_folder, "contents") and src_item_id in src_folder.contents:
            item = src_folder.contents.pop(src_item_id)
        else:
            # Fallback search across tree to locate item and remove from its real parent
            def locate_and_pop(folder):
                if hasattr(folder, "contents"):
                    if src_item_id in folder.contents:
                        return folder.contents.pop(src_item_id)
                    for child in list(folder.contents.values()):
                        if getattr(child, "type", None) == "folder":
                            res = locate_and_pop(child)
                            if res:
                                return res
                return None

            item = locate_and_pop(self.contents.get("/"))

        if not item:
            raise KeyError(f"Source item not found: {src_path}")

        # Update item's path
        if item.type == "folder":
            item.path = ("/" + dest_folder_path.strip("/") + "/").replace("//", "/")

            def update_children_paths(folder, parent_p):
                for child in folder.contents.values():
                    if child.type == "folder":
                        child.path = ("/" + parent_p.strip("/") + "/" + folder.id + "/").replace("//", "/")
                        update_children_paths(child, ("/" + parent_p.strip("/") + "/" + folder.id).replace("//", "/"))
                    else:
                        child.path = ("/" + parent_p.strip("/") + "/" + folder.id).replace("//", "/")

            update_children_paths(item, dest_folder_path)
        else:
            item.path = dest_folder_path

        dest_folder.contents[item.id] = item
        item.modified_at = datetime.now(timezone.utc).isoformat()
        try:
            from utils.properties import ActivityTracker, FolderStatsCalculator
            ActivityTracker.record_activity(item, "moved", details={"src_path": src_path, "dest_path": dest_folder_path})
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass
        self.save()
        logger.info(f"Moved item '{item.name}' ({item.id}) from '{src_path}' to '{dest_folder_path}'.")

    def copy_file_folder(self, src_path: str, dest_folder_path: str = None) -> str:
        import copy
        src_path = ("/" + src_path.strip("/")).replace("//", "/")
        if len(src_path.strip("/").split("/")) > 1:
            src_parent_path = "/" + "/".join(src_path.strip("/").split("/")[:-1])
            src_item_id = src_path.strip("/").split("/")[-1]
        else:
            src_parent_path = "/"
            src_item_id = src_path.strip("/")

        if not dest_folder_path:
            dest_folder_path = src_parent_path
        else:
            dest_folder_path = ("/" + dest_folder_path.strip("/")).replace("//", "/")

        src_folder = self.get_directory(src_parent_path)
        dest_folder = self.get_directory(dest_folder_path)

        if not dest_folder:
            raise KeyError(f"Destination folder not found: {dest_folder_path}")

        item = None
        if src_folder and hasattr(src_folder, "contents") and src_item_id in src_folder.contents:
            item = src_folder.contents[src_item_id]
        else:
            try:
                item = self.get_file(src_path)
            except Exception:
                # Fallback search by ID
                def find_any_item(folder):
                    if hasattr(folder, "contents"):
                        if src_item_id in folder.contents:
                            return folder.contents[src_item_id]
                        for child in folder.contents.values():
                            if getattr(child, "type", None) == "folder":
                                res = find_any_item(child)
                                if res:
                                    return res
                    return None
                item = find_any_item(self.contents.get("/"))

        if not item:
            raise KeyError(f"Source item not found: {src_path}")

        new_item = copy.deepcopy(item)
        new_item.id = getRandomID()
        new_item.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_item.uploaded_at = new_item.upload_date
        new_item.modified_at = new_item.upload_date
        new_item.accessed_at = new_item.upload_date
        new_item.activity_history = []

        # Copies must NOT inherit live share tokens from the original
        if hasattr(new_item, "auth_hashes"):
            new_item.auth_hashes = []

        # Rename copy
        if new_item.type == "file":
            if "." in new_item.name:
                name_p, ext_p = new_item.name.rsplit(".", 1)
                new_item.name = f"Copy of {name_p}.{ext_p}"
            else:
                new_item.name = f"Copy of {new_item.name}"
            new_item.path = dest_folder_path if dest_folder_path == "/" else dest_folder_path
        else:
            new_item.name = f"Copy of {new_item.name}"
            new_item.path = ("/" + dest_folder_path.strip("/") + "/").replace("//", "/")

            def regenerate_ids(folder, parent_p):
                for cid in list(folder.contents.keys()):
                    child = folder.contents.pop(cid)
                    child.id = getRandomID()
                    child.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    child.uploaded_at = child.upload_date
                    child.modified_at = child.upload_date
                    child.accessed_at = child.upload_date
                    child.activity_history = []
                    if hasattr(child, "auth_hashes"):
                        child.auth_hashes = []
                    if child.type == "folder":
                        child.path = ("/" + parent_p.strip("/") + "/" + folder.id + "/").replace("//", "/")
                        regenerate_ids(child, ("/" + parent_p.strip("/") + "/" + folder.id).replace("//", "/"))
                    else:
                        child.path = ("/" + parent_p.strip("/") + "/" + folder.id).replace("//", "/")
                    folder.contents[child.id] = child

            regenerate_ids(new_item, dest_folder_path)

        dest_folder.contents[new_item.id] = new_item
        try:
            from utils.properties import ActivityTracker, FolderStatsCalculator
            ActivityTracker.record_activity(new_item, "copied", details={"src_name": item.name})
            FolderStatsCalculator.invalidate_cache()
        except Exception:
            pass
        self.save()
        logger.info(f"Copied item '{item.name}' to '{dest_folder_path}' as '{new_item.name}' ({new_item.id}).")
        return new_item.id

    def search_file_folder(
        self,
        query: str = "",
        search_root: str = "/",
        item_type: str = "all",
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        date_filter: Optional[str] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
        extension: Optional[str] = None,
        file_type: Optional[str] = None,
    ):
        """
        Comprehensive drive-wide search engine with multi-criteria filtering:
        - Substring / token matching (case-insensitive & Unicode safe)
        - Type filtering: 'all', 'folder', 'file', 'document', 'spreadsheet', 'presentation', 'image', 'video', 'audio', 'pdf', 'archive', 'code'
        - Size range filtering: min_size / max_size in bytes
        - Date filtering: 'today', '7days', '30days', 'year', or date_after / date_before ISO strings
        - Location scoping: root '/' (default entire drive) or specific folder path
        - Extension filtering: e.g. 'pdf', 'png', or comma-separated extensions
        """
        if file_type and item_type == "all":
            item_type = file_type

        logger.info(
            f"Search initiated: query='{query}' root='{search_root}' type='{item_type}' "
            f"size=[{min_size}, {max_size}] date_filter='{date_filter}' ext='{extension}'"
        )
        import copy
        import unicodedata
        from datetime import datetime, timedelta

        CATEGORY_EXTENSIONS = {
            "document": {"pdf", "doc", "docx", "txt", "rtf", "odt", "pages", "csv", "xls", "xlsx", "ppt", "pptx", "md", "epub"},
            "spreadsheet": {"xls", "xlsx", "csv", "ods", "tsv", "numbers"},
            "presentation": {"ppt", "pptx", "key", "odp"},
            "image": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff", "ico", "heic", "avif"},
            "video": {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "3gp", "ts"},
            "audio": {"mp3", "wav", "ogg", "m4a", "flac", "aac", "wma", "opus", "m4r"},
            "pdf": {"pdf"},
            "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "iso", "xz", "tgz"},
            "code": {"py", "js", "html", "css", "json", "ts", "jsx", "tsx", "cpp", "c", "h", "hpp", "java", "go", "rs", "sh", "bat", "ps1", "yml", "yaml", "xml", "sql", "md"}
        }

        def normalize_str(s: str) -> str:
            if not s:
                return ""
            nfkd = unicodedata.normalize("NFKD", str(s))
            # Strip combining diacritics so 'Café Résumé' matches 'cafe resume'
            no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
            return no_accents.casefold().strip()

        # Parse inline query operators if present (e.g., "report type:pdf size:>10mb")
        raw_query = str(query or "").strip()
        extracted_type = item_type
        extracted_ext = extension
        clean_tokens = []

        for token in raw_query.split():
            token_lower = token.lower()
            if token_lower.startswith("type:") and len(token) > 5:
                extracted_type = token[5:].strip().lower()
            elif token_lower.startswith("ext:") and len(token) > 4:
                extracted_ext = token[4:].strip().lower().lstrip(".")
            elif token_lower.startswith("after:") and len(token) > 6:
                date_after = token[6:].strip()
            elif token_lower.startswith("before:") and len(token) > 7:
                date_before = token[7:].strip()
            else:
                clean_tokens.append(token)

        effective_query = " ".join(clean_tokens)
        query_norm = normalize_str(effective_query)
        query_words = query_norm.split() if query_norm else []

        # Target extension set
        target_exts = set()
        if extracted_ext:
            target_exts = {e.strip().lower().lstrip(".") for e in extracted_ext.split(",") if e.strip()}

        # Date boundary calculations
        now = datetime.now()
        min_date = None
        max_date = None

        if date_filter == "today":
            min_date = datetime(now.year, now.month, now.day)
        elif date_filter == "7days":
            min_date = now - timedelta(days=7)
        elif date_filter == "30days":
            min_date = now - timedelta(days=30)
        elif date_filter == "year":
            min_date = datetime(now.year, 1, 1)

        if date_after:
            try:
                min_date = datetime.strptime(date_after[:10], "%Y-%m-%d")
            except Exception:
                pass

        if date_before:
            try:
                max_date = datetime.strptime(date_before[:10], "%Y-%m-%d") + timedelta(days=1)
            except Exception:
                pass

        # Resolve search starting root
        root_res = self.get_directory(search_root) if search_root and search_root != "/" else None
        root_folder = (root_res[0] if isinstance(root_res, tuple) else root_res) if root_res else self.contents.get("/")
        if not root_folder:
            root_folder = self.contents.get("/")

        search_results = {}

        def detect_device(path_str: str) -> str:
            lower = path_str.lower()
            if any(k in lower for k in ["oneplus", "samsung", "pixel", "xiaomi", "redmi", "oppo", "vivo", "realme", "phone", "mobile", "android", "shared storage"]):
                return "Mobile"
            if any(k in lower for k in ["computer", "c_drive", "d_drive", "windows", "desktop", "laptop", "pc"]):
                return "Computer"
            return ""

        def parse_item_date(date_val) -> Optional[datetime]:
            if not date_val:
                return None
            try:
                date_str = str(date_val)[:19]
                if len(date_str) >= 10:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
            except Exception:
                pass
            return None

        def traverse_directory(folder, current_human_path=""):
            if not hasattr(folder, "contents"):
                return

            for item in folder.contents.values():
                if getattr(item, "trash", False):
                    continue

                item_name = getattr(item, "name", "")
                item_name_norm = normalize_str(item_name)
                item_type_val = getattr(item, "type", "file")
                item_size = getattr(item, "size", 0) or 0
                item_date_raw = getattr(item, "upload_date", "")
                item_date = parse_item_date(item_date_raw)

                # 1. Type filter verification
                if extracted_type and extracted_type != "all":
                    if extracted_type == "folder" and item_type_val != "folder":
                        if item_type_val == "folder":
                            traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                        continue
                    elif extracted_type == "file" and item_type_val != "file":
                        if item_type_val == "folder":
                            traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                        continue
                    elif extracted_type in CATEGORY_EXTENSIONS:
                        if item_type_val != "file":
                            if item_type_val == "folder":
                                traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                            continue
                        item_ext = item_name_norm.rsplit(".", 1)[-1] if "." in item_name_norm else ""
                        if item_ext not in CATEGORY_EXTENSIONS[extracted_type]:
                            continue

                # 2. Extension filter verification
                if target_exts:
                    item_ext = item_name_norm.rsplit(".", 1)[-1] if "." in item_name_norm else ""
                    if item_ext not in target_exts:
                        if item_type_val == "folder":
                            traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                        continue

                # 3. Size filter verification (applies to files and non-zero folders)
                if min_size is not None and item_size < min_size:
                    if item_type_val == "folder":
                        traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                    continue
                if max_size is not None and item_size > max_size:
                    if item_type_val == "folder":
                        traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                    continue

                # 4. Date filter verification
                if min_date and item_date and item_date < min_date:
                    if item_type_val == "folder":
                        traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                    continue
                if max_date and item_date and item_date > max_date:
                    if item_type_val == "folder":
                        traverse_directory(item, (current_human_path + "/" + item_name).replace("//", "/"))
                    continue

                # 5. Text / Token matching
                matches = True
                if query_words:
                    # All query tokens must match within the item name
                    for word in query_words:
                        if word not in item_name_norm:
                            matches = False
                            break

                parent_path = current_human_path if current_human_path else "/"
                full_path = (current_human_path + "/" + item_name).replace("//", "/") if current_human_path else f"/{item_name}"
                device_type = detect_device(full_path)

                if matches:
                    item_copy = copy.deepcopy(item)
                    item_copy.display_path = parent_path
                    item_copy.human_path = full_path
                    item_copy.device = device_type
                    search_results[item.id] = item_copy

                if item_type_val == "folder":
                    traverse_directory(item, full_path)

        start_path = "" if search_root in ["/", "root", ""] else getattr(root_folder, "name", "")
        traverse_directory(root_folder, start_path)
        logger.info(f"Search completed. Found {len(search_results)} matching items.")
        return search_results

    def collect_items_for_zip(self, paths: list[str]) -> tuple[str, list[dict]]:
        """
        Collects all files for given paths (files or folders).
        Returns (archive_suggested_name, list of {file_id, file_name, archive_path, size})
        """
        items_to_download = []
        seen_paths = set()
        default_zip_name = "Download"

        if len(paths) == 1:
            single_path = ("/" + paths[0].strip("/")).replace("//", "/")
            try:
                res = self.get_directory(single_path)
                folder = res[0] if isinstance(res, tuple) else res
                if folder and hasattr(folder, "name") and folder.name != "/":
                    default_zip_name = folder.name
            except Exception:
                pass

        def add_file(f, rel_archive_path):
            if rel_archive_path in seen_paths:
                return
            seen_paths.add(rel_archive_path)
            local_path = self.resolve_local_file_path(f)
            items_to_download.append({
                "file_id": getattr(f, "file_id", 0),
                "file_name": f.name,
                "archive_path": rel_archive_path,
                "size": getattr(f, "size", 0),
                "local_path": local_path,
            })

        def traverse_folder(folder, current_prefix=""):
            if not hasattr(folder, "contents"):
                return
            for child in folder.contents.values():
                if getattr(child, "trash", False):
                    continue
                if getattr(child, "type", "") == "file":
                    add_file(child, f"{current_prefix}/{child.name}".strip("/"))
                elif getattr(child, "type", "") == "folder":
                    traverse_folder(child, f"{current_prefix}/{child.name}".strip("/"))

        for p in paths:
            clean = ("/" + p.replace("/share_", "").replace("share_", "").strip("/")).replace("//", "/")
            
            # 1. Check if it's a folder
            folder_res = self.get_directory(clean)
            folder_obj = folder_res[0] if isinstance(folder_res, tuple) else folder_res
            if folder_obj and getattr(folder_obj, "type", "") == "folder":
                folder_root_name = folder_obj.name if folder_obj.name != "/" else "Drive"
                prefix = folder_root_name if (len(paths) > 1 or folder_root_name != "Drive") else ""
                traverse_folder(folder_obj, prefix)
                continue

            # 2. Check if it's a file
            try:
                file_obj = self.get_file(clean)
                if file_obj and getattr(file_obj, "type", "") == "file":
                    add_file(file_obj, file_obj.name)
            except Exception:
                pass

        return default_zip_name, items_to_download

    def get_drive_stats(self):
        total_files = 0
        total_bytes = 0

        def count_items(folder):
            nonlocal total_files, total_bytes
            if hasattr(folder, "contents"):
                for item in folder.contents.values():
                    if getattr(item, "trash", False):
                        continue
                    if item.type == "file":
                        total_files += 1
                        total_bytes += getattr(item, "size", 0)
                    elif item.type == "folder":
                        count_items(item)

        count_items(self.contents.get("/"))
        return total_files, total_bytes



class NewBotMode:
    def __init__(self, drive_data: NewDriveData) -> None:
        self.drive_data = drive_data

        # Set the current folder to root directory by default
        self.current_folder = "/"
        self.current_folder_name = "/ (root directory)"

    def set_folder(self, folder_path: str, name: str) -> None:
        self.current_folder = folder_path
        self.current_folder_name = name
        self.drive_data.save()
        logger.info(f"Current folder set to '{name}' at path '{folder_path}'.")


DRIVE_DATA: NewDriveData = None
BOT_MODE: NewBotMode = None
LAST_REMOTE_BACKUP_FILE_ID: Optional[str] = None
LAST_REMOTE_SYNC_TIME: float = 0.0
_METADATA_SYNC_LOCK = asyncio.Lock()
_BACKUP_LOCK = asyncio.Lock()  # Prevents concurrent backup_drive_data executions
# Debounce: when loop=False triggers pile up, coalesce into a single delayed flush
_BACKUP_FLUSH_SCHEDULED: bool = False
_BACKUP_FLUSH_DELAY: float = 10.0  # seconds to coalesce rapid backup requests
# Track which client authored/can access the Telegram backup message
_BACKUP_AUTHOR_CLIENT_ID = None


async def sync_drive_data_from_telegram(force: bool = False) -> bool:
    """
    Synchronizes drive.data metadata from the Telegram Storage Channel.
    If an updated backup document is detected on Telegram (e.g. uploaded by Render or another server),
    it downloads it safely, validates the structure, and reloads DRIVE_DATA in memory & on disk.
    """
    global DRIVE_DATA, LAST_REMOTE_BACKUP_FILE_ID, LAST_REMOTE_SYNC_TIME, _BACKUP_AUTHOR_CLIENT_ID

    if not config.STORAGE_CHANNEL:
        return False

    import time
    now = time.time()
    if not force and (now - LAST_REMOTE_SYNC_TIME < 5):
        return False

    LAST_REMOTE_SYNC_TIME = now

    async with _METADATA_SYNC_LOCK:
        try:
            from utils.clients import multi_clients, premium_clients, is_telegram_ready
            if not is_telegram_ready():
                return False

            all_active = {**multi_clients, **premium_clients}
            if not all_active:
                return False

            client_candidates = []
            if _BACKUP_AUTHOR_CLIENT_ID and _BACKUP_AUTHOR_CLIENT_ID in all_active:
                client_candidates.append((_BACKUP_AUTHOR_CLIENT_ID, all_active[_BACKUP_AUTHOR_CLIENT_ID]))
            for cid, cl in all_active.items():
                if cid != _BACKUP_AUTHOR_CLIENT_ID:
                    client_candidates.append((cid, cl))

            msg: Optional[Message] = None
            for cid, candidate_client in client_candidates:
                try:
                    if config.DATABASE_BACKUP_MSG_ID:
                        try:
                            cand_msg = await candidate_client.get_messages(
                                config.STORAGE_CHANNEL, config.DATABASE_BACKUP_MSG_ID
                            )
                            if cand_msg and not getattr(cand_msg, "empty", True) and cand_msg.document:
                                msg = cand_msg
                                _BACKUP_AUTHOR_CLIENT_ID = cid
                                break
                        except Exception:
                            pass

                    # Fallback 1: check pinned message in storage channel
                    if not msg:
                        try:
                            chat = await candidate_client.get_chat(config.STORAGE_CHANNEL)
                            if chat and chat.pinned_message and chat.pinned_message.document:
                                msg = chat.pinned_message
                                config.DATABASE_BACKUP_MSG_ID = chat.pinned_message.id
                                _BACKUP_AUTHOR_CLIENT_ID = cid
                                _persist_backup_msg_id(msg.id)
                                break
                        except Exception:
                            pass

                    # Fallback 2: scan recent messages in storage channel for backup document
                    if not msg:
                        try:
                            async for hist_msg in candidate_client.get_chat_history(config.STORAGE_CHANNEL, limit=100):
                                if hist_msg and hist_msg.document and (
                                    hist_msg.document.file_name == "drive.data" or
                                    (hist_msg.caption and "TG Drive Data Backup File" in hist_msg.caption)
                                ):
                                    msg = hist_msg
                                    config.DATABASE_BACKUP_MSG_ID = hist_msg.id
                                    _BACKUP_AUTHOR_CLIENT_ID = cid
                                    _persist_backup_msg_id(msg.id)
                                    break
                        except Exception:
                            pass
                except Exception:
                    continue

            if not msg or not msg.document:
                logger.debug("Remote Telegram backup message or document not found.")
                return False

            remote_file_id = msg.document.file_id
            if not force and LAST_REMOTE_BACKUP_FILE_ID and remote_file_id == LAST_REMOTE_BACKUP_FILE_ID:
                return False

            # Download to an isolated temporary file to prevent corruption of the active drive.data
            temp_dl = Path(f"./cache/remote_drive_{random.randint(1000, 9999)}.tmp")
            dl_result = await msg.download(file_name=str(temp_dl.resolve()))
            if not dl_result or not os.path.exists(dl_result):
                return False

            try:
                new_drive_data = load_drive_data_from_file(dl_result)

                if new_drive_data is None or not hasattr(new_drive_data, "contents") or "/" not in new_drive_data.contents:
                    logger.warning("Downloaded remote drive.data could not be deserialized or has invalid structure, skipping reload.")
                    return False

                # ── Stale-rollback protection ─────────────────────────────────
                # Only protect against rollback if local has pending unpushed changes (isUpdated=True)
                # and local drive is non-empty. On clean boots or empty local state, always accept remote backup.
                remote_items_count = _count_total_drive_items(new_drive_data)
                local_items_count = _count_total_drive_items(DRIVE_DATA)
                local_is_updated = bool(DRIVE_DATA is not None and getattr(DRIVE_DATA, "isUpdated", False))

                if not force and local_is_updated and local_items_count > 0:
                    remote_ts = float(getattr(new_drive_data, "last_modified", 0) or 0)
                    local_ts = float(getattr(DRIVE_DATA, "last_modified", 0) or 0) if DRIVE_DATA is not None else 0.0
                    if remote_ts and local_ts and remote_ts < local_ts:
                        logger.warning(
                            f"Remote Telegram backup is older than unpushed local changes "
                            f"(remote {remote_ts:.0f} < local {local_ts:.0f}, local: {local_items_count} items, remote: {remote_items_count} items). "
                            f"Skipping reload to protect pending local modifications."
                        )
                        return False

                DRIVE_DATA = new_drive_data
                LAST_REMOTE_BACKUP_FILE_ID = remote_file_id

                # Save atomically to local disk cache
                DRIVE_DATA.save()
                # Clear updated flag since local matches remote
                DRIVE_DATA.isUpdated = False

                logger.info(f"✅ Successfully synchronized drive metadata from Telegram backup ({remote_items_count} item(s) restored, Document ID: {remote_file_id[:12]}...).")
                return True
            finally:
                if os.path.exists(dl_result):
                    try:
                        os.remove(dl_result)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Metadata sync from Telegram error: {e}")
            return False


def _persist_backup_msg_id(msg_id: int):
    """Safely updates DATABASE_BACKUP_MSG_ID in .env file if it exists."""
    env_file = Path(".env")
    if not env_file.is_file():
        return
    try:
        content = env_file.read_text(encoding="utf-8")
        if "DATABASE_BACKUP_MSG_ID=" in content:
            new_lines = []
            for line in content.splitlines():
                if line.strip().startswith("DATABASE_BACKUP_MSG_ID="):
                    new_lines.append(f"DATABASE_BACKUP_MSG_ID={msg_id}")
                else:
                    new_lines.append(line)
            env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        else:
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f"\nDATABASE_BACKUP_MSG_ID={msg_id}\n")
    except Exception as e:
        logger.debug(f"Could not persist DATABASE_BACKUP_MSG_ID to .env: {e}")


async def auto_sync_telegram_loop():
    """Periodic background task that checks Telegram for remote metadata updates every 15 seconds."""
    logger.info("Starting Telegram metadata auto-sync loop (15s interval).")
    while True:
        try:
            await asyncio.sleep(15)
            # Only pull remote if we don't have pending local unsaved modifications
            if DRIVE_DATA is not None and not getattr(DRIVE_DATA, "isUpdated", False):
                await sync_drive_data_from_telegram(force=False)
        except Exception as e:
            logger.debug(f"Auto-sync loop warning: {e}")
            await asyncio.sleep(15)


async def _execute_backup() -> bool:
    """Core Telegram backup logic. Returns True on success.
    Protected by _BACKUP_LOCK to prevent concurrent executions."""
    global DRIVE_DATA, LAST_REMOTE_BACKUP_FILE_ID, _BACKUP_AUTHOR_CLIENT_ID

    drive = ensure_drive_data()
    if not drive or not getattr(drive, "isUpdated", False):
        return False

    if not config.STORAGE_CHANNEL:
        logger.warning("Telegram backup skipped: STORAGE_CHANNEL not configured.")
        return False

    logger.info("Backing up drive data to Telegram.")
    from utils.clients import multi_clients, premium_clients

    # Build candidate list: prioritize known author client, otherwise test all active clients
    all_active = {**multi_clients, **premium_clients}
    if not all_active:
        return False

    client_candidates = []
    if _BACKUP_AUTHOR_CLIENT_ID and _BACKUP_AUTHOR_CLIENT_ID in all_active:
        client_candidates.append((_BACKUP_AUTHOR_CLIENT_ID, all_active[_BACKUP_AUTHOR_CLIENT_ID]))
    for cid, cl in all_active.items():
        if cid != _BACKUP_AUTHOR_CLIENT_ID:
            client_candidates.append((cid, cl))

    time_text = f"📅 **Last Updated :** {get_current_utc_time()} (UTC +00:00)"
    caption = (
        f"🔐 **TG Drive Data Backup File**\n\n"
        "Do not edit or delete this message. This is a backup file for the tg drive data.\n\n"
        f"{time_text}"
    )

    media_doc = InputMediaDocument(str(drive_cache_path.resolve()), caption=caption)
    msg = None
    last_author_err = None

    # Step 1: If DATABASE_BACKUP_MSG_ID is known, try editing existing message
    if config.DATABASE_BACKUP_MSG_ID:
        for cid, candidate_client in client_candidates:
            try:
                msg = await candidate_client.edit_message_media(
                    config.STORAGE_CHANNEL,
                    config.DATABASE_BACKUP_MSG_ID,
                    media=media_doc,
                )
                _BACKUP_AUTHOR_CLIENT_ID = cid
                break
            except FloodWait as fw:
                wait_time = fw.value + 1
                logger.warning(f"Backup FloodWait: sleeping {wait_time}s before next candidate.")
                try:
                    from utils import tg_gate
                    tg_gate.note_flood(cid, float(fw.value))
                except Exception:
                    pass
                await asyncio.sleep(wait_time)
                continue
            except Exception as edit_err:
                if "MESSAGE_AUTHOR_REQUIRED" in str(edit_err):
                    last_author_err = edit_err
                    continue
                # If message not found or deleted, break to send a new message
                break

    # Step 2: If message doesn't exist or edit failed, create a fresh backup message
    if not msg:
        for cid, candidate_client in client_candidates:
            try:
                msg = await candidate_client.send_document(
                    config.STORAGE_CHANNEL,
                    document=str(drive_cache_path.resolve()),
                    caption=caption,
                    file_name="drive.data"
                )
                if msg:
                    config.DATABASE_BACKUP_MSG_ID = msg.id
                    _BACKUP_AUTHOR_CLIENT_ID = cid
                    _persist_backup_msg_id(msg.id)
                    try:
                        await msg.pin()
                    except Exception:
                        pass
                    logger.info(f"✨ Created new Telegram backup message ID: {msg.id}")
                    break
            except Exception as send_err:
                logger.warning(f"Failed to send fresh backup message via client {cid}: {send_err}")
                continue

    if not msg:
        if last_author_err:
            raise last_author_err
        raise RuntimeError("Failed to backup Telegram drive.data with any connected client.")

    if msg and msg.document:
        LAST_REMOTE_BACKUP_FILE_ID = msg.document.file_id

    DRIVE_DATA.isUpdated = False
    logger.info("Drive data backed up to Telegram successfully.")
    return True


async def _flush_backup_coalesced():
    """Delayed flush handler: waits _BACKUP_FLUSH_DELAY then executes a single backup.
    Multiple rapid calls are coalesced into one execution."""
    global _BACKUP_FLUSH_SCHEDULED
    await asyncio.sleep(_BACKUP_FLUSH_DELAY)
    _BACKUP_FLUSH_SCHEDULED = False
    async with _BACKUP_LOCK:
        try:
            success = await _execute_backup()
            if success:
                # Pin only during debounced flush (covers batch uploads)
                try:
                    from utils.clients import multi_clients, premium_clients
                    all_active = {**multi_clients, **premium_clients}
                    if _BACKUP_AUTHOR_CLIENT_ID and _BACKUP_AUTHOR_CLIENT_ID in all_active:
                        client = all_active[_BACKUP_AUTHOR_CLIENT_ID]
                        msg = await client.get_messages(config.STORAGE_CHANNEL, config.DATABASE_BACKUP_MSG_ID)
                        if msg:
                            await msg.pin()
                except Exception as pin_e:
                    logger.debug(f"Pinning backup message note: {pin_e}")
        except Exception as e:
            logger.error(f"Coalesced backup error: {e}")


async def backup_drive_data(loop=True):
    """Backup drive data to Telegram.

    When loop=True (periodic background task), runs in a continuous loop.
    When loop=False (triggered by uploads), debounces rapid calls:
    multiple calls within _BACKUP_FLUSH_DELAY seconds are coalesced into one."""
    global _BACKUP_FLUSH_SCHEDULED

    if not loop:
        # Debounce: schedule a flush if one isn't already pending
        if not _BACKUP_FLUSH_SCHEDULED:
            _BACKUP_FLUSH_SCHEDULED = True
            asyncio.create_task(_flush_backup_coalesced())
            logger.info("Scheduled coalesced backup flush (debounced).")
        return

    # Continuous loop mode for periodic background backups
    logger.info("Starting periodic backup drive data task.")
    while True:
        try:
            await asyncio.sleep(config.DATABASE_BACKUP_TIME)
            async with _BACKUP_LOCK:
                try:
                    await _execute_backup()
                    # Pin on periodic loop backup
                    try:
                        from utils.clients import multi_clients, premium_clients
                        all_active = {**multi_clients, **premium_clients}
                        if _BACKUP_AUTHOR_CLIENT_ID and _BACKUP_AUTHOR_CLIENT_ID in all_active:
                            client = all_active[_BACKUP_AUTHOR_CLIENT_ID]
                            msg = await client.get_messages(config.STORAGE_CHANNEL, config.DATABASE_BACKUP_MSG_ID)
                            if msg:
                                await msg.pin()
                    except Exception as pin_e:
                        logger.debug(f"Pinning backup message note: {pin_e}")
                except Exception as e:
                    logger.error(f"Backup Error: {e}")
                    await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Backup loop error: {e}")
            await asyncio.sleep(10)


async def init_drive_data():
    global DRIVE_DATA

    logger.info("Initializing drive data.")
    drive = ensure_drive_data()
    root_dir = drive.get_directory("/")
    if not hasattr(root_dir, "auth_hashes"):
        root_dir.auth_hashes = []

    def traverse_directory(folder):
        for item in folder.contents.values():
            if item.type == "folder":
                traverse_directory(item)

                if not hasattr(item, "auth_hashes"):
                    item.auth_hashes = []

    traverse_directory(root_dir)
    drive.save()
    logger.info("Drive data initialization completed.")


async def loadDriveData():
    global DRIVE_DATA, BOT_MODE

    logger.info("Loading drive data from Telegram backup...")
    success = await sync_drive_data_from_telegram(force=True)
    if not success:
        logger.info("Remote sync skipped or offline, falling back to local cached drive.data...")
        DRIVE_DATA = ensure_drive_data()

    await init_drive_data()

    if config.MAIN_BOT_TOKEN:
        from utils.bot_mode import start_bot_mode

        BOT_MODE = NewBotMode(DRIVE_DATA)
        await start_bot_mode(DRIVE_DATA, BOT_MODE)
        logger.info("Bot mode started.")
