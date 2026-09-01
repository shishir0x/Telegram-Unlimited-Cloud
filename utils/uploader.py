import asyncio
import os
import random
import time

from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message
from config import STORAGE_CHANNEL
from utils.clients import (
    get_client,
    multi_clients,
    premium_clients,
    work_loads,
    premium_work_loads,
)
from utils import tg_gate
from utils.logger import Logger
from urllib.parse import unquote_plus

logger = Logger(__name__)
PROGRESS_CACHE = {}
STOP_TRANSMISSION = []

MAX_SEND_ATTEMPTS = 5          # total attempts per file (FloodWait + transient errors)
TRANSIENT_BACKOFF = 2.0        # base backoff for non-FloodWait transmission errors


def _client_key(client) -> str:
    """Returns a consistent string identifier for client rate-limit tracking."""
    if isinstance(client, Client):
        if hasattr(client, "name") and client.name:
            return str(client.name)
        return str(id(client))
    return str(client)


async def progress_callback(current, total, id, client: Client, file_path, filename=""):
    global PROGRESS_CACHE, STOP_TRANSMISSION

    PROGRESS_CACHE[id] = ("running", current, total, filename)
    if id in STOP_TRANSMISSION:
        logger.info(f"Stopping transmission {id}")
        # Consume the flag so stale entries don't cancel future re-uploads
        try:
            STOP_TRANSMISSION.remove(id)
        except ValueError:
            pass
        client.stop_transmission()
        try:
            os.remove(file_path)
        except Exception:
            pass


def _pick_flood_safe_client(premium_required: bool):
    """
    Returns (client_or_None). Selects the least-loaded client whose
    per-client FloodWait cooldown has expired. Returns None when every
    candidate is mid-cooldown (caller waits on the global gate).
    Raises only when no clients are connected at all.
    """
    if premium_required and premium_clients:
        pool, loads = premium_clients, premium_work_loads
    elif multi_clients:
        pool, loads = multi_clients, work_loads
    elif premium_clients:
        pool, loads = premium_clients, premium_work_loads
    else:
        raise RuntimeError("No active Telegram clients are currently connected. Please verify bot tokens in .env.")

    usable = [
        (cid, cl) for cid, cl in pool.items()
        if tg_gate.client_available(_client_key(cl)) and tg_gate.client_available(cid)
    ]
    if not usable:
        return None  # every candidate is mid-cooldown

    cid, client = min(usable, key=lambda item: loads.get(item[0], 0))
    loads[cid] = loads.get(cid, 0) + 1
    return client


async def _wait_for_any_client() -> None:
    """Sleep until at least one client exits its FloodWait cooldown."""
    all_clients = list({**multi_clients, **premium_clients}.values())
    all_keys = [_client_key(cl) for cl in all_clients] + list({**multi_clients, **premium_clients}.keys())
    wake = tg_gate.next_client_wake(all_keys)
    remaining = wake - time.monotonic()
    if remaining > 0:
        logger.info(f"All Telegram clients in cooldown; resuming in {remaining:.1f}s")
        await asyncio.sleep(min(remaining + 0.3, tg_gate.MAX_GLOBAL_WAIT))


