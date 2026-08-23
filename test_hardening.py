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

if __name__ == "__main__":
    test_password_hashing_and_verification()
    print("[OK] test_password_hashing_and_verification passed")
    test_path_sanitization()
    print("[OK] test_path_sanitization passed")
    test_checksum_calculation_and_verification()
    print("[OK] test_checksum_calculation_and_verification passed")
    test_new_file_conflict_resolution()
    print("[OK] test_new_file_conflict_resolution passed")
    print("\n[ALL HARDENING UNIT TESTS PASSED SUCCESSFULLY!]")
