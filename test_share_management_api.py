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

    res_pub_stream = client.get(f"/share/{share_token}/file")
    assert res_pub_stream.status_code == 410

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

    print("\n[PASS] Shared Links Management API flow verified successfully!")

if __name__ == "__main__":
    test_shared_links_management_flow()
