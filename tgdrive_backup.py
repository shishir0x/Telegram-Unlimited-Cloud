"""
TGDrive Universal Backup Manager - Git-Style Sync Engine & Real-Time Cloud Telemetry
===================================================================================
Features:
- Full Folder Hierarchy Pre-Sync (including empty folders!)
- Git-style change tracking: [+] Added, [M] Modified, [=] Unchanged (Zero duplicates)
- Live terminal progress with Speed, ETA, Transferred Bytes & Remaining File Count
- Real-time Web UI telemetry broadcast (/api/updateSyncStatus)
- Support for Local Disks (C:, D:) and Android Phones via MTP USB
"""

import os
import sys

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import json
import hashlib
import string
import random
import shutil
import argparse
import tempfile
import subprocess
import requests
from dotenv import load_dotenv
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

# Load environment variables from .env
load_dotenv()

# System & heavy dependency directories to ignore during backup
IGNORED_DIRS = {
    # Windows System & Profile Junk
    "appdata",
    "application data",
    "local settings",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
    "$winreagent",
    "system volume information",
    "recovery",
    "config.msi",
    "msocache",
    "perflogs",
    "intel",
    "all users",
    "default user",
    "cookies",
    "recent",
    "sendto",
    "start menu",
    "nethood",
    "printhood",
    "templates",
    "searches",
    "saved games",
    "contacts",
    "links",
    "virtualbox vms",
    ".virtualbox",
    "crossdevice",
    "cross device",
    "phonelink",
    "phone link",
    "your phone",
    "microsoft",
    "microsoftedgebackups",

    # Heavy Dependency & Build Folders (Crucial for C: Drive!)
    "node_modules",
    "bower_components",
    "jspm_packages",
    "dist",
    "build",
    "out",
    "target",
    "bin",
    "obj",
    "pkg",
    "release",
    "debug",

    # Python Virtualenvs & Caches
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "virtualenv",
    ".tox",
    ".eggs",

    # Package Manager, AI Tools & Dev Caches
    ".cache",
    ".npm",
    ".yarn",
    ".pnpm-store",
    ".cargo",
    ".rustup",
    ".gradle",
    ".m2",
    ".nuget",
    ".dart_tool",
    ".pub-cache",
    ".gemini",
    ".antigravity",
    ".cursor",
    ".windsurf",
    ".ollama",
    ".docker",
    ".conda",
    ".anaconda",
    ".dotnet",
    ".android",

    # IDEs & VCS Metadata
    ".git",
    ".github",
    ".svn",
    ".hg",
    ".vscode",
    ".vscode-insiders",
    ".vscode-cli",
    ".idea",
    ".vs",

    # Temporary Folders
    "temp",
    "tmp",
    "cache",
    "caches",
    "logs",

    # Android Folders to Skip (Android/media is preserved for photos/WhatsApp)
    "android/data",
    "android/obb",
    ".trash",
    ".thumbnails",
    "lost.dir",
    ".android_secure",
    ".soundrecordrecycler",
    ".filemanagerrecycler",
    ".aceself",
    ".slogan",
}

IGNORED_FILES_LOWER = {
    "desktop.ini",
    "thumbs.db",
    "iconcache.db",
    "ntuser.ini",
    ".ds_store",
    ".nomedia",
    "hiberfil.sys",
    "pagefile.sys",
    "swapfile.sys",
    "dumpstack.log",
}

IGNORED_FILE_PREFIXES = ("ntuser.dat", "usrclass.dat", "~$", ".~", "dumpstack.log", "iconcache")
IGNORED_FILE_EXTENSIONS = (
    ".tmp", ".temp", ".crdownload", ".part", ".log1", ".log2", ".dmp",
    ".cache", ".lock", ".iso", ".vmdk", ".vdi", ".vhdx", ".swp", ".sys", ".lnk"
)

MANIFEST_PATH = Path.home() / ".tgdrive_sync_manifest.json"


def generate_random_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def compute_fast_file_hash(filepath: Path) -> str:
    """Computes fast MD5 (first 2MB + size) for rapid change detection."""
    try:
        sz = filepath.stat().st_size
        h = hashlib.md5()
        h.update(str(sz).encode())
        with open(filepath, "rb") as f:
            chunk = f.read(2 * 1024 * 1024)
            h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def print_progress_bar(iteration: int, total: int, prefix: str = '', suffix: str = '', length: int = 15, fill: str = '█', is_finished: bool = False):
    if total <= 0:
        percent = 100.0
        filled_length = length
    else:
        percent = min(100.0, (iteration / float(total)) * 100.0)
        filled_length = int(length * iteration // total)
    bar = fill * filled_length + '░' * (length - filled_length)
    msg = f"\r  {prefix} [{bar}] {percent:5.1f}% {suffix}"
    sys.stdout.write(f"{msg:<65}")
    sys.stdout.flush()
    if is_finished:
        sys.stdout.write('\n')


def should_skip_directory(dir_name: str, full_rel_path: str = "") -> bool:
    name_lower = dir_name.lower().strip()
    path_lower = full_rel_path.lower().replace("\\", "/").strip("/")

    # Explicitly allow Android root and Android/media (for WhatsApp, Telegram, etc.)
    if path_lower == "android" or path_lower.endswith("/android") or "android/media" in path_lower:
        if "android/data" in path_lower or "android/obb" in path_lower:
            return True
        if name_lower.startswith((".", "$")):
            return True
        return False

    # Check direct name
    if name_lower in IGNORED_DIRS:
        return True

    # Check path parts
    path_parts = [p.lower() for p in Path(full_rel_path).parts] if full_rel_path else [name_lower]
    if any(part in IGNORED_DIRS for part in path_parts):
        return True

    # Check known junk substring patterns
    if any(f"/{ig}/" in f"/{path_lower}/" for ig in ["node_modules", "appdata", ".git", ".venv", "venv", "__pycache__", ".cache", ".npm", ".cargo", ".gradle"]):
        return True

    # Always skip hidden or system folders starting with '.' or '$'
    if name_lower.startswith((".", "$")):
        return True
    return False


def should_skip_file(file_name: str) -> bool:
    name_lower = file_name.lower().strip()
    if name_lower in IGNORED_FILES_LOWER:
        return True
    if any(name_lower.startswith(p) for p in IGNORED_FILE_PREFIXES):
        return True
    if any(name_lower.endswith(e) for e in IGNORED_FILE_EXTENSIONS):
        return True
    return False


def convert_local_path_to_tg_structure(local_path: Path) -> str:
    """
    Converts C:\\Users\\shishir0x\\Documents
    into C_Drive/Users/shishir0x/Documents
    """
    resolved = local_path.resolve()
    drive = resolved.drive
    if drive:
        drive_name = f"{drive[0].upper()}_Drive"
        rest = str(resolved).replace(drive, "").strip("\\/").replace("\\", "/")
        if rest:
            return f"{drive_name}/{rest}"
        return drive_name
    else:
        return str(resolved).strip("/").replace("\\", "/")


# ==========================================
# Sync Manifest Tracker (Git-like state)
# ==========================================
class SyncManifest:
    def __init__(self):
        self.data: Dict[str, Dict] = {}
        self.load()

    def load(self):
        if MANIFEST_PATH.exists():
            try:
                with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self):
        try:
            with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def get_file_record(self, rel_key: str) -> Optional[Dict]:
        return self.data.get(rel_key)

    def update_file_record(self, rel_key: str, size: int, mtime: float, fhash: str):
        self.data[rel_key] = {
            "size": size,
            "mtime": mtime,
            "hash": fhash,
            "last_synced": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.save()


# ==========================================
# Windows MTP (Phone) Helper using Shell COM
# ==========================================
class MTPPhoneManager:
    @staticmethod
    def get_connected_phones():
        if os.name != "nt":
            return []
        try:
            cmd = '''
$s = New-Object -ComObject Shell.Application
$thisPC = $s.Namespace(17)
$phones = @()
foreach ($item in $thisPC.Items()) {
    $p = [string]$item.Path
    if ($p -like "*usb#*" -or $p -like "*wpdbusenumroot*" -or $p -like "*::*") {
        $phones += [PSCustomObject]@{ name = $item.Name; path = $item.Path }
    }
}
$phones | ConvertTo-Json -Compress
'''
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, dict):
                    data = [data]
                return data
            return []
        except Exception:
            return []


