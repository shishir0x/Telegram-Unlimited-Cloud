import asyncio
import os
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message
from config import STORAGE_CHANNEL
from utils.clients import get_client
from utils.logger import Logger
from urllib.parse import unquote_plus

logger = Logger(__name__)
PROGRESS_CACHE = {}
STOP_TRANSMISSION = []


async def progress_callback(current, total, id, client: Client, file_path, filename=""):
    global PROGRESS_CACHE, STOP_TRANSMISSION

    PROGRESS_CACHE[id] = ("running", current, total, filename)
    if id in STOP_TRANSMISSION:
        logger.info(f"Stopping transmission {id}")
        client.stop_transmission()
        try:
            os.remove(file_path)
        except Exception:
            pass


async def start_file_uploader(
    file_path, id, directory_path, filename, file_size, delete=True, conflict="keep_both"
):
    global PROGRESS_CACHE
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    logger.info(f"Uploading file {file_path} {id} (conflict mode: {conflict})")

    try:
        if file_size > 1.98 * 1024 * 1024 * 1024:
            client: Client = get_client(premium_required=True)
        else:
            client: Client = get_client()

        PROGRESS_CACHE[id] = ("running", 0, file_size, filename)

        # Handle Telegram transmission with automatic FloodWait retry
        max_attempts = 3
        message = None
        for attempt in range(max_attempts):
            try:
                message: Message = await client.send_document(
                    STORAGE_CHANNEL,
                    file_path,
                    progress=progress_callback,
                    progress_args=(id, client, file_path, filename),
                    disable_notification=True,
                )
                break
            except FloodWait as fw:
                wait_time = fw.value + 1
                logger.warning(f"Telegram FloodWait encountered ({wait_time}s). Pausing upload attempt {attempt + 1}/{max_attempts}...")
                await asyncio.sleep(wait_time)
                # Try getting another client from pool
                try:
                    client = get_client(premium_required=(file_size > 1.98 * 1024 * 1024 * 1024))
                except Exception:
                    pass
            except Exception as send_err:
                if attempt == max_attempts - 1:
                    raise send_err
                logger.warning(f"Upload transmission retry {attempt + 1}: {send_err}")
                await asyncio.sleep(2)

        if not message:
            raise RuntimeError("Failed to obtain Telegram message confirmation after retries.")

        size = (
            message.photo
            or message.document
            or message.video
            or message.audio
            or message.sticker
        ).file_size

        filename = unquote_plus(filename)

        drive.new_file(directory_path, filename, message.id, size, conflict=conflict)
        PROGRESS_CACHE[id] = ("completed", size, size, filename)

        # Keep cache bounded to latest 200 upload items
        if len(PROGRESS_CACHE) > 200:
            excess = len(PROGRESS_CACHE) - 200
            for old_k in list(PROGRESS_CACHE.keys())[:excess]:
                PROGRESS_CACHE.pop(old_k, None)

        # Pre-generate 10KB thumbnail for instant browser rendering
        try:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "tiff"]:
                from PIL import Image
                thumb_cache_dir = os.path.join(".", "cache", "thumbs")
                os.makedirs(thumb_cache_dir, exist_ok=True)
                with Image.open(str(file_path)) as img:
                    img = img.convert("RGB")
                    img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                    img.save(os.path.join(thumb_cache_dir, f"{message.id}.jpg"), format="JPEG", quality=75, optimize=True)
        except Exception as e:
            logger.warning(f"Failed to pre-generate upload thumbnail for {filename}: {e}")

        logger.info(f"Uploaded file {file_path} {id}")
    except Exception as e:
        logger.error(f"Failed to upload file {file_path} {id}: {e}")
        PROGRESS_CACHE[id] = ("error", 0, file_size, filename)
    finally:
        if delete:
            try:
                os.remove(file_path)
            except Exception:
                pass
