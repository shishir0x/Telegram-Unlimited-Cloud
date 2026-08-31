"""
Phase 2 — Synchronization Engine Test Suite
=============================================
Tests the core sync engine: ChangeTracker, SyncService, ConflictDetector,
database repository methods, and end-to-end mutation-to-changelog flow.

Does NOT require Telegram connectivity (tests database-level operations only).
Does NOT test FastAPI endpoints (those require session auth and Telegram clients).
"""

import unittest
from database.connection import init_db, get_db_session
from database.models import FileModel, FolderModel, utc_now
from database.repository import DatabaseRepository
from utils.sync import (
    ChangeTracker, SyncService, ConflictDetector, ConflictError,
    FILE_CREATED, FILE_RENAMED, FILE_MOVED, FILE_DELETED, FILE_TRASHED, FILE_RESTORED,
    FOLDER_CREATED, FOLDER_RENAMED, FOLDER_MOVED, FOLDER_DELETED, FOLDER_TRASHED, FOLDER_RESTORED,
)
from utils.websocket_manager import WebSocketManager


class Phase2SyncTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    # ------------------------------------------------------------------
    # 1. Schema & Models
    # ------------------------------------------------------------------

    def test_01_sync_version_model_exists(self):
        """SyncVersionModel table exists and has the global row."""
        with get_db_session() as s:
            from database.models import SyncVersionModel
            row = s.query(SyncVersionModel).filter(SyncVersionModel.id == "global").first()
            self.assertIsNotNone(row, "sync_version table should have a 'global' row")
            self.assertGreaterEqual(int(row.version), 0)

    def test_02_changelog_model_exists(self):
        """ChangeLogModel table exists and is queryable."""
        with get_db_session() as s:
            from database.models import ChangeLogModel
            count = s.query(ChangeLogModel).count()
            self.assertIsInstance(count, int)

    # ------------------------------------------------------------------
    # 2. Version Counter
    # ------------------------------------------------------------------

    def test_03_increment_version(self):
        """Incrementing the version returns a monotonically increasing integer."""
        v1 = DatabaseRepository.get_current_version()
        v2 = DatabaseRepository.increment_version()
        self.assertEqual(v2, v1 + 1)
        v3 = DatabaseRepository.increment_version()
        self.assertEqual(v3, v2 + 1)

    def test_04_increment_version_sequential(self):
        """Multiple sequential increments produce strictly increasing versions."""
        versions = [DatabaseRepository.increment_version() for _ in range(10)]
        for i in range(1, len(versions)):
            self.assertGreater(versions[i], versions[i - 1])

    # ------------------------------------------------------------------
    # 3. Changelog Recording
    # ------------------------------------------------------------------

    def test_05_record_change(self):
        """Recording a change inserts a row with correct fields."""
        ver = DatabaseRepository.increment_version()
        entry = DatabaseRepository.record_change(
            version=ver, user_id="admin", entity_id="TEST_ENTITY_01",
            entity_type="file", operation=FILE_CREATED,
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.version, ver)
        self.assertEqual(entry.entity_id, "TEST_ENTITY_01")
        self.assertEqual(entry.operation, FILE_CREATED)

    def test_06_get_changes_since(self):
        """Querying changes since a version returns only newer entries."""
        base_ver = DatabaseRepository.get_current_version()
        v1 = DatabaseRepository.increment_version()
        DatabaseRepository.record_change(
            version=v1, user_id="admin", entity_id="SINCE_A",
            entity_type="file", operation=FILE_CREATED,
        )
        v2 = DatabaseRepository.increment_version()
        DatabaseRepository.record_change(
            version=v2, user_id="admin", entity_id="SINCE_B",
            entity_type="folder", operation=FOLDER_CREATED,
        )

        changes = DatabaseRepository.get_changes_since("admin", base_ver)
        entity_ids = [c["entity_id"] for c in changes]
        self.assertIn("SINCE_A", entity_ids)
        self.assertIn("SINCE_B", entity_ids)

        changes_v1 = DatabaseRepository.get_changes_since("admin", v1)
        entity_ids_v1 = [c["entity_id"] for c in changes_v1]
        self.assertNotIn("SINCE_A", entity_ids_v1)
        self.assertIn("SINCE_B", entity_ids_v1)

    def test_07_get_last_change(self):
        """get_last_change returns the most recent entry."""
        ver = DatabaseRepository.increment_version()
        DatabaseRepository.record_change(
            version=ver, user_id="admin", entity_id="LAST_TEST",
            entity_type="file", operation=FILE_RENAMED,
            old_name="old.pdf", new_name="new.pdf",
        )
        last = DatabaseRepository.get_last_change(user_id="admin")
        self.assertIsNotNone(last)
        self.assertEqual(last["entity_id"], "LAST_TEST")
        self.assertEqual(last["operation"], FILE_RENAMED)
        self.assertEqual(last["old_name"], "old.pdf")
        self.assertEqual(last["new_name"], "new.pdf")

    # ------------------------------------------------------------------
    # 4. ChangeTracker Convenience Methods
    # ------------------------------------------------------------------

    def test_08_tracker_file_created(self):
        ver = ChangeTracker.file_created("CT_FC_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FC_01"]
        self.assertIn(FILE_CREATED, ops)

    def test_09_tracker_file_renamed(self):
        ver = ChangeTracker.file_renamed("CT_FR_01", "old.txt", "new.txt")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "CT_FR_01"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["old_name"], "old.txt")
        self.assertEqual(matching[0]["new_name"], "new.txt")

    def test_10_tracker_file_moved(self):
        ver = ChangeTracker.file_moved("CT_FM_01", "old_f", "new_f")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "CT_FM_01"]
        self.assertEqual(matching[0]["operation"], FILE_MOVED)
        self.assertEqual(matching[0]["old_folder_id"], "old_f")
        self.assertEqual(matching[0]["new_folder_id"], "new_f")

    def test_11_tracker_file_deleted(self):
        ver = ChangeTracker.file_deleted("CT_FD_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FD_01"]
        self.assertIn(FILE_DELETED, ops)

    def test_12_tracker_file_trashed(self):
        ver = ChangeTracker.file_trashed("CT_FT_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FT_01"]
        self.assertIn(FILE_TRASHED, ops)

    def test_13_tracker_folder_created(self):
        ver = ChangeTracker.folder_created("CT_FOLDER_FC_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "CT_FOLDER_FC_01"]
        self.assertEqual(matching[0]["entity_type"], "folder")

    def test_14_tracker_folder_renamed(self):
        ver = ChangeTracker.folder_renamed("CT_FOLDER_FR_01", "Old Dir", "New Dir")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "CT_FOLDER_FR_01"]
        self.assertEqual(matching[0]["operation"], FOLDER_RENAMED)
        self.assertEqual(matching[0]["old_name"], "Old Dir")
        self.assertEqual(matching[0]["new_name"], "New Dir")

    def test_15_tracker_folder_moved(self):
        ver = ChangeTracker.folder_moved("CT_FOLDER_FM_01", "src_p", "dst_p")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "CT_FOLDER_FM_01"]
        self.assertEqual(matching[0]["operation"], FOLDER_MOVED)

    def test_16_tracker_folder_trashed(self):
        ver = ChangeTracker.folder_trashed("CT_FOLDER_FT_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FOLDER_FT_01"]
        self.assertIn(FOLDER_TRASHED, ops)

    def test_17_tracker_folder_deleted(self):
        ver = ChangeTracker.folder_deleted("CT_FOLDER_FD_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FOLDER_FD_01"]
        self.assertIn(FOLDER_DELETED, ops)

    def test_18_tracker_file_restored(self):
        ver = ChangeTracker.file_restored("CT_FILE_RS_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FILE_RS_01"]
        self.assertIn(FILE_RESTORED, ops)

    def test_19_tracker_folder_restored(self):
        ver = ChangeTracker.folder_restored("CT_FOLDER_RS_01")
        self.assertGreater(ver, 0)
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        ops = [c["operation"] for c in changes if c["entity_id"] == "CT_FOLDER_RS_01"]
        self.assertIn(FOLDER_RESTORED, ops)

    # ------------------------------------------------------------------
    # 5. SyncService
    # ------------------------------------------------------------------

    def test_20_sync_service_status(self):
        status = SyncService.get_status(user_id="admin")
        self.assertIn("version", status)
        self.assertIn("server_time", status)
        self.assertIn("last_change", status)
        self.assertIsInstance(status["version"], int)

    def test_21_sync_service_get_changes(self):
        base = DatabaseRepository.get_current_version()
        ChangeTracker.file_created("SVC_TEST")
        result = SyncService.get_changes_since(user_id="admin", since_version=base)
        self.assertIn("current_version", result)
        self.assertIn("changes", result)
        self.assertIsInstance(result["changes"], list)

    # ------------------------------------------------------------------
    # 6. Conflict Detection
    # ------------------------------------------------------------------

    def test_22_no_conflict_current_version(self):
        current = DatabaseRepository.get_current_version()
        conflict = ConflictDetector.check_version_conflict("NONEXISTENT_ENTITY", current)
        self.assertIsNone(conflict)

    def test_23_conflict_detected(self):
        with get_db_session() as s:
            f = FileModel(
                id="CONFLICT_TEST_01", user_id="admin", name="ctest.pdf",
                original_name="ctest.pdf", size=1024, telegram_message_id=99999,
                folder_id="root", trash=False, tags=[], metadata_extra={},
                activity_history=[], created_at=utc_now(), updated_at=utc_now(),
            )
            s.add(f)
            s.flush()

        current = DatabaseRepository.get_current_version()
        DatabaseRepository.increment_version()

        conflict = ConflictDetector.check_version_conflict("CONFLICT_TEST_01", current)
        self.assertIsNotNone(conflict)
        self.assertTrue(conflict["conflict"])
        self.assertEqual(conflict["entity_id"], "CONFLICT_TEST_01")

        # Cleanup
        with get_db_session() as s:
            s.query(FileModel).filter(FileModel.id == "CONFLICT_TEST_01").delete()
            s.flush()

    def test_24_conflict_error_raised(self):
        with get_db_session() as s:
            f = FileModel(
                id="CONFLICT_TEST_02", user_id="admin", name="ctest2.pdf",
                original_name="ctest2.pdf", size=1024, telegram_message_id=99998,
                folder_id="root", trash=False, tags=[], metadata_extra={},
                activity_history=[], created_at=utc_now(), updated_at=utc_now(),
            )
            s.add(f)
            s.flush()

        current = DatabaseRepository.get_current_version()
        DatabaseRepository.increment_version()
        with self.assertRaises(ConflictError):
            ConflictDetector.assert_no_conflict("CONFLICT_TEST_02", current)

        with get_db_session() as s:
            s.query(FileModel).filter(FileModel.id == "CONFLICT_TEST_02").delete()
            s.flush()

    def test_25_no_conflict_for_nonexistent_entity(self):
        """Non-existent entities should not trigger conflicts."""
        current = DatabaseRepository.get_current_version()
        conflict = ConflictDetector.check_version_conflict("DOES_NOT_EXIST", current)
        self.assertIsNone(conflict)

    # ------------------------------------------------------------------
    # 7. WebSocket Manager
    # ------------------------------------------------------------------

    def test_26_ws_manager_init(self):
        mgr = WebSocketManager()
        self.assertEqual(mgr.count_connections(), 0)
        self.assertEqual(mgr.get_connected_users(), [])

    def test_27_ws_manager_user_count(self):
        mgr = WebSocketManager()
        self.assertEqual(mgr.get_user_connection_count("admin"), 0)

    # ------------------------------------------------------------------
    # 8. All operation constants are correct
    # ------------------------------------------------------------------

    def test_28_operation_constants(self):
        """Verify all operation constants have expected values."""
        self.assertEqual(FILE_CREATED, "FILE_CREATED")
        self.assertEqual(FILE_RENAMED, "FILE_RENAMED")
        self.assertEqual(FILE_MOVED, "FILE_MOVED")
        self.assertEqual(FILE_DELETED, "FILE_DELETED")
        self.assertEqual(FILE_TRASHED, "FILE_TRASHED")
        self.assertEqual(FILE_RESTORED, "FILE_RESTORED")
        self.assertEqual(FOLDER_CREATED, "FOLDER_CREATED")
        self.assertEqual(FOLDER_RENAMED, "FOLDER_RENAMED")
        self.assertEqual(FOLDER_MOVED, "FOLDER_MOVED")
        self.assertEqual(FOLDER_DELETED, "FOLDER_DELETED")
        self.assertEqual(FOLDER_TRASHED, "FOLDER_TRASHED")
        self.assertEqual(FOLDER_RESTORED, "FOLDER_RESTORED")

    def test_29_changelog_extra_field(self):
        """Verify extra JSON payload is stored correctly."""
        ver = DatabaseRepository.increment_version()
        DatabaseRepository.record_change(
            version=ver, user_id="admin", entity_id="EXTRA_TEST",
            entity_type="file", operation=FILE_CREATED,
            extra={"file_size": 1024, "mime_type": "application/pdf"},
        )
        changes = DatabaseRepository.get_changes_since("admin", ver - 1)
        matching = [c for c in changes if c["entity_id"] == "EXTRA_TEST"]
        self.assertEqual(matching[0]["extra"]["file_size"], 1024)
        self.assertEqual(matching[0]["extra"]["mime_type"], "application/pdf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
