"""End-to-end API tests for the Secure Share System (offline, no Telegram)."""
import os
import sys
import time

import requests  # noqa: F401  (kept for parity with other test files)
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")

import config  # noqa: E402
from main import app  # noqa: E402
from utils import shareManager  # noqa: E402
from utils.directoryHandler import ensure_drive_data, File  # noqa: E402
from utils.auth import SESSION_COOKIE_NAME, create_session  # noqa: E402

client = TestClient(app)  # no lifespan → fully offline


def login() -> None:
    ip = "testclient"
    token = create_session(ip=ip)
    client.cookies.set(SESSION_COOKIE_NAME, token)


def seed_drive():
    drive = ensure_drive_data()
    root = drive.contents["/"]
    folder = root.contents.get("ShareTestFolder")
    if folder is None:
        drive.new_folder("/", "ShareTestFolder")
        folder = next(
            f for f in root.contents.values()
            if getattr(f, "name", "") == "ShareTestFolder"
        )
        # a nested folder + file + a private sibling outside the share
        drive.new_folder(f"/{folder.id}", "Inner")
        inner = folder.contents[next(k for k, v in folder.contents.items() if getattr(v, 'name', '') == 'Inner')]
        secret = File("secret.txt", 999999, 42, "/")
        inner.contents[secret.id] = secret

    if "shared_photo.jpg" not in [getattr(v, "name", "") for v in folder.contents.values()]:
        f = File("shared_photo.jpg", 555555, 2048, "/")
        folder.contents[f.id] = f

    file_entry = next(v for v in folder.contents.values() if getattr(v, "name", "") == "shared_photo.jpg")
    return folder, file_entry


def main():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[OK] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}")

    login()
    folder, file_entry = seed_drive()

    # ── create folder share ────────────────────────────────────────────
    r = client.post("/api/share/create", json={"target": f"/{folder.id}", "expires_in_hours": 24})
    check("create folder share 200", r.status_code == 200)
    body = r.json()
    check("create returns url", body.get("status") == "ok" and "/s/" in body.get("url", ""))
    tok = body["share"]["token"]
    check("token entropy >= 40 chars", len(tok) >= 40)

    # ── public page serves ─────────────────────────────────────────────
    r = client.get(f"/s/{tok}")
    check("GET /s/<token> serves page", r.status_code == 200 and b"share-app" in r.content)

    # ── meta (unlocked, no password) ───────────────────────────────────
    r = client.post("/api/share/meta", json={"token": tok})
    m = r.json()
    check("meta ok for folder share", r.status_code == 200 and m.get("type") == "folder")
    names = {c["name"] for c in m.get("children", [])}
    check("children include Inner + photo", {"Inner", "shared_photo.jpg"} <= names)
    serialized = str(m)
    check("meta leaks no telegram msg id 555555/999999", "555555" not in serialized and "999999" not in serialized)

    # ── invalid token ──────────────────────────────────────────────────
    r = client.post("/api/share/meta", json={"token": "A" * 60})
    check("invalid token -> 404 invalid", r.status_code == 404 and r.json().get("error") == "invalid")

    # ── scope escape blocked ───────────────────────────────────────────
    r = client.post("/api/share/meta", json={"token": tok, "rel": "../../"})
    check("path traversal rel rejected (gone)", r.status_code == 404)

    # ── download without dl flag on previewable file (inline preview) ──
    photo_rel = next(c["key"] for c in m["children"] if c["name"] == "shared_photo.jpg")
    r = client.post("/api/share/meta", json={"token": tok, "rel": photo_rel})
    # navigating into a file key via meta should fail (files are leaves)
    check("meta on file-key under folder -> gone", r.status_code == 404)

    # permission gating on stream endpoint (no Telegram touched: 403 fires first)
    r = client.post("/api/share/create", json={"target": f"/{folder.id}/{photo_rel}", "allow_download": False})
    ftok = r.json()["share"]["token"]
    r = client.get(f"/share/{ftok}/file?dl=1")
    check("download blocked when allow_download=False", r.status_code == 403)
    r = client.get(f"/share/{ftok}/thumb")
    check("thumb allowed when preview allowed (may 404 w/o TG)", r.status_code in (200, 404))

    # ── password flow ──────────────────────────────────────────────────
    r = client.post("/api/share/create", json={"target": f"/{folder.id}", "password": "hunter2"})
    ptok = r.json()["share"]["token"]
    check("has_password true in admin payload", r.json()["share"]["has_password"] is True)

    r = client.post("/api/share/unlock", json={"token": ptok, "password": "wrong"})
    check("wrong password -> 401 bad_password", r.status_code == 401 and r.json()["error"] == "bad_password")
    unlock_resp = client.post("/api/share/unlock", json={"token": ptok, "password": "hunter2"})
    check("correct password -> ok + cookie", unlock_resp.status_code == 200 and "shu_" in unlock_resp.headers.get("set-cookie", ""))
    r = client.post("/api/share/meta", json={"token": ptok})
    check("meta unlocked after cookie", r.status_code == 200 and r.json().get("status") == "ok")

    # fresh client (no cookie) stays locked
    anon = TestClient(app)
    r = anon.post("/api/share/meta", json={"token": ptok})
    check("anon meta locked", r.json().get("status") == "locked")

    # ── expiry flow ────────────────────────────────────────────────────
    rec = shareManager.create_share(str(folder.id), "folder", "ExpiredTest", expires_at=time.time() - 5)
    r = client.post("/api/share/meta", json={"token": rec["token"]})
    check("expired link -> error expired", r.status_code == 404 and r.json()["error"] == "expired")

    # ── revoke & regenerate ────────────────────────────────────────────
    r = client.post("/api/share/create", json={"target": f"/{folder.id}"})
    rtok = r.json()["share"]["token"]
    r = client.post("/api/share/regenerate", json={"token": rtok})
    new_tok = r.json()["share"]["token"]
    check("regenerate issues new token", r.status_code == 200 and new_tok != rtok)
    r = client.post("/api/share/meta", json={"token": rtok})
    check("old token dead after regenerate (revoked)", r.status_code == 404 and r.json()["error"] == "revoked")
    r = client.post("/api/share/meta", json={"token": new_tok})
    check("new token live after regenerate", r.status_code == 200)

    r = client.post("/api/share/revoke", json={"token": new_tok})
    check("revoke ok", r.status_code == 200)
    r = client.post("/api/share/meta", json={"token": new_tok})
    check("revoked -> error revoked", r.status_code == 404 and r.json()["error"] == "revoked")

    # ── admin list & authz ─────────────────────────────────────────────
    r = client.post("/api/share/list", json={})
    check("admin list ok", r.status_code == 200 and len(r.json().get("shares", [])) >= 1)
    anon2 = TestClient(app)
    r = anon2.post("/api/share/create", json={"target": f"/{folder.id}"})
    check("anon create forbidden (401)", r.status_code == 401)
    r = anon2.post("/api/share/revoke", json={"token": tok})
    check("anon revoke forbidden (401)", r.status_code == 401)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
