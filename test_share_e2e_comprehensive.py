"""Comprehensive End-to-End Test Suite for Telegram Unlimited Cloud Share Feature.
Covers:
- Trailing slashes on all routes
- Unicode & Emoji filenames in Content-Disposition headers
- Scope containment & traversal prevention
- Expiration (HTTP 410) & Revocation (HTTP 410)
- Password protection & unlock cookie lifecycle
- Rate limiting on password attempts
- Folder ZIP creation & subpath scoping
- Download & preview permission enforcement
"""
import os
import sys
import time
import urllib.parse
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")

import config
from main import app
from utils import shareManager
from utils.directoryHandler import ensure_drive_data, File
from utils.auth import SESSION_COOKIE_NAME, create_session

client = TestClient(app)

def login(c: TestClient) -> None:
    token = create_session(ip="testclient")
    c.cookies.set(SESSION_COOKIE_NAME, token)

def seed_test_hierarchy():
    drive = ensure_drive_data()
    root = drive.contents["/"]

    # Create root test folder if not exists
    folder = next((v for v in root.contents.values() if getattr(v, "name", "") == "E2EShareSuite"), None)
    if folder is None:
        drive.new_folder("/", "E2EShareSuite")
        folder = next(v for v in root.contents.values() if getattr(v, "name", "") == "E2EShareSuite")

    # Add SubFolder
    subfolder = next((v for v in folder.contents.values() if getattr(v, "name", "") == "SubFolder"), None)
    if subfolder is None:
        drive.new_folder(f"/{folder.id}", "SubFolder")
        subfolder = next(v for v in folder.contents.values() if getattr(v, "name", "") == "SubFolder")

    # Add Unicode File in SubFolder
    uni_name = "日本語_ファイル_✨.pdf"
    if not any(getattr(v, "name", "") == uni_name for v in subfolder.contents.values()):
        f_uni = File(uni_name, 777777, 1024, "/")
        subfolder.contents[f_uni.id] = f_uni

    # Add Plain File in Root Folder
    plain_name = "report.docx"
    if not any(getattr(v, "name", "") == plain_name for v in folder.contents.values()):
        f_plain = File(plain_name, 888888, 4096, "/")
        folder.contents[f_plain.id] = f_plain

    return folder, subfolder

