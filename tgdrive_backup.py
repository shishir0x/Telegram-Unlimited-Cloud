"""
TGDrive Auto-Backup Client
==========================
Clean 4-target backup manager:
1. C: Drive (C:\\Users)
2. D: Drive (D:\\)
3. Mobile Storage
4. SD Card Storage

Filters out Windows/Android system files, temp cache, and registry files.
"""

import os
import sys
import time
import ctypes
import string
import random
import shutil
import argparse
import requests
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional

# System directories to ignore (Windows, Android, Dev junk)
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
    # Development / Cache Junk
    "node_modules",
    "__pycache__",
    ".git",
    ".vscode",
    ".idea",
    ".venv",
    "venv",
    ".cache",
    ".temp",
}

# System files to ignore
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

# Prefixes/Extensions for system & temporary lock files
IGNORED_FILE_PREFIXES = ("ntuser.dat", "usrclass.dat", "~$", ".~", "dumpstack.log")
IGNORED_FILE_EXTENSIONS = (".tmp", ".crdownload", ".part", ".log1", ".log2", ".dmp")


def generate_random_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def should_skip_directory(dir_name: str, full_rel_path: str) -> bool:
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


def find_removable_drives() -> List[str]:
    """Find plugged-in SD cards / USB flash drives."""
    removable = []
    if os.name == "nt":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                if os.path.exists(drive_path):
                    try:
                        dtype = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                        if dtype == 2:  # DRIVE_REMOVABLE
                            removable.append(drive_path)
                    except Exception:
                        pass
            bitmask >>= 1
    return removable


