"""
Unit and Integration Test Suite for Production-Grade Folder Upload
Telegram-Unlimited-Cloud
"""
import os
import sys
import time
import shutil
import tempfile
import asyncio
import unittest
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.directoryHandler import (
    NewDriveData,
    Folder,
    File,
    sanitize_name,
)
from utils.transfer_manager import (
    TransferManager,
    TransferItem,
    TransferType,
    TransferState,
    TransferStore,
)


import utils.directoryHandler as dh

class TestFolderUploadSystem(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tg_folder_test_")
        self._orig_cache_path = dh.drive_cache_path
        self._orig_backup_path = dh.drive_backup_path
        self._orig_checksum_path = dh.drive_checksum_path
        self._orig_json_mirror_path = dh.drive_json_mirror_path
        
        # Isolate paths to temp dir
        dh.drive_cache_path = Path(self.test_dir) / "drive.data"
        dh.drive_backup_path = Path(self.test_dir) / "drive.data.bak"
        dh.drive_checksum_path = Path(self.test_dir) / "drive.data.sha256"
        dh.drive_json_mirror_path = Path(self.test_dir) / "tgdrive_backup.json"
        
        self.data = NewDriveData({"/": Folder("/", "/")}, [])

    def tearDown(self):
        dh.drive_cache_path = self._orig_cache_path
        dh.drive_backup_path = self._orig_backup_path
        dh.drive_checksum_path = self._orig_checksum_path
        dh.drive_json_mirror_path = self._orig_json_mirror_path
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sanitize_name_path_traversal(self):
        """Verify filename and folder name sanitization against traversal attacks."""
        sanitized = sanitize_name("../../../evil.exe")
        self.assertNotIn("/", sanitized)
        self.assertNotIn("\\", sanitized)
        self.assertNotIn("..", sanitized)
        self.assertTrue(sanitized.endswith("evil.exe"))
        self.assertEqual(sanitize_name("test\x00null.txt"), "testnull.txt")
        self.assertEqual(sanitize_name(""), "Unnamed")

    def test_resolve_or_create_folder_hierarchy(self):
        """Verify nested folder hierarchy creation (Photos/2026/January)."""
        # Create hierarchy Photos/2026/January starting from root
        jan_path = self.data.resolve_or_create_folder_hierarchy("/", "Photos/2026/January")
        self.assertTrue(jan_path.startswith("/"))

        # Retrieve the directory structure and verify hierarchy
        root = self.data.contents["/"]
        photos_folder = None
        for item in root.contents.values():
            if getattr(item, "type", "") == "folder" and item.name == "Photos":
                photos_folder = item
                break
        self.assertIsNotNone(photos_folder, "Photos folder should exist at root")

        year_folder = None
        for item in photos_folder.contents.values():
            if getattr(item, "type", "") == "folder" and item.name == "2026":
                year_folder = item
                break
        self.assertIsNotNone(year_folder, "2026 folder should exist inside Photos")

        jan_folder = None
        for item in year_folder.contents.values():
            if getattr(item, "type", "") == "folder" and item.name == "January":
                jan_folder = item
                break
        self.assertIsNotNone(jan_folder, "January folder should exist inside 2026")

        # Resolving existing hierarchy should not create duplicate folders
        jan_path_2 = self.data.resolve_or_create_folder_hierarchy("/", "Photos/2026/January")
        self.assertEqual(jan_path, jan_path_2)
        self.assertEqual(len(year_folder.contents), 1, "Should not duplicate January folder")

    def test_nested_file_placement(self):
        """Verify files placed in nested subfolders are indexed properly."""
        jan_path = self.data.resolve_or_create_folder_hierarchy("/", "Photos/2026/January")
        
        file1_id = self.data.new_file(jan_path, "img1.jpg", file_id=1001, size=50000)
        file2_id = self.data.new_file(jan_path, "img2.jpg", file_id=1002, size=75000)

        # Lookup January directory
        jan_dir = self.data.get_directory(jan_path, is_admin=True)
        if isinstance(jan_dir, tuple):
            jan_dir = jan_dir[0]

        self.assertIn(file1_id, jan_dir.contents)
        self.assertIn(file2_id, jan_dir.contents)
        self.assertEqual(jan_dir.contents[file1_id].name, "img1.jpg")
        self.assertEqual(jan_dir.contents[file2_id].name, "img2.jpg")

    def test_empty_directory_preservation(self):
        """Verify empty folder preservation via ensure_folder_tree."""
        folder_list = [
            "Photos/2026/January",
            "Photos/2026/February",
            "Photos/2026/EmptyDrafts",
        ]
        created = self.data.ensure_folder_tree("/", folder_list)
        self.assertEqual(len(created), 3)

        # Check that EmptyDrafts exists under Photos/2026
        photos = next(i for i in self.data.contents["/"].contents.values() if i.name == "Photos")
        year = next(i for i in photos.contents.values() if i.name == "2026")
        folder_names = [i.name for i in year.contents.values() if getattr(i, "type", "") == "folder"]
        self.assertIn("January", folder_names)
        self.assertIn("February", folder_names)
        self.assertIn("EmptyDrafts", folder_names)

    def test_duplicate_file_handling_in_folder_upload(self):
        """Verify conflict resolution (keep_both vs replace) within nested folders."""
        jan_path = self.data.resolve_or_create_folder_hierarchy("/", "Documents/Reports")
        
        # Upload first file
        f1_id = self.data.new_file(jan_path, "Summary.pdf", file_id=2001, size=10000)
        
        # Upload second file with same name and conflict='keep_both'
        f2_id = self.data.new_file(jan_path, "Summary.pdf", file_id=2002, size=15000, conflict="keep_both")
        self.assertNotEqual(f1_id, f2_id)

        rep_dir = self.data.get_directory(jan_path, is_admin=True)
        if isinstance(rep_dir, tuple):
            rep_dir = rep_dir[0]

        f1 = rep_dir.contents[f1_id]
        f2 = rep_dir.contents[f2_id]
        self.assertEqual(f1.name, "Summary.pdf")
        self.assertEqual(f2.name, "Summary (1).pdf")

        # Upload third file with conflict='replace' targeting Summary.pdf
        f3_id = self.data.new_file(jan_path, "Summary.pdf", file_id=2003, size=20000, conflict="replace")
        self.assertEqual(f3_id, f1_id)
        self.assertEqual(rep_dir.contents[f1_id].size, 20000)
        self.assertEqual(rep_dir.contents[f1_id].file_id, 2003)

    def test_transfer_item_relative_path_and_batch_id(self):
        """Verify TransferItem serializes and deserializes relative_path and batch_id."""
        item = TransferItem(
            id="tx_test_123",
            type=TransferType.UPLOAD,
            filename="img1.jpg",
            size=1024,
            target_path="/AB12CD",
            relative_path="Photos/2026/January/img1.jpg",
            batch_id="batch_photos_01",
        )
        d = item.to_dict()
        self.assertEqual(d["relative_path"], "Photos/2026/January/img1.jpg")
        self.assertEqual(d["batch_id"], "batch_photos_01")

        restored = TransferItem.from_dict(d)
        self.assertEqual(restored.relative_path, "Photos/2026/January/img1.jpg")
        self.assertEqual(restored.batch_id, "batch_photos_01")
        self.assertEqual(restored.target_path, "/AB12CD")

    def test_transfer_manager_queue_upload_with_folder_metadata(self):
        """Verify TransferManager queues uploads with relative_path and batch_id."""
        async def run_test():
            store_file = Path(self.test_dir) / "transfers.json"
            tm = TransferManager(store_path=store_file, is_singleton=False, max_concurrent_uploads=2)

            test_file = Path(self.test_dir) / "sample.txt"
            test_file.write_text("hello world")

            item = await tm.queue_upload(
                file_path=str(test_file),
                id="tx_batch_001",
                target_path="/FOLDER_ID",
                filename="sample.txt",
                file_size=11,
                relative_path="Documents/2026/sample.txt",
                batch_id="batch_999",
            )

            self.assertEqual(item.relative_path, "Documents/2026/sample.txt")
            self.assertEqual(item.batch_id, "batch_999")
            self.assertEqual(item.target_path, "/FOLDER_ID")
            self.assertEqual(item.state, TransferState.QUEUED)

            # Check store serialization
            await tm.store.save(force=True)
            new_store = TransferStore(filepath=store_file)
            self.assertIn("tx_batch_001", new_store.transfers)
            saved_item = new_store.transfers["tx_batch_001"]
            self.assertEqual(saved_item.relative_path, "Documents/2026/sample.txt")
            self.assertEqual(saved_item.batch_id, "batch_999")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
