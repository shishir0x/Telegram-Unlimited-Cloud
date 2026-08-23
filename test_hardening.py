"""
Unit tests for Security, Metadata Integrity, Conflict Handling, and Path Traversal Protections.
"""
import os
import shutil
import tempfile
from utils.auth import hash_password, verify_password, sanitize_path
from utils.directoryHandler import calculate_file_sha256, verify_file_checksum, NewDriveData

def test_password_hashing_and_verification():
    raw = "MySuperSecretPassword!2026"
    hashed = hash_password(raw)
    assert hashed.startswith("pbkdf2:sha256:"), "Hash must have pbkdf2 prefix"
    assert verify_password(raw, hashed) is True, "Valid password must verify"
    assert verify_password("WrongPassword", hashed) is False, "Invalid password must fail"
    assert verify_password(raw, raw) is True, "Plaintext legacy fallback must verify"

def test_path_sanitization():
    cases = [
        ("../../etc/passwd", "/etc/passwd"),
        ("..\\..\\windows\\system32", "/windows/system32"),
        ("//folder//subfolder//", "/folder/subfolder"),
        ("folder\x00/evil", "/folder/evil"),
        ("normal/path/test", "/normal/path/test"),
        ("/", "/"),
        ("", "/"),
        ("///", "/"),
        ("a/b/../c", "/a/c"),
    ]
    for raw, expected in cases:
        sanitized = sanitize_path(raw)
        assert sanitized == expected, f"Failed for {raw}: got {sanitized}, expected {expected}"

def test_checksum_calculation_and_verification():
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(b"Telegram Unlimited Cloud Test Data")
        tf_path = tf.name

    try:
        sha = calculate_file_sha256(tf_path)
        assert len(sha) == 64, "SHA256 must be 64 hex characters"
        assert verify_file_checksum(tf_path, sha) is True
        assert verify_file_checksum(tf_path, "invalidsha" * 6) is False
    finally:
        if os.path.exists(tf_path):
            os.remove(tf_path)

def test_new_file_conflict_resolution():
    from utils.directoryHandler import Folder
    root_folder = Folder("/", "/")
    drive = NewDriveData(contents={"/": root_folder}, used_ids=["root"])
    drive.save = lambda: None  # Mock save so unit test never writes to production drive.data
    folder_path = drive.new_folder("/", "TestFolder")
    folder_id = folder_path.strip("/").split("/")[-1]
    target_folder = drive.contents["/"].contents[folder_id]

    # 1. First file
    file_id_1 = drive.new_file(f"/{folder_id}", "report.pdf", 101, 1000)
    assert file_id_1 in target_folder.contents
    assert target_folder.contents[file_id_1].name == "report.pdf"

    # 2. Add with conflict='keep_both' -> auto-numbering
    file_id_2 = drive.new_file(f"/{folder_id}", "report.pdf", 102, 2000, conflict="keep_both")
    assert file_id_2 in target_folder.contents
    assert target_folder.contents[file_id_2].name == "report (1).pdf"

    # 3. Add again with conflict='keep_both' -> auto-numbering (2)
    file_id_3 = drive.new_file(f"/{folder_id}", "report.pdf", 103, 3000, conflict="keep_both")
    assert file_id_3 in target_folder.contents
    assert target_folder.contents[file_id_3].name == "report (2).pdf"

    # 4. Add with conflict='replace' -> replaces original report.pdf
    file_id_4 = drive.new_file(f"/{folder_id}", "report.pdf", 104, 5000, conflict="replace")
    assert target_folder.contents[file_id_4].size == 5000
    assert target_folder.contents[file_id_4].file_id == 104
    assert file_id_4 == file_id_1, "Replaced file should update original object in-place"