class TGDriveBackupClient:
    def __init__(self, base_url: str, password: str, drive_root: str):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.drive_root = "/" + drive_root.strip("/") + "/"
        self.session = requests.Session()
        self._created_folders: Set[str] = set()

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

    def get_existing_items(self, remote_folder: str) -> Tuple[Set[str], Set[str]]:
        """Get existing subfolders and files in a remote directory to enable resume/skip."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/getDirectory",
                json={"password": self.password, "path": remote_folder},
                timeout=15,
            )
            data = res.json()
            if data.get("status") != "ok":
                return set(), set()

            contents = data.get("data", {}).get("contents", {})
            folders = set()
            files = set()

            for item_id, item in contents.items():
                if item.get("trash"):
                    continue
                if item.get("type") == "folder":
                    folders.add(item.get("name"))
                elif item.get("type") == "file":
                    files.add(item.get("name"))

            return folders, files
        except Exception:
            return set(), set()

    def ensure_remote_folder_chain(self, remote_folder_path: str):
        """Recursively ensure that all parent directories exist on TGDrive."""
        cleaned = ("/" + remote_folder_path.strip("/") + "/").replace("//", "/")
        if cleaned == "/" or cleaned in self._created_folders:
            return

        parts = [p for p in cleaned.split("/") if p]
        current_path = "/"

        for part in parts:
            existing_folders, _ = self.get_existing_items(current_path)
            target_path = (current_path + part + "/").replace("//", "/")

            if part not in existing_folders and target_path not in self._created_folders:
                try:
                    res = self.session.post(
                        f"{self.base_url}/api/createNewFolder",
                        json={
                            "password": self.password,
                            "path": current_path,
                            "name": part,
                        },
                        timeout=15,
                    )
                    data = res.json()
                    status = data.get("status")
                    if status in ["ok", "Folder with the name already exist in current directory"]:
                        self._created_folders.add(target_path)
                except Exception as e:
                    print(f"    [!] Error creating folder '{part}': {e}")

            current_path = target_path
            self._created_folders.add(current_path)

    def upload_file(self, local_file_path: Path, remote_folder: str) -> bool:
        """Upload a single file to TGDrive with progress tracking."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size
        upload_id = generate_random_id()

        self.ensure_remote_folder_chain(remote_folder)

        print(f"  ⬆️ Uploading: {file_name} ({format_size(file_size)})")
        print(f"     ➔ Folder: {remote_folder}")

        try:
            with open(local_file_path, "rb") as f:
                files = {"file": (file_name, f)}
                form_data = {
                    "path": remote_folder,
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

            # Poll for Telegram upload completion
            print("    ⏳ Syncing to Telegram storage...", end="", flush=True)
            for _ in range(60):
                time.sleep(1)
                try:
                    prog_res = self.session.post(
                        f"{self.base_url}/api/getUploadProgress",
                        json={"password": self.password, "id": upload_id},
                        timeout=10,
                    )
                    prog_data = prog_res.json()
                    if prog_data.get("status") == "ok":
                        status = prog_data["data"][0]
                        if status == "completed":
                            duration = max(time.time() - start_time, 0.1)
                            speed = file_size / duration
                            print(f"\r    ✅ Synced to Telegram in {duration:.1f}s ({format_size(int(speed))}/s)")
                            return True
                except Exception:
                    pass
            print("\r    ⚠️ Sync queued to Telegram background worker.")
            return True

        except Exception as e:
            print(f"    ❌ Upload error: {e}")
            return False

    def sync_directory(self, source_dir: Path):
        """Recursively scan and backup directory tree with system file exclusion."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Target path does not exist: {source_dir}")
            return

        print("\n" + "=" * 65)
        print(f"🔍 Scanning clean user files in: {source_dir}")
        print("   (Automatically filtering out Windows/Android system files)")
        print(f"🌐 Remote Destination: {self.base_url}{self.drive_root}")
        print("=" * 65)

        total_files = 0
        total_bytes = 0
        file_list = []

        for root, dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir)
            
            # Prune ignored directories in-place so os.walk skips descending into them
            dirs[:] = [
                d for d in dirs
                if not should_skip_directory(d, os.path.join(rel_dir, d))
            ]

            for file in files:
                if should_skip_file(file):
                    continue

                p = Path(root) / file
                try:
                    sz = p.stat().st_size
                    file_list.append(p)
                    total_files += 1
                    total_bytes += sz
                except (PermissionError, FileNotFoundError):
                    continue

        if total_files == 0:
            print("⚠️ No valid user files found to backup.")
            return

        print(f"\n📦 Found {total_files} clean files ({format_size(total_bytes)} total)")
        confirm = input("▶️ Start backup now? (Y/n): ").strip().lower()
        if confirm == "n":
            print("Backup cancelled.")
            return

        success_count = 0
        skip_count = 0
        fail_count = 0

        for idx, local_file in enumerate(file_list, start=1):
            rel_path = local_file.relative_to(source_dir).parent
            if str(rel_path) == ".":
                remote_folder = self.drive_root
            else:
                remote_folder = (self.drive_root + str(rel_path).replace("\\", "/") + "/").replace("//", "/")

            print(f"\n[{idx}/{total_files}] Processing: {local_file.name}")

            # Check if file already exists in remote folder
            _, existing_files = self.get_existing_items(remote_folder)
            if local_file.name in existing_files:
                print(f"  ⏭️ Already exists in {remote_folder} (Skipping)")
                skip_count += 1
                continue

            if self.upload_file(local_file, remote_folder):
                success_count += 1
            else:
                fail_count += 1

        print("\n" + "=" * 65)
        print("🎉 Backup Summary:")
        print(f"   ✅ Uploaded:                {success_count}")
        print(f"   ⏭️ Skipped (Already saved): {skip_count}")
        print(f"   ❌ Failed:                  {fail_count}")
        print(f"   📦 Total Files:             {total_files}")
        print(f"   🌐 View Drive:              {self.base_url}/?path={self.drive_root}")
        print("=" * 65)


def show_clean_menu() -> Tuple[Path, str]:
    """Present the clean 4-option backup menu."""
    user_home = Path.home()
    c_users_path = Path("C:/Users") if os.path.exists("C:/Users") else user_home
    d_drive_path = Path("D:/")

    removable_drives = find_removable_drives()
    default_sd = Path(removable_drives[0]) if removable_drives else None

    print("\n" + "=" * 60)
    print("       🚀 TGDrive Universal Backup Manager")
    print("=" * 60)
    print("Choose what you want to backup:\n")
    print(f"  [1] 💻 C: Drive (C:\\Users - User Profile & Files)")
    print(f"  [2] 💾 D: Drive ({'Available: D:\\' if d_drive_path.exists() else 'Not detected'})")
    print(f"  [3] 📱 Mobile Storage (Internal Phone / DCIM / Media)")
    print(f"  [4] 🗂️  SD Card Storage ({f'Detected: {default_sd}' if default_sd else 'Removable Memory Card'})")
    print(f"  [5] ✍️  Custom Folder Path")
    print("=" * 60)

    while True:
        choice = input("\nSelect an option (1-5): ").strip()
        
        if choice == "1":
            # Backup C:\Users (or current user's profile)
            print(f"\nTarget: {c_users_path}")
            sub = input(f"Backup entire C:\\Users or specific user folder? (Press Enter for '{user_home.name}' folder, or type 'all'): ").strip().lower()
            if sub == "all":
                return c_users_path, "/C_Users_Backup"
            else:
                return user_home, f"/{user_home.name}_Backup"

        elif choice == "2":
            if not d_drive_path.exists():
                print("❌ D: Drive not found on this computer.")
                continue
            return d_drive_path, "/D_Drive_Backup"

        elif choice == "3":
            # Mobile storage path
            print("\n📱 Mobile Storage Backup:")
            print("Connect your phone via USB (File Transfer / MTP) or enter the path where your phone storage is mounted.")
            phone_path = input("Enter Phone / Mobile folder path (e.g. E:\\ or D:\\Phone or custom path): ").strip().strip('"').strip("'")
            p = Path(phone_path)
            if not p.exists():
                print(f"❌ Path not found: {phone_path}")
                continue
            return p, "/Mobile_Storage_Backup"

        elif choice == "4":
            # SD Card Storage
            if default_sd and default_sd.exists():
                print(f"\nDetected SD Card at: {default_sd}")
                use_detected = input("Use this SD Card? (Y/n): ").strip().lower()
                if use_detected != "n":
                    return default_sd, "/SD_Card_Backup"
            
            sd_path = input("Enter SD Card Drive Letter or Path (e.g. E:\\ or F:\\): ").strip().strip('"').strip("'")
            p = Path(sd_path)
            if not p.exists():
                print(f"❌ SD Card path not found: {sd_path}")
                continue
            return p, "/SD_Card_Backup"

        elif choice == "5":
            manual_path = input("\nEnter custom folder path: ").strip().strip('"').strip("'")
            p = Path(manual_path)
            if not p.exists():
                print(f"❌ Path does not exist: {manual_path}")
                continue
            return p, f"/{p.name}_Backup"
        else:
            print("Please enter a valid choice (1-5).")


def main():
    parser = argparse.ArgumentParser(
        description="Universal TGDrive Backup Manager."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        help="Local path to directory (e.g. C:\\Users or D:\\)",
    )
    parser.add_argument(
        "--url",
        "-u",
        type=str,
        default="https://telegram-unlimited-cloud.onrender.com",
        help="TGDrive instance URL",
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
        help="Remote destination folder on TGDrive",
    )

    args = parser.parse_args()

    if args.source:
        source_path = Path(args.source)
        dest_folder = args.dest or f"/{source_path.name}_Backup"
    else:
        source_path, suggested_dest = show_clean_menu()
        dest_folder = args.dest or suggested_dest

    custom_dest = input(f"\nTGDrive Destination Folder [Default: {dest_folder}]: ").strip()
    if custom_dest:
        dest_folder = custom_dest

    client = TGDriveBackupClient(
        base_url=args.url,
        password=args.password,
        drive_root=dest_folder,
    )

    print(f"\nConnecting to TGDrive at {args.url}...")
    if not client.verify_auth():
        print("❌ Authentication failed! Check your TGDrive URL and password.")
        sys.exit(1)

    print("✅ Connected & Authenticated!")
    client.sync_directory(source_path)


if __name__ == "__main__":
    main()
