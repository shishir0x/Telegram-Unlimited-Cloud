import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.transfer_manager import (
    TransferItem,
    TransferManager,
    TransferState,
    TransferStore,
    TransferType,
)


def make_mock_tg_message(msg_id=12345, file_id="fid_123", file_unique_id="fuid_123", file_size=1000):
    """Helper to return a concrete mock Pyrogram message with picklable scalar fields."""
    msg = MagicMock()
    msg.id = int(msg_id)
    doc = MagicMock()
    doc.file_id = str(file_id)
    doc.file_unique_id = str(file_unique_id)
    doc.file_size = int(file_size)
    msg.document = doc
    msg.photo = None
    msg.video = None
    msg.audio = None
    msg.sticker = None
    return msg


class TestTransferManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tm_test_")
        self.store_path = os.path.join(self.test_dir, "transfers.json")
        self.cache_dir = os.path.join(self.test_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.mgr = TransferManager(
            store_path=self.store_path,
            max_concurrent_uploads=2,
            max_concurrent_downloads=2,
            is_singleton=False,
        )
        await self.mgr.start()

    async def asyncTearDown(self):
        await self.mgr.shutdown()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_1_successful_upload(self):
        """Test 1: Successful upload pipeline and state transitions."""
        temp_file = os.path.join(self.cache_dir, "test_upload.txt")
        with open(temp_file, "wb") as f:
            f.write(b"Hello World" * 1024)

        item_id = "test_up_1"
        mock_client = MagicMock()
        mock_msg = make_mock_tg_message(12345, "tg_file_id_123", "tg_unique_id_123", 11264)

        async def mock_send_doc(*args, **kwargs):
            progress = kwargs.get("progress")
            if progress:
                await progress(5500, 11264)
                await progress(11264, 11264)
            return mock_msg

        mock_client.send_document = AsyncMock(side_effect=mock_send_doc)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            item = await self.mgr.queue_upload(
                file_path=temp_file,
                id=item_id,
                target_path="/Documents",
                filename="test_upload.txt",
                file_size=11264,
            )
            self.assertEqual(item.state, TransferState.QUEUED)

            for _ in range(40):
                await asyncio.sleep(0.1)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.COMPLETED.value:
                    break

            final_item = self.mgr.get_transfer(item_id)
            self.assertIsNotNone(final_item)
            self.assertEqual(final_item["state"], TransferState.COMPLETED.value)
            self.assertEqual(final_item["percentage"], 100.0)
            self.assertEqual(final_item["transferred"], 11264)
            self.assertIsNone(final_item["error_reason"])
            self.assertFalse(os.path.exists(temp_file))

    async def test_2_failed_upload_and_error_reason(self):
        """Test 2: Failed upload transition with error reason recorded."""
        temp_file = os.path.join(self.cache_dir, "test_fail.txt")
        with open(temp_file, "wb") as f:
            f.write(b"Fail Test Payload")

        item_id = "test_fail_1"
        mock_client = MagicMock()
        mock_client.send_document = AsyncMock(side_effect=ValueError("Telegram Bot blocked or chat not found"))

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            item = await self.mgr.queue_upload(
                file_path=temp_file,
                id=item_id,
                target_path="/",
                filename="test_fail.txt",
                file_size=17,
            )
            item.max_retries = 0  # Fail immediately on first error

            for _ in range(40):
                await asyncio.sleep(0.1)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.FAILED.value:
                    break

            final_item = self.mgr.get_transfer(item_id)
            self.assertEqual(final_item["state"], TransferState.FAILED.value)
            self.assertIn("Telegram Bot blocked", final_item["error_reason"])

    async def test_3_retry_operation(self):
        """Test 3: Retrying a failed transfer resets state and re-queues."""
        temp_file = os.path.join(self.cache_dir, "test_retry.txt")
        with open(temp_file, "wb") as f:
            f.write(b"Retry Payload")

        item_id = "test_retry_1"
        attempts = 0
        mock_client = MagicMock()
        mock_msg = make_mock_tg_message(54321, "fid_retry", "fuid_retry", 13)

        async def mock_send_doc(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionResetError("Network reset")
            return mock_msg

        mock_client.send_document = AsyncMock(side_effect=mock_send_doc)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            item = await self.mgr.queue_upload(
                file_path=temp_file,
                id=item_id,
                target_path="/",
                filename="test_retry.txt",
                file_size=13,
            )
            item.max_retries = 0  # Fail after 1st attempt so we can test manual retry

            for _ in range(40):
                await asyncio.sleep(0.1)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.FAILED.value:
                    break

            self.assertEqual(self.mgr.get_transfer(item_id)["state"], TransferState.FAILED.value)

            # Re-create temp file for retry if removed
            if not os.path.exists(temp_file):
                with open(temp_file, "wb") as f:
                    f.write(b"Retry Payload")

            # Trigger retry
            retried_item = await self.mgr.retry_transfer(item_id)
            self.assertIsNotNone(retried_item)
            self.assertEqual(retried_item.state, TransferState.QUEUED)

            for _ in range(40):
                await asyncio.sleep(0.1)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.COMPLETED.value:
                    break

            self.assertEqual(self.mgr.get_transfer(item_id)["state"], TransferState.COMPLETED.value)

    async def test_4_cancellation_and_cleanup(self):
        """Test 4: Cancellation token immediately halts transfer and cleans temp files."""
        temp_file = os.path.join(self.cache_dir, "test_cancel.bin")
        with open(temp_file, "wb") as f:
            f.write(b"0" * 100000)

        item_id = "test_cancel_1"
        mock_client = MagicMock()

        async def mock_long_send(*args, **kwargs):
            for _ in range(100):
                await asyncio.sleep(0.05)
            return make_mock_tg_message()

        mock_client.send_document = AsyncMock(side_effect=mock_long_send)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            await self.mgr.queue_upload(
                file_path=temp_file,
                id=item_id,
                target_path="/",
                filename="test_cancel.bin",
                file_size=100000,
            )
            await asyncio.sleep(0.1)

            success = await self.mgr.cancel_transfer(item_id)
            self.assertTrue(success)

            for _ in range(30):
                await asyncio.sleep(0.05)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.CANCELLED.value:
                    break

            self.assertEqual(self.mgr.get_transfer(item_id)["state"], TransferState.CANCELLED.value)
            self.assertFalse(os.path.exists(temp_file))

    async def test_5_restart_recovery(self):
        """Test 5: Transfers survive server restart and recover state."""
        temp_file = os.path.join(self.cache_dir, "test_recover.txt")
        with open(temp_file, "wb") as f:
            f.write(b"Persisted Recovery File")

        item = await self.mgr.queue_upload(
            file_path=temp_file,
            id="recover_id_1",
            target_path="/Backup",
            filename="test_recover.txt",
            file_size=23,
        )
        item.state = TransferState.UPLOADING
        self.mgr.store.mark_dirty()
        await self.mgr.store.save(force=True)

        # Simulate restart with fresh manager instance and trigger recovery via start()
        mock_client = MagicMock()
        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            mgr2 = TransferManager(store_path=self.store_path, max_concurrent_uploads=2, is_singleton=False)
            await mgr2.start()
            self.assertIn("recover_id_1", mgr2.store.transfers)
            recovered_item = mgr2.store.transfers["recover_id_1"]
            self.assertEqual(recovered_item.state, TransferState.QUEUED)
            self.assertEqual(recovered_item.retry_count, 0)
            await mgr2.shutdown()

    async def test_6_bounded_concurrency(self):
        """Test 6: Semaphore strictly limits active concurrent transfers."""
        active_count = 0
        max_active_seen = 0
        mock_client = MagicMock()

        async def mock_concurrent_send(*args, **kwargs):
            nonlocal active_count, max_active_seen
            active_count += 1
            if active_count > max_active_seen:
                max_active_seen = active_count
            await asyncio.sleep(0.15)
            active_count -= 1
            return make_mock_tg_message(999, "fid", "fuid", 4)

        mock_client.send_document = AsyncMock(side_effect=mock_concurrent_send)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            for i in range(5):
                tf = os.path.join(self.cache_dir, f"file_{i}.txt")
                with open(tf, "wb") as f:
                    f.write(b"data")
                await self.mgr.queue_upload(file_path=tf, id=f"job_{i}", target_path="/", filename=f"file_{i}.txt", file_size=4)

            for _ in range(50):
                await asyncio.sleep(0.05)
                stats = self.mgr.get_all_transfers()["stats"]
                if stats["total_active"] == 0:
                    break

            self.assertLessEqual(max_active_seen, 2)
            self.assertGreater(max_active_seen, 0)

    async def test_7_duplicate_job_prevention(self):
        """Test 7: Duplicate job IDs or duplicate in-flight files are safely handled."""
        tf = os.path.join(self.cache_dir, "dup.txt")
        with open(tf, "wb") as f:
            f.write(b"dup")

        mock_client = MagicMock()

        async def mock_upload_slow(*args, **kwargs):
            await asyncio.sleep(0.3)
            return make_mock_tg_message(111, "fid", "fuid", 3)

        mock_client.send_document = AsyncMock(side_effect=mock_upload_slow)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):
            item1 = await self.mgr.queue_upload(file_path=tf, id="dup_id", target_path="/", filename="dup.txt", file_size=3)
            item2 = await self.mgr.queue_upload(file_path=tf, id="dup_id", target_path="/", filename="dup.txt", file_size=3)

            self.assertEqual(item1.id, item2.id)
            all_transfers = self.mgr.get_all_transfers()["transfers"]
            self.assertEqual(len([t for t in all_transfers if t["id"] == "dup_id"]), 1)

    async def test_8_telegram_floodwait_and_backoff(self):
        """Test 8: Automatic retry with exponential backoff on transient errors."""
        tf = os.path.join(self.cache_dir, "flood.txt")
        with open(tf, "wb") as f:
            f.write(b"flood")

        attempts = 0
        mock_client = MagicMock()

        async def mock_flood_upload(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("Temporary connection failure")
            return make_mock_tg_message(222, "fid", "fuid", 5)

        mock_client.send_document = AsyncMock(side_effect=mock_flood_upload)

        # Patch asyncio.sleep during backoff in transfer_manager so test runs rapidly without sleeping real seconds
        orig_sleep = asyncio.sleep
        async def fast_sleep(sec):
            await orig_sleep(0.05)

        with patch("utils.uploader._pick_flood_safe_client", return_value=mock_client), \
             patch("utils.transfer_manager.asyncio.sleep", side_effect=fast_sleep):
            item = await self.mgr.queue_upload(file_path=tf, id="flood_id", target_path="/", filename="flood.txt", file_size=5)
            item.max_retries = 4

            for _ in range(200):
                await orig_sleep(0.05)
                cur = self.mgr.get_transfer("flood_id")
                if cur and cur["state"] == TransferState.COMPLETED.value:
                    break

            final_item = self.mgr.get_transfer("flood_id")
            self.assertEqual(final_item["state"], TransferState.COMPLETED.value)
            self.assertGreaterEqual(attempts, 3)

    async def test_9_remote_download_pipeline(self):
        """Test 9: Remote download queue and progress tracking."""
        item_id = "dl_test_1"
        downloaded_file = os.path.join(self.cache_dir, "file.zip")
        with open(downloaded_file, "wb") as f:
            f.write(b"0" * 1000)

        mock_downloader_instance = MagicMock()
        mock_downloader_instance.is_running = False
        mock_downloader_instance.download_success = True
        mock_downloader_instance.total_size = 1000
        mock_downloader_instance.output_path = downloaded_file
        mock_downloader_instance.start = AsyncMock()

        mock_client = MagicMock()
        mock_client.send_document = AsyncMock(return_value=make_mock_tg_message(333, "fid_dl", "fuid_dl", 1000))

        with patch("techzdl.TechZDL", return_value=mock_downloader_instance), \
             patch("utils.downloader.validate_download_url", return_value="https://example.com/file.zip"), \
             patch("utils.uploader._pick_flood_safe_client", return_value=mock_client):

            item = await self.mgr.queue_download(
                url="https://example.com/file.zip",
                id=item_id,
                target_path="/Downloads",
                filename="file.zip",
            )
            self.assertEqual(item.type, TransferType.DOWNLOAD)
            self.assertEqual(item.state, TransferState.QUEUED)

            for _ in range(40):
                await asyncio.sleep(0.1)
                cur = self.mgr.get_transfer(item_id)
                if cur and cur["state"] == TransferState.COMPLETED.value:
                    break

            final_item = self.mgr.get_transfer(item_id)
            self.assertEqual(final_item["state"], TransferState.COMPLETED.value)
            self.assertEqual(final_item["percentage"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
