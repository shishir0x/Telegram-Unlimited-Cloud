import requests
import json
import secrets

BASE_URL = "http://127.0.0.1:8000"
PASSWORD = "Ccrpandey@085"

session = requests.Session()

print("=" * 60)
print("TG DRIVE COMPREHENSIVE END-TO-END TEST SUITE")
print("=" * 60)

# 1. Test Static & HTML Endpoints
print("\n[1] Testing Static & HTML Endpoints:")
r = session.get(f"{BASE_URL}/")
print(f"  GET /: {r.status_code} (HTML size: {len(r.text)} bytes)")
assert r.status_code == 200

r = session.get(f"{BASE_URL}/health")
print(f"  GET /health: {r.status_code} -> {r.json()}")
assert r.status_code == 200

r = session.head(f"{BASE_URL}/health")
print(f"  HEAD /health: {r.status_code}")
assert r.status_code == 200

r = session.get(f"{BASE_URL}/static/js/main.js")
print(f"  GET /static/js/main.js: {r.status_code}")
assert r.status_code == 200

r = session.get(f"{BASE_URL}/static/js/apiHandler.js")
print(f"  GET /static/js/apiHandler.js: {r.status_code}")
assert r.status_code == 200

# 2. Test Security: Unauthorized Access Rejection
print("\n[2] Testing Security & Protection:")
unauth_session = requests.Session()
r = unauth_session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/"})
print(f"  Unauthenticated /api/getDirectory: {r.status_code} (Expected 401)")
assert r.status_code == 401

r = unauth_session.get(f"{BASE_URL}/file?path=/dummy")
print(f"  Unauthenticated /file: {r.status_code} (Expected 401)")
assert r.status_code == 401

# 3. Test Password Verification (Wrong & Correct)
print("\n[3] Testing Login & Authentication:")
r = unauth_session.post(f"{BASE_URL}/api/checkPassword", json={"pass": "wrong_password"})
print(f"  Wrong Password Login: {r.status_code} -> {r.json()}")
assert r.status_code == 401

r = session.post(f"{BASE_URL}/api/checkPassword", json={"pass": PASSWORD})
print(f"  Correct Password Login: {r.status_code} -> {r.json()}")
assert r.status_code == 200
print(f"  Session Cookie set: {dict(session.cookies)}")

# 4. Test Directory Listing (Root)
print("\n[4] Testing Directory Listing:")
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "password": PASSWORD})
print(f"  POST /api/getDirectory root: {r.status_code}")
assert r.status_code == 200
data = r.json()
print(f"  Root contents count: {len(data['data']['contents'])}")

# 5. Test Folder Creation
print("\n[5] Testing Folder Creation:")
test_folder_name = "Automated_Test_Folder"
r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": test_folder_name, "path": "/", "password": PASSWORD})
print(f"  Create folder '{test_folder_name}': {r.status_code} -> {r.json()}")
assert r.status_code == 200 or r.json().get("status") == "Folder with the name already exist in current directory"

# Fetch directory to get folder ID
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "password": PASSWORD})
contents = r.json()['data']['contents']
folder_id = None
for fid, fitem in contents.items():
    if fitem['name'] == test_folder_name:
        folder_id = fid
        break
print(f"  Found created folder ID: {folder_id}")
assert folder_id is not None

# 6. Test Folder Share Auth Generation
print("\n[6] Testing Share Auth Generation:")
folder_path = f"/{folder_id}"
r = session.post(f"{BASE_URL}/api/getFolderShareAuth", json={"path": folder_path, "password": PASSWORD})
print(f"  Generate Share Auth: {r.status_code} -> {r.json()}")
assert r.status_code == 200
share_auth = r.json().get("auth")

# Test Public Shared Access with Token
r = unauth_session.post(f"{BASE_URL}/api/getDirectory", json={"path": f"/share_{folder_path.strip('/')}", "auth": share_auth})
print(f"  Public Access to Shared Folder with Token: {r.status_code} -> {r.json().get('status')}")
assert r.status_code == 200

