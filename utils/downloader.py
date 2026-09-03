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

import ipaddress
import socket

cache_dir = Path("./cache")
cache_dir.mkdir(parents=True, exist_ok=True)


def is_blocked_ip(ip_str: str) -> bool:
    """Returns True if the IP address belongs to private, loopback, link-local, or reserved ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def validate_download_url(url: str) -> str:
    """
    Validate that the URL uses an allowable HTTP/HTTPS scheme and has a valid,
    publicly reachable hostname, protecting against Server-Side Request Forgery (SSRF).
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL is required")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Invalid URL scheme: only http and https are permitted")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: missing hostname")

    # Allow private downloads if explicitly permitted in test/development environments
    if os.getenv("ALLOW_PRIVATE_DOWNLOADS", "").strip().lower() in ("true", "1", "yes"):
        return url.strip()

    # Block standard loopback and meta-address names directly
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "testclient"):
        raise ValueError(f"SSRF protection: access to '{hostname}' is restricted")

    # Resolve hostname and block private, loopback, or link-local ranges (e.g. AWS 169.254.169.254)
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if is_blocked_ip(ip_str):
                raise ValueError(f"SSRF protection: address '{ip_str}' belongs to a private, loopback, or link-local network")
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve host '{hostname}': {e}")

    return url.strip()


async def download_progress_callback(status, current, total, id):
    global DOWNLOAD_PROGRESS

    if len(DOWNLOAD_PROGRESS) > 200:
        excess = len(DOWNLOAD_PROGRESS) - 200
        for old_k in list(DOWNLOAD_PROGRESS.keys())[:excess]:
            DOWNLOAD_PROGRESS.pop(old_k, None)

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
        try:
            if 'downloader' in locals() and hasattr(downloader, "output_path") and downloader.output_path:
                p = Path(downloader.output_path)
                if p.exists():
                    p.unlink(missing_ok=True)
        except Exception:
            pass
        from utils.extra import clean_memory
        clean_memory()


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

