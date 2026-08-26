"""
Comprehensive Automated Test Suite for Google Drive-Style Properties & Details System
"""
import io
import os
import sys
import time
import zipfile
from pathlib import Path
from PIL import Image

# Import properties modules
from utils.properties import (
    MetadataExtractor,
    FolderStatsCalculator,
    ActivityTracker,
    PropertiesFormatter,
    MetadataWorker
)
from utils.directoryHandler import Folder, File, NewDriveData


def test_metadata_extractor_image():
    print("Testing MetadataExtractor image parsing...")
    img = Image.new("RGB", (640, 480), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    test_file = Path("./cache/test_img.png")
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_bytes(buf.getvalue())

    meta = MetadataExtractor.extract_all(test_file, "image/png")
    assert meta.get("width") == 640, f"Expected width 640, got {meta.get('width')}"
    assert meta.get("height") == 480, f"Expected height 480, got {meta.get('height')}"
    assert "640" in str(meta.get("dimensions")) and "480" in str(meta.get("dimensions"))
    assert meta.get("mode") == "RGB"
    assert "sha256" in meta and len(meta["sha256"]) == 64
    if test_file.exists():
        test_file.unlink()
    print("  -> Image metadata extracted successfully.")


def test_metadata_extractor_pdf():
    print("Testing MetadataExtractor PDF parsing...")
    # Minimal PDF with 2 pages in trailer / catalog
    fake_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page >>\nendobj\n"
        b"4 0 obj\n<< /Type /Page >>\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \ntrailer\n<< /Root 1 0 R /Size 5 >>\nstartxref\n180\n%%EOF"
    )
    test_file = Path("./cache/test_doc.pdf")
    test_file.write_bytes(fake_pdf)

    meta = MetadataExtractor.extract_all(test_file, "application/pdf")
    assert meta.get("page_count") == 2, f"Expected 2 pages, got {meta.get('page_count')}"
    if test_file.exists():
        test_file.unlink()
    print("  -> PDF page count parsed successfully.")


def test_metadata_extractor_archive():
    print("Testing MetadataExtractor ZIP archive parsing...")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("file1.txt", "Hello World" * 50)
        zf.writestr("file2.py", "print('hello')")
    
    test_file = Path("./cache/test_archive.zip")
    test_file.write_bytes(buf.getvalue())

    meta = MetadataExtractor.extract_all(test_file, "application/zip")
    assert meta.get("archive_file_count") == 2, f"Expected 2 entries, got {meta.get('archive_file_count')}"
    assert meta.get("archive_uncompressed_size") > 0
    if test_file.exists():
        test_file.unlink()
    print("  -> Archive TOC extracted safely without extracting files to disk.")


def test_folder_stats_calculator():
    print("Testing FolderStatsCalculator recursive statistics...")
    root = Folder("Root", "/")
    sub1 = Folder("Documents", f"/{root.id}")
    sub2 = Folder("Photos", f"/{root.id}")
    root.contents[sub1.id] = sub1
    root.contents[sub2.id] = sub2

    f1 = File("report.pdf", 101, 5000, f"/{root.id}/{sub1.id}")
    f2 = File("image.png", 102, 15000, f"/{root.id}/{sub2.id}")
    f3 = File("video.mp4", 103, 30000, f"/{root.id}/{sub2.id}")
    sub1.contents[f1.id] = f1
    sub2.contents[f2.id] = f2
    sub2.contents[f3.id] = f3

    stats = FolderStatsCalculator.calculate(root)
    assert stats["total_files"] == 3, f"Expected 3 files, got {stats['total_files']}"
    assert stats["total_folders"] == 2, f"Expected 2 subfolders, got {stats['total_folders']}"
    assert stats["total_size_bytes"] == 50000, f"Expected 50000 bytes, got {stats['total_size_bytes']}"
    assert stats["largest_file_name"] == "video.mp4"
    assert stats["largest_file_size"] == 30000
    assert stats["media_breakdown"]["images"] == 1
    assert stats["media_breakdown"]["videos"] == 1
    assert stats["media_breakdown"]["documents"] == 1

    # Invalidate cache
    FolderStatsCalculator.invalidate_cache(root.id)
    print("  -> Recursive folder statistics & media category breakdown computed successfully.")


def test_activity_tracker():
    print("Testing ActivityTracker event logging & throttling...")
    f = File("data.csv", 555, 100, "/")
    f.activity_history = []
    ActivityTracker.record_activity(f, "created", actor="Admin", details="File initialized")
    assert len(f.activity_history) == 1

    # High frequency actions should throttle within 5 minutes
    ActivityTracker.record_activity(f, "previewed", actor="Admin")
    assert len(f.activity_history) == 2
    ActivityTracker.record_activity(f, "previewed", actor="Admin") # Throttled
    assert len(f.activity_history) == 2, "Second preview within 5 mins should be throttled"

    flat_timeline = ActivityTracker.get_timeline(f)
    assert len(flat_timeline) == 2
    assert flat_timeline[0]["action"] == "previewed"
    assert flat_timeline[1]["action"] == "created"

    grouped_timeline = ActivityTracker.get_grouped_timeline(f)
    assert len(grouped_timeline) >= 1
    assert grouped_timeline[0]["date_label"] == "Today"
    assert len(grouped_timeline[0]["events"]) == 2
    print("  -> Activity history recorded and timeline grouped by relative dates.")