# ==========================================
# TGDrive Backup Client
# ==========================================
class TGDriveBackupClient:
    def __init__(self, base_url: str, password: str, drive_root: str = ""):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.drive_root = drive_root.strip("/")
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=30, max_retries=3)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.manifest = SyncManifest()
        self._folder_id_cache: Dict[str, str] = {"": "/"}

    def verify_auth(self) -> bool:
        """Verify password with TGDrive server."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/checkPassword",
                json={"pass": self.password, "password": self.password},
                timeout=10,
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("status") == "ok"
            elif res.status_code == 401:
                print(f"[!] Invalid admin password for {self.base_url}")
                return False
            return False
        except Exception as e:
            print(f"[!] Connection error to {self.base_url}: {e}")
            return False

    def push_web_sync_status(self, sync_data: dict, log_msg: Optional[str] = None):
        """Broadcasts real-time telemetry to the TGDrive Web UI (/api/updateSyncStatus)."""
        try:
            payload = {
                "password": self.password,
                "sync_data": sync_data,
            }
            if log_msg:
                payload["log"] = log_msg
            self.session.post(
                f"{self.base_url}/api/updateSyncStatus",
                json=payload,
                timeout=3,
            )
        except Exception:
            pass

    def get_directory_contents(self, folder_id_path: str = "/") -> Dict:
        """Fetch files and folders inside a given folder ID path from TGDrive."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/getDirectory",
                json={"pass": self.password, "password": self.password, "path": folder_id_path},
                timeout=15,
            )
            if res.status_code == 200:
                json_data = res.json()
                if json_data.get("status") == "ok":
                    data_field = json_data.get("data", {})
                    if isinstance(data_field, dict):
                        return data_field.get("contents", {})
                    return json_data.get("contents", {})
            return {}
        except Exception:
            return {}

    def resolve_or_create_folder_id_path(self, human_path: str) -> str:
        """
        Converts human-readable path 'OnePlus_Nord_CE4/Internal_shared_storage/DCIM'
        into Telegram internal ID path '/ABC123/XYZ789/' creating folders on the fly.
        """
        clean_path = human_path.strip("/").replace("\\", "/")
        if not clean_path:
            return "/"

        if clean_path in self._folder_id_cache:
            return self._folder_id_cache[clean_path]

        parts = [p for p in clean_path.split("/") if p]
        current_id_path = "/"
        current_named_prefix = ""

        for part in parts:
            current_named_prefix = f"{current_named_prefix}/{part}".strip("/")
            if current_named_prefix in self._folder_id_cache:
                current_id_path = self._folder_id_cache[current_named_prefix]
                continue

            contents = self.get_directory_contents(current_id_path)
            found_id = None

            if contents:
                for item_id, item in contents.items():
                    if item.get("type") == "folder" and not item.get("trash"):
                        if item.get("name") == part:
                            found_id = item.get("id") or item_id
                            break

            if not found_id:
                try:
                    self.session.post(
                        f"{self.base_url}/api/createNewFolder",
                        json={
                            "pass": self.password,
                            "password": self.password,
                            "path": current_id_path,
                            "name": part,
                        },
                        timeout=15,
                    )
                    # Re-fetch directory contents after creation attempt
                    contents = self.get_directory_contents(current_id_path)
                    for item_id, item in contents.items():
                        if item.get("type") == "folder" and not item.get("trash"):
                            if item.get("name") == part:
                                found_id = item.get("id") or item_id
                                break
                except Exception as e:
                    print(f"    [!] Error creating folder '{part}': {e}")

            if not found_id:
                print(f"    [!] Warning: Unable to resolve internal ID for '{part}' inside '{current_id_path}'")
                found_id = part

            current_id_path = (current_id_path + found_id + "/").replace("//", "/")
            self._folder_id_cache[current_named_prefix] = current_id_path

        return current_id_path

    def get_existing_files_in_folder(self, folder_id_path: str) -> Dict[str, Dict]:
        """Returns map of filename -> item details in folder."""
        contents = self.get_directory_contents(folder_id_path)
        existing = {}
        if not contents:
            return existing
        for _, item in contents.items():
            if item.get("type") == "file" and not item.get("trash"):
                existing[item.get("name")] = item
        return existing

    def sync_folders_first(self, all_folders: List[str], base_tg_path: str):
        """
        Step 1: Creates all folder hierarchies on TGDrive first (including empty folders!).
        Skips folders that are already verified.
        """
        verified_folders = set(self.manifest.data.get("__verified_folders__", []))
        unverified = [f for f in all_folders if f"{base_tg_path}/{f}".strip("/") not in verified_folders]
        
        if not unverified:
            print(f"\n📁 Step 1: All {len(all_folders):,} folder paths already verified & present in Cloud Drive (Skipped).")
            return

        total_folders = len(unverified)
        print(f"\n📁 Step 1: Pre-Syncing & Creating {total_folders:,} New/Pending Folder Hierarchies...")
        self.push_web_sync_status({
            "state": "syncing_folders",
            "folders_total": total_folders,
            "folders_created": 0,
        }, f"Starting folder pre-sync for {total_folders} directories...")

        created_count = 0
        for idx, rel_folder in enumerate(unverified, start=1):
            if rel_folder in [".", ""]:
                full_tg_folder = base_tg_path
            else:
                full_tg_folder = f"{base_tg_path}/{rel_folder}".strip("/")

            self.resolve_or_create_folder_id_path(full_tg_folder)
            verified_folders.add(full_tg_folder)
            created_count += 1

            pct = (idx / total_folders) * 100.0
            short_name = rel_folder if rel_folder else "Root"
            if len(short_name) > 22:
                short_name = "…" + short_name[-21:]
            bar_fill = int(18 * idx // total_folders)
            sys.stdout.write(f"\r  |{'█' * bar_fill:<18}| {pct:5.1f}% ({idx}/{total_folders}) [{short_name:<22}]  ")
            sys.stdout.flush()

            if idx % 10 == 0 or idx == total_folders:
                self.push_web_sync_status({
                    "state": "syncing_folders",
                    "folders_total": total_folders,
                    "folders_created": idx,
                    "current_item": short_name
                })

        self.manifest.data["__verified_folders__"] = list(verified_folders)
        self.manifest.save()
        sys.stdout.write("\n")
        print(f"  ✅ All {total_folders:,} folders verified & created in Cloud Drive!")
        self.push_web_sync_status({
            "state": "syncing_folders",
            "folders_total": total_folders,
            "folders_created": total_folders
        }, f"Verified all {total_folders} folders on cloud drive.")

    def upload_file(self, local_file_path: Path, human_remote_folder: str, file_idx: int = 1, total_files: int = 1, remaining_files: int = 0, remaining_bytes: int = 0) -> bool:
        """Upload a single file to TGDrive with real-time terminal progress bar and web broadcast."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size
        mtime = local_file_path.stat().st_mtime

        if file_size == 0:
            print(f"  ⏭️ Skipping 0-byte empty file: {file_name}")
            return True

        upload_id = generate_random_id()
        remote_id_path = self.resolve_or_create_folder_id_path(human_remote_folder)

        rem_str = f"Remaining: {remaining_files} files ({format_size(remaining_bytes)})"
        print(f"\n[{file_idx}/{total_files}] ⬆️ {file_name} ({format_size(file_size)}) | {rem_str}")
        print(f"       ➔ Location: /{human_remote_folder}/")

        self.push_web_sync_status({
            "state": "syncing_files",
            "current_item": file_name,
            "current_index": file_idx,
            "total_items": total_files,
            "remaining_items": remaining_files,
            "total_bytes": remaining_bytes + file_size,
        }, f"Uploading [{file_idx}/{total_files}]: {file_name} ({format_size(file_size)})")

        try:
            with open(local_file_path, "rb") as f:
                files = {"file": (file_name, f)}
                form_data = {
                    "path": remote_id_path,
                    "password": self.password,
                    "id": upload_id,
                    "total_size": str(file_size),
                }

                start_time = time.time()
                res = self.session.post(
                    f"{self.base_url}/api/upload",
                    files=files,
                    data=form_data,
                    timeout=600,
                )

                if res.status_code != 200:
                    print(f"    ❌ Server returned HTTP {res.status_code}: {res.text}")
                    return False

                data = res.json()
                if data.get("status") != "ok":
                    print(f"    ❌ Upload failed: {data.get('status')}")
                    return False

            # Poll for Telegram upload completion with real-time progress bar
            at_100_count = 0
            max_poll_seconds = max(20, min(180, int(file_size / (100 * 1024)) + 15))
            max_iterations = int(max_poll_seconds / 0.4)

            for _ in range(max_iterations):
                time.sleep(0.4)
                try:
                    prog_res = self.session.post(
                        f"{self.base_url}/api/getUploadProgress",
                        json={"password": self.password, "id": upload_id},
                        timeout=10,
                    )
                    if prog_res.status_code == 200:
                        prog_data = prog_res.json()
                        if prog_data.get("status") == "ok":
                            status = prog_data["data"][0]
                            current_bytes = prog_data["data"][1]
                            total_bytes = prog_data["data"][2] or file_size

                            duration = max(time.time() - start_time, 0.1)
                            speed = current_bytes / duration
                            speed_str = f"{format_size(int(speed))}/s"

                            if status == "running":
                                print_progress_bar(current_bytes, total_bytes, prefix="Syncing:", suffix=f"{format_size(current_bytes)}/{format_size(total_bytes)} ({speed_str})", is_finished=False)
                                self.push_web_sync_status({
                                    "current_bytes": current_bytes,
                                    "speed_str": speed_str
                                })
                                if current_bytes >= total_bytes and total_bytes > 0:
                                    at_100_count += 1
                                    # If at 100% for 4 seconds (10 ticks), auto-advance to next file!
                                    if at_100_count >= 10:
                                        print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})", is_finished=True)
                                        fhash = compute_fast_file_hash(local_file_path)
                                        self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, mtime, fhash)
                                        return True
                            elif status == "completed":
                                print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})", is_finished=True)
                                fhash = compute_fast_file_hash(local_file_path)
                                self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, mtime, fhash)
                                return True
                            elif status == "error":
                                print(f"\n    ⚠️ Upload error reported. Skipping '{file_name}' to continue queue.")
                                return False
                except Exception:
                    pass

            print(f"\r    ⏭️ Upload timeout ({max_poll_seconds}s). Auto-advancing to next file...")
            fhash = compute_fast_file_hash(local_file_path)
            self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, mtime, fhash)
            return True

        except Exception as e:
            print(f"    ❌ Upload error: {e}")
            return False

    def upload_in_memory_file(self, file_bytes: bytes, file_name: str, human_remote_folder: str, file_idx: int = 1, total_files: int = 1, remaining_files: int = 0, remaining_bytes: int = 0) -> bool:
        """Upload in-memory file bytes directly to TGDrive with zero persistent disk storage."""
        file_size = len(file_bytes)
        if file_size == 0:
            print(f"  ⏭️ Skipping 0-byte empty file: {file_name}")
            return True

        upload_id = generate_random_id()
        remote_id_path = self.resolve_or_create_folder_id_path(human_remote_folder)

        rem_str = f"Remaining: {remaining_files} files ({format_size(remaining_bytes)})"
        print(f"\n[{file_idx}/{total_files}] ⬆️ {file_name} ({format_size(file_size)}) | {rem_str}")
        print(f"       ➔ Location: /{human_remote_folder}/")

        self.push_web_sync_status({
            "state": "syncing_files",
            "current_item": file_name,
            "current_path": f"/{human_remote_folder}/".replace("//", "/"),
            "current_size": format_size(file_size),
            "current_index": file_idx,
            "total_items": total_files,
            "remaining_items": remaining_files,
            "total_bytes": remaining_bytes + file_size,
        }, f"Streaming [{file_idx}/{total_files}]: {file_name} ({format_size(file_size)})")

        try:
            import io
            file_obj = io.BytesIO(file_bytes)
            files = {"file": (file_name, file_obj)}
            form_data = {
                "path": remote_id_path,
                "password": self.password,
                "id": upload_id,
                "total_size": str(file_size),
            }

            start_time = time.time()
            res = self.session.post(
                f"{self.base_url}/api/upload",
                files=files,
                data=form_data,
                timeout=600,
            )

            if res.status_code != 200:
                print(f"    ❌ Server returned HTTP {res.status_code}: {res.text}")
                return False

            data = res.json()
            if data.get("status") != "ok":
                print(f"    ❌ Upload failed: {data.get('status')}")
                return False

            # Poll for Telegram upload completion with real-time progress bar
            at_100_count = 0
            max_poll_seconds = max(20, min(180, int(file_size / (100 * 1024)) + 15))
            max_iterations = int(max_poll_seconds / 0.4)

            for _ in range(max_iterations):
                time.sleep(0.4)
                try:
                    prog_res = self.session.post(
                        f"{self.base_url}/api/getUploadProgress",
                        json={"password": self.password, "id": upload_id},
                        timeout=10,
                    )
                    if prog_res.status_code == 200:
                        prog_data = prog_res.json()
                        if prog_data.get("status") == "ok":
                            status = prog_data["data"][0]
                            current_bytes = prog_data["data"][1]
                            total_bytes = prog_data["data"][2] or file_size

                            duration = max(time.time() - start_time, 0.1)
                            speed = current_bytes / duration
                            speed_str = f"{format_size(int(speed))}/s"

                            if status == "running":
                                print_progress_bar(current_bytes, total_bytes, prefix="Syncing:", suffix=f"{format_size(current_bytes)}/{format_size(total_bytes)} ({speed_str})", is_finished=False)
                                self.push_web_sync_status({
                                    "current_bytes": current_bytes,
                                    "speed_str": speed_str
                                })
                                if current_bytes >= total_bytes and total_bytes > 0:
                                    at_100_count += 1
                                    # If at 100% for 4 seconds (10 ticks), auto-advance to next file!
                                    if at_100_count >= 10:
                                        print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})", is_finished=True)
                                        h = hashlib.md5(str(file_size).encode() + file_bytes[:2*1024*1024]).hexdigest()
                                        self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, time.time(), h)
                                        return True
                            elif status == "completed":
                                print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})", is_finished=True)
                                h = hashlib.md5(str(file_size).encode() + file_bytes[:2*1024*1024]).hexdigest()
                                self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, time.time(), h)
                                return True
                            elif status == "error":
                                print(f"\n    ⚠️ Upload error reported. Skipping '{file_name}' to continue queue.")
                                return False
                except Exception:
                    pass

            print(f"\r    ⏭️ Upload timeout ({max_poll_seconds}s). Auto-advancing to next file...")
            h = hashlib.md5(str(file_size).encode() + file_bytes[:2*1024*1024]).hexdigest()
            self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, time.time(), h)
            return True

        except Exception as e:
            print(f"    ❌ Upload error: {e}")
            return False

    def sync_local_directory(self, source_dir: Path, target_tg_root: Optional[str] = None):
        """Recursively scan and backup directory tree with Git-style change detection."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Target path does not exist: {source_dir}")
            return

        if not target_tg_root:
            target_tg_root = convert_local_path_to_tg_structure(source_dir)

        print("\n" + "=" * 68)
        print(f"🔍 Source Path:      {source_dir}")
        print(f"🌐 Cloud Location:  /{target_tg_root}/")
        print("=" * 68)

        self.push_web_sync_status({
            "state": "scanning",
            "source": str(source_dir)
        }, f"Scanning source: {source_dir}")

        # ── Step 1: Discover all folders and files ───────────────────────────
        print("⏳ Scanning directory structure (skipping AppData, node_modules & dev junk)...")
        all_folders: List[str] = []
        all_files: List[Tuple[Path, str]] = []  # (local_path, rel_folder)

        scan_tick = 0
        for root, dirs, files in os.walk(source_dir, followlinks=False, onerror=lambda e: None):
            rel_dir = os.path.relpath(root, source_dir).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            dirs[:] = [d for d in dirs if not should_skip_directory(d, f"{rel_dir}/{d}")]
            if rel_dir:
                all_folders.append(rel_dir)

            for f in files:
                if not should_skip_file(f):
                    all_files.append((Path(root) / f, rel_dir))

            scan_tick += 1
            if scan_tick % 25 == 0:
                sys.stdout.write(f"\r  🔍 Discovered: {len(all_folders):,} folders, {len(all_files):,} files...")
                sys.stdout.flush()

        sys.stdout.write(f"\r  ✅ Scan complete: {len(all_folders):,} folders, {len(all_files):,} files found.\n")
        sys.stdout.flush()

        # ── Step 2: Pre-sync all folders ─────────────────────────────────────
        self.sync_folders_first(all_folders, target_tg_root)

        # ── Step 3: Compute Git-Style Diff ───────────────────────────────────
        print("\n⏳ Step 2: Analyzing file changes against Cloud Drive...")
        added_files = []
        modified_files = []
        unchanged_files = []
        remote_folders_cache: Dict[str, Dict[str, Dict]] = {}

        for p, rel_folder in all_files:
            try:
                sz = p.stat().st_size
                mtime = p.stat().st_mtime
            except Exception:
                continue

            human_folder = f"{target_tg_root}/{rel_folder}".strip("/") if rel_folder else target_tg_root
            manifest_key = f"{human_folder}/{p.name}"
            record = self.manifest.get_file_record(manifest_key)

            # Check local manifest
            if record and record.get("size") == sz and abs(record.get("mtime", 0) - mtime) < 1.0:
                unchanged_files.append((p, human_folder))
                continue

            # Check remote folder cache
            if human_folder not in remote_folders_cache:
                remote_id = self.resolve_or_create_folder_id_path(human_folder)
                remote_folders_cache[human_folder] = self.get_existing_files_in_folder(remote_id)

            remote_files = remote_folders_cache[human_folder]
            if p.name in remote_files:
                remote_sz = remote_files[p.name].get("size", 0)
                if remote_sz == sz:
                    unchanged_files.append((p, human_folder))
                    self.manifest.update_file_record(manifest_key, sz, mtime, "")
                else:
                    modified_files.append((p, human_folder))
            else:
                added_files.append((p, human_folder))

        files_to_sync = added_files + modified_files
        total_sync_bytes = sum(p.stat().st_size for p, _ in files_to_sync)

        print("\n" + "─" * 68)
        print("📊 Git-Style Sync Summary:")
        print(f"   📁 Folders Verified: {len(all_folders):>5} directories")
        print(f"   🟢 [+] Added:       {len(added_files):>5} new files (to upload)")
        print(f"   🟡 [M] Modified:    {len(modified_files):>5} changed files (to update)")
        print(f"   ⚪ [=] Unchanged:   {len(unchanged_files):>5} up-to-date files (skipped)")
        print("─" * 68)
        print(f"📦 Total To Sync:     {len(files_to_sync):>5} files ({format_size(total_sync_bytes)})")
        print("=" * 68)

        if not files_to_sync:
            print("\n✨ All files and folders are already in sync! Nothing to upload.")
            self.push_web_sync_status({
                "state": "completed",
                "files_total": len(all_files),
                "files_uploaded": 0,
                "files_skipped": len(unchanged_files),
                "remaining_items": 0
            }, "Sync complete: All files are up to date.")
            return

        # ── Step 4: Upload with live status ──────────────────────────────────
        total_count = len(files_to_sync)
        remaining_bytes = total_sync_bytes

        for idx, (local_file, human_folder) in enumerate(files_to_sync, start=1):
            file_size = local_file.stat().st_size
            rem_count = total_count - idx
            remaining_bytes = max(0, remaining_bytes - file_size)

            self.upload_file(
                local_file,
                human_folder,
                file_idx=idx,
                total_files=total_count,
                remaining_files=rem_count,
                remaining_bytes=remaining_bytes
            )

        print("\n" + "=" * 68)
        print("🎉 Sync Completed Successfully!")
        print(f"   ✅ Total Uploaded: {total_count} files")
        print(f"   🌐 View on Cloud:  {self.base_url}/?path=/")
        print("=" * 68)

        self.push_web_sync_status({
            "state": "completed",
            "files_total": len(all_files),
            "files_uploaded": total_count,
            "files_skipped": len(unchanged_files),
            "remaining_items": 0
        }, f"Sync completed! {total_count} files uploaded to cloud.")

    def sync_mtp_phone_folder(self, phone_name: str, storage_name: str, subfolder_rel: str = "", auto_confirm: bool = False):
        """Sync files from an MTP connected phone into TGDrive with full folder hierarchy & zero duplicates."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%tgdrive_phone_staging%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                    capture_output=True,
                    timeout=5
                )
            except Exception:
                pass

        staging_dir = Path(tempfile.gettempdir()) / "tgdrive_phone_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        clean_phone = phone_name.replace(" ", "_")
        clean_storage = storage_name.replace(" ", "_")
        base_tg_prefix = f"{clean_phone}/{clean_storage}"

        print("\n" + "=" * 68)
        print(f"📱 Phone Source:     {phone_name} ➔ {storage_name} ➔ {subfolder_rel or 'Root'}")
        print(f"🌐 Cloud Location:  /{base_tg_prefix}/{subfolder_rel}".rstrip("/") + "/")
        print("=" * 68)

        self.push_web_sync_status({
            "state": "scanning",
            "source": f"{phone_name} ({storage_name})"
        }, f"Scanning phone storage: {phone_name}...")

        # ── Step 1: Scan MTP Phone Folders & Files ────────────────────────────
        print("⏳ Scanning phone folders and files over USB MTP (Live Stream)...")
        escaped_phone = phone_name.replace("'", "''")
        escaped_storage = storage_name.replace("'", "''")
        escaped_subpath = subfolder_rel.replace('/', '\\').replace("'", "''").strip('\\')

        ps_scan = f'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject Shell.Application
$thisPC = $shell.Namespace(17)
$phone = $thisPC.Items() | Where-Object {{ $_.Name -like "*{escaped_phone}*" }}
if (-not $phone) {{ Write-Error "Phone not found"; exit 1 }}

$storage = $phone.GetFolder.Items() | Where-Object {{ $_.Name -like "*{escaped_storage}*" }}
if (-not $storage) {{ Write-Error "Storage not found"; exit 1 }}

$targetRoot = $storage
$subPath = "{escaped_subpath}"
if ($subPath) {{
    $parts = $subPath.Split('\\')
    foreach ($p in $parts) {{
        $targetRoot = $targetRoot.GetFolder.Items() | Where-Object {{ $_.Name -eq $p -or $_.Name -like "*$p*" }}
        if (-not $targetRoot) {{ Write-Error "Subfolder $p not found"; exit 1 }}
    }}
}}

$skipDirs = @('.trash', '.thumbnails', 'lost.dir', '.soundrecordrecycler', '.filemanagerrecycler', '.aceself', '.slogan', '.statuses', 'cache', '.cache')

function Scan-Stream($folderItem, $relPath) {{
    if ($relPath) {{
        Write-Output ('DIR|' + $relPath)
    }}
    $pLower = $relPath.ToLower()
    foreach ($item in $folderItem.GetFolder.Items()) {{
        $name = $item.Name
        $nameLower = $name.ToLower()
        if ($item.IsFolder) {{
            $isAndroidDataOrObb = ($pLower -eq 'android' -or $pLower.EndsWith('/android')) -and ($nameLower -eq 'data' -or $nameLower -eq 'obb')
            if (-not $isAndroidDataOrObb -and $nameLower -notin $skipDirs -and -not $nameLower.StartsWith('.')) {{
                $childRel = if ($relPath) {{ $relPath + '/' + $name }} else {{ $name }}
                Scan-Stream $item $childRel
            }}
        }} else {{
            if (-not $nameLower.StartsWith('.') -and -not $nameLower.StartsWith('~$')) {{
                Write-Output ('FILE|' + $relPath + '|' + $name)
            }}
        }}
    }}
}}

Scan-Stream $targetRoot ""
Write-Output "SCAN_STREAM_DONE"
'''
        ps_scan_file = staging_dir / "scan.ps1"
        with open(ps_scan_file, "w", encoding="utf-8") as f:
            f.write(ps_scan)

        discovered_folders: List[str] = []
        discovered_files: List[Dict[str, str]] = []
        scan_start_time = time.time()
        last_ui_update = 0.0

        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_scan_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )

            for line in iter(proc.stdout.readline, ''):
                line = line.strip()
                if not line:
                    continue
                if line == "SCAN_STREAM_DONE":
                    break
                if line.startswith("DIR|"):
                    dir_path = line[4:]
                    discovered_folders.append(dir_path)
                elif line.startswith("FILE|"):
                    parts = line[5:].split("|", 1)
                    rel_dir = parts[0] if len(parts) > 1 else ""
                    fname = parts[1] if len(parts) > 1 else parts[0]
                    discovered_files.append({"name": fname, "folder": rel_dir})

                    now = time.time()
                    if now - last_ui_update >= 0.25:
                        last_ui_update = now
                        short_f = rel_dir[-18:] if len(rel_dir) > 18 else rel_dir
                        line_text = f"\r  ⚡ Scanning: {len(discovered_files):,} files | {len(discovered_folders):,} dirs... [{short_f}]"
                        sys.stdout.write(f"{line_text:<68}")
                        sys.stdout.flush()

            proc.terminate()
            # Clear live line before final summary
            sys.stdout.write("\r" + " " * 68 + "\r")
            sys.stdout.flush()
        except Exception as e:
            print(f"\n❌ Error scanning phone: {e}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return
        finally:
            if ps_scan_file.exists():
                ps_scan_file.unlink(missing_ok=True)

        elapsed = time.time() - scan_start_time
        print(f"  📂 Discovered {len(discovered_folders):,} folders and {len(discovered_files):,} files on phone in {elapsed:.1f}s.")

        # ── Step 2: Pre-sync all folder paths (including empty ones!) ─────────
        self.sync_folders_first(discovered_folders, base_tg_prefix)

        # ── Step 3: Compute Diff & Filter Unchanged Files ─────────────────────
        print("\n⏳ Step 2: Computing changes against Telegram Cloud (Zero Duplicates)...")
        files_to_download = []
        unchanged_count = 0
        remote_cache: Dict[str, Dict[str, Dict]] = {}

        for item in discovered_files:
            fname = item["name"]
            rel_folder = item["folder"].replace("\\", "/")
            human_folder = f"{base_tg_prefix}/{rel_folder}".strip("/") if rel_folder else base_tg_prefix
            manifest_key = f"{human_folder}/{fname}"

            record = self.manifest.get_file_record(manifest_key)
            if record:
                unchanged_count += 1
                continue

            if human_folder not in remote_cache:
                remote_id = self.resolve_or_create_folder_id_path(human_folder)
                remote_cache[human_folder] = self.get_existing_files_in_folder(remote_id)

            remote_files = remote_cache[human_folder]
            if fname in remote_files:
                unchanged_count += 1
                self.manifest.update_file_record(manifest_key, remote_files[fname].get("size", 0), 0, "")
            else:
                files_to_download.append((fname, rel_folder, human_folder))

        print("\n" + "─" * 68)
        print("📊 Git-Style Phone Change Summary:")
        print(f"   📁 Folders Verified: {len(discovered_folders):>5} directories")
        print(f"   🟢 [+] Added / New: {len(files_to_download):>5} files (to sync)")
        print(f"   ⚪ [=] Up-to-date:   {unchanged_count:>5} files (skipped - 0 duplicates)")
        print("─" * 68)
        print(f"📦 Total To Sync:     {len(files_to_download):>5} files")
        print("=" * 68)

        if not files_to_download:
            print("\n✨ All phone files and folders are already in sync! Nothing to upload.")
            shutil.rmtree(staging_dir, ignore_errors=True)
            self.push_web_sync_status({
                "state": "completed",
                "files_total": len(discovered_files),
                "files_uploaded": 0,
                "files_skipped": unchanged_count,
                "remaining_items": 0
            }, "Phone sync complete: All files are up to date.")
            return

        # ── Step 4: Persistent Real-Time Zero-Disk MTP Streaming Worker ───────
        total_sync_files = len(files_to_download)
        print(f"\n⚡ Step 3: Direct In-Memory Streaming {total_sync_files:,} files from phone to Telegram Cloud...\n")

        folder_dest = staging_dir / "stream_dest"
        if folder_dest.exists():
            shutil.rmtree(folder_dest, ignore_errors=True)
        folder_dest.mkdir(parents=True, exist_ok=True)
        dest_native_path = str(folder_dest.resolve())

        ps_worker_code = f'''
$shell = New-Object -ComObject Shell.Application
$destPath = @'
{dest_native_path}
'@
$destFolder = $shell.Namespace($destPath)
$thisPC = $shell.Namespace(17)
$phone = $thisPC.Items() | Where-Object {{ $_.Name -like "*{phone_name}*" }} | Select-Object -First 1
$storage = $phone.GetFolder.Items() | Where-Object {{ $_.Name -like "*{storage_name}*" }} | Select-Object -First 1

Write-Output "WORKER_READY"

while ($line = [Console]::In.ReadLine()) {{
    if ($line -eq "QUIT") {{ break }}
    if (-not $destFolder) {{
        $destFolder = $shell.Namespace($destPath)
    }}

    $bytes = [System.Convert]::FromBase64String($line)
    $decoded = [System.Text.Encoding]::UTF8.GetString($bytes)
    $splitIdx = $decoded.IndexOf('|')
    if ($splitIdx -ge 0) {{
        $relFolder = $decoded.Substring(0, $splitIdx)
        $fileName = $decoded.Substring($splitIdx + 1)
    }} else {{
        $relFolder = ""
        $fileName = $decoded
    }}

    $target = $storage
    $folderClean = $relFolder.Trim('/').Trim('\\')
    if ($folderClean) {{
        $subParts = $folderClean.Replace('\\', '/').Split('/')
        foreach ($p in $subParts) {{
            if ($p) {{
                $foundSub = $null
                foreach ($item in $target.GetFolder.Items()) {{
                    if ($item.IsFolder -and ($item.Name -eq $p -or $item.Name.Trim() -eq $p.Trim())) {{
                        $foundSub = $item
                        break
                    }}
                }}
                $target = $foundSub
                if (-not $target) {{ break }}
            }}
        }}
    }}

    $cleanReq = $fileName -replace '[\\?\\u200B]', ''
    $fileItem = $null
    if ($target) {{
        foreach ($item in $target.GetFolder.Items()) {{
            if (-not $item.IsFolder) {{
                $n = $item.Name -replace '[\\?\\u200B]', ''
                if ($item.Name -eq $fileName -or $n -eq $cleanReq -or $item.Name.Trim() -eq $fileName.Trim()) {{
                    $fileItem = $item
                    break
                }}
            }}
        }}
    }}

    if ($fileItem -and $destFolder) {{
        $destFolder.CopyHere($fileItem, 16)
        $found = $false
        $prevLen = -1
        $stableCount = 0
        for ($w = 0; $w -lt 400; $w++) {{
            $copied = Get-ChildItem -LiteralPath $destPath -File | Select-Object -First 1
            if ($copied -and $copied.Length -gt 0) {{
                if ($copied.Length -eq $prevLen) {{
                    $stableCount++
                    if ($stableCount -ge 2) {{
                        Write-Output ("READY|" + $copied.Length)
                        $found = $true
                        break
                    }}
                }} else {{
                    $prevLen = $copied.Length
                    $stableCount = 0
                }}
            }}
            Start-Sleep -Milliseconds 100
        }}
        if (-not $found) {{
            Write-Output "FAILED"
        }}
    }} else {{
        Write-Output "NOT_FOUND"
    }}
}}
'''
        ps_worker_file = staging_dir / "mtp_worker.ps1"
        with open(ps_worker_file, "w", encoding="utf-8") as f:
            f.write(ps_worker_code)

        proc = subprocess.Popen(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps_worker_file)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )

        ready_line = proc.stdout.readline().strip() if proc.stdout else ""
        if ready_line != "WORKER_READY":
            print(f"⚠️ Worker init warning: {ready_line}")

        import base64
        uploaded_count = 0

        # Push initial pending queue to Web UI
        pending_sample = [
            {"name": f[0], "path": f[2], "size": "Queued"}
            for f in files_to_download[:80]
        ]
        self.push_web_sync_status({
            "state": "syncing_files",
            "source": f"{phone_name} ({storage_name})",
            "total_items": total_sync_files,
            "current_index": 0,
            "remaining_items": total_sync_files,
            "pending_queue": pending_sample,
        }, f"Starting cloud transfer of {total_sync_files:,} files...")

        try:
            for idx, (fname, rel_folder, human_folder) in enumerate(files_to_download, start=1):
                rem_files = total_sync_files - idx

                # Clean destination directory before each file
                folder_dest.mkdir(parents=True, exist_ok=True)
                for existing in folder_dest.iterdir():
                    try:
                        if existing.is_file():
                            existing.unlink(missing_ok=True)
                    except Exception:
                        pass

                # Request worker to extract single file with Base64 encoding
                raw_payload = f"{rel_folder}|{fname}"
                b64_msg = base64.b64encode(raw_payload.encode('utf-8')).decode('ascii')
                proc.stdin.write(f"{b64_msg}\n")
                proc.stdin.flush()
                resp = proc.stdout.readline().strip()

                # Find the extracted file safely from directory listing
                extracted_files = [f for f in folder_dest.iterdir() if f.is_file()]
                if extracted_files and extracted_files[0].stat().st_size > 0:
                    target_staged = extracted_files[0]
                    file_bytes = target_staged.read_bytes()

                    # Purge from disk IMMEDIATELY before cloud upload
                    try:
                        target_staged.unlink(missing_ok=True)
                    except Exception:
                        pass

                    success = self.upload_in_memory_file(
                        file_bytes,
                        fname,
                        human_folder,
                        file_idx=idx,
                        total_files=total_sync_files,
                        remaining_files=rem_files,
                        remaining_bytes=0
                    )
                    if success:
                        uploaded_count += 1
                        # Push completed file and next 30 upcoming queued files to Web UI
                        upcoming_slice = [
                            {"name": f[0], "path": f[2], "size": "Queued"}
                            for f in files_to_download[idx:idx + 30]
                        ]
                        self.push_web_sync_status({
                            "state": "syncing_files",
                            "current_index": idx,
                            "remaining_items": rem_files,
                            "pending_queue": upcoming_slice,
                            "completed_item": {
                                "name": fname,
                                "path": human_folder,
                                "size": format_size(len(file_bytes)),
                                "time": time.strftime("%H:%M:%S")
                            }
                        })
                else:
                    print(f"\n[{idx}/{total_sync_files}] ⚠️ Could not extract '{fname}' from phone. Skipping.")

        finally:
            try:
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
                proc.terminate()
            except Exception:
                pass
            print("\n🧹 Cleaning up temporary cache...")
            shutil.rmtree(staging_dir, ignore_errors=True)

        print("\n" + "=" * 68)
        print("🎉 Phone Sync Complete Summary:")
        print(f"   ✅ Synced:      {uploaded_count} files")
        print(f"   ⚪ Skipped:     {unchanged_count} up-to-date files")
        print(f"   🌐 View Drive:  {self.base_url}/?path=/")
        print("=" * 68)

        self.push_web_sync_status({
            "state": "completed",
            "files_total": len(discovered_files),
            "files_uploaded": total_sync_files,
            "files_skipped": unchanged_count,
            "remaining_items": 0
        }, f"Phone sync complete! {total_sync_files} files uploaded to cloud.")


def show_clean_menu() -> Tuple[str, Path, Optional[str], Optional[Dict]]:
    """Present the clean 5-option backup menu."""
    user_home = Path.home()
    c_users_path = Path("C:/Users") if os.path.exists("C:/Users") else user_home
    d_drive_path = Path("D:/")

    phones = MTPPhoneManager.get_connected_phones()
    detected_phone_name = phones[0]["name"] if phones else None

    print("\n" + "=" * 68)
    print("       🚀 TGDrive Universal Backup Manager (Git-Style Sync)")
    print("=" * 68)
    print("Choose what you want to backup:\n")
    print(f"  [1] 💻 C: Drive (C:\\Users - User Folder Tree)")
    print(f"  [2] 💾 D: Drive ({'Available: D:\\' if d_drive_path.exists() else 'Not detected'})")
    print(f"  [3] 📱 Mobile Storage ({f'Detected: {detected_phone_name}' if detected_phone_name else 'Connected Phone'})")
    print(f"  [4] 🗂️  SD Card Storage (Phone SD Card or USB Reader)")
    print(f"  [5] ✍️  Custom Folder Path (Any local directory)")
    print("=" * 68)

    while True:
        choice = input("\nSelect an option (1-5): ").strip()

        if choice == "1":
            sub = input(f"Backup entire C:\\Users or specific user folder? (Press Enter for '{user_home.name}', or type 'all'): ").strip().lower()
            if sub == "all":
                return "local", c_users_path, "C_Drive/Users", None
            else:
                return "local", user_home, f"C_Drive/Users/{user_home.name}", None

        elif choice == "2":
            if not d_drive_path.exists():
                print("❌ D: Drive not found on this computer.")
                continue
            return "local", d_drive_path, "D_Drive", None

        elif choice == "3":
            if detected_phone_name:
                print(f"\n📱 Connected Phone Detected: {detected_phone_name}")
                print(f"   Target: This PC\\{detected_phone_name}\\Internal shared storage")
                sub = input("Folder to backup (Press Enter to backup ALL phone folders, or type a folder like 'Download', 'DCIM', 'Pictures'): ").strip()
                return "mtp", Path("."), None, {
                    "phone": detected_phone_name,
                    "storage": "Internal shared storage",
                    "subfolder": "" if sub.lower() in ["all", ""] else sub
                }
            else:
                p_str = input("Enter Phone Mount / Folder path: ").strip().strip('"').strip("'")
                if not p_str:
                    print("❌ No path entered. Please connect your phone in 'File Transfer' mode or provide a valid path.")
                    continue
                p = Path(p_str)
                if not p.exists():
                    print(f"❌ Path does not exist: {p_str}")
                    continue
                return "local", p, None, None

        elif choice == "4":
            if detected_phone_name:
                print(f"\n📱 Reading SD Card on Phone: {detected_phone_name}")
                print(f"   Target: This PC\\{detected_phone_name}\\SD card")
                sub = input("Folder on SD card (Press Enter to backup ALL SD card folders, or type a folder): ").strip()
                return "mtp", Path("."), None, {
                    "phone": detected_phone_name,
                    "storage": "SD card",
                    "subfolder": "" if sub.lower() in ["all", ""] else sub
                }
            else:
                sd_path = input("Enter SD Card Drive Letter (e.g. E:\\ or F:\\): ").strip().strip('"').strip("'")
                if not sd_path:
                    print("❌ No path entered.")
                    continue
                p = Path(sd_path)
                if not p.exists():
                    print(f"❌ Path does not exist: {sd_path}")
                    continue
                return "local", p, None, None

        elif choice == "5":
            manual_path = input("\nEnter custom folder path (e.g. C:\\Projects\\MyFolder): ").strip().strip('"').strip("'")
            p = Path(manual_path)
            if not p.exists():
                print(f"❌ Path does not exist: {manual_path}")
                continue
            return "local", p, None, None
        else:
            print("Please enter a valid choice (1-5).")


def main():
    default_pwd = os.getenv("ADMIN_PASSWORD", "admin")
    default_url = os.getenv("WEBSITE_URL") or "http://127.0.0.1:8000"

    parser = argparse.ArgumentParser(
        description="Universal TGDrive Backup Manager (Git-Style Diff Sync)."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        help="Local path to directory (e.g. C:\\Users\\nitro\\Desktop\\Notion Drive)",
    )
    parser.add_argument(
        "--url",
        "-u",
        type=str,
        default=default_url,
        help=f"TGDrive instance URL (default: {default_url})",
    )
    parser.add_argument(
        "--password",
        "-p",
        type=str,
        default=default_pwd,
        help="TGDrive admin password (auto-loaded from .env)",
    )
    parser.add_argument(
        "--dest",
        "-d",
        type=str,
        default=None,
        help="Custom destination folder prefix on TGDrive (optional)",
    )

    args = parser.parse_args()

    if args.source:
        mode = "local"
        source_path = Path(args.source)
        custom_tg_dest = args.dest
        mtp_info = None
    else:
        mode, source_path, custom_tg_dest, mtp_info = show_clean_menu()

    client = TGDriveBackupClient(
        base_url=args.url,
        password=args.password,
    )

    print(f"\nConnecting to TGDrive at {args.url}...")
    if not client.verify_auth():
        print(f"\n❌ Authentication failed on {args.url}!")
        print("   1. Make sure your local server is running: 'uvicorn main:app --port 8000'")
        print("   2. Check ADMIN_PASSWORD in your .env file matches.")
        sys.exit(1)

    print("✅ Connected & Authenticated!")

    if mode == "mtp" and mtp_info:
        client.sync_mtp_phone_folder(
            phone_name=mtp_info["phone"],
            storage_name=mtp_info["storage"],
            subfolder_rel=mtp_info["subfolder"]
        )
    else:
        client.sync_local_directory(source_path, target_tg_root=custom_tg_dest)


if __name__ == "__main__":
    main()
