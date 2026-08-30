"""
test_archive_manager.py — Security & Functional Test Suite for Archive Manager
Telegram-Unlimited-Cloud

Tests all security guarantees:
1. Path traversal defense in inspect
2. Path traversal defense in extract
3. Zip bomb defense: max extracted size
4. Zip bomb defense: max file count
5. Zip bomb defense: suspicious compression ratio detection
6. Nesting depth limit enforcement
7. Normal extraction preserving folder tree and sizes
8. Extract selected members only
9. Safe conflict resolution without silent overwrite
10. Temporary sandbox isolation and cleanup
11. Download token lifecycle and path validation
"""

import os
import sys
import time
import shutil
import zipfile
import tempfile
import unittest
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.archive_manager import (
    ArchiveSecurity,
    ArchiveEntry,
    ArchiveManifest,
    ExtractResult,
    _sanitize_member_path,
    _nesting_depth,
    _keep_both_name,
    detect_format,
    inspect_archive,
    extract_archive,
    make_sandbox,
    cleanup_archive_temp,
    register_download_token,
    resolve_download_token,
    ARCHIVE_TEMP_DIR,
)


class TestArchiveManagerSecurity(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="tg_archive_test_"))
        self.sandboxes_to_clean = []

    def tearDown(self):
        for s in self.sandboxes_to_clean:
            cleanup_archive_temp(s)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_zip(self, name: str, files_dict: dict, compress_type=zipfile.ZIP_DEFLATED) -> Path:
        """Helper to create a zip file with arbitrary file names and contents."""
        zip_path = self.test_dir / name
        with zipfile.ZipFile(zip_path, "w", compression=compress_type) as zf:
            for arcname, data in files_dict.items():
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zf.writestr(arcname, data)
        return zip_path

    def test_01_path_traversal_sanitizer(self):
        """Verify _sanitize_member_path blocks traversal and normalization attacks."""
        self.assertIsNone(_sanitize_member_path("../../../evil.sh"))
        self.assertIsNone(_sanitize_member_path("..\\..\\evil.bat"))
        self.assertIsNone(_sanitize_member_path("/etc/passwd"))
        self.assertIsNone(_sanitize_member_path("C:\\Windows\\System32\\cmd.exe"))
        self.assertIsNone(_sanitize_member_path("a/../../../../root.txt"))
        self.assertIsNone(_sanitize_member_path("foo/\x00/bar.txt"))

        # Valid paths
        self.assertEqual(_sanitize_member_path("docs/readme.txt"), "docs/readme.txt")
        self.assertEqual(_sanitize_member_path("a/b/c/file.png"), "a/b/c/file.png")
        self.assertEqual(_sanitize_member_path("simple.txt"), "simple.txt")

    def test_02_path_traversal_blocked_in_inspect(self):
        """Verify malicious paths in zip are skipped during inspect without raising unhandled crash."""
        zip_path = self._create_zip("traversal_inspect.zip", {
            "valid/file.txt": "normal content",
            "../../escaped.txt": "evil content",
            "safe/../escaped2.txt": "escaped content",
        })

        manifest = inspect_archive(zip_path)
        extracted_paths = [e.path for e in manifest.entries]

        # Traversal members must NOT be included in entries
        for p in extracted_paths:
            self.assertFalse(p.startswith(".."))
            self.assertFalse("/../" in p)

    def test_03_path_traversal_blocked_in_extract(self):
        """Verify extracting an archive with traversal members never writes files outside the sandbox."""
        zip_path = self._create_zip("traversal_extract.zip", {
            "valid.txt": "valid content",
            "../../outside.txt": "should not be created outside",
            "sub/../../outside2.txt": "should not be created",
        })

        sandbox = make_sandbox()
        self.sandboxes_to_clean.append(sandbox)

        result = extract_archive(zip_path, member_paths=None, sandbox=sandbox)

        # All extracted files must reside strictly inside sandbox
        self.assertTrue(len(result.extracted_files) >= 1)
        for f in result.extracted_files:
            self.assertTrue(str(f.resolve()).startswith(str(sandbox.resolve())))

        # Outside file must not exist anywhere in test_dir or parent
        self.assertFalse((self.test_dir / "outside.txt").exists())
        self.assertFalse((sandbox.parent / "outside.txt").exists())
        self.assertTrue(len(result.skipped) >= 1)

    def test_04_zip_bomb_max_extracted_size(self):
        """Verify archive exceeding max_extract_size raises ValueError in inspect."""
        zip_path = self._create_zip("large_archive.zip", {
            "big1.bin": b"0" * (100 * 1024),
            "big2.bin": b"0" * (100 * 1024),
        })

        # Set max_extract_size to 150 KB (less than 200 KB total)
        sec = ArchiveSecurity(max_extract_size=150 * 1024)

        with self.assertRaises(ValueError) as ctx:
            inspect_archive(zip_path, security=sec)
        self.assertIn("exceeds safety limit", str(ctx.exception))

    def test_05_zip_bomb_max_file_count(self):
        """Verify archive with too many members raises ValueError in inspect."""
        files = {f"file_{i}.txt": f"content {i}" for i in range(25)}
        zip_path = self._create_zip("many_files.zip", files)

        sec = ArchiveSecurity(max_extract_files=10)

        with self.assertRaises(ValueError) as ctx:
            inspect_archive(zip_path, security=sec)
        self.assertIn("exceeds safety limit of 10 files", str(ctx.exception))

    def test_06_zip_bomb_suspicious_compression_ratio(self):
        """Verify member with huge compression ratio is flagged and rejected."""
        # 1 MB of repetitive zeroes compresses down to ~1 KB (>1000x ratio)
        large_zeros = b"A" * (1024 * 1024)
        zip_path = self._create_zip("ratio_bomb.zip", {
            "bomb.txt": large_zeros,
        })

        # Limit ratio to 50x
        sec = ArchiveSecurity(max_ratio=50)

        with self.assertRaises(ValueError) as ctx:
            inspect_archive(zip_path, security=sec)
        self.assertIn("zip-bomb suspected", str(ctx.exception))

    def test_07_max_nesting_depth_enforced(self):
        """Verify deeply nested directories beyond max_nesting_depth are rejected."""
        deep_path = "/".join([f"d{i}" for i in range(20)]) + "/deep.txt"
        zip_path = self._create_zip("deep.zip", {
            deep_path: "deep file content",
        })

        sec = ArchiveSecurity(max_nesting_depth=10)

        with self.assertRaises(ValueError) as ctx:
            inspect_archive(zip_path, security=sec)
        self.assertIn("exceeds safety limit", str(ctx.exception))

    def test_08_safe_extraction_hierarchy_and_sizes(self):
        """Verify a normal archive extracts its complete tree, hierarchy and sizes accurately."""
        files = {
            "root.txt": "hello root",
            "Photos/2026/Jan/img1.jpg": "image 1 content",
            "Photos/2026/Jan/img2.jpg": "image 2 content",
            "Photos/2026/notes.txt": "notes content",
        }
        zip_path = self._create_zip("normal.zip", files)

        manifest = inspect_archive(zip_path)
        self.assertEqual(manifest.format, "zip")
        self.assertEqual(manifest.total_files, 4)
        self.assertTrue(manifest.total_size > 0)

        # Check tree structure
        self.assertTrue(len(manifest.tree) >= 1)

        # Extract all
        sandbox = make_sandbox()
        self.sandboxes_to_clean.append(sandbox)

        result = extract_archive(zip_path, member_paths=None, sandbox=sandbox)
        self.assertEqual(len(result.extracted_files), 4)

        # Verify on-disk presence and content
        extracted_rel = [str(f.relative_to(sandbox)).replace("\\", "/") for f in result.extracted_files]
        self.assertIn("root.txt", extracted_rel)
        self.assertIn("Photos/2026/Jan/img1.jpg", extracted_rel)
        self.assertIn("Photos/2026/Jan/img2.jpg", extracted_rel)
        self.assertIn("Photos/2026/notes.txt", extracted_rel)

        with open(sandbox / "Photos/2026/Jan/img1.jpg", "r") as f:
            self.assertEqual(f.read(), "image 1 content")

    def test_09_extract_selected_files_only(self):
        """Verify extracting specific members extracts only requested items."""
        files = {
            "include1.txt": "include 1",
            "include2.txt": "include 2",
            "ignore.txt": "ignore me",
            "sub/ignore2.txt": "ignore me 2",
        }
        zip_path = self._create_zip("selected.zip", files)

        sandbox = make_sandbox()
        self.sandboxes_to_clean.append(sandbox)

        result = extract_archive(zip_path, member_paths=["include1.txt", "include2.txt"], sandbox=sandbox)
        self.assertEqual(len(result.extracted_files), 2)
        extracted_names = [f.name for f in result.extracted_files]
        self.assertIn("include1.txt", extracted_names)
        self.assertIn("include2.txt", extracted_names)
        self.assertNotIn("ignore.txt", extracted_names)

    def test_10_conflict_resolution_keep_both(self):
        """Verify _keep_both_name generates unique names without silent overwrites."""
        target = self.test_dir / "report.pdf"
        target.write_text("first version")

        copy1 = _keep_both_name(target)
        self.assertEqual(copy1.name, "report (1).pdf")
        copy1.write_text("second version")

        copy2 = _keep_both_name(target)
        self.assertEqual(copy2.name, "report (2).pdf")

    def test_11_sandbox_cleanup_and_temp_isolation(self):
        """Verify sandbox cleanup removes temp dir safely and doesn't delete outside."""
        sandbox = make_sandbox()
        test_file = sandbox / "test.txt"
        test_file.write_text("sandbox file")
        self.assertTrue(sandbox.exists())
        self.assertTrue(test_file.exists())

        cleanup_archive_temp(sandbox)
        self.assertFalse(sandbox.exists())

        # Calling cleanup on non-existent or external path shouldn't crash
        external_dir = self.test_dir / "external"
        external_dir.mkdir()
        cleanup_archive_temp(external_dir)
        # External dir outside ARCHIVE_TEMP_DIR must NOT be deleted
        self.assertTrue(external_dir.exists())

    def test_12_download_token_lifecycle_and_validation(self):
        """Verify short-lived download token registration, lookup, and path resolution."""
        sandbox = make_sandbox()
        self.sandboxes_to_clean.append(sandbox)

        f1 = sandbox / "doc.txt"
        f1.write_text("document")

        file_map = {"doc.txt": f1}
        token = register_download_token(sandbox, file_map)
        self.assertIsNotNone(token)
        self.assertTrue(len(token) > 10)

        # Resolving valid member
        resolved = resolve_download_token(token, "doc.txt")
        self.assertEqual(resolved, f1)

        # Resolving missing member
        self.assertIsNone(resolve_download_token(token, "nonexistent.txt"))

        # Resolving traversal attempt in member parameter
        self.assertIsNone(resolve_download_token(token, "../../etc/passwd"))

        # Resolving with invalid token
        self.assertIsNone(resolve_download_token("invalid_token_123", "doc.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
