import os
import time
import zipfile
import asyncio
import secrets
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Callable
from config import STORAGE_CHANNEL
from utils.clients import get_client
from utils.logger import Logger

logger = Logger(__name__)

TEMP_ZIP_DIR = Path("./cache/temp_zips")
TEMP_ZIP_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_temp_zip(zip_path: Path):
    """Background task to remove temporary ZIP file after transfer."""
    try:
        if zip_path.exists():
            zip_path.unlink(missing_ok=True)
            logger.info(f"Cleaned up temporary zip archive: {zip_path.name}")
    except Exception as e:
        logger.warning(f"Failed to delete temporary zip {zip_path}: {e}")


async def create_zip_archive(
    items: List[Dict],
    suggested_name: str = "Download",
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Tuple[Path, str, int]:
    """
    Downloads items from Telegram Storage Channel and packs them into a ZIP archive.
    
    Args:
        items: List of dicts with keys:
               - file_id (int): Telegram message ID
               - file_name (str): Original file name
               - archive_path (str): Relative path inside ZIP archive (e.g. 'Photos/2024/IMG_01.jpg')
               - size (int): File size in bytes
        suggested_name: Base name for the ZIP file (without extension)
        progress_callback: Optional callback(current_idx, total_items, current_filename)
        
    Returns:
        Tuple of (zip_file_path, sanitized_zip_filename, total_zip_size)
    """
    TEMP_ZIP_DIR.mkdir(parents=True, exist_ok=True)
    
    # Sanitize zip filename
    clean_name = "".join(c for c in suggested_name if c.isalnum() or c in (" ", "_", "-", "(", ")", ".")).strip()
    if not clean_name:
        clean_name = "Download"
    final_zip_filename = f"{clean_name}.zip"
    
    unique_token = secrets.token_hex(4)
    zip_path = TEMP_ZIP_DIR / f"{clean_name}_{unique_token}.zip"
    # Scratch file used to stream each download through disk instead of RAM,
    # preventing OOM when archiving multi-GB selections.
    scratch_path = TEMP_ZIP_DIR / f".scratch_{unique_token}.bin"
    
    total_items = len(items)
    logger.info(f"Starting ZIP creation for {total_items} items into '{final_zip_filename}'")
    
    try:
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
            for idx, item in enumerate(items, start=1):
                file_id = item["file_id"]
                file_name = item["file_name"]
                rel_archive_path = item["archive_path"].replace("\\", "/").lstrip("/")
                
                if progress_callback:
                    try:
                        progress_callback(idx, total_items, file_name)
                    except Exception:
                        pass
                
                try:
                    client = get_client()
                    msg = await client.get_messages(STORAGE_CHANNEL, int(file_id))
                    
                    if not msg:
                        logger.warning(f"Telegram message {file_id} not found for ZIP inclusion. Skipping.")
                        continue
                    
                    # Stream media to a scratch file (bounded memory footprint)
                    downloaded = await client.download_media(msg, file_name=str(scratch_path))
                    if downloaded and os.path.exists(scratch_path) and scratch_path.stat().st_size > 0:
                        # Create ZipInfo with standard timestamp
                        zinfo = zipfile.ZipInfo(rel_archive_path, date_time=time.localtime(time.time())[:6])
                        zinfo.compress_type = zipfile.ZIP_DEFLATED
                        zinfo.external_attr = 0o644 << 16  # standard file permissions
                        
                        with open(scratch_path, "rb") as src:
                            with zip_file.open(zinfo, mode="w") as dest:
                                while True:
                                    chunk = src.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    dest.write(chunk)
                        
                        logger.info(f"[{idx}/{total_items}] Added '{rel_archive_path}' to ZIP ({scratch_path.stat().st_size} bytes)")
                    else:
                        logger.warning(f"Empty download buffer for msg {file_id} ({file_name}). Skipping.")
                except Exception as e:
                    logger.error(f"Error packing file '{file_name}' (ID: {file_id}) into ZIP: {e}")
                    continue
                finally:
                    try:
                        if scratch_path.exists():
                            scratch_path.unlink(missing_ok=True)
                    except Exception:
                        pass
    except Exception:
        cleanup_temp_zip(zip_path)
        raise

    total_zip_size = zip_path.stat().st_size if zip_path.exists() else 0
    logger.info(f"ZIP archive created: {final_zip_filename} ({total_zip_size / (1024*1024):.2f} MB)")
    return zip_path, final_zip_filename, total_zip_size