async def _send_with_flood_protection(
    client_ref: dict,
    file_path,
    id,
    filename,
    premium_required: bool,
    was_cancelled: asyncio.Event,
) -> Message | None:
    """
    Sends one document with full FloodWait immunity:
      - bounded concurrency via the shared gate
      - adaptive spacing + jitter before every attempt
      - flooded clients skipped; if all are flooded, sleeps out the cooldown
      - FloodWait feeds the global gate so OTHER parallel uploads pause too
    The selected client is written into client_ref['client'] so callers can
    cancel/stop the right transmission.
    """
    last_error = None

    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        if id in STOP_TRANSMISSION or was_cancelled.is_set():
            return None

        client = _pick_flood_safe_client(premium_required)
        while client is None:
            # Every connected client is mid-FloodWait: sleep out the cooldown.
            await _wait_for_any_client()
            if id in STOP_TRANSMISSION or was_cancelled.is_set():
                return None
            client = _pick_flood_safe_client(premium_required)
        client_ref["client"] = client

        try:
            c_key = _client_key(client)
            async with tg_gate.send_slot(client_key=c_key):
                if id in STOP_TRANSMISSION or was_cancelled.is_set():
                    return None
                message: Message = await client.send_document(
                    STORAGE_CHANNEL,
                    file_path,
                    progress=progress_callback,
                    progress_args=(id, client, file_path, filename),
                    disable_notification=True,
                )
            tg_gate.note_success(client_key=c_key)
            return message

        except FloodWait as fw:
            wait_time = float(fw.value)
            c_key = _client_key(client)
            # Check if alternative client can immediately take over
            has_alt = (_pick_flood_safe_client(premium_required) is not None)
            logger.warning(
                f"Telegram FloodWait {wait_time:.0f}s on client {getattr(client, 'name', c_key)} (upload {id}, "
                f"attempt {attempt}/{MAX_SEND_ATTEMPTS}). Alternate available: {has_alt}"
            )
            tg_gate.note_flood(c_key, wait_time, has_alternatives=has_alt)
            PROGRESS_CACHE[id] = ("waiting", 0, PROGRESS_CACHE.get(id, (0, 0, 0, ""))[2], filename)

            # Rotate to a different client on the next attempt
            client_ref["client"] = None
            if has_alt:
                # Immediate retry on next available client
                continue
            else:
                await asyncio.sleep(min(wait_time + 0.5, 60.0))
            continue

        except Exception as send_err:
            last_error = send_err
            if attempt == MAX_SEND_ATTEMPTS:
                raise
            backoff = TRANSIENT_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(f"Upload {id} transient error (attempt {attempt}/{MAX_SEND_ATTEMPTS}): {send_err}. Retrying in {backoff:.1f}s")
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Upload {id}: giving up after {MAX_SEND_ATTEMPTS} attempts ({last_error})")


async def start_file_uploader(
    file_path, id, directory_path, filename, file_size, delete=True, conflict="keep_both"
):
    global PROGRESS_CACHE
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    logger.info(f"Uploading file {file_path} {id} (conflict mode: {conflict})")
    client_ref = {"client": None}
    cancelled = asyncio.Event()

    try:
        premium_required = file_size > 1.98 * 1024 * 1024 * 1024
        PROGRESS_CACHE[id] = ("running", 0, file_size, filename)

        message = await _send_with_flood_protection(
            client_ref, file_path, id, filename, premium_required, cancelled
        )

        if message is None:
            logger.info(f"Upload {id} cancelled by user.")
            PROGRESS_CACHE[id] = ("cancelled", 0, file_size, filename)
            return

        size = (
            message.photo
            or message.document
            or message.video
            or message.audio
            or message.sticker
        ).file_size

        filename = unquote_plus(filename)

        new_item_id = drive.new_file(directory_path, filename, message.id, size, conflict=conflict)

        # Record change event for sync engine
        try:
            from utils.sync import record_change_async
            await record_change_async("FILE_CREATED", new_item_id, "file")
        except Exception as sync_err:
            logger.debug(f"Sync tracking note (upload): {sync_err}")

        # Extract rich properties (sha256, dimensions, codecs, pages) before temp file deletion
        try:
            from utils.properties import MetadataWorker
            new_file_obj = drive.find_item_by_id(new_item_id)
            if new_file_obj:
                MetadataWorker._process_item(new_file_obj, file_path)
                drive.save()
        except Exception as meta_err:
            logger.debug(f"Post-upload metadata extraction note: {meta_err}")

        from utils.directoryHandler import backup_drive_data
        asyncio.create_task(backup_drive_data(loop=False))
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
