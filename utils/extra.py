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
    elif mime_type.startswith("image/") or ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".ico"]:
        category = "Image"
        icon = "image"
    elif mime_type.startswith("video/") or ext in [".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".wmv"]:
        category = "Video"
        icon = "video"
    elif mime_type.startswith("audio/") or ext in [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus"]:
        category = "Audio"
        icon = "audio"
    elif ext in [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"]:
        category = "Archive"
        icon = "archive"
    elif ext in [".py", ".js", ".html", ".css", ".json", ".xml", ".sh", ".bat", ".ps1", ".cpp", ".c", ".java", ".rs", ".go", ".ts", ".yaml", ".yml", ".md"]:
        category = "Source Code"
        icon = "code"
    elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".csv"]:
        category = "Office Document"
        icon = "doc"
    
    return mime_type, category, ext, icon


def convert_class_to_dict(data, isObject, showtrash=False):
    if isObject == True:
        data = data.__dict__.copy()
    new_data = {"contents": {}}

    for key in data["contents"]:
        item = data["contents"][key]
        if item.trash == showtrash:
            if item.type == "folder":
                folder = item
                new_data["contents"][key] = {
                    "name": folder.name,
                    "type": folder.type,
                    "id": folder.id,
                    "path": folder.path,
                    "category": "Folder",
                    "mime_type": "inode/directory",
                    "extension": "",
                    "owner": getattr(folder, "owner", "Admin"),
                    "upload_date": getattr(folder, "upload_date", ""),
                }
            else:
                file = item
                mime_type, category, ext, icon = get_file_details(file.name)
                new_data["contents"][key] = {
                    "name": file.name,
                    "type": file.type,
                    "size": file.size,
                    "id": file.id,
                    "file_id": getattr(file, "file_id", 0),
                    "path": file.path,
                    "category": category,
                    "mime_type": mime_type,
                    "extension": ext,
                    "icon": icon,
                    "owner": getattr(file, "owner", "Admin"),
                    "upload_date": getattr(file, "upload_date", ""),
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
        if item.is_file() and not item.name.endswith(".session") and not item.name.endswith(".session-journal") and item.name != "drive.data":
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
