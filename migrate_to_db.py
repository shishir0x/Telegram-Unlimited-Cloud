"""
TG Drive Database Migration Utility
====================================
Migrates existing drive metadata from local drive.data / tgdrive_backup.json
into the authoritative shared PostgreSQL / cloud database.

Ensures:
- 100% preservation of existing folder IDs, file IDs, and Telegram message IDs.
- Correct foreign-key parent-child insertion order (parents before children).
- Idempotent upserting (safe to re-run without duplicating records).
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Tuple

# Ensure current working directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from database.connection import init_db, get_db_session, test_database_connection
from database.models import FolderModel, FileModel, utc_now
from utils.logger import Logger

logger = Logger("migrate_to_db")


def parse_iso_or_none(dt_str) -> datetime:
    if not dt_str:
        return utc_now()
    if isinstance(dt_str, datetime):
        return dt_str
    try:
        return datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return utc_now()


def load_legacy_snapshot() -> Tuple[Optional[dict], str]:
    """Loads the best available legacy drive metadata snapshot from local disk."""
    json_path = Path("./cache/tgdrive_backup.json")
    pickle_path = Path("./cache/drive.data")

    # 1. Prefer JSON mirror if valid
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "contents" in data:
                return data, str(json_path)
        except Exception as e:
            logger.warning(f"Could not load tgdrive_backup.json: {e}")

    # 2. Try dill/pickle loader
    if pickle_path.exists():
        try:
            from utils.directoryHandler import load_drive_data_from_file
            obj = load_drive_data_from_file(pickle_path)
            if obj and hasattr(obj, "to_dict"):
                return obj.to_dict(), str(pickle_path)
        except Exception as e:
            logger.warning(f"Could not load drive.data: {e}")

    return None, "none"


def migrate_data(dry_run: bool = False) -> Tuple[int, int]:
    """
    Reads the legacy data structure and inserts all folders and files into the database.
    Returns (folders_imported, files_imported).
    """
    snapshot, source_name = load_legacy_snapshot()
    if not snapshot:
        logger.warning("No legacy drive data found to migrate.")
        return 0, 0

    logger.info(f"Loaded legacy drive snapshot from {source_name}")

    contents = snapshot.get("contents", {})
    root_data = contents.get("/", {})
    if not root_data:
        logger.warning("Root directory ('/') not found in snapshot.")
        return 0, 0

    # Ensure tables exist
    if not dry_run:
        init_db()

    folder_records: list[dict] = []
    file_records: list[dict] = []

    # BFS queue: (node_dict, parent_folder_id)
    # Root folder itself has id='root'
    queue = [(root_data, None)]

    while queue:
        node, parent_id = queue.pop(0)
        node_id = str(node.get("id") or "root")
        if node.get("name") == "/" or node_id == "/":
            node_id = "root"

        node_name = str(node.get("name") or ("/" if node_id == "root" else "Unnamed"))
        node_type = str(node.get("type") or "folder")

        if node_type == "folder":
            folder_records.append({
                "id": node_id,
                "user_id": str(node.get("owner") or "admin"),
                "name": node_name,
                "parent_folder_id": parent_id if node_id != "root" else None,
                "path": str(node.get("path") or "/"),
                "trash": bool(node.get("trash", False)),
                "trashed_at": parse_iso_or_none(node.get("trashed_at")) if node.get("trashed_at") else None,
                "tags": list(node.get("tags") or []),
                "auth_hashes": list(node.get("auth_hashes") or []),
                "activity_history": list(node.get("activity_history") or []),
                "created_at": parse_iso_or_none(node.get("created_at") or node.get("upload_date")),
                "updated_at": parse_iso_or_none(node.get("modified_at") or node.get("upload_date")),
            })

            # Process child contents
            child_contents = node.get("contents", {})
            if isinstance(child_contents, dict):
                for child_k, child_v in child_contents.items():
                    if isinstance(child_v, dict):
                        queue.append((child_v, node_id))
                    elif hasattr(child_v, "to_dict"):
                        queue.append((child_v.to_dict(), node_id))

        elif node_type == "file":
            # Parent folder is the parent_id
            fid = node_id
            fname = node_name
            ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ""
            ext = ext[:128]
            mime = (node.get("mime_type") or "application/octet-stream")[:128]

            file_records.append({
                "id": fid,
                "user_id": str(node.get("owner") or "admin"),
                "name": fname,
                "original_name": fname,
                "mime_type": mime,
                "extension": ext,
                "size": int(node.get("size") or 0),
                "telegram_message_id": int(node.get("file_id") or 0),
                "telegram_file_id": None,
                "telegram_chat_id": config.STORAGE_CHANNEL if config.STORAGE_CHANNEL else None,
                "folder_id": parent_id or "root",
                "checksum": node.get("sha256"),
                "trash": bool(node.get("trash", False)),
                "trashed_at": parse_iso_or_none(node.get("trashed_at")) if node.get("trashed_at") else None,
                "tags": list(node.get("tags") or []),
                "metadata_extra": dict(node.get("metadata_extra") or {}),
                "activity_history": list(node.get("activity_history") or []),
                "created_at": parse_iso_or_none(node.get("created_at") or node.get("upload_date")),
                "updated_at": parse_iso_or_none(node.get("modified_at") or node.get("upload_date")),
            })

    logger.info(f"Discovered {len(folder_records)} folders and {len(file_records)} files to migrate.")

    if dry_run:
        print(f"[DRY-RUN] Would insert {len(folder_records)} folders and {len(file_records)} files.")
        return len(folder_records), len(file_records)

    with get_db_session() as session:
        # Fast bulk lookup of existing IDs in 1 query each
        existing_folder_ids = set(r[0] for r in session.query(FolderModel.id).all())
        existing_file_ids = set(r[0] for r in session.query(FileModel.id).all())

        # 1. Insert folders
        folders_upserted = 0
        new_folders = []
        for f_data in folder_records:
            if f_data["id"] not in existing_folder_ids:
                new_folders.append(FolderModel(**f_data))
                existing_folder_ids.add(f_data["id"])
                folders_upserted += 1

        if new_folders:
            for i in range(0, len(new_folders), 200):
                session.add_all(new_folders[i:i+200])
                session.flush()

        # 2. Insert files
        files_upserted = 0
        new_files = []
        for f_data in file_records:
            if f_data["id"] not in existing_file_ids:
                new_files.append(FileModel(**f_data))
                existing_file_ids.add(f_data["id"])
                files_upserted += 1

        if new_files:
            for i in range(0, len(new_files), 200):
                session.add_all(new_files[i:i+200])
                session.flush()

    logger.info(f"Migration completed successfully! Processed {len(folder_records)} folders ({folders_upserted} new), {len(file_records)} files ({files_upserted} new).")
    return len(folder_records), len(file_records)


def verify_database_counts():
    """Prints current item counts in the database."""
    connected, msg = test_database_connection()
    print(f"Connection status: {msg}")
    if not connected:
        return

    init_db()
    from database.repository import DatabaseRepository
    f_cnt, fl_cnt = DatabaseRepository.count_total_items()
    print(f"Total Folders in DB: {f_cnt}")
    print(f"Total Files in DB:   {fl_cnt}")
    print(f"Total Combined:      {f_cnt + fl_cnt}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate TG Drive metadata to database")
    parser.add_argument("--dry-run", action="store_true", help="Simulate migration without writing to DB")
    parser.add_argument("--verify", action="store_true", help="Verify current record counts in DB")
    args = parser.parse_args()

    if args.verify:
        verify_database_counts()
    elif args.dry_run:
        migrate_data(dry_run=True)
    else:
        migrate_data(dry_run=False)
        verify_database_counts()
