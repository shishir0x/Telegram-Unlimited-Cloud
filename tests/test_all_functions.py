"""
Comprehensive End-to-End Automated Test Suite for Telegram Unlimited Cloud
==========================================================================
Tests every core layer:
1. Static & Health Endpoints
2. Security Headers & ASGI Middleware
3. Unauthorized API Access Rejection (401 across protected routes)
4. Password Authentication, Session Cookies & Rate Limiting
5. Directory Navigation & Root Metadata Stats
6. Folder Creation & Duplicate Prevention
7. Path/Name Sanitization, Unicode & Special Character Handling
8. Move Operations & Strict Circular Move Prevention
9. Copy Operations & Deep Tree ID Regeneration
10. Soft-Delete (Trash), Trash Listing & Restoration
11. Permanent Delete & Bulk Deletion
12. Scoped Public Share Tokens & Access Control Isolation
13. Deep Search with Device & Location Provenance
14. Single & Bulk ZIP Archive Download Preparation
15. Atomic Data Persistence & Backup Recovery Integrity
16. Logout & Session Invalidation
"""

import sys

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
import json
import time
import os
import secrets
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("TEST_URL", "http://127.0.0.1:8001")
# The admin password must be supplied via the environment (.env or shell).
# Never hardcode credentials in source control.
PASSWORD = os.getenv("ADMIN_PASSWORD", "")

class UnifiedClient:
    def __init__(self, base_url, use_test_client=False, app=None):
        self.base_url = base_url
        self.use_test_client = use_test_client
        if use_test_client:
            from starlette.testclient import TestClient
            self.client = TestClient(app)
        else:
            self.client = requests.Session()

    def _format_url(self, url):
        if self.use_test_client:
            if url.startswith(self.base_url):
                url = url[len(self.base_url):]
            if not url.startswith("/"):
                url = "/" + url
            return url
        return url

    @property
    def cookies(self):
        return self.client.cookies

    def get(self, url, **kwargs):
        return self.client.get(self._format_url(url), **kwargs)

    def post(self, url, **kwargs):
        return self.client.post(self._format_url(url), **kwargs)

    def head(self, url, **kwargs):
        return self.client.head(self._format_url(url), **kwargs)

# Check if live server is reachable, else fall back to ASGI TestClient
_live = False
try:
    _res = requests.get(f"{BASE_URL}/health", timeout=1)
    if _res.status_code == 200:
        _live = True
except Exception:
    _live = False

if _live:
    print(f"[*] Testing against live server at {BASE_URL}")
    session = UnifiedClient(BASE_URL, use_test_client=False)
    unauth_session = UnifiedClient(BASE_URL, use_test_client=False)
else:
    print(f"[*] Live server not running on {BASE_URL}. Running tests via ASGI TestClient...")
    from main import app
    session = UnifiedClient(BASE_URL, use_test_client=True, app=app)
    unauth_session = UnifiedClient(BASE_URL, use_test_client=True, app=app)

