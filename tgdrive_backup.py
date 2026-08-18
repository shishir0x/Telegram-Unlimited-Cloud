"""
TGDrive Universal Backup Client (Exact Local & Device Path Mirroring)
====================================================================
Accurately mirrors the exact folder structure of any storage device to TGDrive:
- Local Disks: C:\\Users\\nitro\\Documents\\PowerShell -> /C_Drive/Users/nitro/Documents/PowerShell/
- D: Drive: D:\\Photos\\2024 -> /D_Drive/Photos/2024/
- Mobile Storage: OnePlus Nord CE4 -> /OnePlus_Nord_CE4/Internal_Storage/Download/NagarikApp/
- SD Card: /SD_Card/...
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


def convert_local_path_to_tg_structure(local_path: Path) -> str:
    """
    Converts C:\\Users\\nitro\\Documents\\PowerShell
    into C_Drive/Users/nitro/Documents/PowerShell
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
        self._folder_id_cache: Dict[str, str] = {"": "/"}  # Maps "Folder/Subfolder" -> "/ID1/ID2/"

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
        """Get directory contents by its TGDrive ID path (e.g. '/' or '/ID1/ID2/')."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/getDirectory",
                json={"password": self.password, "path": remote_id_path},
                timeout=15,
            )
            data = res.json()
            if data.get("status") == "ok":
                return data.get("data", {}).get("contents", {})
        except Exception as e:
            print(f"[!] Error reading directory {remote_id_path}: {e}")
        return {}

    def resolve_or_create_folder_id_path(self, named_path: str) -> str:
        """
        Converts human folder path like "C_Drive/Users/nitro/Documents/PowerShell"
        into TGDrive ID path like "/A1B2C3/D4E5F6/G7H8I9/".
        Automatically creates missing folders along the way.
        """
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
                    # Re-fetch directory to get the new folder's assigned ID
                    contents = self.get_directory_contents(current_id_path)
                    for item_id, item in contents.items():
                        if item.get("type") == "folder" and item.get("name") == part:
                            found_id = item.get("id") or item_id
                            break
                except Exception as e:
                    print(f"    [!] Error creating folder '{part}': {e}")

            if not found_id:
                found_id = part  # fallback

            current_id_path = (current_id_path + found_id + "/").replace("//", "/")
            self._folder_id_cache[current_named_prefix] = current_id_path

        return current_id_path

    def get_existing_files_in_folder(self, folder_id_path: str) -> Set[str]:
        """Returns the set of filenames already in this folder."""
        contents = self.get_directory_contents(folder_id_path)
        existing = set()
        for _, item in contents.items():
            if item.get("type") == "file" and not item.get("trash"):
                existing.add(item.get("name"))
        return existing

    def upload_file(self, local_file_path: Path, human_remote_folder: str) -> bool:
        """Upload a single file to TGDrive with progress tracking."""
        file_name = local_file_path.name
        file_size = local_file_path.stat().st_size

        if file_size == 0:
            print(f"  ⏭️ Skipping 0-byte empty file: {file_name}")
            return True

        upload_id = generate_random_id()

        # Resolve ID path
        remote_id_path = self.resolve_or_create_folder_id_path(human_remote_folder)

        print(f"  ⬆️ Uploading: {file_name} ({format_size(file_size)})")
        print(f"     ➔ Path: /{human_remote_folder}/")

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

    def sync_local_directory(self, source_dir: Path, target_tg_root: Optional[str] = None):
        """Recursively scan and backup local directory tree with exact mirrored paths."""
        source_dir = source_dir.resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            print(f"[!] Target path does not exist: {source_dir}")
            return

        if not target_tg_root:
            target_tg_root = convert_local_path_to_tg_structure(source_dir)

        print("\n" + "=" * 65)
        print(f"🔍 Source Local Path:  {source_dir}")
        print(f"🌐 Mirrored TGDrive Path: /{target_tg_root}/")
        print("=" * 65)

        file_list = []
        total_bytes = 0

        for root, dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir)
            dirs[:] = [d for d in dirs if not should_skip_directory(d, os.path.join(rel_dir, d))]

            for file in files:
                if should_skip_file(file):
                    continue
                p = Path(root) / file
                try:
                    sz = p.stat().st_size
                    file_list.append(p)
                    total_bytes += sz
                except (PermissionError, FileNotFoundError):
                    continue

        if not file_list:
            print("⚠️ No valid files found to backup.")
            return

        print(f"\n📦 Found {len(file_list)} files ({format_size(total_bytes)} total)")
        confirm = input("▶️ Start backup now? (Y/n): ").strip().lower()
        if confirm == "n":
            print("Backup cancelled.")
            return

        self._upload_local_files_mirrored(source_dir, file_list, target_tg_root)

    def _upload_local_files_mirrored(self, base_path: Path, file_list: List[Path], base_tg_path: str):
        total_files = len(file_list)
        success_count = 0
        skip_count = 0
        fail_count = 0

        for idx, local_file in enumerate(file_list, start=1):
            rel_path = local_file.relative_to(base_path).parent
            if str(rel_path) == ".":
                human_folder = base_tg_path
            else:
                human_folder = f"{base_tg_path}/{str(rel_path).replace(chr(92), '/')}".strip("/")

            print(f"\n[{idx}/{total_files}] Processing: {local_file.name}")

            # Check if file already exists
            remote_id_path = self.resolve_or_create_folder_id_path(human_folder)
            existing_files = self.get_existing_files_in_folder(remote_id_path)
            if local_file.name in existing_files:
                print(f"  ⏭️ Already exists in /{human_folder}/ (Skipping)")
                skip_count += 1
                continue

            if self.upload_file(local_file, human_folder):
                success_count += 1
            else:
                fail_count += 1

        print("\n" + "=" * 65)
        print("🎉 Backup Summary:")
        print(f"   ✅ Uploaded:                {success_count}")
        print(f"   ⏭️ Skipped (Already saved): {skip_count}")
        print(f"   ❌ Failed:                  {fail_count}")
        print(f"   📦 Total Files:             {total_files}")
        print(f"   🌐 View Drive:              {self.base_url}/?path=/")
        print("=" * 65)

    def sync_mtp_phone_folder(self, phone_name: str, storage_name: str, subfolder_rel: str = "", auto_confirm: bool = False):
        """Sync files from an MTP connected phone into TGDrive preserving full phone path."""
        import subprocess
        staging_dir = Path(tempfile.gettempdir()) / "tgdrive_phone_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        clean_phone_name = phone_name.replace(" ", "_")
        clean_storage_name = storage_name.replace(" ", "_")
        
        # Base TG path: e.g. OnePlus_Nord_CE4/Internal_Storage
        base_tg_prefix = f"{clean_phone_name}/{clean_storage_name}"

        print("\n" + "=" * 65)
        print(f"📱 Phone Source:     {phone_name} ➔ {storage_name} ➔ {subfolder_rel or 'Root'}")
        print(f"🌐 Mirrored TG Path: /{base_tg_prefix}/{subfolder_rel}".rstrip("/") + "/")
        print("=" * 65)

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
                if not should_skip_file(f):
                    staged_files.append(p)
                    total_bytes += p.stat().st_size

        if not staged_files:
            print("⚠️ No files found in the specified phone folder.")
            return

        print(f"📦 Successfully read {len(staged_files)} files ({format_size(total_bytes)}) from phone.")
        if not auto_confirm:
            confirm = input("▶️ Start uploading to TGDrive? (Y/n): ").strip().lower()
            if confirm == "n":
                print("Upload cancelled.")
                shutil.rmtree(staging_dir, ignore_errors=True)
                return

        try:
            self._upload_local_files_mirrored(staging_dir, staged_files, base_tg_prefix)
        finally:
            print("🧹 Cleaning up temporary phone cache...")
            shutil.rmtree(staging_dir, ignore_errors=True)


def show_clean_menu() -> Tuple[str, Path, Optional[str], Optional[Dict]]:
    """Present the clean 4-option backup menu."""
    user_home = Path.home()
    c_users_path = Path("C:/Users") if os.path.exists("C:/Users") else user_home
    d_drive_path = Path("D:/")

    phones = MTPPhoneManager.get_connected_phones()
    detected_phone_name = phones[0]["name"] if phones else None

    print("\n" + "=" * 60)
    print("       🚀 TGDrive Universal Backup Manager")
    print("=" * 60)
    print("Choose what you want to backup:\n")
    print(f"  [1] 💻 C: Drive (C:\\Users - Exact User Folder Tree)")
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
                sub = input("Folder to backup on phone [Default: Download/NagarikApp, or type folder / press Enter for all]: ").strip()
                if not sub:
                    sub = "Download/NagarikApp"
                return "mtp", Path("."), None, {
                    "phone": detected_phone_name,
                    "storage": "Internal shared storage",
                    "subfolder": "" if sub == "all" else sub
                }
            else:
                p_str = input("Enter Phone Mount / Folder path: ").strip().strip('"').strip("'")
                return "local", Path(p_str), None, None

        elif choice == "4":
            if detected_phone_name:
                print(f"\n📱 Reading SD Card on Phone: {detected_phone_name}")
                sub = input("Folder on SD card to backup (Press Enter for all): ").strip()
                return "mtp", Path("."), None, {
                    "phone": detected_phone_name,
                    "storage": "SD card",
                    "subfolder": sub
                }
            else:
                sd_path = input("Enter SD Card Drive Letter (e.g. E:\\ or F:\\): ").strip().strip('"').strip("'")
                return "local", Path(sd_path), None, None

        elif choice == "5":
            manual_path = input("\nEnter custom folder path (e.g. C:\\Users\\nitro\\Documents\\PowerShell): ").strip().strip('"').strip("'")
            p = Path(manual_path)
            if not p.exists():
                print(f"❌ Path does not exist: {manual_path}")
                continue
            return "local", p, None, None
        else:
            print("Please enter a valid choice (1-5).")


def main():
    parser = argparse.ArgumentParser(
        description="Universal TGDrive Backup Manager (Exact Device Path Mirroring)."
    )
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        help="Local path to directory (e.g. C:\\Users\\nitro\\Documents\\PowerShell)",
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
        client.sync_local_directory(source_path, target_tg_root=custom_tg_dest)


if __name__ == "__main__":
    main()