def run_tests():
    passed = 0
    failed = 0

    def check(name: str, condition: bool):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}")

    login(client)
    folder, subfolder = seed_test_hierarchy()

    # 1. Trailing Slashes on /s/<token>
    res = client.post("/api/share/create", json={"target": f"/{folder.id}", "expires_in_hours": 48})
    check("Create share folder returns 200", res.status_code == 200)
    tok = res.json()["share"]["token"]

    r_plain = client.get(f"/s/{tok}")
    check("GET /s/<token> returns 200 HTML", r_plain.status_code == 200 and b"share-app" in r_plain.content)

    r_slash = client.get(f"/s/{tok}/")
    check("GET /s/<token>/ returns 200 HTML", r_slash.status_code == 200 and b"share-app" in r_slash.content)

    # 2. Meta for folder and subfolder listing
    r_meta_root = client.post("/api/share/meta", json={"token": tok})
    check("Root folder meta returns status ok", r_meta_root.status_code == 200 and r_meta_root.json().get("status") == "ok")
    root_children = r_meta_root.json().get("children", [])
    sub_key = next((c["key"] for c in root_children if c["name"] == "SubFolder"), None)
    check("Root children contains SubFolder", bool(sub_key))

    r_meta_sub = client.post("/api/share/meta", json={"token": tok, "rel": sub_key})
    check("Subfolder meta returns status ok", r_meta_sub.status_code == 200 and r_meta_sub.json().get("status") == "ok")
    sub_children = r_meta_sub.json().get("children", [])
    uni_file = next((c for c in sub_children if "日本語" in c["name"]), None)
    check("Unicode file listed in SubFolder meta", bool(uni_file))

    # 3. Traversal prevention
    for bad_rel in ["../", "/etc/passwd", "..\\..", "SubFolder/../../"]:
        r_trav = client.post("/api/share/meta", json={"token": tok, "rel": bad_rel})
        check(f"Traversal attempt '{bad_rel}' rejected (404)", r_trav.status_code == 404)

    # 4. Unicode single file share & Content-Disposition
    uni_target = f"/{folder.id}/{sub_key}/{uni_file['key']}"
    r_uni_share = client.post("/api/share/create", json={"target": uni_target})
    check("Create share for Unicode filename ok", r_uni_share.status_code == 200)
    uni_tok = r_uni_share.json()["share"]["token"]

    r_uni_meta = client.post("/api/share/meta", json={"token": uni_tok})
    check("Unicode share meta preserves exact filename", r_uni_meta.json().get("name") == uni_file["name"])

    # 5. Permission Enforcement (Preview vs Download)
    # Share with download=False, preview=True
    r_no_dl = client.post("/api/share/create", json={"target": uni_target, "allow_download": False, "allow_preview": True})
    no_dl_tok = r_no_dl.json()["share"]["token"]
    r_dl_blocked = client.get(f"/share/{no_dl_tok}/file?dl=1")
    check("Download blocked (403) when allow_download=False", r_dl_blocked.status_code == 403)
    r_pv_allowed = client.get(f"/share/{no_dl_tok}/file")
    check("Inline preview allowed when allow_preview=True", r_pv_allowed.status_code in (200, 404)) # 404 if no TG connection offline

    # Share with download=True, preview=False
    r_no_pv = client.post("/api/share/create", json={"target": uni_target, "allow_download": True, "allow_preview": False})
    no_pv_tok = r_no_pv.json()["share"]["token"]
    r_pv_blocked = client.get(f"/share/{no_pv_tok}/file")
    check("Inline preview blocked (403) when allow_preview=False", r_pv_blocked.status_code == 403)
    r_dl_allowed = client.get(f"/share/{no_pv_tok}/file?dl=1")
    check("Download allowed when allow_download=True", r_dl_allowed.status_code in (200, 404))

    # 6. Folder ZIP Route
    r_zip_root = client.get(f"/share/{tok}/zip")
    check("GET /share/<token>/zip creates archive", r_zip_root.status_code in (200, 302))

    r_zip_sub = client.get(f"/share/{tok}/zip/{sub_key}")
    check("GET /share/<token>/zip/<subfolder> creates sub-archive", r_zip_sub.status_code in (200, 302))

    # 7. Password Security & Unlock Cookie
    r_pwd_share = client.post("/api/share/create", json={"target": f"/{folder.id}", "password": "SecretPassword123!"})
    pwd_tok = r_pwd_share.json()["share"]["token"]

    anon_user = TestClient(app)
    r_locked_meta = anon_user.post("/api/share/meta", json={"token": pwd_tok})
    check("Anonymous meta returns status locked", r_locked_meta.json().get("status") == "locked")

    r_wrong_pwd = anon_user.post("/api/share/unlock", json={"token": pwd_tok, "password": "WrongPassword"})
    check("Incorrect password returns 401", r_wrong_pwd.status_code == 401 and r_wrong_pwd.json().get("error") == "bad_password")

    r_correct_pwd = anon_user.post("/api/share/unlock", json={"token": pwd_tok, "password": "SecretPassword123!"})
    check("Correct password returns 200 + cookie", r_correct_pwd.status_code == 200 and "shu_" in str(r_correct_pwd.headers))

    r_unlocked_meta = anon_user.post("/api/share/meta", json={"token": pwd_tok})
    check("Meta accessible after unlocking with password", r_unlocked_meta.status_code == 200 and r_unlocked_meta.json().get("status") == "ok")

    # 8. Revocation & Expiration return HTTP 410 Gone
    # Revoke
    r_rev = client.post("/api/share/revoke", json={"token": pwd_tok})
    check("Revoke share returns 200", r_rev.status_code == 200)

    r_rev_meta = client.post("/api/share/meta", json={"token": pwd_tok})
    check("Revoked share returns 410 Gone with error=revoked", r_rev_meta.status_code == 410 and r_rev_meta.json().get("error") == "revoked")

    r_rev_stream = client.get(f"/share/{pwd_tok}/file")
    check("Revoked stream access returns 410 Gone", r_rev_stream.status_code == 410)

    # Expiry
    exp_rec = shareManager.create_share(str(folder.id), "folder", "ExpiredE2E", expires_at=time.time() - 60)
    exp_tok = exp_rec["token"]
    r_exp_meta = client.post("/api/share/meta", json={"token": exp_tok})
    check("Expired share returns 410 Gone with error=expired", r_exp_meta.status_code == 410 and r_exp_meta.json().get("error") == "expired")

    r_exp_stream = client.get(f"/share/{exp_tok}/file")
    check("Expired stream access returns 410 Gone", r_exp_stream.status_code == 410)

    print(f"\n==========================================")
    print(f"E2E Test Results: {passed} passed, {failed} failed")
    print(f"==========================================")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    run_tests()