def test_properties_formatter():
    print("Testing PropertiesFormatter serialization schemas...")
    root_folder = Folder("/", "/")
    drive = NewDriveData({"/": root_folder}, [])
    root = drive.contents["/"]
    f = File("whitepaper.pdf", 999, 10240, "/")
    f.metadata_extra = {"page_count": 12, "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"}
    root.contents[f.id] = f

    file_props = PropertiesFormatter.get_file_properties(f, drive, is_admin=True, request_base_url="http://localhost:5000/")
    assert file_props["basic"]["id"] == f.id
    assert file_props["basic"]["name"] == "whitepaper.pdf"
    assert file_props["basic"]["extension"] == "pdf"
    assert file_props["content"]["page_count"] == 12
    assert file_props["checksums"]["sha256"].startswith("abcdef")
    assert "telegram_channel_id" in file_props["storage"]

    folder_props = PropertiesFormatter.get_folder_properties(root, drive, is_admin=True)
    assert folder_props["basic"]["id"] == "root"
    assert "folder_stats" in folder_props
    assert folder_props["folder_stats"]["total_files"] == 1

    # Security check: Non-admin properties should NOT expose telegram channel or internal server details
    public_file_props = PropertiesFormatter.get_file_properties(f, drive, is_admin=False)
    assert public_file_props["storage"].get("telegram_channel_id") is None
    print("  -> Properties formatted cleanly with verified security sanitization.")


def test_fastapi_properties_api_endpoints():
    print("Testing FastAPI properties and activity REST endpoints...")
    from fastapi.testclient import TestClient
    from main import app
    from utils.auth import create_session, SESSION_COOKIE_NAME
    from utils.directoryHandler import ensure_drive_data

    client = TestClient(app)
    drive = ensure_drive_data()
    root = drive.contents["/"]

    # Create a dummy test file in drive
    test_f = File("api_sample.txt", 8888, 2048, "/")
    test_f.metadata_extra = {"sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"}
    root.contents[test_f.id] = test_f

    # 1. Unauthenticated request without share token should return 401
    resp_unauth = client.get(f"/api/files/{test_f.id}/properties")
    assert resp_unauth.status_code == 401, f"Expected 401 unauth, got {resp_unauth.status_code}"

    # 2. Authenticated Admin request should return 200 with full schema
    session_token = create_session(ip="testclient")
    client.cookies.set(SESSION_COOKIE_NAME, session_token)

    resp_file = client.get(f"/api/files/{test_f.id}/properties")
    assert resp_file.status_code == 200, f"Expected 200 for file properties, got {resp_file.status_code}"
    file_data = resp_file.json()
    assert file_data["basic"]["name"] == "api_sample.txt"
    assert file_data["checksums"]["sha256"].startswith("123456")

    # 3. Folder properties endpoint
    resp_folder = client.get(f"/api/folders/root/properties")
    assert resp_folder.status_code == 200, f"Expected 200 for folder properties, got {resp_folder.status_code}"
    folder_data = resp_folder.json()
    assert folder_data["type"] == "folder"
    assert "folder_stats" in folder_data

    # 4. Activity endpoints
    resp_f_act = client.get(f"/api/files/{test_f.id}/activity")
    assert resp_f_act.status_code == 200
    assert "activity" in resp_f_act.json()

    resp_dir_act = client.get(f"/api/folders/root/activity")
    assert resp_dir_act.status_code == 200
    assert "activity" in resp_dir_act.json()

    # 5. Enrich endpoint
    resp_enrich = client.post("/api/properties/enrich", json={"id": test_f.id})
    assert resp_enrich.status_code == 200
    assert resp_enrich.json().get("status") == "ok"

    # Cleanup test file
    root.contents.pop(test_f.id, None)
    print("  -> FastAPI properties and activity REST endpoints tested and validated successfully.")


def run_all_tests():
    print("=" * 60)
    print("Running Google Drive-Style Properties & Details System Test Suite")
    print("=" * 60)
    test_metadata_extractor_image()
    test_metadata_extractor_pdf()
    test_metadata_extractor_archive()
    test_folder_stats_calculator()
    test_activity_tracker()
    test_properties_formatter()
    test_fastapi_properties_api_endpoints()
    print("=" * 60)
    print("ALL PROPERTIES SYSTEM TESTS PASSED SUCCESSFULLY! (7/7)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
