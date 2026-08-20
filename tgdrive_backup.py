"""
TGDrive Universal Backup Manager - Git-Style Change Detection & Real-Time Sync
===============================================================================
Features:
- Git-like change tracking: [+] Added, [M] Modified, [=] Unchanged
- Real-time terminal progress bar with upload speed & ETA
- Simultaneous real-time status reflection on Google Drive Web UI
- Exact folder structure mirroring for PC Disks, Mobile Phones, and SD Cards
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
import requests
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

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
    "android/data",
    "android/obb",
    ".trash",
    ".thumbnails",
    "lost.dir",
    ".android_secure",
    ".estrongs",
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
    Converts C:\\Users\\nitro\\Desktop\\Notion Drive
    into C_Drive/Users/nitro/Desktop/Notion Drive
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
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            this_pc = shell.Namespace(17) # ssfDRIVES
            phones = []
            for item in this_pc.Items():
                path_str = str(item.Path)
                if "usb#" in path_str.lower() or "wpdbusenumroot" in path_str.lower() or "::" in path_str:
                    phones.append({"name": item.Name, "item": item})
            return phones
        except Exception:
            return MTPPhoneManager._get_phones_via_powershell()

    @staticmethod
    def _get_phones_via_powershell():
        import subprocess
        cmd = '''$s = New-Object -ComObject Shell.Application; $s.Namespace(17).Items() | Where-Object { $_.Path -like "*usb#*" -or $_.Path -like "*::*" } | Select-Object -ExpandProperty Name'''
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=True)
            names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return [{"name": name, "item": None} for name in names]
        except Exception:
            return []


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
                json={"pass": self.password},
                timeout=15,
            )
            data = res.json()
            return data.get("status") == "ok"
        except Exception as e:
            print(f"[!] Connection error to {self.base_url}: {e}")
            return False

    def get_directory_contents(self, remote_id_path: str) -> Dict:
        """Get directory contents by its TGDrive ID path."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/getDirectory",
                json={"password": self.password, "path": remote_id_path},
                timeout=15,
            )
            if res.status_code != 200:
                print(f"[!] HTTP {res.status_code} reading directory {remote_id_path}")
                return {}
            data = res.json()
            if data.get("status") == "ok":
                return data.get("data", {}).get("contents", {})
            else:
                print(f"[!] Server error reading directory {remote_id_path}: {data.get('status')}")
        except json.JSONDecodeError:
            print(f"[!] Invalid JSON response for directory {remote_id_path}")
        except Exception as e:
            print(f"[!] Error reading directory {remote_id_path}: {e}")
        return {}

    def resolve_or_create_folder_id_path(self, named_path: str) -> str:
        """Converts human named path into TGDrive ID path, auto-creating missing folders."""
        cleaned = named_path.strip("/")
        if not cleaned:
            return "/"

        if cleaned in self._folder_id_cache:
            return self._folder_id_cache[cleaned]

        parts = [p for p in cleaned.split("/") if p]
        current_id_path = "/"
        current_named_prefix = ""

        for part in parts:
            current_named_prefix = f"{current_named_prefix}/{part}".strip("/")
            if current_named_prefix in self._folder_id_cache:
                current_id_path = self._folder_id_cache[current_named_prefix]
                continue

            contents = self.get_directory_contents(current_id_path)
            found_id = None

            if contents:  # Only search if we got valid contents
                for item_id, item in contents.items():
                    if item.get("type") == "folder" and not item.get("trash"):
                        if item.get("name") == part:
                            found_id = item.get("id") or item_id
                            break

            if not found_id:
                try:
                    res = self.session.post(
                        f"{self.base_url}/api/createNewFolder",
                        json={
                            "password": self.password,
                            "path": current_id_path,
                            "name": part,
                        },
                        timeout=15,
                    )
                    if res.status_code == 200:
                        res_data = res.json()
                        if res_data.get("status") == "ok":
                            # Re-fetch contents to find the new folder
                            contents = self.get_directory_contents(current_id_path)
                            for item_id, item in contents.items():
                                if item.get("type") == "folder" and item.get("name") == part:
                                    found_id = item.get("id") or item_id
                                    break
                        else:
                            print(f"    [!] Folder creation failed: {res_data.get('status')}")
                except Exception as e:
                    print(f"    [!] Error creating folder '{part}': {e}")

            if not found_id:
                print(f"    [!] Warning: Could not resolve folder '{part}', using name as fallback")
                found_id = part

            current_id_path = (current_id_path + found_id + "/").replace("//", "/")
            self._folder_id_cache[current_named_prefix] = current_id_path

        return current_id_path

    def get_existing_files_in_folder(self, folder_id_path: str) -> Dict[str, Dict]:
        """Returns map of filename -> item details in folder."""
        contents = self.get_directory_contents(folder_id_path)
        existing = {}
        if not contents:
            print(f"    [!] No contents found in folder {folder_id_path}")
            return existing
        for _, item in contents.items():
            if item.get("type") == "file" and not item.get("trash"):
                existing[item.get("name")] = item
        return existing

    def upload_file(self, local_file_path: Path, human_remote_folder: str, file_idx: int = 1, total_files: int = 1) -> bool:
        """Upload a single file to TGDrive with real-time terminal progress bar."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size
        mtime = local_file_path.stat().st_mtime

        if file_size == 0:
            print(f"  ⏭️ Skipping 0-byte empty file: {file_name}")
            return True

        upload_id = generate_random_id()
        remote_id_path = self.resolve_or_create_folder_id_path(human_remote_folder)

        print(f"\n[{file_idx}/{total_files}] ⬆️ {file_name} ({format_size(file_size)})")
        print(f"       ➔ Path: /{human_remote_folder}/")

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
            last_current = 0
            for _ in range(120):
                time.sleep(0.5)
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
                        elif status == "completed":
                            print_progress_bar(total_bytes, total_bytes, prefix="Syncing:", suffix=f"Completed in {duration:.1f}s ({speed_str})")
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

    def analyze_git_style_changes(self, source_dir: Path, base_tg_path: str) -> Tuple[List[Path], List[Path], List[Path]]:
        """
        Git-style diff analysis:
        - [+] Added (New files)
        - [M] Modified (Changed files)
        - [=] Unchanged (Up-to-date files)
        """
        added_files = []
        modified_files = []
        unchanged_files = []

        # Cache of remote directory listings
        remote_folders_cache: Dict[str, Dict[str, Dict]] = {}

        for root, dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir)
            dirs[:] = [d for d in dirs if not should_skip_directory(d, os.path.join(rel_dir, d))]

            for file in files:
                if should_skip_file(file):
                    continue
                p = Path(root) / file
                try:
                    sz = p.stat().st_size
                    mtime = p.stat().st_mtime
                except Exception:
                    continue

                rel_p = p.relative_to(source_dir).parent
                if str(rel_p) == ".":
                    human_folder = base_tg_path
                else:
                    human_folder = f"{base_tg_path}/{str(rel_p).replace(chr(92), '/')}".strip("/")

                manifest_key = f"{human_folder}/{file}"
                record = self.manifest.get_file_record(manifest_key)

                # Check manifest first (super fast)
                if record and record.get("size") == sz and abs(record.get("mtime", 0) - mtime) < 1.0:
                    unchanged_files.append(p)
                    continue

                # Check remote directory
                if human_folder not in remote_folders_cache:
                    remote_id = self.resolve_or_create_folder_id_path(human_folder)
                    remote_folders_cache[human_folder] = self.get_existing_files_in_folder(remote_id)

                remote_files = remote_folders_cache[human_folder]
                if file in remote_files:
                    remote_sz = remote_files[file].get("size", 0)
                    if remote_sz == sz:
                        unchanged_files.append(p)
                        self.manifest.update_file_record(manifest_key, sz, mtime, "")
                    else:
                        modified_files.append(p)
                else:
                    added_files.append(p)

        return added_files, modified_files, unchanged_files

    def sync_local_directory(self, source_dir: Path, target_tg_root: Optional[str] = None):
        """Recursively scan and backup directory tree with Git-style change detection."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Target path does not exist: {source_dir}")
            return

        if not target_tg_root:
            target_tg_root = convert_local_path_to_tg_structure(source_dir)

        print("\n" + "=" * 68)
        print(f"🔍 Analyzing Local Source:  {source_dir}")
        print(f"🌐 Mirrored TGDrive Target: /{target_tg_root}/")
        print("=" * 68)

        print("⏳ Running Git-style change analysis against Cloud Drive...")
        added, modified, unchanged = self.analyze_git_style_changes(source_dir, target_tg_root)

        files_to_sync = added + modified
        total_sync_bytes = sum(f.stat().st_size for f in files_to_sync)

        print("\n" + "─" * 68)
        print("📊 Git-Style Change Summary:")
        print(f"   🟢 [+] Added:     {len(added):>4} new files (to upload)")
        print(f"   🟡 [M] Modified:  {len(modified):>4} changed files (to update)")
        print(f"   ⚪ [=] Unchanged: {len(unchanged):>4} up-to-date files (skipped)")
        print("─" * 68)
        print(f"📦 Total to sync: {len(files_to_sync)} files ({format_size(total_sync_bytes)})")
        print("=" * 68)

        if not files_to_sync:
            print("\n✨ Everything is already up to date! Nothing to sync.")
            return

        confirm = input("\n▶️ Proceed with sync? (Y/n): ").strip().lower()
        if confirm == "n":
            print("Sync cancelled.")
            return

        self._upload_local_files_mirrored(source_dir, files_to_sync, target_tg_root)

    def _upload_local_files_mirrored(self, base_path: Path, file_list: List[Path], base_tg_path: str):
        total_files = len(file_list)
        success_count = 0
        fail_count = 0

        for idx, local_file in enumerate(file_list, start=1):
            rel_path = local_file.relative_to(base_path).parent
            if str(rel_path) == ".":
                human_folder = base_tg_path
            else:
                human_folder = f"{base_tg_path}/{str(rel_path).replace(chr(92), '/')}".strip("/")

            if self.upload_file(local_file, human_folder, file_idx=idx, total_files=total_files):
                success_count += 1
            else:
                fail_count += 1

        print("\n" + "=" * 68)
        print("🎉 Sync Complete Summary:")
        print(f"   ✅ Synced:      {success_count}")
        print(f"   ❌ Failed:      {fail_count}")
        print(f"   📦 Total:       {total_files}")
        print(f"   🌐 View Drive:  {self.base_url}/?path=/")
        print("=" * 68)

    def sync_mtp_phone_folder(self, phone_name: str, storage_name: str, subfolder_rel: str = "", auto_confirm: bool = False):
        """Sync files from an MTP connected phone into TGDrive preserving full phone path."""
        import subprocess
        staging_dir = Path(tempfile.gettempdir()) / "tgdrive_phone_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        clean_phone_name = phone_name.replace(" ", "_")
        clean_storage_name = storage_name.replace(" ", "_")
        base_tg_prefix = f"{clean_phone_name}/{clean_storage_name}"

        print("\n" + "=" * 68)
        print(f"📱 Phone Source:     {phone_name} ➔ {storage_name} ➔ {subfolder_rel or 'Root'}")
        print(f"🌐 Mirrored TG Path: /{base_tg_prefix}/{subfolder_rel}".rstrip("/") + "/")
        print("=" * 68)

        ps_script = f'''
$shell = New-Object -ComObject Shell.Application
$destFolder = $shell.Namespace("{str(staging_dir)}")

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

function Extract-MTP($item, $relPath) {{
    $localTargetDir = Join-Path "{str(staging_dir)}" $relPath
    if (-not (Test-Path $localTargetDir)) {{
        New-Item -ItemType Directory -Path $localTargetDir -Force | Out-Null
    }}
    $localFolderObj = $shell.Namespace($localTargetDir)

    foreach ($sub in $item.GetFolder.Items()) {{
        $name = $sub.Name
        if ($sub.IsFolder) {{
            if ($name -notin @("Android", "lost.dir", ".trash", ".thumbnails")) {{
                $childRel = if ($relPath) {{ Join-Path $relPath $name }} else {{ $name }}
                Extract-MTP $sub $childRel
            }}
        }} else {{
            if ($name -notlike "~$*" -and $name -notlike ".*") {{
                $localFolderObj.CopyHere($sub, 16)
                $destFile = Join-Path $localTargetDir $name
                $timeout = 0
                while ((-not (Test-Path $destFile) -or (Get-Item $destFile).Length -eq 0) -and $timeout -lt 20) {{
                    Start-Sleep -Milliseconds 250
                    $timeout++
                }}
            }}
        }}
    }}
}}

Extract-MTP $targetRoot $subPath
'''
        print("⏳ Extracting files from connected phone via USB...")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                check=True
            )
        except Exception as e:
            print(f"❌ Error communicating with phone: {e}")
            return

        staged_files = []
        for root, _, files in os.walk(staging_dir):
            for f in files:
                p = Path(root) / f
                if not should_skip_file(f):
                    staged_files.append(p)

        if not staged_files:
            print("⚠️ No files found in the specified phone folder.")
            return

        print(f"📦 Staged {len(staged_files)} files from phone. Analyzing changes...")
        added, modified, unchanged = self.analyze_git_style_changes(staging_dir, base_tg_prefix)
        files_to_sync = added + modified

        print("\n" + "─" * 68)
        print(f"📊 Git-Style Phone Change Summary:")
        print(f"   🟢 [+] Added:     {len(added)} new files")
        print(f"   🟡 [M] Modified:  {len(modified)} changed files")
        print(f"   ⚪ [=] Unchanged: {len(unchanged)} up-to-date files (skipped)")
        print("─" * 68)

        if not files_to_sync:
            print("✨ All phone files are already up to date on Cloud Drive!")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return

        if not auto_confirm:
            confirm = input("▶️ Start uploading to TGDrive? (Y/n): ").strip().lower()
            if confirm == "n":
                print("Upload cancelled.")
                shutil.rmtree(staging_dir, ignore_errors=True)
                return

        try:
            self._upload_local_files_mirrored(staging_dir, files_to_sync, base_tg_prefix)
        finally:
            print("🧹 Cleaning up temporary phone cache...")
            shutil.rmtree(staging_dir, ignore_errors=True)


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
        default="http://127.0.0.1:8000",
        help="TGDrive instance URL (default: http://127.0.0.1:8000 or https://telegram-unlimited-cloud.onrender.com)",
    )
    parser.add_argument(
        "--password",
        "-p",
        type=str,
        default="admin",
        help="TGDrive admin password",
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
        # Fallback to Render URL if local is unreachable
        if "127.0.0.1" in args.url:
            print("[!] Local server not answering, falling back to Render cloud...")
            client.base_url = "https://telegram-unlimited-cloud.onrender.com"
            if not client.verify_auth():
                print("❌ Authentication failed!")
                sys.exit(1)
        else:
            print("❌ Authentication failed! Check your URL and password.")
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
