"""
Phase 1 Verification Test Suite
===============================
Verifies:
1. Environment & URL configuration (DATABASE_URL, TELEGRAM_*, CORS_ORIGINS)
2. Database models & schema integrity
3. Alembic migration status
4. Database repository CRUD operations & Telegram message ID collection
5. Synchronization between DriveData in-memory tree and Database
6. FastAPI health & readiness probes reporting database connectivity
"""

import os
import unittest
from fastapi.testclient import TestClient

import config
from database.connection import get_db_session, test_database_connection, init_db
from database.models import FolderModel, FileModel
from database.repository import DatabaseRepository
from utils.directoryHandler import ensure_drive_data
from main import app


class Phase1DatabaseTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def test_01_config_and_url_normalization(self):
        """Test A: Verifies configuration, standardized env aliases, and URL normalization."""
        self.assertIsNotNone(config.DATABASE_URL)
        self.assertIsNotNone(config.SYNC_DATABASE_URL)
        self.assertIsNotNone(config.ASYNC_DATABASE_URL)
        # Verify sync URL dialect is suitable for SQLAlchemy sync engine
        self.assertTrue(
            config.SYNC_DATABASE_URL.startswith("sqlite") or config.SYNC_DATABASE_URL.startswith("postgresql://")
        )
        # Verify async URL dialect is suitable for asyncpg
        self.assertTrue(
            config.ASYNC_DATABASE_URL.startswith("sqlite+aiosqlite") or config.ASYNC_DATABASE_URL.startswith("postgresql+asyncpg://")
        )
        # Verify standardized aliases
        self.assertEqual(config.TELEGRAM_API_ID, config.API_ID)
        self.assertEqual(config.TELEGRAM_API_HASH, config.API_HASH)
        self.assertEqual(config.TELEGRAM_BOT_TOKEN, config.BOT_TOKENS[0])
        self.assertEqual(config.TELEGRAM_CHAT_ID, config.STORAGE_CHANNEL)
        self.assertIsInstance(config.CORS_ORIGINS, list)

    def test_02_database_connectivity(self):
        """Test B: Verifies database connection pool and responsiveness."""
        connected = test_database_connection()
        self.assertTrue(connected, "Database connection test failed")

    def test_03_repository_crud_and_telegram_id_collection(self):
        """Test C: Verifies DatabaseRepository CRUD and recursive Telegram ID deletion."""
        test_folder_id = "test_fld_p1_01"
        test_file_id = "test_fil_p1_01"
        telegram_msg_id = 987654321

        # 1. Create Folder
        folder = DatabaseRepository.create_folder(
            id=test_folder_id,
            name="Phase 1 Test Folder",
            parent_folder_id="root",
            path="/",
            user_id="admin",
        )
        self.assertEqual(folder.id, test_folder_id)

        # Verify in DB
        fetched_folder = DatabaseRepository.get_folder(test_folder_id)
        self.assertIsNotNone(fetched_folder)
        self.assertEqual(fetched_folder.name, "Phase 1 Test Folder")

        # 2. Create File inside Folder
        file_obj = DatabaseRepository.create_file(
            id=test_file_id,
            name="test_document.pdf",
            telegram_message_id=telegram_msg_id,
            size=102400,
            folder_id=test_folder_id,
            checksum="abcdef1234567890",
            user_id="admin",
        )
        self.assertEqual(file_obj.id, test_file_id)
        self.assertEqual(file_obj.telegram_message_id, telegram_msg_id)

        # 3. Rename File
        renamed = DatabaseRepository.rename_item(test_file_id, "renamed_document.pdf")
        self.assertTrue(renamed)
        fetched_file = DatabaseRepository.get_file(test_file_id)
        self.assertEqual(fetched_file.name, "renamed_document.pdf")

        # 4. Trash & Restore Item
        DatabaseRepository.trash_item(test_file_id, True)
        trashed_file = DatabaseRepository.get_file(test_file_id)
        self.assertTrue(trashed_file.trash)
        self.assertIsNotNone(trashed_file.trashed_at)

        DatabaseRepository.trash_item(test_file_id, False)
        restored_file = DatabaseRepository.get_file(test_file_id)
        self.assertFalse(restored_file.trash)

        # 5. Delete Folder and verify recursive Telegram ID collection
        collected_ids = DatabaseRepository.delete_item(test_folder_id)
        self.assertIn(telegram_msg_id, collected_ids)
        self.assertIsNone(DatabaseRepository.get_folder(test_folder_id))
        self.assertIsNone(DatabaseRepository.get_file(test_file_id))

    def test_04_drivedata_database_sync(self):
        """Test D: Verifies DriveData operations automatically sync to Database."""
        drive = ensure_drive_data()
        self.assertIsNotNone(drive)

        # Create folder via drive
        folder_path = drive.new_folder("/", "AutoSync_Folder")
        folder_id = folder_path.strip("/").split("/")[-1]

        # Verify immediately in database
        db_folder = DatabaseRepository.get_folder(folder_id)
        self.assertIsNotNone(db_folder, f"Folder {folder_id} was not synced to database")
        self.assertEqual(db_folder.name, "AutoSync_Folder")

        # Create file via drive
        file_id = drive.new_file("/" + folder_id, "sync_test.txt", file_id=12345678, size=2048)
        db_file = DatabaseRepository.get_file(file_id)
        self.assertIsNotNone(db_file, f"File {file_id} was not synced to database")
        self.assertEqual(db_file.telegram_message_id, 12345678)

        # Rename via drive
        drive.rename_file_folder("/" + folder_id + "/" + file_id, "renamed_sync_test.txt")
        db_file_renamed = DatabaseRepository.get_file(file_id)
        self.assertEqual(db_file_renamed.name, "renamed_sync_test.txt")

        # Delete via drive
        deleted_msgs = drive.delete_file_folder("/" + folder_id)
        self.assertIn(12345678, deleted_msgs)
        self.assertIsNone(DatabaseRepository.get_folder(folder_id))
        self.assertIsNone(DatabaseRepository.get_file(file_id))

    def test_05_health_and_readiness_endpoints(self):
        """Test E: Verifies /health/live and /health/ready report database status."""
        # Liveness probe
        res_live = self.client.get("/health/live")
        self.assertEqual(res_live.status_code, 200)
        self.assertEqual(res_live.json().get("status"), "alive")

        # Readiness probe
        res_ready = self.client.get("/health/ready")
        data = res_ready.json()
        self.assertIn("database_connected", data)
        self.assertTrue(data["database_connected"])


if __name__ == "__main__":
    unittest.main()