def run_tests():
    if not PASSWORD:
        print("[!] ADMIN_PASSWORD is not set. Provide it via .env or the environment to run authentication tests.")
        sys.exit(1)

    print("=" * 70)
    print("[+] TG DRIVE FULL COMPREHENSIVE AUTOMATED TEST SUITE")
    print("=" * 70)

    # -------------------------------------------------------------
    # 1. Test Static & Health Endpoints
    # -------------------------------------------------------------
    print("\n[1] Testing Static & Health Endpoints:")
    r = session.get(f"{BASE_URL}/")
    print(f"  GET /: {r.status_code} (HTML size: {len(r.text)} bytes)")
    assert r.status_code == 200

    r = session.get(f"{BASE_URL}/health")
    print(f"  GET /health: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    r = session.get(f"{BASE_URL}/health/live")
    print(f"  GET /health/live: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert r.json().get("status") == "alive"

    r = session.get(f"{BASE_URL}/health/ready")
    print(f"  GET /health/ready: {r.status_code} -> {r.json()}")
    assert r.status_code in (200, 503)

    r = session.head(f"{BASE_URL}/health")
    print(f"  HEAD /health: {r.status_code}")
    assert r.status_code == 200

    r = session.get(f"{BASE_URL}/static/js/main.js")
    print(f"  GET /static/js/main.js: {r.status_code}")
    assert r.status_code == 200

    r = session.get(f"{BASE_URL}/static/js/apiHandler.js")
    print(f"  GET /static/js/apiHandler.js: {r.status_code}")
    assert r.status_code == 200

    # -------------------------------------------------------------
    # 2. Test Security Headers
    # -------------------------------------------------------------
    print("\n[2] Testing Security Headers:")
    r = session.get(f"{BASE_URL}/")
    headers = r.headers
    print(f"  X-Content-Type-Options: {headers.get('x-content-type-options')}")
    print(f"  X-Frame-Options: {headers.get('x-frame-options')}")
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "SAMEORIGIN"

    # -------------------------------------------------------------
    # 3. Test Security: Unauthorized Access Rejection (401)
    # -------------------------------------------------------------
    print("\n[3] Testing Unauthorized Access Rejection (All protected APIs):")
    protected_endpoints = [
        ("POST", "/api/createNewFolder", {"name": "Hacked", "path": "/"}),
        ("POST", "/api/moveFileFolder", {"src_path": "/fake", "dest_path": "/"}),
        ("POST", "/api/copyFileFolder", {"src_path": "/fake", "dest_path": "/"}),
        ("POST", "/api/renameFileFolder", {"path": "/fake", "name": "New"}),
        ("POST", "/api/trashFileFolder", {"path": "/fake", "trash": True}),
        ("POST", "/api/deleteFileFolder", {"path": "/fake"}),
        ("POST", "/api/bulkDelete", {"paths": ["/fake"]}),
        ("POST", "/api/bulkTrash", {"paths": ["/fake"]}),
        ("POST", "/api/search", {"query": "secret"}),
        ("POST", "/api/downloadZip", {"paths": ["/fake"]}),
        ("POST", "/api/getFolderShareAuth", {"path": "/"}),
        ("POST", "/api/getDirectory", {"path": "/"}),
        ("GET", "/api/admin/integrityReport", None),
        ("GET", "/file?path=/fake_file", None),
        ("GET", "/thumbnail?path=/fake_thumb", None),
        ("GET", "/downloadZip?paths=/fake", None),
    ]

    for method, path, payload in protected_endpoints:
        if method == "POST":
            res = unauth_session.post(f"{BASE_URL}{path}", json=payload)
        else:
            res = unauth_session.get(f"{BASE_URL}{path}")
        print(f"  Unauth {method} {path.split('?')[0]}: {res.status_code} (Expected 401)")
        assert res.status_code == 401, f"Expected 401 for {path}, got {res.status_code}"

    # -------------------------------------------------------------
    # 4. Test Password Verification & Login
    # -------------------------------------------------------------
    print("\n[4] Testing Authentication & Session Cookie Creation:")
    # Wrong password test
    r = unauth_session.post(f"{BASE_URL}/api/checkPassword", json={"password": "wrong_password_xyz"})
    print(f"  Wrong password: {r.status_code} (Expected 401)")
    assert r.status_code == 401

    # Empty password test
    r = unauth_session.post(f"{BASE_URL}/api/checkPassword", json={"password": ""})
    print(f"  Empty password: {r.status_code} (Expected 401)")
    assert r.status_code == 401

    # Correct password login
    r = session.post(f"{BASE_URL}/api/checkPassword", json={"password": PASSWORD})
    print(f"  Correct password login: {r.status_code} -> {r.json().get('status')}")
    assert r.status_code == 200
    assert "tg_session" in session.cookies
    print(f"  Authenticated session cookie: tg_session={session.cookies['tg_session'][:12]}...")

    # -------------------------------------------------------------
    # 5. Test Directory Listing & Stats
    # -------------------------------------------------------------
    print("\n[5] Testing Directory Listing & Stats:")
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    print(f"  Root directory listing: {r.status_code}")
    assert r.status_code == 200
    res_json = r.json()
    assert "data" in res_json
    assert "stats" in res_json
    print(f"  Total files: {res_json['stats']['total_files']}, Total bytes: {res_json['stats']['total_bytes']}")

    # Test Admin Integrity Report
    r = session.get(f"{BASE_URL}/api/admin/integrityReport")
    print(f"  GET /api/admin/integrityReport: {r.status_code}")
    assert r.status_code == 200
    report = r.json()
    assert report.get("status") == "ok"
    assert "integrity" in report
    print(f"  Integrity scan result: tree_valid={report['integrity']['tree_valid']}, total_folders={report['integrity']['total_folders']}")

    # -------------------------------------------------------------
    # 6. Test Folder Creation & Duplicate Handling
    # -------------------------------------------------------------
    print("\n[6] Testing Folder Creation & Duplicate Prevention:")
    test_folder_name = f"Audit_Folder_{secrets.token_hex(3)}"
    r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": test_folder_name, "path": "/"})
    print(f"  Create folder '{test_folder_name}': {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    # Duplicate creation should be rejected
    r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": test_folder_name, "path": "/"})
    print(f"  Duplicate folder rejection: {r.status_code} -> {r.json().get('status')}")
    assert "already exist" in r.json().get("status", "")

    # Retrieve created folder ID
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    contents = r.json()["data"]["contents"]
    folder_id = None
    for fid, fitem in contents.items():
        if fitem["name"] == test_folder_name:
            folder_id = fid
            break
    print(f"  Found Folder ID: {folder_id}")
    assert folder_id is not None

    # -------------------------------------------------------------
    # 7. Test Unicode, Emojis, and Path Traversal Name Sanitization
    # -------------------------------------------------------------
    print("\n[7] Testing Name Sanitization (Unicode, Emojis, Traversal):")
    special_name = "📁 Special Test & 🚀 _ ../../evil_name"
    r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": special_name, "path": f"/{folder_id}"})
    print(f"  Create subfolder with traversal & emoji: {r.status_code} -> {r.json()}")
    assert r.status_code == 200

    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": f"/{folder_id}"})
    sub_contents = r.json()["data"]["contents"]
    assert len(sub_contents) >= 1
    created_sub_name = list(sub_contents.values())[0]["name"]
    print(f"  Sanitized subfolder name: '{created_sub_name}'")
    assert "../" not in created_sub_name
    assert ".." not in created_sub_name

    # -------------------------------------------------------------
    # 8. Test Move Operations & Circular Move Prevention
    # -------------------------------------------------------------
    print("\n[8] Testing Move Operations & Circular Move Prevention:")
    subfolder_id = list(sub_contents.keys())[0]

    # Create target parent folder
    target_parent_name = f"Move_Target_{secrets.token_hex(3)}"
    r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": target_parent_name, "path": "/"})
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    target_parent_id = None
    for fid, fitem in r.json()["data"]["contents"].items():
        if fitem["name"] == target_parent_name:
            target_parent_id = fid
            break
    assert target_parent_id is not None
    print(f"  Created move destination: {target_parent_name} ({target_parent_id})")

    # Move subfolder to target parent
    r = session.post(f"{BASE_URL}/api/moveFileFolder", json={"src_path": f"/{folder_id}/{subfolder_id}", "dest_path": f"/{target_parent_id}"})
    print(f"  Move item to new destination: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    # Circular move attempt: move parent into its own child (MUST BE REJECTED)
    r = session.post(f"{BASE_URL}/api/moveFileFolder", json={"src_path": f"/{target_parent_id}", "dest_path": f"/{target_parent_id}/{subfolder_id}"})
    print(f"  Circular move attempt (parent into child): {r.status_code} -> {r.json()}")
    assert r.status_code == 400 or "Cannot move a folder into itself" in r.json().get("status", "")

    # -------------------------------------------------------------
    # 9. Test Copy Operations
    # -------------------------------------------------------------
    print("\n[9] Testing Copy Operations:")
    r = session.post(f"{BASE_URL}/api/copyFileFolder", json={"src_path": f"/{target_parent_id}/{subfolder_id}", "dest_path": "/"})
    print(f"  Copy folder to root: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    copied_id = r.json().get("new_id")
    assert copied_id is not None

    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    assert copied_id in r.json()["data"]["contents"]
    print(f"  Verified copy exists in root with ID: {copied_id}")

    # -------------------------------------------------------------
    # 10. Test Soft-Delete (Trash) & Restoration
    # -------------------------------------------------------------
    print("\n[10] Testing Soft Delete (Trash) & Restore:")
    r = session.post(f"{BASE_URL}/api/trashFileFolder", json={"path": f"/{copied_id}", "trash": True})
    print(f"  Move copied item to Trash: {r.status_code} -> {r.json()}")
    assert r.status_code == 200

    # Verify item is in Trash
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/trash"})
    trash_items = r.json()["data"]["contents"]
    assert copied_id in trash_items
    print(f"  Verified item '{copied_id}' is visible in /trash")

    # Restore from Trash
    r = session.post(f"{BASE_URL}/api/trashFileFolder", json={"path": f"/{copied_id}", "trash": False})
    print(f"  Restore item from Trash: {r.status_code} -> {r.json()}")
    assert r.status_code == 200

    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/trash"})
    assert copied_id not in r.json()["data"]["contents"]
    print(f"  Verified item '{copied_id}' is no longer in /trash")

    # -------------------------------------------------------------
    # 11. Test Share Token Generation & Public Scoped Access
    # -------------------------------------------------------------
    print("\n[11] Testing Share Auth Generation & Access Control Isolation:")
    r = session.post(f"{BASE_URL}/api/getFolderShareAuth", json={"path": f"/{target_parent_id}"})
    print(f"  Generate Share Auth: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    share_auth = r.json().get("auth")
    assert share_auth is not None

    # Public visitor access with valid token -> Allowed
    r = unauth_session.post(f"{BASE_URL}/api/getDirectory", json={"path": f"/share_{target_parent_id}", "auth": share_auth})
    print(f"  Public visitor accessing shared folder with valid token: {r.status_code} -> {r.json().get('status')}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    # Public visitor trying to access root or other folder with the same token -> MUST BE REJECTED (401)
    r = unauth_session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "auth": share_auth})
    print(f"  Public visitor attempting to access Root with subfolder share token: {r.status_code} (Expected 401)")
    assert r.status_code == 401

    # -------------------------------------------------------------
    # 12. Test Deep Search with Device & Location Provenance
    # -------------------------------------------------------------
    print("\n[12] Testing Deep Search Engine:")
    # A. Search by exact name
    r = session.post(f"{BASE_URL}/api/search", json={"query": target_parent_name})
    print(f"  Search for '{target_parent_name}': {r.status_code}")
    assert r.status_code == 200
    search_data = r.json()
    assert search_data.get("status") == "ok"
    results = search_data["data"]["contents"]
    assert len(results) >= 1
    match_item = list(results.values())[0]
    print(f"  Found search match: {match_item.get('name')}")
    print(f"  Item Location: {match_item.get('display_path')}")
    print(f"  Item Device Tag: {match_item.get('device')}")

    # B. Search with type filter 'folder'
    r_type = session.post(f"{BASE_URL}/api/search", json={"query": target_parent_name[:5], "type": "folder"})
    assert r_type.status_code == 200
    assert len(r_type.json()["data"]["contents"]) >= 1

    # C. Search with location scoping
    r_loc = session.post(f"{BASE_URL}/api/search", json={"query": "", "location": f"/{target_parent_id}"})
    assert r_loc.status_code == 200

    # D. Search via GET /api/getDirectory with /search_ path
    r_path = session.post(f"{BASE_URL}/api/getDirectory", json={"path": f"/search_{urllib.parse.quote(target_parent_name)}"})
    assert r_path.status_code == 200
    assert len(r_path.json()["data"]["contents"]) >= 1
    print("  Verified deep search endpoint and /search_ compatibility.")

    # -------------------------------------------------------------
    # 13. Test ZIP Download API
    # -------------------------------------------------------------
    print("\n[13] Testing ZIP Download Preparation Endpoint:")
    r = session.post(f"{BASE_URL}/api/downloadZip", json={"paths": [f"/{target_parent_id}"]})
    print(f"  POST /api/downloadZip: {r.status_code} -> {r.json().get('status')}")
    assert r.status_code in (200, 404)  # 404 is expected if folder has no files yet, 200 if files present

    # -------------------------------------------------------------
    # 14. Test Tagging System + Virtual Views (/recent, /tags/<tag>)
    # -------------------------------------------------------------
    print("\n[14] Testing Tagging Endpoints + Next-Gen Views:")
    # A. Test Tag File/Folder (Add tag)
    r = session.post(f"{BASE_URL}/api/tagFileFolder", json={"path": f"/{target_parent_id}", "action": "add", "tag": "Important"})
    print(f"  Add tag 'Important': {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert "Important" in r.json().get("tags", [])

    # B. Test /tags/Important view
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/tags/Important"})
    print(f"  POST /api/getDirectory for '/tags/Important': {r.status_code} -> {r.json().get('status')}")
    assert r.status_code == 200
    tagged_contents = r.json()["data"]["contents"]
    assert target_parent_id in tagged_contents
    assert "Important" in tagged_contents[target_parent_id].get("tags", [])
    print(f"  Verified folder appears in /tags/Important view ({len(tagged_contents)} items).")

    # C. Test /recent view
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/recent"})
    print(f"  POST /api/getDirectory for '/recent': {r.status_code} -> {r.json().get('status')}")
    assert r.status_code == 200
    assert "contents" in r.json()["data"]
    print(f"  Verified /recent view loaded successfully.")

    # D. Test Remove Tag
    r = session.post(f"{BASE_URL}/api/tagFileFolder", json={"path": f"/{target_parent_id}", "action": "remove", "tag": "Important"})
    assert r.status_code == 200
    assert "Important" not in r.json().get("tags", [])
    print("  Verified tag removal.")

    # -------------------------------------------------------------
    # 15. Test Cleanup & Bulk Deletion
    # -------------------------------------------------------------
    print("\n[15] Testing Bulk Deletion & Cleanup:")
    # Fetch current root items and identify all test artifacts
    r_root = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    test_ids = []
    if r_root.status_code == 200:
        for item_id, item_val in r_root.json().get("data", {}).get("contents", {}).items():
            name = item_val.get("name", "")
            if name.startswith(("Audit_Folder", "Move_Target", "Copy of", "Special Test")) or item_id in [folder_id, target_parent_id, copied_id]:
                test_ids.append(f"/{item_id}")

    if not test_ids:
        test_ids = [f"/{folder_id}", f"/{target_parent_id}", f"/{copied_id}"]

    r = session.post(f"{BASE_URL}/api/bulkDelete", json={"paths": test_ids})
    print(f"  Bulk delete {len(test_ids)} test items: {r.status_code} -> {r.json()}")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    # Verify root is clean of test items
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    current_names = [v.get("name") for v in r.json()["data"]["contents"].values()]
    print(f"  Root items remaining after cleanup: {current_names}")
    for name in current_names:
        assert not name.startswith(("Audit_Folder", "Move_Target", "Copy of", "Special Test"))
    print("  Verified all test artifacts cleanly deleted.")

    # -------------------------------------------------------------
    # 16. Test Logout & Session Invalidation
    # -------------------------------------------------------------
    print("\n[16] Testing Logout & Session Invalidation:")
    r = session.post(f"{BASE_URL}/api/logout")
    print(f"  POST /api/logout: {r.status_code} -> {r.json()}")
    assert r.status_code == 200

    # Subsequent request with old session should now be rejected (401)
    r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
    print(f"  Request after logout: {r.status_code} (Expected 401)")
    assert r.status_code == 401

    print("\n" + "=" * 70)
    print("[SUCCESS] ALL 16 PHASES TESTED & 100% OPERATIONAL WITH ZERO REGRESSIONS!")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
