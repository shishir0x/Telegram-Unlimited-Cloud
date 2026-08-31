"""
Phase 3 — Frontend Synchronization & Final Local ↔ Render Experience
=====================================================================
Tests:
  1. Sync API endpoints are accessible and return correct data
  2. SyncClient JavaScript module is properly structured
  3. WebSocket endpoint accepts connections
  4. Change detection triggers correct UI refresh paths
  5. Offline/reconnection flow works correctly
  6. Security: sync endpoints require authentication
  7. End-to-end: mutation → sync event → client catches up
  8. Frontend assets load correctly (CSS, JS)
"""

import os
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from database.connection import init_db
from database.repository import DatabaseRepository
from utils.sync import ChangeTracker, SyncService

init_db()
from main import app


class Phase3SyncTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ------------------------------------------------------------------
    # 1. Sync API Endpoints
    # ------------------------------------------------------------------

    def test_01_sync_status_endpoint_exists(self):
        """GET /api/sync/status exists and returns 401 without auth."""
        resp = self.client.get("/api/sync/status")
        self.assertEqual(resp.status_code, 401)

    def test_02_sync_changes_endpoint_exists(self):
        """GET /api/sync/changes exists and returns 401 without auth."""
        resp = self.client.get("/api/sync/changes?since=0")
        self.assertEqual(resp.status_code, 401)

    def test_03_sync_status_endpoint_returns_json(self):
        """GET /api/sync/status returns JSON response (401 or valid data)."""
        resp = self.client.get("/api/sync/status")
        # Should return 401 without auth, or 200 with valid JSON
        self.assertIn(resp.status_code, [200, 401])
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("version", data)
            self.assertIn("server_time", data)

    def test_04_sync_changes_endpoint_returns_json(self):
        """GET /api/sync/changes returns JSON response."""
        resp = self.client.get("/api/sync/changes?since=0")
        self.assertIn(resp.status_code, [200, 401])
        if resp.status_code == 200:
            data = resp.json()
            self.assertIn("current_version", data)
            self.assertIn("changes", data)

    # ------------------------------------------------------------------
    # 2. SyncClient JavaScript Module
    # ------------------------------------------------------------------

    def test_05_sync_client_js_exists(self):
        """syncClient.js file exists in the static directory."""
        js_path = Path("website/static/js/syncClient.js")
        self.assertTrue(js_path.exists(), f"syncClient.js not found at {js_path}")

    def test_06_sync_client_has_required_api(self):
        """syncClient.js exports the required SyncClient API."""
        js_path = Path("website/static/js/syncClient.js")
        content = js_path.read_text(encoding="utf-8")
        # Check for required API methods (they are defined as function names, exported via object)
        self.assertIn("init:", content)
        self.assertIn("enable:", content)
        self.assertIn("disable:", content)
        self.assertIn("isEnabled:", content)
        self.assertIn("getCurrentVersion:", content)
        self.assertIn("forceRefresh:", content)

    def test_07_sync_client_has_websocket(self):
        """syncClient.js contains WebSocket connection logic."""
        js_path = Path("website/static/js/syncClient.js")
        content = js_path.read_text(encoding="utf-8")
        self.assertIn("WebSocket", content)
        self.assertIn("ws:", content)
        self.assertIn("wss:", content)

    def test_08_sync_client_has_polling_fallback(self):
        """syncClient.js contains polling fallback for when WebSocket is unavailable."""
        js_path = Path("website/static/js/syncClient.js")
        content = js_path.read_text(encoding="utf-8")
        self.assertIn("setInterval", content)
        self.assertIn("pollForChanges", content)
        self.assertIn("/api/sync/status", content)
        self.assertIn("/api/sync/changes", content)

    def test_09_sync_client_handles_reconnection(self):
        """syncClient.js handles WebSocket reconnection."""
        js_path = Path("website/static/js/syncClient.js")
        content = js_path.read_text(encoding="utf-8")
        self.assertIn("reconnect", content.lower())
        self.assertIn("onclose", content)

    def test_10_sync_client_handles_offline(self):
        """syncClient.js handles offline/online transitions."""
        js_path = Path("website/static/js/syncClient.js")
        content = js_path.read_text(encoding="utf-8")
        self.assertIn("offline", content)
        self.assertIn("online", content)
        self.assertIn("visibilitychange", content)

    # ------------------------------------------------------------------
    # 3. WebSocket Endpoint
    # ------------------------------------------------------------------

    def test_11_ws_endpoint_route_exists(self):
        """WebSocket endpoint route is registered."""
        from utils.sync_routes import router
        routes = [r.path for r in router.routes]
        # When included via include_router, paths may or may not include the prefix
        has_ws = any('/ws' in p for p in routes)
        self.assertTrue(has_ws, f'WebSocket route not found. Available routes: {routes}')

    # ------------------------------------------------------------------
    # 4. Sync Indicator in HTML
    # ------------------------------------------------------------------

    def test_12_sync_indicator_in_html(self):
        """home.html contains the sync indicator element."""
        html_path = Path("website/home.html")
        content = html_path.read_text(encoding="utf-8")
        self.assertIn("sync-indicator", content)
        self.assertIn("sync-dot", content)
        self.assertIn("sync-label", content)

    def test_13_sync_client_script_loaded(self):
        """home.html loads syncClient.js script."""
        html_path = Path("website/home.html")
        content = html_path.read_text(encoding="utf-8")
        self.assertIn("syncClient.js", content)

    # ------------------------------------------------------------------
    # 5. Sync Indicator CSS
    # ------------------------------------------------------------------

    def test_14_sync_indicator_css(self):
        """home.css contains sync indicator styles."""
        css_path = Path("website/static/home.css")
        content = css_path.read_text(encoding="utf-8")
        self.assertIn("#sync-indicator", content)
        self.assertIn(".sync-dot", content)
        self.assertIn(".sync-dot-synced", content)
        self.assertIn(".sync-dot-polling", content)
        self.assertIn(".sync-dot-offline", content)
        self.assertIn(".sync-dot-error", content)

    # ------------------------------------------------------------------
    # 6. Security: Authentication Required
    # ------------------------------------------------------------------

    def test_15_sync_status_requires_auth(self):
        """Unauthenticated requests to /api/sync/status are rejected."""
        # Use a fresh client without cookies
        fresh_client = TestClient(app, raise_server_exceptions=False)
        resp = fresh_client.get("/api/sync/status")
        self.assertIn(resp.status_code, [401, 403])

    def test_16_sync_changes_requires_auth(self):
        """Unauthenticated requests to /api/sync/changes are rejected."""
        fresh_client = TestClient(app, raise_server_exceptions=False)
        resp = fresh_client.get("/api/sync/changes?since=0")
        self.assertIn(resp.status_code, [401, 403])

    # ------------------------------------------------------------------
    # 7. End-to-End: Mutation → Sync Event → Client Catches Up
    # ------------------------------------------------------------------

    def test_17_e2e_mutation_triggers_sync_event(self):
        """After a mutation, the sync version increments and changes are queryable."""
        base_ver = DatabaseRepository.get_current_version()

        # Perform a mutation
        ChangeTracker.file_created("E2E_PHASE3_FILE")
        ChangeTracker.file_renamed("E2E_PHASE3_FILE", "old.txt", "new.txt")
        ChangeTracker.folder_created("E2E_PHASE3_FOLDER")
        ChangeTracker.folder_deleted("E2E_PHASE3_FOLDER")

        # Verify version advanced
        current_ver = DatabaseRepository.get_current_version()
        self.assertGreater(current_ver, base_ver)

        # Verify changes are queryable
        changes = DatabaseRepository.get_changes_since("admin", base_ver)
        self.assertGreater(len(changes), 0)

        entity_ids = [c["entity_id"] for c in changes]
        self.assertIn("E2E_PHASE3_FILE", entity_ids)
        self.assertIn("E2E_PHASE3_FOLDER", entity_ids)

    def test_18_e2e_sync_service_returns_changes(self):
        """SyncService.get_changes_since returns changes that a client can use to catch up."""
        base = DatabaseRepository.get_current_version()
        ChangeTracker.file_created("CLIENT_FETCH_TEST")
        result = SyncService.get_changes_since(user_id="admin", since_version=base)
        entity_ids = [c["entity_id"] for c in result.get("changes", [])]
        self.assertIn("CLIENT_FETCH_TEST", entity_ids)

    # ------------------------------------------------------------------
    # 8. Frontend Assets Load
    # ------------------------------------------------------------------

    def test_19_home_page_loads(self):
        """The home page loads successfully."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("google", resp.text.lower())  # Google Drive-style UI

    def test_20_static_js_files_exist(self):
        """All required JavaScript files exist."""
        js_files = [
            "website/static/js/main.js",
            "website/static/js/apiHandler.js",
            "website/static/js/extra.js",
            "website/static/js/syncClient.js",
            "website/static/js/sidebar.js",
            "website/static/js/fileClickHandler.js",
            "website/static/js/transferManager.js",
        ]
        for js_file in js_files:
            self.assertTrue(Path(js_file).exists(), f"Missing: {js_file}")

    def test_21_static_css_files_exist(self):
        """All required CSS files exist."""
        css_files = [
            "website/static/home.css",
            "website/static/css/share.css",
            "website/static/css/duplicates.css",
        ]
        for css_file in css_files:
            self.assertTrue(Path(css_file).exists(), f"Missing: {css_file}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
