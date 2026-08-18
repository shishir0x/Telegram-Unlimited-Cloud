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
    if WEBSITE_URL is not None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(WEBSITE_URL) as response:
                        if response.status == 200:
                            logger.info(f"Pinged website at {get_current_utc_time()}")
                        else:
                            logger.warning(f"Failed to ping website: {response.status}")
                except Exception as e:
                    logger.warning(f"Failed to ping website: {e}")

                await asyncio.sleep(60)  # Ping website every minute


import shutil


def reset_cache_dir():
    cache_dir = Path("./cache")
    downloads_dir = Path("./downloads")
    shutil.rmtree(cache_dir, ignore_errors=True)
    shutil.rmtree(downloads_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Cache and downloads directory reset")


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
