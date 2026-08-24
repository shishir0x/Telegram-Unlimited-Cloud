import mimetypes
from urllib.parse import unquote_plus
import re
import urllib.parse
from pathlib import Path
from config import WEBSITE_URL
import asyncio, aiohttp
from utils.directoryHandler import get_current_utc_time, getRandomID
from utils.logger import Logger

logger = Logger(__name__)


def get_file_details(file_name: str):
    ext = Path(file_name).suffix.lower()
    mime_type, _ = mimetypes.guess_type(file_name)
    if not mime_type:
        mime_type = "application/octet-stream"

    category = "Document"
    icon = "file"
    if ext == ".pdf" or mime_type == "application/pdf":
        category = "PDF Document"
        icon = "pdf"
    elif mime_type.startswith("image/") or ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".ico", ".tiff", ".tif", ".heic", ".heif", ".avif", ".psd", ".ai"]:
        category = "Image"
        icon = "image"
    elif mime_type.startswith("video/") or ext in [".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".wmv", ".m4v", ".3gp", ".ogv", ".vob"]:
        category = "Video"
        icon = "video"
    elif mime_type.startswith("audio/") or ext in [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus", ".wma", ".mid", ".midi"]:
        category = "Audio"
        icon = "audio"
    elif ext in [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".dmg", ".tgz"]:
        category = "Archive"
        icon = "archive"
    elif ext in [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".json", ".xml", ".sh", ".bat", ".ps1", ".cpp", ".c", ".h", ".hpp", ".java", ".rs", ".go", ".php", ".rb", ".sql", ".yaml", ".yml", ".md", ".env", ".toml", ".ini"]:
        category = "Source Code"
        icon = "code"
    elif ext in [".doc", ".docx", ".rtf", ".odt"]:
        category = "Word Document"
        icon = "doc"
    elif ext in [".xls", ".xlsx", ".csv", ".tsv", ".ods"]:
        category = "Excel Spreadsheet"
        icon = "sheet"
    elif ext in [".ppt", ".pptx", ".odp", ".key"]:
        category = "PowerPoint Presentation"
        icon = "slide"
    elif ext in [".apk", ".exe", ".msi", ".app", ".deb", ".rpm"]:
        category = "Application"
        icon = "app"
    elif ext in [".txt", ".log", ".nfo"]:
        category = "Text Document"
        icon = "text"
    
    return mime_type, category, ext, icon


def compute_folder_stats(folder):
    """Recursively computes total size (bytes), file count, and subfolder count for a folder."""
    total_size = 0
    file_count = 0
    folder_count = 0
    contents = getattr(folder, "contents", None)
    if contents is None and isinstance(folder, dict):
        contents = folder.get("contents")
    if isinstance(contents, dict):
        for child in contents.values():
            is_trash = getattr(child, "trash", False) if not isinstance(child, dict) else child.get("trash", False)
            if is_trash:
                continue
            ctype = getattr(child, "type", "") if not isinstance(child, dict) else child.get("type", "")
            if ctype == "file":
                csize = getattr(child, "size", 0) if not isinstance(child, dict) else child.get("size", 0)
                try:
                    total_size += int(csize or 0)
                except (ValueError, TypeError):
                    pass
                file_count += 1
            elif ctype == "folder":
                folder_count += 1
                sub_size, sub_files, sub_folders = compute_folder_stats(child)
                total_size += sub_size
                file_count += sub_files
                folder_count += sub_folders
    return total_size, file_count, folder_count


def convert_class_to_dict(data, isObject, showtrash=False):
    if isObject == True:
        data = getattr(data, "__dict__", {}).copy() if hasattr(data, "__dict__") else (data.copy() if isinstance(data, dict) else {})
    new_data = {"contents": {}}

    raw_contents = data.get("contents", {}) if isinstance(data, dict) else getattr(data, "contents", {})
    if not isinstance(raw_contents, dict):
        return new_data

    for key, item in raw_contents.items():
        is_trash = bool(getattr(item, "trash", False) if not isinstance(item, dict) else item.get("trash", False))
        if is_trash == showtrash:
            item_type = getattr(item, "type", "file") if not isinstance(item, dict) else item.get("type", "file")
            item_name = getattr(item, "name", "Unnamed") if not isinstance(item, dict) else item.get("name", "Unnamed")
            item_id = getattr(item, "id", key) if not isinstance(item, dict) else item.get("id", key)
            item_path = getattr(item, "path", "/") if not isinstance(item, dict) else item.get("path", "/")
            item_date = getattr(item, "upload_date", "") if not isinstance(item, dict) else item.get("upload_date", "")
            item_tags = getattr(item, "tags", []) if not isinstance(item, dict) else item.get("tags", [])
            item_owner = getattr(item, "owner", "Admin") if not isinstance(item, dict) else item.get("owner", "Admin")
            item_device = getattr(item, "device", "") if not isinstance(item, dict) else item.get("device", "")
            item_display_path = getattr(item, "display_path", item_path) if not isinstance(item, dict) else item.get("display_path", item_path)
            item_human_path = getattr(item, "human_path", item_display_path) if not isinstance(item, dict) else item.get("human_path", item_display_path)

            if item_type == "folder":
                folder = item
                f_size, f_files, f_folders = compute_folder_stats(folder)
                new_data["contents"][key] = {
                    "name": item_name,
                    "type": "folder",
                    "id": item_id,
                    "size": f_size,
                    "file_count": f_files,
                    "folder_count": f_folders,
                    "path": item_path,
                    "display_path": item_display_path,
                    "human_path": item_human_path,
                    "device": item_device,
                    "category": "Folder",
                    "mime_type": "inode/directory",
                    "extension": "",
                    "tags": item_tags,
                    "owner": item_owner,
                    "upload_date": item_date,
                }
            else:
                file = item
                f_size = getattr(file, "size", 0) if not isinstance(file, dict) else file.get("size", 0)
                f_file_id = getattr(file, "file_id", 0) if not isinstance(file, dict) else file.get("file_id", 0)
                mime_type, category, ext, icon = get_file_details(item_name)
                new_data["contents"][key] = {
                    "name": item_name,
                    "type": "file",
                    "size": f_size,
                    "id": item_id,
                    "file_id": f_file_id,
                    "path": item_path,
                    "display_path": item_display_path,
                    "human_path": item_human_path,
                    "device": item_device,
                    "category": category,
                    "mime_type": mime_type,
                    "extension": ext,
                    "icon": icon,
                    "tags": item_tags,
                    "owner": item_owner,
                    "upload_date": item_date,
                }
    return new_data



async def auto_ping_website():
    if WEBSITE_URL is not None and WEBSITE_URL.strip():
        url = WEBSITE_URL.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        
        # Ensure we ping the health endpoint
        if not url.endswith("/health") and not url.endswith("/ping"):
            target_url = f"{url.rstrip('/')}/health"
        else:
            target_url = url

        headers = {"User-Agent": "TG-Drive-KeepAlive/1.0"}
        
        # Initial sleep before first ping to allow server startup
        await asyncio.sleep(10)
        
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                try:
                    async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        if response.status in [200, 301, 302]:
                            logger.info(f"Keep-alive ping success to {target_url} at {get_current_utc_time()}")
                        else:
                            logger.warning(f"Keep-alive ping returned status {response.status}")
                except Exception as e:
                    logger.warning(f"Keep-alive ping exception: {e}")

                await asyncio.sleep(240)  # Ping every 4 minutes to keep Render free tier awake


import shutil


def reset_cache_dir():
    cache_dir = Path("./cache")
    downloads_dir = Path("./downloads")
    
    # Clean downloads directory completely
    shutil.rmtree(downloads_dir, ignore_errors=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    
    # In cache directory, only remove temp chunks and files, preserve .session and drive.data files
    cache_dir.mkdir(parents=True, exist_ok=True)
    for item in cache_dir.iterdir():
        if item.is_file() and not item.name.endswith(".session") and not item.name.endswith(".session-journal") and item.name != "drive.data" and item.name != "auth_sessions.json":
            try:
                item.unlink(missing_ok=True)
            except Exception:
                pass
        elif item.is_dir() and item.name != "thumbs":
            shutil.rmtree(item, ignore_errors=True)
            
    (cache_dir / "thumbs").mkdir(parents=True, exist_ok=True)
    logger.info("Cache and downloads directory reset (sessions, drive.data and thumbs dir preserved)")


def parse_content_disposition(content_disposition):
    # Split the content disposition into parts
    parts = content_disposition.split(";")

    # Initialize filename variable
    filename = None

    # Loop through parts to find the filename
    for part in parts:
        part = part.strip()
        if part.startswith("filename="):
            # If filename is found
            filename = part.split("=", 1)[1]
        elif part.startswith("filename*="):
            # If filename* is found
            match = re.match(r"filename\*=(\S*)''(.*)", part)
            if match:
                encoding, value = match.groups()
                try:
                    filename = urllib.parse.unquote(value, encoding=encoding)
                except ValueError:
                    # Handle invalid encoding
                    pass

    if filename is None:
        raise Exception("Failed to get filename")
    return filename


def get_filename(headers, url):
    try:
        if headers.get("Content-Disposition"):
            filename = parse_content_disposition(headers["Content-Disposition"])
        else:
            filename = unquote_plus(url.strip("/").split("/")[-1])

        filename = filename.strip(' "')
    except:
        filename = unquote_plus(url.strip("/").split("/")[-1])

    filename = filename.strip()

    if filename == "" or "." not in filename:
        if headers.get("Content-Type"):
            extension = mimetypes.guess_extension(headers["Content-Type"])
            if extension:
                filename = f"{getRandomID()}{extension}"
            else:
                filename = getRandomID()
        else:
            filename = getRandomID()

    return filename
