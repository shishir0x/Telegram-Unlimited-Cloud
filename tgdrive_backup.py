"""
TGDrive Auto-Backup & Storage Scanner
====================================
Automatically scans local disks, SD cards, USB drives, and folders,
allowing you to choose what to backup and uploads the exact nested
folder structure to your TGDrive instance.
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


def generate_random_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def get_drive_type_label(drive_path: str) -> str:
    if os.name != "nt":
        return "Local Storage"
    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
        types = {
            2: "📱 Removable (SD Card / USB)",
            3: "💾 Fixed Local Disk",
            4: "🌐 Network Drive",
            5: "💿 CD/DVD Drive",
            6: "⚡ RAM Disk",
        }
        return types.get(drive_type, "💾 Disk Drive")
    except Exception:
        return "💾 Disk Drive"


def get_available_storage_targets() -> List[Dict[str, str]]:
    targets = []

    # 1. Detect all Windows Drive Letters (C:\, D:\, E:\, etc.)
    if os.name == "nt":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                if os.path.exists(drive_path):
                    label = get_drive_type_label(drive_path)
                    try:
                        usage = shutil.disk_usage(drive_path)
                        free_str = format_size(usage.free)
                        total_str = format_size(usage.total)
                        desc = f"{label} [{letter}:] ({free_str} free / {total_str})"
                    except Exception:
                        desc = f"{label} [{letter}:]"
                    targets.append({
                        "name": f"Drive {letter}:",
                        "path": drive_path,
                        "description": desc,
                        "is_drive": True
                    })
            bitmask >>= 1
    else:
        # Unix / macOS / Linux / Android
        for mount in ["/storage/emulated/0", "/sdcard", "/Volumes", os.path.expanduser("~")]:
            if os.path.exists(mount):
                targets.append({
                    "name": os.path.basename(mount) or mount,
                    "path": mount,
                    "description": f"Storage: {mount}",
                    "is_drive": True
                })

    # 2. Check for Specific Project Folders (e.g. Notion Drive, Projects, etc.)
    user_home = Path.home()
    notion_drive = user_home / "Desktop" / "Notion Drive"
    if notion_drive.exists():
        targets.append({
            "name": "Notion Drive",
            "path": str(notion_drive),
            "description": f"📂 Notion Drive ({notion_drive})",
            "is_drive": False
        })

    # 3. Standard User Library Folders
    common_folders = [
        ("Desktop", user_home / "Desktop"),
        ("Documents", user_home / "Documents"),
        ("Downloads", user_home / "Downloads"),
        ("Pictures / Photos", user_home / "Pictures"),
        ("Videos", user_home / "Videos"),
    ]

    for name, folder_path in common_folders:
        if folder_path.exists():
            targets.append({
                "name": name,
                "path": str(folder_path),
                "description": f"📁 {name} ({folder_path})",
                "is_drive": False
            })

    return targets


class TGDriveBackupClient:
    def __init__(self, base_url: str, password: str, drive_root: str = "/Backup"):
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
            print(f"[!] Connection failed to {self.base_url}: {e}")
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
                    print(f"    [!] Error creating folder '{part}' in '{current_path}': {e}")

            current_path = target_path
            self._created_folders.add(current_path)

    def upload_file(self, local_file_path: Path, remote_folder: str) -> bool:
        """Upload a single file to TGDrive with progress tracking."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size
        upload_id = generate_random_id()

        # Ensure folder exists
        self.ensure_remote_folder_chain(remote_folder)

        print(f"  ⬆️ Uploading: {file_name} ({format_size(file_size)})")
        print(f"     ➔ Destination: {remote_folder}")

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
            print("    ⏳ Syncing to Telegram storage channel...", end="", flush=True)
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
        """Recursively scan and backup the entire directory tree."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Source folder does not exist: {source_dir}")
            return

        print(f"\n" + "=" * 60)
        print(f"📂 Scanning storage directory: {source_dir}")
        print(f"🌐 Remote TGDrive Destination: {self.base_url}{self.drive_root}")
        print("=" * 60)

        total_files = 0
        total_bytes = 0
        file_list = []

        for root, dirs, files in os.walk(source_dir):
            # Skip hidden folders / git
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["$RECYCLE.BIN", "System Volume Information", "node_modules", "__pycache__"]]
            for file in files:
                if file.startswith("~$") or file.endswith(".tmp"):
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
            print("⚠️ No files found to backup in this location.")
            return

        print(f"📦 Discovered: {total_files} files ({format_size(total_bytes)} total)")
        confirm = input("\n▶️ Proceed with backup? (Y/n): ").strip().lower()
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
                print(f"  ⏭️ Already backed up in {remote_folder} (Skipping)")
                skip_count += 1
                continue

            if self.upload_file(local_file, remote_folder):
                success_count += 1
            else:
                fail_count += 1

        print("\n" + "=" * 60)
        print("🎉 Backup Completed!")
        print(f"   ✅ Uploaded:                {success_count}")
        print(f"   ⏭️ Skipped (Already saved): {skip_count}")
        print(f"   ❌ Failed:                  {fail_count}")
        print(f"   📦 Total Files:             {total_files}")
        print(f"   🌐 View your drive:         {self.base_url}/?path={self.drive_root}")
        print("=" * 60)


def interactive_target_selector() -> Tuple[Path, str]:
    targets = get_available_storage_targets()

    print("\n" + "=" * 65)
    print("       🚀 TGDrive Storage Backup & Device Scanner")
    print("=" * 65)
    print("Detected Storage Disks & Locations:\n")

    for i, t in enumerate(targets, start=1):
        print(f"  [{i}] {t['description']}")

    custom_idx = len(targets) + 1
    print(f"  [{custom_idx}] ✍️  Custom Path (Type a custom folder path)")
    print("=" * 65)

    while True:
        choice = input(f"\nSelect a disk or folder to backup (1-{custom_idx}): ").strip()
        if not choice:
            continue
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(targets):
                selected = targets[choice_num - 1]
                source_path = Path(selected["path"])
                folder_name = selected["name"].replace(":", "").replace(" ", "_").replace("/", "_")
                
                # If a whole disk is selected, ask if they want a subfolder or whole disk
                if selected.get("is_drive"):
                    print(f"\nYou selected entire drive: {source_path}")
                    sub = input("Do you want to backup a specific folder inside it? (Press Enter for entire drive, or type folder name): ").strip()
                    if sub:
                        candidate = source_path / sub
                        if candidate.exists():
                            source_path = candidate
                            folder_name = candidate.name
                
                default_dest = f"/{folder_name}"
                return source_path, default_dest
            elif choice_num == custom_idx:
                manual_path = input("\nEnter custom folder path: ").strip().strip('"').strip("'")
                p = Path(manual_path)
                if not p.exists():
                    print(f"❌ Path does not exist: {manual_path}")
                    continue
                return p, f"/{p.name}"
            else:
                print("Invalid choice number. Try again.")
        except ValueError:
            print("Please enter a valid number.")


def main():
    parser = argparse.ArgumentParser(
        description="Backup local disk / SD card folder tree to TGDrive."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        help="Local path to directory or SD card (e.g. D:\\Photos or C:\\Users\\nitro\\Desktop\\Notion Drive)",
    )
    parser.add_argument(
        "--url",
        "-u",
        type=str,
        default="https://telegram-unlimited-cloud.onrender.com",
        help="TGDrive instance URL (default: https://telegram-unlimited-cloud.onrender.com)",
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
        help="Remote destination root folder on TGDrive (e.g. /Notion_Drive)",
    )

    args = parser.parse_args()

    if args.source:
        source_path = Path(args.source)
        dest_folder = args.dest or f"/{source_path.name}"
    else:
        source_path, suggested_dest = interactive_target_selector()
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

    print("✅ Authenticated successfully with TGDrive!")
    client.sync_directory(source_path)


if __name__ == "__main__":
    main()
