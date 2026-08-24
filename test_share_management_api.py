"""
Automated Integration Test for Shared Links Management API
Tests:
1. /api/share/list returns list of created shares with all attributes.
2. /api/share/revoke revokes an active link immediately.
3. /api/share/regenerate creates a new token and revokes the old one.
"""
import os
os.environ.setdefault("TESTING", "1")

from fastapi.testclient import TestClient
import main
from utils.auth import SESSION_COOKIE_NAME, create_session
from utils.directoryHandler import ensure_drive_data, File

client = TestClient(main.app)
token = create_session(ip="testclient")
client.cookies.set(SESSION_COOKIE_NAME, token)

def test_shared_links_management_flow():
    drive = ensure_drive_data()
    root = drive.contents["/"]
    
    # Ensure test file exists in drive
    f_test = File("manage_test_file.txt", 999999, 1024, "/")
    root.contents[f_test.id] = f_test

    # 1. Create a share
    res = client.post("/api/share/create", json={
        "target": f"/{f_test.id}",
        "expires_in_hours": 168,
        "allow_download": True,
        "allow_preview": True
    })
    assert res.status_code == 200, res.text
    share_token = res.json()["share"]["token"]

    # 2. List shares
    res_list = client.post("/api/share/list", json={})
    assert res_list.status_code == 200, res_list.text
    shares = res_list.json()["shares"]
    assert any(s["token"] == share_token for s in shares)

    target_share = next(s for s in shares if s["token"] == share_token)
    assert target_share["name"] == "manage_test_file.txt"
    assert target_share["type"] == "file"
    assert target_share["revoked"] is False
    assert target_share["expires_at"] is not None

    # 3. Revoke share
    res_revoke = client.post("/api/share/revoke", json={"token": share_token})
    assert res_revoke.status_code == 200, res_revoke.text
    assert res_revoke.json()["status"] == "ok"

    # Verify public access returns 410 Gone
    res_pub_meta = client.post("/api/share/meta", json={"token": share_token})
    assert res_pub_meta.status_code == 410
    assert res_pub_meta.json().get("error") == "revoked"

    # 4. Regenerate share
    res_regen = client.post("/api/share/regenerate", json={"token": share_token})
    assert res_regen.status_code == 200, res_regen.text
    new_share = res_regen.json()["share"]
    new_token = new_share["token"]
    assert new_token != share_token
    assert new_share["revoked"] is False

    # Verify new public access works
    res_new_pub = client.get(f"/s/{new_token}")
    assert res_new_pub.status_code == 200

    # 5. Update share settings
    res_update = client.post("/api/share/update", json={
        "token": new_token,
        "expires_in_hours": 48,
        "password": "SecretPassword123",
        "allow_download": False,
        "allow_preview": True
    })
    assert res_update.status_code == 200, res_update.text
    updated_share = res_update.json()["share"]
    assert updated_share["has_password"] is True
    assert updated_share["allow_download"] is False
    assert updated_share["allow_preview"] is True

    # 6. Verify updated password protection
    res_meta_locked = client.post("/api/share/meta", json={"token": new_token})
    assert res_meta_locked.status_code == 200
    assert res_meta_locked.json().get("status") == "locked"
    assert res_meta_locked.json().get("has_password") is True

    # Bad password returns 401
    res_unlock_bad = client.post("/api/share/unlock", json={"token": new_token, "password": "WrongPassword"})
    assert res_unlock_bad.status_code == 401

    # Correct password unlocks
    res_unlock_good = client.post("/api/share/unlock", json={"token": new_token, "password": "SecretPassword123"})
    assert res_unlock_good.status_code == 200

    # Verify unlocking with cookie allows metadata
    res_meta_unlocked = client.post("/api/share/meta", json={"token": new_token})
    assert res_meta_unlocked.status_code == 200
    assert res_meta_unlocked.json().get("status") == "ok"

    # 7. Test delete share
    res_del = client.post("/api/share/delete", json={"token": share_token})
    assert res_del.status_code == 200, res_del.text
    assert res_del.json()["status"] == "ok"

    # Verify deleted share is gone from list
    res_list2 = client.post("/api/share/list", json={})
    assert not any(s["token"] == share_token for s in res_list2.json()["shares"])

    # 8. Test clear_inactive
    # Revoke new_token
    client.post("/api/share/revoke", json={"token": new_token})
    res_clear = client.post("/api/share/clear_inactive", json={})
    assert res_clear.status_code == 200, res_clear.text
    assert res_clear.json()["status"] == "ok"
    assert res_clear.json()["deleted_count"] >= 1

    # Verify new_token is also cleaned
    res_list3 = client.post("/api/share/list", json={})
    assert not any(s["token"] == new_token for s in res_list3.json()["shares"])

    print("\n[PASS] Shared Links Management API (List, Revoke, Regenerate, Update, Delete, Clear Inactive) verified successfully!")

if __name__ == "__main__":
    test_shared_links_management_flow()

