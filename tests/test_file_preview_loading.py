import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from main import app
from utils.directoryHandler import ensure_drive_data, File, Folder
from utils.auth import create_session
from utils.zipper import create_zip_archive, cleanup_temp_zip


class TestFilePreviewAndLoading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.token = create_session(ip="testclient")
        cls.client.cookies.set("tg_session", cls.token)
        cls.drive = ensure_drive_data()

        # Create temporary mock directory and test files
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = cls.temp_dir.name

        # Create sample text file
        cls.sample_txt = os.path.join(cls.temp_path, "sample.txt")
        with open(cls.sample_txt, "wb") as f:
            f.write(b"Hello World! This is a test file for local file streaming and preview." * 10)

        # Create sample image file
        cls.sample_img = os.path.join(cls.temp_path, "sample_img.jpg")
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(cls.sample_img, "JPEG")

        # Mount into drive contents
        folder = Folder("TestDrive", "/TestDrive")
        folder.id = "testdrive_folder"
        cls.drive.contents["/TestDrive"] = folder
        cls.drive.contents["/"].contents["testdrive_folder"] = folder

        cls.txt_file = File(
            name="sample.txt",
            file_id=0,
            size=os.path.getsize(cls.sample_txt),
            path="/TestDrive"
        )
        cls.txt_file.id = "TEST_TXT_01"
        cls.txt_file.device = cls.temp_path
        cls.drive.contents["/TestDrive"].contents["TEST_TXT_01"] = cls.txt_file

        cls.img_file = File(
            name="sample_img.jpg",
            file_id=0,
            size=os.path.getsize(cls.sample_img),
            path="/TestDrive"
        )
        cls.img_file.id = "TEST_IMG_01"
        cls.img_file.device = cls.temp_path
        cls.drive.contents["/TestDrive"].contents["TEST_IMG_01"] = cls.img_file

    @classmethod
    def tearDownClass(cls):
        cls.drive.contents.pop("/TestDrive", None)
        cls.drive.contents["/"].contents.pop("testdrive_folder", None)
        cls.temp_dir.cleanup()

    def test_01_local_file_stream_and_headers(self):
        """Test streaming a local file returns 200 with proper headers."""
        test_file = self.drive.find_item_by_id("TEST_TXT_01")
        self.assertIsNotNone(test_file, "TEST_TXT_01 file must exist in drive data")

        resp = self.client.get(f"/file?path={test_file.path}/{test_file.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("content-length", resp.headers)
        self.assertEqual(resp.headers.get("accept-ranges"), "bytes")
        self.assertIn("etag", resp.headers)
        self.assertTrue(len(resp.content) > 0)

    def test_02_http_range_request(self):
        """Test RFC 7233 byte-range seeking for media/video playback."""
        test_file = self.drive.find_item_by_id("TEST_TXT_01")
        resp = self.client.get(
            f"/file?path={test_file.path}/{test_file.id}",
            headers={"Range": "bytes=0-49"}
        )
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(len(resp.content), 50)
        self.assertTrue(resp.headers.get("content-range", "").startswith("bytes 0-49/"))

    def test_03_human_readable_path_resolution(self):
        """Test resolving files by human path or path ID."""
        test_file = self.drive.find_item_by_id("TEST_TXT_01")
        resp = self.client.get(f"/file?path={test_file.path}/{test_file.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(int(resp.headers.get("content-length")), test_file.size)

    def test_04_etag_304_not_modified(self):
        """Test 304 Not Modified when client sends matching If-None-Match."""
        test_file = self.drive.find_item_by_id("TEST_TXT_01")
        resp = self.client.get(f"/file?path={test_file.path}/{test_file.id}")
        etag = resp.headers.get("etag")
        self.assertIsNotNone(etag)

        resp_304 = self.client.get(
            f"/file?path={test_file.path}/{test_file.id}",
            headers={"If-None-Match": etag}
        )
        self.assertEqual(resp_304.status_code, 304)

    def test_05_local_image_thumbnail_generation(self):
        """Test on-the-fly thumbnail generation from local image."""
        img_file = self.drive.find_item_by_id("TEST_IMG_01")
        self.assertIsNotNone(img_file)

        resp = self.client.get(f"/thumbnail?path={img_file.path}/{img_file.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("content-type"), "image/jpeg")
        self.assertTrue(len(resp.content) > 0)

    def test_06_zip_creation_with_local_files(self):
        """Test creating a ZIP archive with local files."""
        test_file = self.drive.find_item_by_id("TEST_TXT_01")
        lp = self.drive.resolve_local_file_path(test_file)
        self.assertTrue(os.path.isfile(lp))

        items = [{
            "file_id": 0,
            "file_name": test_file.name,
            "archive_path": test_file.name,
            "size": test_file.size,
            "local_path": lp
        }]

        async def run_zip():
            return await create_zip_archive(items, suggested_name="test_local_zip")

        zip_path, filename, size = asyncio.run(run_zip())
        try:
            self.assertTrue(os.path.isfile(zip_path))
            self.assertTrue(size > 0)
            self.assertEqual(filename, "test_local_zip.zip")
        finally:
            cleanup_temp_zip(zip_path)

    def test_07_unsynced_offline_file_handling(self):
        """Test that attempting to stream an unsynced offline file gives descriptive 404."""
        resp = self.client.get("/file?path=/NonExistentDrive/FakeFolder/ghost.txt")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
