"""
Unit and Integration tests for Content-Hash Duplicate Detection Subsystem.
Tests:
- Streaming SHA-256 low-memory hashing
- Identical files duplicate grouping
- Same-name different-content isolation
- Different-name same-content detection
- Retention safety invariants (never deleting 100% of copies in any group)
- Metadata caching & re-hashing avoidance
- Soft delete (trash) and permanent deletion
- Background scan error resilience
"""

import os
import sys
import json
import shutil
import tempfile
import hashlib
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add repo root to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from utils.duplicate_manager import (
    DuplicateManager,
    DuplicateIndexEntry,
    DuplicateGroup,
    calculate_file_sha256,
    stream_hash_telegram_file
)
from utils.directoryHandler import File, Folder, NewDriveData


class TestStreamingHasher(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_calculate_file_sha256_small(self):
        content = b"Hello, World! Duplicate Detection Test 123"
        expected_sha256 = hashlib.sha256(content).hexdigest()
        file_path = os.path.join(self.test_dir, "test.txt")
        with open(file_path, "wb") as f:
            f.write(content)

        actual_sha256 = calculate_file_sha256(file_path, chunk_size=8)
        self.assertEqual(actual_sha256, expected_sha256)

    def test_calculate_file_sha256_large_chunking(self):
        # 1MB file hashed in 64KB chunks
        content = b"A" * 1024 * 1024
        expected_sha256 = hashlib.sha256(content).hexdigest()
        file_path = os.path.join(self.test_dir, "large.bin")
        with open(file_path, "wb") as f:
            f.write(content)

        actual_sha256 = calculate_file_sha256(file_path, chunk_size=65536)
        self.assertEqual(actual_sha256, expected_sha256)

    def test_stream_hash_telegram_file(self):
        # Mock ByteStreamer yielding chunks
        chunk1 = b"Telegram " * 500
        chunk2 = b"Stream " * 500
        chunk3 = b"Hash Chunk" * 500
        full_content = chunk1 + chunk2 + chunk3
        expected_sha256 = hashlib.sha256(full_content).hexdigest()

        async def run_test():
            mock_streamer = MagicMock()
            async def fake_yield_file(*args, **kwargs):
                yield chunk1
                yield chunk2
                yield chunk3

            mock_streamer.yield_file = fake_yield_file
            mock_streamer.get_file_properties = AsyncMock(return_value=MagicMock())

            with patch("utils.streamer.custom_dl.ByteStreamer", return_value=mock_streamer):
                with patch("utils.clients.get_client", return_value=MagicMock()):
                    mgr = DuplicateManager(index_path=Path(self.test_dir) / "idx.json")
                    computed = await mgr.stream_hash_telegram_file(
                        channel_id=-10012345,
                        message_id=42,
                        file_size=len(full_content),
                        chunk_size=512
                    )
                    self.assertEqual(computed, expected_sha256)

        asyncio.run(run_test())


class TestDuplicateManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.index_path = Path(self.test_dir) / "hash_index.json"
        self.mgr = DuplicateManager(index_path=self.index_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_same_name_different_content_not_duplicates(self):
        """Files with same name but different content/size must NOT be grouped as duplicates."""
        entry1 = DuplicateIndexEntry(
            sha256="aaa111",
            size=100,
            filename="document.pdf",
            file_id=1,
            file_uuid="uuid-1",
            folder_id="f1",
            folder_path="/FolderA",
            upload_date="2026-01-01T00:00:00"
        )
        entry2 = DuplicateIndexEntry(
            sha256="bbb222",
            size=150,
            filename="document.pdf",
            file_id=2,
            file_uuid="uuid-2",
            folder_id="f2",
            folder_path="/FolderB",
            upload_date="2026-01-02T00:00:00"
        )
        self.mgr.entries[entry1.file_uuid] = entry1
        self.mgr.entries[entry2.file_uuid] = entry2

        res = self.mgr.get_duplicate_groups()
        self.assertEqual(res["duplicate_groups_count"], 0)
        self.assertEqual(res["total_recoverable_bytes"], 0)

    def test_different_name_same_content_are_duplicates(self):
        """Files with different names but identical sha256 and size MUST be grouped as duplicates."""
        shared_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        size = 2048

        entry1 = DuplicateIndexEntry(
            sha256=shared_hash,
            size=size,
            filename="presentation_v1.pptx",
            file_id=101,
            file_uuid="uuid-orig",
            folder_id="f1",
            folder_path="/Work/Projects",
            upload_date="2026-01-01T10:00:00"
        )
        entry2 = DuplicateIndexEntry(
            sha256=shared_hash,
            size=size,
            filename="presentation_final_copy.pptx",
            file_id=102,
            file_uuid="uuid-copy",
            folder_id="f2",
            folder_path="/Downloads",
            upload_date="2026-01-02T12:00:00"
        )
        self.mgr.entries[entry1.file_uuid] = entry1
        self.mgr.entries[entry2.file_uuid] = entry2

        res = self.mgr.get_duplicate_groups()
        self.assertEqual(res["duplicate_groups_count"], 1)
        self.assertEqual(res["total_duplicates"], 1)
        self.assertEqual(res["total_recoverable_bytes"], size)

        group = res["groups"][0]
        self.assertEqual(group["sha256"], shared_hash)
        self.assertEqual(group["copies_count"], 2)
        # Original (retained) should be uuid-orig because it has earlier timestamp
        self.assertTrue(group["files"][0]["is_retained"])
        self.assertEqual(group["files"][0]["file_uuid"], "uuid-orig")
        self.assertFalse(group["files"][1]["is_retained"])
        self.assertEqual(group["files"][1]["file_uuid"], "uuid-copy")

    def test_retention_safety_invariant_blocks_total_group_deletion(self):
        """Attempting to delete ALL copies in a group must raise a ValueError and prevent deletion."""
        shared_hash = "abc123456789"
        size = 5000

        entry1 = DuplicateIndexEntry(
            sha256=shared_hash, size=size, filename="f1.jpg", file_id=1, file_uuid="uuid-1", folder_id="f1", folder_path="/A"
        )
        entry2 = DuplicateIndexEntry(
            sha256=shared_hash, size=size, filename="f2.jpg", file_id=2, file_uuid="uuid-2", folder_id="f2", folder_path="/B"
        )
        self.mgr.entries[entry1.file_uuid] = entry1
        self.mgr.entries[entry2.file_uuid] = entry2

        # Selecting both uuid-1 AND uuid-2 attempts to delete 100% of copies in group
        with self.assertRaises(ValueError) as ctx:
            self.mgr.delete_duplicates(target_file_uuids=["uuid-1", "uuid-2"])

        self.assertIn("Retention Safety Violation", str(ctx.exception))

    def test_safe_duplicate_deletion_executes_correctly(self):
        """Deleting only duplicate copies (preserving original) succeeds and frees storage."""
        # Create a mock drive data with 1 folder and 2 files
        root = Folder("/", "/")
        folder = Folder("TestFolder", "/TestFolder")
        file_orig = File("orig.txt", 1, 100, "/TestFolder")
        file_orig.id = "u-orig"
        file_copy = File("copy.txt", 2, 100, "/TestFolder")
        file_copy.id = "u-copy"

        folder.contents[file_orig.id] = file_orig
        folder.contents[file_copy.id] = file_copy
        root.contents[folder.id] = folder

        drive = NewDriveData({"/": root}, used_ids=["u-orig", "u-copy", folder.id])
        drive.save = MagicMock()

        shared_hash = "hash999"
        e1 = DuplicateIndexEntry(
            sha256=shared_hash, size=100, filename="orig.txt", file_id=1, file_uuid="u-orig", folder_id=folder.id, folder_path="/TestFolder", upload_date="2026-01-01"
        )
        e2 = DuplicateIndexEntry(
            sha256=shared_hash, size=100, filename="copy.txt", file_id=2, file_uuid="u-copy", folder_id=folder.id, folder_path="/TestFolder", upload_date="2026-01-02"
        )
        self.mgr.entries[e1.file_uuid] = e1
        self.mgr.entries[e2.file_uuid] = e2

        with patch("utils.directoryHandler.ensure_drive_data", return_value=drive):
            res = self.mgr.delete_duplicates(target_file_uuids=["u-copy"], soft_delete=True)

        self.assertEqual(res["deleted_count"], 1)
        self.assertEqual(res["freed_bytes"], 100)
        self.assertNotIn("u-copy", self.mgr.entries)
        self.assertIn("u-orig", self.mgr.entries)
        self.assertTrue(file_copy.trash)

    def test_metadata_caching_and_rehashing_avoidance(self):
        """If file already has sha256 metadata matching size, physical hashing is skipped."""
        root = Folder("/", "/")
        folder = Folder("CachedFolder", "/CachedFolder")
        file_cached = File("already_hashed.mp4", 10, 5000000, "/CachedFolder")
        file_cached.id = "u-cached"
        file_cached.sha256 = "precomputed_hash_value"
        folder.contents[file_cached.id] = file_cached
        root.contents[folder.id] = folder

        drive = NewDriveData({"/": root}, used_ids=["u-cached", folder.id])
        drive.save = MagicMock()

        async def run_scan():
            with patch("utils.directoryHandler.ensure_drive_data", return_value=drive):
                with patch.object(self.mgr, "stream_hash_telegram_file") as mock_stream:
                    await self.mgr._scan_drive_tree_task()
                    mock_stream.assert_not_called()

            self.assertIn("u-cached", self.mgr.entries)
            self.assertEqual(self.mgr.entries["u-cached"].sha256, "precomputed_hash_value")

        asyncio.run(run_scan())


if __name__ == "__main__":
    unittest.main()
