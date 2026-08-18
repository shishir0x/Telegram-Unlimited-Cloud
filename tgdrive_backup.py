"""
TGDrive Auto-Backup Client
==========================
Recursively scans any local drive, SD card, or folder and uploads
the complete folder hierarchy to your TGDrive instance.
"""

import os
import sys
import time
import string
import random
import argparse
import requests
from pathlib import Path
from typing import Set, Tuple


def generate_random_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


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
            print(f"[!] Connection failed: {e}")
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
                    print(f"    [!] Error creating folder {part} in {current_path}: {e}")

            current_path = target_path
            self._created_folders.add(current_path)

    def upload_file(self, local_file_path: Path, remote_folder: str) -> bool:
        """Upload a single file to TGDrive with progress tracking."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size
        upload_id = generate_random_id()

        # Ensure folder exists
        self.ensure_remote_folder_chain(remote_folder)

        print(f"  ⬆️ Uploading: {file_name} ({format_size(file_size)}) ➔ {remote_folder}")

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
                    timeout=300,
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

        print(f"\n📂 Scanning local storage: {source_dir}")
        print(f"🌐 Target TGDrive Destination: {self.base_url}{self.drive_root}\n")

        total_files = 0
        total_bytes = 0
        file_list = []

        for root, _, files in os.walk(source_dir):
            for file in files:
                p = Path(root) / file
                try:
                    sz = p.stat().st_size
                    file_list.append(p)
                    total_files += 1
                    total_bytes += sz
                except (PermissionError, FileNotFoundError):
                    continue

        print(f"📦 Found {total_files} files ({format_size(total_bytes)} total)")
        confirm = input("▶️ Start backup to TGDrive? (y/N): ").strip().lower()
        if confirm != "y":
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

        print("\n" + "=" * 50)
        print("🎉 Backup Summary:")
        print(f"   Uploaded: {success_count}")
        print(f"   Skipped (already exists): {skip_count}")
        print(f"   Failed:   {fail_count}")
        print(f"   Total:    {total_files}")
        print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Backup local disk / SD card folder tree to TGDrive."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        help="Local path to directory or SD card (e.g. D:\\Photos or /sdcard/DCIM)",
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
        default="/My_Backup",
        help="Remote destination root folder on TGDrive (e.g. /My_Backup)",
    )

    args = parser.parse_args()

    source = args.source
    if not source:
        source = input("Enter the path of the folder / SD card to backup: ").strip().strip('"').strip("'")

    client = TGDriveBackupClient(
        base_url=args.url,
        password=args.password,
        drive_root=args.dest,
    )

    print(f"Connecting to TGDrive at {args.url}...")
    if not client.verify_auth():
        print("❌ Authentication failed! Check your TGDrive URL and password.")
        sys.exit(1)

    print("✅ Authenticated successfully with TGDrive!")
    client.sync_directory(Path(source))


if __name__ == "__main__":
    main()