def test_drive_search_engine():
    from utils.directoryHandler import Folder, File
    root_folder = Folder("/", "/")
    drive = NewDriveData(contents={"/": root_folder}, used_ids=["root"])
    drive.save = lambda: None  # Mock save

    # Create directory structure
    c_drive_path = drive.new_folder("/", "C_Drive")
    c_drive_id = c_drive_path.strip("/").split("/")[-1]
    
    docs_path = drive.new_folder(f"/{c_drive_id}", "Documents")
    docs_id = docs_path.strip("/").split("/")[-1]

    photos_path = drive.new_folder(f"/{c_drive_id}", "Photos")
    photos_id = photos_path.strip("/").split("/")[-1]

    # Add diverse files
    f_pdf = drive.new_file(f"/{c_drive_id}/{docs_id}", "Financial_Report_2026.pdf", 201, 2 * 1024 * 1024)
    f_img = drive.new_file(f"/{c_drive_id}/{photos_id}", "Summer Vacation Tokyo.png", 202, 500 * 1024)
    f_code = drive.new_file(f"/{c_drive_id}/{docs_id}", "server_backup.py", 203, 10 * 1024)
    f_huge = drive.new_file(f"/{c_drive_id}/{docs_id}", "Archive_Full_Backup.zip", 204, 150 * 1024 * 1024)
    f_unicode = drive.new_file(f"/{c_drive_id}/{docs_id}", "Café Résumé 2026.docx", 205, 50 * 1024)

    # 1. Partial & Case-Insensitive Search
    res1 = drive.search_file_folder("financial")
    assert f_pdf in res1, "Should find Financial_Report_2026.pdf"
    assert res1[f_pdf].name == "Financial_Report_2026.pdf"

    # 2. Multi-token substring match
    res2 = drive.search_file_folder("summer tokyo")
    assert f_img in res2, "Should find Summer Vacation Tokyo.png"

    # 3. Unicode normalized search ('cafe' matching 'Café')
    res3 = drive.search_file_folder("cafe resume")
    assert f_unicode in res3, "Unicode normalized search should match 'Café Résumé'"

    # 4. Type filtering (PDF)
    res_pdf = drive.search_file_folder("", file_type="pdf")
    assert f_pdf in res_pdf
    assert f_img not in res_pdf
    assert f_code not in res_pdf

    # 5. Type filtering (Images)
    res_img = drive.search_file_folder("", file_type="image")
    assert f_img in res_img
    assert f_pdf not in res_img

    # 6. Type filtering (Folders)
    res_folders = drive.search_file_folder("", file_type="folder")
    assert c_drive_id in res_folders
    assert docs_id in res_folders
    assert f_pdf not in res_folders

    # 7. Size filtering (Huge > 100MB)
    res_huge = drive.search_file_folder("", min_size=100 * 1024 * 1024)
    assert f_huge in res_huge
    assert f_pdf not in res_huge

    # 8. Location scoping (only within Documents)
    res_scoped = drive.search_file_folder("", search_root=f"/{c_drive_id}/{docs_id}")
    assert f_pdf in res_scoped
    assert f_code in res_scoped
    assert f_img not in res_scoped, "Photos item should not be in scoped search"

    # 9. Inline query operator (type:code or ext:py)
    res_op = drive.search_file_folder("type:code server")
    assert f_code in res_op
    assert f_pdf not in res_op

    # 10. Provenance attached
    assert hasattr(res1[f_pdf], "display_path") or "display_path" in res1[f_pdf].__dict__

if __name__ == "__main__":
    test_password_hashing_and_verification()
    print("[OK] test_password_hashing_and_verification passed")
    test_path_sanitization()
    print("[OK] test_path_sanitization passed")
    test_checksum_calculation_and_verification()
    print("[OK] test_checksum_calculation_and_verification passed")
    test_new_file_conflict_resolution()
    print("[OK] test_new_file_conflict_resolution passed")
    test_drive_search_engine()
    print("[OK] test_drive_search_engine passed")
    print("\n[ALL HARDENING UNIT TESTS PASSED SUCCESSFULLY!]")