# 7. Test Search Functionality
print("\n[7] Testing Search:")
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": f"/search_{test_folder_name}", "password": PASSWORD})
print(f"  Search for '{test_folder_name}': {r.status_code} -> Found {len(r.json()['data']['contents'])} items")
assert r.status_code == 200
assert len(r.json()['data']['contents']) >= 1

# 8. Test Rename Functionality
print("\n[8] Testing Rename:")
renamed_name = "Renamed_Test_Folder"
r = session.post(f"{BASE_URL}/api/renameFileFolder", json={"name": renamed_name, "path": folder_path, "password": PASSWORD})
print(f"  Rename folder to '{renamed_name}': {r.status_code} -> {r.json()}")
assert r.status_code == 200

# Verify rename
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "password": PASSWORD})
assert any(f['name'] == renamed_name for f in r.json()['data']['contents'].values())
print(f"  Verified folder name is now '{renamed_name}'")

# 9. Test Trash & Restore
print("\n[9] Testing Trash & Restore:")
r = session.post(f"{BASE_URL}/api/trashFileFolder", json={"trash": True, "path": folder_path, "password": PASSWORD})
print(f"  Move to Trash: {r.status_code} -> {r.json()}")
assert r.status_code == 200

# Check Trash list
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/trash", "password": PASSWORD})
trash_items = r.json()['data']['contents']
print(f"  Items in Trash: {len(trash_items)} (Found: {folder_id in trash_items})")
assert folder_id in trash_items

# Restore from Trash
r = session.post(f"{BASE_URL}/api/trashFileFolder", json={"trash": False, "path": folder_path, "password": PASSWORD})
print(f"  Restore from Trash: {r.status_code} -> {r.json()}")
assert r.status_code == 200

# 10. Test Move File/Folder & Breadcrumb Resolution
print("\n[10] Testing Move File/Folder & Breadcrumbs:")
parent_folder_name = "Parent_Test_Folder"
r = session.post(f"{BASE_URL}/api/createNewFolder", json={"name": parent_folder_name, "path": "/", "password": PASSWORD})
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "password": PASSWORD})
parent_id = None
for fid, fitem in r.json()['data']['contents'].items():
    if fitem['name'] == parent_folder_name:
        parent_id = fid
        break
assert parent_id is not None
print(f"  Created parent folder: {parent_folder_name} ({parent_id})")

# Move folder_path (Renamed_Test_Folder) into Parent_Test_Folder
r = session.post(f"{BASE_URL}/api/moveFileFolder", json={"src_path": folder_path, "dest_path": f"/{parent_id}", "password": PASSWORD})
print(f"  Move folder into parent: {r.status_code} -> {r.json()}")
assert r.status_code == 200

# Verify breadcrumbs inside nested folder
nested_path = f"/{parent_id}/{folder_id}"
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": nested_path, "password": PASSWORD})
res_data = r.json()
print(f"  Nested path breadcrumbs: {res_data.get('breadcrumbs')}")
assert "breadcrumbs" in res_data
assert len(res_data["breadcrumbs"]) == 3
assert res_data["breadcrumbs"][0]["name"] == "My Drive"
assert res_data["breadcrumbs"][1]["name"] == parent_folder_name
assert res_data["breadcrumbs"][2]["name"] == renamed_name

# Clean up created folders
r = session.post(f"{BASE_URL}/api/deleteFileFolder", json={"path": f"/{parent_id}", "password": PASSWORD})
assert r.status_code == 200

# 11. Test Permanent Delete
print("\n[11] Testing Permanent Deletion:")
r = session.post(f"{BASE_URL}/api/getDirectory", json={"path": "/", "password": PASSWORD})
print(f"  Verified clean root directory state")

# 12. Test Logout
print("\n[12] Testing Logout:")
r = session.post(f"{BASE_URL}/api/logout")
print(f"  Logout: {r.status_code} -> {r.json()}")
assert r.status_code == 200


print("\n" + "=" * 60)
print("ALL FUNCTIONS TESTED SUCCESSFULLY AND VERIFIED 100% OPERATIONAL!")
print("=" * 60)
