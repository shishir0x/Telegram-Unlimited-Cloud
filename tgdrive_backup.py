"""
TGDrive Universal Backup Client (with Windows MTP Phone Support)
================================================================
Recursively backs up:
1. C: Drive (C:\\Users)
2. D: Drive
3. Mobile Storage (MTP Phone: OnePlus Nord CE4, etc.)
4. SD Card Storage (Phone SD Card or USB Card Reader)
5. Custom Path

Uploads files while preserving the exact nested folder structure in TGDrive.
"""

import os
import sys
import time
import ctypes
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
    # Windows
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
    # Android
    "android/data",
    "android/obb",
    ".trash",
    ".thumbnails",
    "lost.dir",
    ".android_secure",
    ".estrongs",
    # Dev Junk
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


def generate_random_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


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
        except Exception:
            # Fallback using python comtypes or powershell
            return MTPPhoneManager._get_phones_via_powershell()

        this_pc = shell.Namespace(17) # ssfDRIVES
        phones = []
        for item in this_pc.Items():
            # MTP devices don't have standard drive letters like C:\
            path_str = str(item.Path)
            if "usb#" in path_str.lower() or "wpdbusenumroot" in path_str.lower() or "::" in path_str:
                phones.append({
                    "name": item.Name,
                    "item": item
                })
        return phones

    @staticmethod
    def _get_phones_via_powershell():
        # Fallback to powershell query if win32com is not installed
        import subprocess
        cmd = '''$s = New-Object -ComObject Shell.Application; $s.Namespace(17).Items() | Where-Object { $_.Path -like "*usb#*" -or $_.Path -like "*::*" } | Select-Object -ExpandProperty Name'''
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, check=True)
            names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return [{"name": name, "item": None} for name in names]
        except Exception:
            return []


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

    def sync_local_directory(self, source_dir: Path):
        """Recursively scan and backup local directory tree."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Target path does not exist: {source_dir}")
            return

        print("\n" + "=" * 65)
        print(f"🔍 Scanning clean files in: {source_dir}")
        print(f"🌐 Remote Destination: {self.base_url}{self.drive_root}")
        print("=" * 65)

        total_files = 0
        total_bytes = 0
        file_list = []

        for root, dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir)
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

        print(f"\n📦 Found {total_files} files ({format_size(total_bytes)} total)")
        confirm = input("▶️ Start backup now? (Y/n): ").strip().lower()
        if confirm == "n":
            print("Backup cancelled.")
            return

        self._upload_file_list(source_dir, file_list)

    def _upload_file_list(self, base_path: Path, file_list: List[Path]):
        total_files = len(file_list)
        success_count = 0
        skip_count = 0
        fail_count = 0

        for idx, local_file in enumerate(file_list, start=1):
            rel_path = local_file.relative_to(base_path).parent
            if str(rel_path) == ".":
                remote_folder = self.drive_root
            else:
                remote_folder = (self.drive_root + str(rel_path).replace("\\", "/") + "/").replace("//", "/")

            print(f"\n[{idx}/{total_files}] Processing: {local_file.name}")

            # Check if file already exists
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

    def sync_mtp_phone_folder(self, phone_name: str, storage_name: str, subfolder_rel: str = ""):
        """Sync files from an MTP connected phone into TGDrive."""
        import subprocess
        staging_dir = Path(tempfile.gettempdir()) / "tgdrive_phone_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        print("\n" + "=" * 65)
        print(f"📱 Scanning Phone: {phone_name} ➔ {storage_name} ➔ {subfolder_rel or 'Root'}")
        print("=" * 65)

        # PowerShell script to recursively extract MTP files with relative paths
        ps_script = f'''
$shell = New-Object -ComObject Shell.Application
$destFolder = $shell.Namespace("{str(staging_dir)}")

$thisPC = $shell.Namespace(17)
$phone = $thisPC.Items() | Where-Object {{ $_.Name -like "*{phone_name}*" }}
if (-not $phone) {{ Write-Error "Phone not found"; exit 1 }}

$storage = $phone.GetFolder.Items() | Where-Object {{ $_.Name -like "*{storage_name}*" }}
if (-not $storage) {{ Write-Error "Storage not found"; exit 1 }}

$targetRoot = $storage
$subPath = "{subfolder_rel.replace('/', '\\')}".Trim('\\')
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
                Write-Output "COPY:$relPath/$name"
                $localFolderObj.CopyHere($sub, 16) # 16 = Overwrite / Yes to All
            }}
        }}
    }}
}}

Extract-MTP $targetRoot ""
'''
        print("⏳ Fetching files from connected phone via USB...")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                check=True
            )
        except Exception as e:
            print(f"❌ Error communicating with phone: {e}")
            return

        # Scan staged files
        staged_files = []
        total_bytes = 0
        for root, _, files in os.walk(staging_dir):
            for f in files:
                p = Path(root) / f
                staged_files.append(p)
                total_bytes += p.stat().st_size

        if not staged_files:
            print("⚠️ No files found in the specified phone folder.")
            return

        print(f"📦 Successfully read {len(staged_files)} files ({format_size(total_bytes)}) from phone.")
        confirm = input("▶️ Start uploading to TGDrive? (Y/n): ").strip().lower()
        if confirm == "n":
            print("Upload cancelled.")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return

        try:
            self._upload_file_list(staging_dir, staged_files)
        finally:
            print("🧹 Cleaning up local temporary staging files...")
            shutil.rmtree(staging_dir, ignore_errors=True)


def show_clean_menu() -> Tuple[str, Path, str, Optional[Dict]]:
    """Present the clean 4-option backup menu."""
    user_home = Path.home()
    c_users_path = Path("C:/Users") if os.path.exists("C:/Users") else user_home
    d_drive_path = Path("D:/")

    # Detect connected MTP phones
    phones = MTPPhoneManager.get_connected_phones()
    detected_phone_name = phones[0]["name"] if phones else None

    print("\n" + "=" * 60)
    print("       🚀 TGDrive Universal Backup Manager")
    print("=" * 60)
    print("Choose what you want to backup:\n")
    print(f"  [1] 💻 C: Drive (C:\\Users - User Profile & Files)")
    print(f"  [2] 💾 D: Drive ({'Available: D:\\' if d_drive_path.exists() else 'Not detected'})")
    print(f"  [3] 📱 Mobile Storage ({f'Detected: {detected_phone_name}' if detected_phone_name else 'Connected Phone'})")
    print(f"  [4] 🗂️  SD Card Storage (Phone SD Card or USB Reader)")
    print(f"  [5] ✍️  Custom Folder Path")
    print("=" * 60)

    while True:
        choice = input("\nSelect an option (1-5): ").strip()

        if choice == "1":
            print(f"\nTarget: {c_users_path}")
            sub = input(f"Backup entire C:\\Users or specific user folder? (Press Enter for '{user_home.name}', or type 'all'): ").strip().lower()
            if sub == "all":
                return "local", c_users_path, "/C_Users_Backup", None
            else:
                return "local", user_home, f"/{user_home.name}_Backup", None

        elif choice == "2":
            if not d_drive_path.exists():
                print("❌ D: Drive not found on this computer.")
                continue
            return "local", d_drive_path, "/D_Drive_Backup", None

        elif choice == "3":
            if detected_phone_name:
                print(f"\n📱 Connected Phone Detected: {detected_phone_name}")
                sub = input("Folder to backup on phone [Default: Download/NagarikApp, or type folder name / press Enter for all]: ").strip()
                if not sub:
                    sub = "Download/NagarikApp"
                return "mtp", Path("."), f"/{sub.split('/')[-1] if sub != 'all' else 'Phone_Backup'}", {
                    "phone": detected_phone_name,
                    "storage": "Internal",
                    "subfolder": "" if sub == "all" else sub
                }
            else:
                p_str = input("Enter Phone Mount / Folder path: ").strip().strip('"').strip("'")
                return "local", Path(p_str), "/Mobile_Storage_Backup", None

        elif choice == "4":
            if detected_phone_name:
                print(f"\n📱 Reading SD Card on Phone: {detected_phone_name}")
                sub = input("Folder on SD card to backup (Press Enter for all): ").strip()
                return "mtp", Path("."), "/SD_Card_Backup", {
                    "phone": detected_phone_name,
                    "storage": "SD card",
                    "subfolder": sub
                }
            else:
                sd_path = input("Enter SD Card Drive Letter (e.g. E:\\ or F:\\): ").strip().strip('"').strip("'")
                return "local", Path(sd_path), "/SD_Card_Backup", None

        elif choice == "5":
            manual_path = input("\nEnter custom folder path: ").strip().strip('"').strip("'")
            p = Path(manual_path)
            if not p.exists():
                print(f"❌ Path does not exist: {manual_path}")
                continue
            return "local", p, f"/{p.name}_Backup", None
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
        mode = "local"
        source_path = Path(args.source)
        dest_folder = args.dest or f"/{source_path.name}_Backup"
        mtp_info = None
    else:
        mode, source_path, suggested_dest, mtp_info = show_clean_menu()
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

    if mode == "mtp" and mtp_info:
        client.sync_mtp_phone_folder(
            phone_name=mtp_info["phone"],
            storage_name=mtp_info["storage"],
            subfolder_rel=mtp_info["subfolder"]
        )
    else:
        client.sync_local_directory(source_path)


if __name__ == "__main__":
    main()
