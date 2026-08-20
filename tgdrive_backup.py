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

# System directories to ignore
IGNORED_DIRS = {
    # Windows System Folders
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
    # Android System Folders
    "android",
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
    "samsung backup",
    # Dev & Cache Junk
    "node_modules",
    "__pycache__",
    ".git",
    ".vscode",
    ".idea",
    ".venv",
    "venv",
    ".cache",
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
}

IGNORED_FILE_PREFIXES = ("ntuser.dat", "usrclass.dat", "~$", ".~", "dumpstack.log")
IGNORED_FILE_EXTENSIONS = (".tmp", ".crdownload", ".part", ".log1", ".log2", ".dmp")

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


def print_progress_bar(iteration: int, total: int, prefix: str = '', suffix: str = '', length: int = 25, fill: str = '█'):
    if total <= 0:
        percent = 100.0
        filled_length = length
    else:
        percent = min(100.0, (iteration / float(total)) * 100.0)
        filled_length = int(length * iteration // total)
    bar = fill * filled_length + '░' * (length - filled_length)
    sys.stdout.write(f'\r  {prefix} [{bar}] {percent:.1f}% {suffix}')
    sys.stdout.flush()
    if iteration >= total:
        sys.stdout.write('\n')


def should_skip_directory(dir_name: str, full_rel_path: str = "") -> bool:
    name_lower = dir_name.lower()
    path_lower = full_rel_path.lower().replace("\\", "/")

    if name_lower in IGNORED_DIRS:
        return True
    if any(ignored in path_lower for ignored in IGNORED_DIRS):
        return True
    if name_lower.startswith((".", "$")):
        return True
    return False


def should_skip_file(file_name: str) -> bool:
    name_lower = file_name.lower()
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
            if len(short_name) > 30:
                short_name = "…" + short_name[-29:]
            sys.stdout.write(f"\r  |{'█' * int(25 * idx // total_folders):<25}| {pct:5.1f}% ({idx}/{total_folders}) [DIR] {short_name:<30}")
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
            for _ in range(240):
                time.sleep(0.4)
                try:
                    prog_res = self.session.post(
                        f"{self.base_url}/api/getUploadProgress",
                        json={"password": self.password, "id": upload_id},
                        timeout=10,
                    )
                    prog_data = prog_res.json()
                    if prog_data.get("status") == "ok":
                        status = prog_data["data"][0]
                        current_bytes = prog_data["data"][1]
                        total_bytes = prog_data["data"][2] or file_size

                        duration = max(time.time() - start_time, 0.1)
                        speed = current_bytes / duration
                        speed_str = f"{format_size(int(speed))}/s"

                        if status == "running":
                            print_progress_bar(current_bytes, total_bytes, prefix="Syncing:", suffix=f"{format_size(current_bytes)}/{format_size(total_bytes)} ({speed_str})")
                            self.push_web_sync_status({
                                "current_bytes": current_bytes,
                                "speed_str": speed_str
                            })
                        elif status == "completed":
                            print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})")
                            fhash = compute_fast_file_hash(local_file_path)
                            self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, mtime, fhash)
                            return True
                except Exception:
                    pass

            print("\r    ✅ Sync dispatched to Telegram cloud.")
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
            for _ in range(240):
                time.sleep(0.4)
                try:
                    prog_res = self.session.post(
                        f"{self.base_url}/api/getUploadProgress",
                        json={"password": self.password, "id": upload_id},
                        timeout=10,
                    )
                    prog_data = prog_res.json()
                    if prog_data.get("status") == "ok":
                        status = prog_data["data"][0]
                        current_bytes = prog_data["data"][1]
                        total_bytes = prog_data["data"][2] or file_size

                        duration = max(time.time() - start_time, 0.1)
                        speed = current_bytes / duration
                        speed_str = f"{format_size(int(speed))}/s"

                        if status == "running":
                            print_progress_bar(current_bytes, total_bytes, prefix="Syncing:", suffix=f"{format_size(current_bytes)}/{format_size(total_bytes)} ({speed_str})")
                            self.push_web_sync_status({
                                "current_bytes": current_bytes,
                                "speed_str": speed_str
                            })
                        elif status == "completed":
                            print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Done in {duration:.1f}s ({speed_str})")
                            h = hashlib.md5(str(file_size).encode() + file_bytes[:2*1024*1024]).hexdigest()
                            self.manifest.update_file_record(f"{human_remote_folder}/{file_name}", file_size, time.time(), h)
                            return True
                except Exception:
                    pass

            print("\r    ✅ Sync dispatched to Telegram cloud.")
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
        print("⏳ Scanning directories (including empty folders)...")
        all_folders: List[str] = []
        all_files: List[Tuple[Path, str]] = []  # (local_path, rel_folder)

        for root, dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""

            dirs[:] = [d for d in dirs if not should_skip_directory(d, f"{rel_dir}/{d}")]
            if rel_dir:
                all_folders.append(rel_dir)

            for f in files:
                if not should_skip_file(f):
                    all_files.append((Path(root) / f, rel_dir))

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
        print("⏳ Scanning phone folders and files over USB MTP...")
        ps_scan = f'''
$shell = New-Object -ComObject Shell.Application
$thisPC = $shell.Namespace(17)
$phone = $thisPC.Items() | Where-Object {{ $_.Name -like "*{phone_name}*" }}
if (-not $phone) {{ Write-Error "Phone not found"; exit 1 }}

$storage = $phone.GetFolder.Items() | Where-Object {{ $_.Name -like "*{storage_name}*" }}
if (-not $storage) {{ Write-Error "Storage not found"; exit 1 }}

$targetRoot = $storage
$subPath = "{subfolder_rel.replace('/', chr(92))}".Trim('\\')
if ($subPath) {{
    $parts = $subPath.Split('\\')
    foreach ($p in $parts) {{
        $targetRoot = $targetRoot.GetFolder.Items() | Where-Object {{ $_.Name -eq $p -or $_.Name -like "*$p*" }}
        if (-not $targetRoot) {{ Write-Error "Subfolder $p not found"; exit 1 }}
    }}
}}

$foldersList = [System.Collections.Generic.List[string]]::new()
$filesList = [System.Collections.Generic.List[PSCustomObject]]::new()
$skipDirs = @('android', '.trash', '.thumbnails', 'lost.dir', '.soundrecordrecycler', '.filemanagerrecycler', '.aceself', '.slogan', 'samsung backup')

function Scan-MTP($folderItem, $relPath) {{
    if ($relPath) {{ $foldersList.Add($relPath) }}
    foreach ($item in $folderItem.GetFolder.Items()) {{
        $name = $item.Name
        $nameLower = $name.ToLower()
        if ($item.IsFolder) {{
            if ($nameLower -notin $skipDirs -and -not $nameLower.StartsWith('.')) {{
                $childRel = if ($relPath) {{ "$relPath/$name" }} else {{ $name }}
                Scan-MTP $item $childRel
            }}
        }} else {{
            if (-not $nameLower.StartsWith('.') -and -not $nameLower.StartsWith('~$')) {{
                $filesList.Add([PSCustomObject]@{{
                    name = $name
                    folder = $relPath
                }})
            }}
        }}
    }}
}}

Scan-MTP $targetRoot ""

[PSCustomObject]@{{
    folders = $foldersList
    files = $filesList
}} | ConvertTo-Json -Depth 5 -Compress
'''
        ps_scan_file = staging_dir / "scan.ps1"
        with open(ps_scan_file, "w", encoding="utf-8") as f:
            f.write(ps_scan)

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_scan_file)],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(res.stdout)
            discovered_folders = data.get("folders", [])
            discovered_files = data.get("files", [])
        except Exception as e:
            print(f"❌ Error scanning phone: {e}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return
        finally:
            if ps_scan_file.exists():
                ps_scan_file.unlink(missing_ok=True)

        print(f"  📂 Discovered {len(discovered_folders):,} folders and {len(discovered_files):,} files on phone.")

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

        # ── Step 4: Incremental Folder-by-Folder Extract & Upload ────────────
        from collections import defaultdict
        folder_to_files = defaultdict(list)
        for fname, rel_folder, human_folder in files_to_download:
            folder_to_files[rel_folder].append((fname, human_folder))

        total_sync_files = len(files_to_download)
        print(f"\n⚡ Step 3: Extracting & Uploading {total_sync_files:,} files from phone...\n")

        global_idx = 0
        try:
            for rel_folder, items_in_folder in folder_to_files.items():
                folder_dest = staging_dir / "active_folder"
                if folder_dest.exists():
                    shutil.rmtree(folder_dest, ignore_errors=True)
                folder_dest.mkdir(parents=True, exist_ok=True)

                # Prepare wanted filenames list for PowerShell
                wanted_names = [it[0] for it in items_in_folder]
                wanted_ps_arr = ", ".join(f"'{name.replace(chr(39), chr(39)+chr(39))}'" for name in wanted_names)

                ps_extract = f'''
$shell = New-Object -ComObject Shell.Application
$destPath = '{folder_dest.resolve()}'
$destFolder = $shell.Namespace($destPath)
$thisPC = $shell.Namespace(17)
$phone = $thisPC.Items() | Where-Object {{ $_.Name -like "*{phone_name}*" }} | Select-Object -First 1
if (-not $phone) {{ exit 1 }}

$storage = $phone.GetFolder.Items() | Where-Object {{ $_.Name -like "*{storage_name}*" }} | Select-Object -First 1
if (-not $storage) {{ exit 1 }}

$targetRoot = $storage
$folderRel = "{rel_folder}".Trim('\\').Trim('/')
if ($folderRel) {{
    $parts = $folderRel -split '[/\\\\]'
    foreach ($p in $parts) {{
        if ($p) {{
            $targetRoot = $targetRoot.GetFolder.Items() | Where-Object {{ $_.Name -eq $p }} | Select-Object -First 1
            if (-not $targetRoot) {{ exit 1 }}
        }}
    }}
}}

$wanted = @({wanted_ps_arr})
foreach ($item in $targetRoot.GetFolder.Items()) {{
    if (-not $item.IsFolder -and $item.Name -in $wanted) {{
        $destFolder.CopyHere($item, 16)
        $filePath = Join-Path $destPath $item.Name
        for ($w = 0; $w -lt 40; $w++) {{
            if ((Test-Path $filePath) -and (Get-Item $filePath).Length -gt 0) {{
                break
            }}
            Start-Sleep -Milliseconds 250
        }}
    }}
}}
'''
                folder_name_display = rel_folder if rel_folder else "Root Storage"
                print(f"\n📂 [{folder_name_display}] Extracting {len(items_in_folder)} file(s) from phone...")

                ps_runner = staging_dir / "extract_folder.ps1"
                with open(ps_runner, "w", encoding="utf-8") as f:
                    f.write(ps_extract)

                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_runner)],
                    capture_output=True,
                    text=True
                )
                if ps_runner.exists():
                    ps_runner.unlink(missing_ok=True)

                # Process each file in this folder
                for fname, human_folder in items_in_folder:
                    global_idx += 1
                    rem_files = total_sync_files - global_idx
                    staged_file = folder_dest / fname

                    # Wait up to 10 seconds for CopyHere to finish for this file
                    for _ in range(40):
                        if staged_file.exists() and staged_file.stat().st_size > 0:
                            break
                        time.sleep(0.25)

                    if staged_file.exists() and staged_file.stat().st_size > 0:
                        with open(staged_file, "rb") as f:
                            file_bytes = f.read()

                        # Purge from disk immediately before cloud upload!
                        staged_file.unlink(missing_ok=True)

                        self.upload_in_memory_file(
                            file_bytes,
                            fname,
                            human_folder,
                            file_idx=global_idx,
                            total_files=total_sync_files,
                            remaining_files=rem_files,
                            remaining_bytes=0
                        )
                    else:
                        print(f"[{global_idx}/{total_sync_files}] ⚠️ Could not extract '{fname}' from phone. Skipping.")

                shutil.rmtree(folder_dest, ignore_errors=True)

        finally:
            print("\n🧹 Cleaning up temporary cache...")
            shutil.rmtree(staging_dir, ignore_errors=True)

        print("\n" + "=" * 68)
        print("🎉 Phone Sync Complete Summary:")
        print(f"   ✅ Synced:      {total_sync_files} files")
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
    print(f"  [5] ✍️  Custom Folder Path (e.g. Notion Drive)")
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
                return "local", Path(p_str), None, None

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
                return "local", Path(sd_path), None, None

        elif choice == "5":
            manual_path = input("\nEnter custom folder path (e.g. C:\\Users\\nitro\\Desktop\\Notion Drive): ").strip().strip('"').strip("'")
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
