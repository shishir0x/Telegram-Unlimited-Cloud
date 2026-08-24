import os
import aiohttp, asyncio
from urllib.parse import urlparse
from utils.extra import get_filename
from utils.logger import Logger
from pathlib import Path
from utils.uploader import start_file_uploader
from techzdl import TechZDL

logger = Logger(__name__)

DOWNLOAD_PROGRESS = {}
STOP_DOWNLOAD = []

cache_dir = Path("./cache")
cache_dir.mkdir(parents=True, exist_ok=True)


def validate_download_url(url: str) -> str:
    """Validate that the URL uses an allowable HTTP/HTTPS scheme and has a valid hostname."""
    if not url or not isinstance(url, str):
        raise ValueError("URL is required")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Invalid URL scheme: only http and https are permitted")
    if not parsed.netloc:
        raise ValueError("Invalid URL: missing hostname")
    return url.strip()


async def download_progress_callback(status, current, total, id):
    global DOWNLOAD_PROGRESS

    DOWNLOAD_PROGRESS[id] = (
        status,
        current,
        total,
    )


async def download_file(url, id, path, filename, singleThreaded):
    global DOWNLOAD_PROGRESS, STOP_DOWNLOAD

    clean_url = validate_download_url(url)
    logger.info(f"Downloading file from {clean_url}")

    try:
        downloader = TechZDL(
            clean_url,
            output_dir=cache_dir,
            debug=False,
            progress_callback=download_progress_callback,
            progress_args=(id,),
            max_retries=5,
            single_threaded=singleThreaded,
        )
        await downloader.start(in_background=True)

        await asyncio.sleep(5)

        while downloader.is_running:
            if id in STOP_DOWNLOAD:
                logger.info(f"Stopping download {id}")
                # Consume the flag so stale entries don't cancel future downloads
                try:
                    STOP_DOWNLOAD.remove(id)
                except ValueError:
                    pass
                await downloader.stop()
                DOWNLOAD_PROGRESS[id] = ("cancelled", 0, 0)
                return
            await asyncio.sleep(1)

        if downloader.download_success is False:
            raise downloader.download_error

        DOWNLOAD_PROGRESS[id] = (
            "completed",
            downloader.total_size,
            downloader.total_size,
        )

        logger.info(f"File downloaded to {downloader.output_path}")

        asyncio.create_task(
            start_file_uploader(
                downloader.output_path, id, path, filename, downloader.total_size
            )
        )
    except Exception as e:
        DOWNLOAD_PROGRESS[id] = ("error", 0, 0)
        logger.error(f"Failed to download file: {clean_url} {e}")


async def get_file_info_from_url(url):
    clean_url = validate_download_url(url)
    downloader = TechZDL(
        clean_url,
        output_dir=cache_dir,
        debug=False,
        max_retries=5,
    )
    file_info = await downloader.get_file_info()
    return {"file_size": file_info.get("total_size", 0), "file_name": file_info.get("filename", "downloaded_file")}

