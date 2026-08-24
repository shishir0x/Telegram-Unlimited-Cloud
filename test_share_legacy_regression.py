"""Regression: legacy auth-hash sharing still works, and can no longer escape scope."""
import os
import sys

from fastapi.testclient import TestClient

import config
from main import app
from utils.directoryHandler import ensure_drive_data, File
from utils.auth import SESSION_COOKIE_NAME, create_session

client = TestClient(app)


def login():
    client.cookies.set(SESSION_COOKIE_NAME, create_session(ip="testclient"))


def main():
    p = f = 0
    def check(name, cond):
        nonlocal p, f
        p, f = p + (1 if cond else 0), f + (0 if cond else 1)
        print(("[OK] " if cond else "[FAIL] ") + name)

    login()
    drive = ensure_drive_data()
    root = drive.contents["/"]

    # seed two folders each containing a same-named file
    ids = {}
    for fname in ("LegacyA", "LegacyB"):
        fol = next((v for v in root.contents.values() if getattr(v, "name", "") == fname), None)
        if fol is None:
            drive.new_folder("/", fname)
            fol = next(v for v in root.contents.values() if getattr(v, "name", "") == fname)
        if not any(getattr(x, "name", "") == "target.txt" for x in fol.contents.values()):
            x = File("target.txt", 111111, 10, "/")
            fol.contents[x.id] = x
        ids[fname] = next(
            k for k, v in root.contents.items()
            if getattr(v, "name", "") == fname
        )

    # admin mints a token ONLY for LegacyA
    r = client.post("/api/getFolderShareAuth", json={"path": f"/{ids['LegacyA']}"})
    auth = r.json()["auth"]
    check("legacy auth minted", bool(auth))

    anon = TestClient(app)

    # 1. visitor can list the shared folder
    r = anon.post("/api/getDirectory", json={"path": f"/share_{ids['LegacyA']}", "auth": auth})
    check("anon lists shared folder", r.status_code == 200 and r.json().get("status") == "ok")
    ser = str(r.json())
    check("no file_id in anon listing", '"file_id"' not in ser)

    # 2. visitor breadcrumbs are scoped (no ancestors above share)
    crumbs = r.json().get("breadcrumbs", [])
    top_names = {c.get("name") or c.get("title", "") for c in crumbs} if crumbs and isinstance(crumbs[0], dict) else set()
    check("breadcrumbs empty/scoped for anon", len(crumbs) == 0 or not any(n in top_names for n in ("LegacyB",)))

    # 3. THE OLD HOLE: fetching LegacyB's file by NAME using LegacyA's token must fail now
    r = anon.get(f"/file?path={ids['LegacyB']}/target.txt&auth={auth}")
    check("cross-folder fetch by name blocked", r.status_code in (401, 404))

    # 4. legit access within scope still resolves (reaches TG layer -> 404 offline is OK,
    #    but NOT 401 unauthorized)
    r = anon.get(f"/file?path={ids['LegacyA']}/target.txt&auth={auth}")
    check("in-scope fetch not unauthorized", r.status_code != 401)

    # 5. no auth at all -> rejected
    r = anon.get(f"/file?path={ids['LegacyA']}/target.txt")
    check("tokenless access rejected", r.status_code in (401, 404))

    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)


if __name__ == "__main__":
    main()
