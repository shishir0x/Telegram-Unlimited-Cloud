from utils import fast_crypto
fast_crypto.patch_pyrogram()

from utils.downloader import (
    download_file,
    get_file_info_from_url,
)
import os
from typing import Optional, List, Dict, Any, Union
import asyncio
import secrets
import time
from pathlib import Path
from contextlib import asynccontextmanager
import aiofiles
from fastapi import Depends, FastAPI, HTTPException, Request, File, UploadFile, Form, Response
from fastapi.responses import FileResponse, JSONResponse
from config import ADMIN_PASSWORD, ADMIN_EMAIL, MAX_FILE_SIZE, STORAGE_CHANNEL
from utils.auth import (
    require_auth,
    Session,
    create_session,
    invalidate_session,
    validate_session,
    create_pending_otp,
    verify_otp,
    get_otp_status,
    rate_limit_login,
    rate_limit_otp_request,
    rate_limit_otp_verify,
    rate_limit_public_media,
    rate_limit_strict,
    is_secure_cookie,
    start_cleanup_task,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    get_client_ip,
    is_admin_authenticated,
    verify_password,
    hash_password,
    sanitize_path,
)
from utils.email_service import email_service, EmailDeliveryError
from utils.clients import initialize_clients
from utils.directoryHandler import getRandomID
from utils.extra import auto_ping_website, convert_class_to_dict, reset_cache_dir
from utils.streamer import media_streamer
from utils.uploader import start_file_uploader
from utils.logger import Logger
import urllib.parse




# Startup Event
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reset the cache directory, delete cache files
    reset_cache_dir()

    # Validate configuration on startup (never leaks secrets)
    import config
    is_valid, diagnostics = config.validate_config(raise_on_error=False)
    for diag in diagnostics:
        logger.warning(f"⚙️ Config: {diag}")

    # Initialize Database connection pool & schema
    try:
        from database.connection import init_db, test_database_connection
        init_db()
        if test_database_connection():
            logger.info("🗄️ Database: Connection pool healthy and schema verified")
        else:
            logger.warning("🗄️ Database: Connection test returned false")
    except Exception as e:
        logger.error(f"🗄️ Database initialization error: {e}")

    # Initialize Telegram clients in the background so server starts immediately (<100ms)
    asyncio.create_task(initialize_clients())

    # Start the website auto ping task
    asyncio.create_task(auto_ping_website())

    # Start background session/OTP cleanup task
    start_cleanup_task()

    # Start background metadata enrichment worker
    try:
        from utils.properties import MetadataWorker
        await MetadataWorker.start()
    except Exception as e:
        logger.warning(f"Metadata worker startup note: {e}")

    # Start background Transfer Manager subsystem
    try:
        from utils.transfer_manager import transfer_manager
        await transfer_manager.start()
    except Exception as e:
        logger.warning(f"Transfer manager startup note: {e}")

    # Periodic RAM release to host OS (essential for Render 512MB limit)
    async def auto_memory_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(60)
                from utils.extra import clean_memory
                clean_memory()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    asyncio.create_task(auto_memory_cleanup_loop())

    import socket
    lan_ips = []
    try:
        # Get all host IPs
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ":" not in ip:
                lan_ips.append(ip)
    except Exception:
        pass

    # Sort so physical LAN (192.168.x.x / 172.x) appears before VPN virtual tunnels (10.x / 100.x) if available
    lan_ips.sort(key=lambda x: (not x.startswith("192.168."), not x.startswith("172."), x))

    logger.info("🌐 Local Browser: http://localhost:8000")
    if lan_ips:
        for ip in lan_ips:
            logger.info(f"📱 Mobile Devices on Wi-Fi/LAN: http://{ip}:8000")
    else:
        logger.info("📱 Mobile Devices on Wi-Fi: http://192.168.x.x:8000")

    yield

    # Graceful shutdown: cleanly disconnect Telegram clients & TransferManager
    try:
        from utils.transfer_manager import transfer_manager
        await transfer_manager.shutdown()
    except Exception as e:
        logger.warning(f"Transfer manager shutdown error: {e}")

    try:
        from utils.clients import stop_clients
        await stop_clients()
    except Exception as e:
        logger.warning(f"Shutdown cleanup error: {e}")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
logger = Logger(__name__)

# Phase 2 — Register Synchronization API routes
try:
    from utils.sync_routes import register_sync_routes
    register_sync_routes(app)
except Exception as sync_reg_err:
    logger.warning(f"Sync routes registration note: {sync_reg_err}")


# Pure ASGI Security Headers Middleware (prevents BaseHTTPMiddleware stream buffering issues)
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Scope, Receive, Send

class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                headers["Content-Security-Policy"] = (
                    "default-src 'self' data: blob: https:; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
                    "style-src 'self' 'unsafe-inline' https:; "
                    "img-src 'self' data: blob: https:; "
                    "media-src 'self' blob: https:; "
                    "connect-src 'self' https: wss:; "
                    "frame-ancestors 'self';"
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)

app.add_middleware(SecurityHeadersMiddleware)

# Cross-Origin Resource Sharing (CORS) Middleware
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS

is_wildcard_cors = any(orig == "*" for orig in CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=not is_wildcard_cors,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Page Routes

@app.api_route("/", methods=["GET", "HEAD"])
async def home_page():
    return FileResponse("website/home.html")


@app.api_route("/health", methods=["GET", "HEAD", "POST", "OPTIONS"])
@app.api_route("/ping", methods=["GET", "HEAD", "POST", "OPTIONS"])
@app.api_route("/healthz", methods=["GET", "HEAD", "POST", "OPTIONS"])
async def health_check():
    from utils.clients import get_client_status
    status = get_client_status()
    return JSONResponse(
        {
            "status": "ok",
            "message": "TG Drive is active and healthy",
            "telegram": status,
        },
        status_code=200,
    )


@app.get("/health/live")
async def liveness_check():
    """Liveness probe: verifies process is alive and responsive."""
    return JSONResponse({"status": "alive"}, status_code=200)


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: verifies Telegram connectivity, database connectivity, and metadata readiness."""
    from utils.clients import is_telegram_ready
    from utils.directoryHandler import ensure_drive_data
    from database.connection import test_database_connection
    drive = ensure_drive_data()
    is_tg_ready = is_telegram_ready()
    is_db_ready = test_database_connection()
    is_ready = is_tg_ready and drive is not None and is_db_ready
    if is_ready:
        return JSONResponse(
            {
                "status": "ready",
                "telegram_ready": True,
                "database_connected": True,
                "drive_loaded": True,
            },
            status_code=200,
        )
    return JSONResponse(
        {
            "status": "initializing",
            "telegram_ready": is_tg_ready,
            "database_connected": is_db_ready,
            "drive_loaded": drive is not None,
        },
        status_code=503,
    )


@app.get("/stream")
async def stream_page():
    return FileResponse("website/VideoPlayer.html")


@app.get("/static/{file_path:path}")
async def static_files(file_path):
    if "apiHandler.js" in file_path:
        with open(Path("website/static/js/apiHandler.js"), "r", encoding="utf-8") as f:
            content = f.read()
            content = content.replace("MAX_FILE_SIZE__SDGJDG", str(MAX_FILE_SIZE))
        return Response(content=content, media_type="application/javascript", headers={"Cache-Control": "no-cache, must-revalidate"})

    # Path traversal shield: resolved target must remain strictly inside website/static and be a regular file
    webroot = Path("website/static").resolve()
    try:
        target = (webroot / file_path).resolve()
        if not target.is_relative_to(webroot) or not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(target, headers={"Cache-Control": "no-cache, must-revalidate"})



@app.get("/file")
async def dl_file(request: Request):
    from utils.directoryHandler import ensure_drive_data
    from utils.auth import rate_limit_public_media
    rate_limit_public_media(request, "public_file", 120, 60)
    drive = ensure_drive_data()

    path = request.query_params.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path parameter")

    clean_path = sanitize_path(path.replace("/share_", "").replace("share_", "").strip("/"))
    auth = request.query_params.get("auth")
    is_admin = is_admin_authenticated(request)

    # If not admin, verify if access is authorized through folder share token
    if not is_admin:
        if not auth:
            raise HTTPException(status_code=401, detail="Unauthorized access to file")
        try:
            folder_path = (
                "/" + "/".join(clean_path.strip("/").split("/")[:-1])
                if len(clean_path.strip("/").split("/")) > 1
                else "/"
            )
            folder_res = drive.get_directory(folder_path, is_admin=False, auth=auth)
            if not folder_res:
                raise HTTPException(status_code=401, detail="Unauthorized access to file")
        except Exception as e:
            logger.warning(f"Unauthorized access check failed for '{path}': {e}")
            raise HTTPException(status_code=401, detail="Unauthorized access to file")

    try:
        file = drive.get_file(clean_path, is_admin=is_admin)
        try:
            from utils.properties import ActivityTracker
            import time
            is_preview = bool(request.query_params.get("preview"))
            action = "previewed" if is_preview else "downloaded"
            actor = "Admin" if is_admin else "Shared link user"
            ActivityTracker.record_activity(file, action, actor=actor)
            setattr(file, "accessed_at", time.time())
            if is_preview:
                setattr(file, "viewed_at", time.time())
            else:
                setattr(file, "downloaded_at", time.time())
        except Exception:
            pass

        # 1. Direct local file streaming with HTTP Range support
        local_path = drive.resolve_local_file_path(file)
        if local_path and os.path.isfile(local_path):
            from utils.streamer import local_file_streamer
            return await local_file_streamer(local_path, file.name, request)

        # 2. Telegram cloud streaming via MTProto
        if getattr(file, "file_id", 0) and int(file.file_id) > 0:
            return await media_streamer(STORAGE_CHANNEL, file.file_id, file.name, request)

        # 3. File metadata exists but source is offline / pending sync
        raise HTTPException(
            status_code=404,
            detail=f"File '{file.name}' is currently offline on local disk and has not yet been synced to Telegram cloud.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error streaming file '{path}': {e}")
        raise HTTPException(status_code=404, detail="File not found")



@app.get("/downloadZip")
async def download_zip(request: Request):
    """
    OneDrive/Google Drive style ZIP download for a single folder or multiple selections.
    Preserves internal directory tree hierarchies.
    """
    from utils.directoryHandler import ensure_drive_data
    from starlette.background import BackgroundTask
    from utils.zipper import create_zip_archive, cleanup_temp_zip
    drive = ensure_drive_data()

    is_admin = is_admin_authenticated(request)
    auth = request.query_params.get("auth")

    from utils.auth import rate_limit_public_media
    rate_limit_public_media(request, "public_zip", 60, 60)

    raw_path = request.query_params.get("path")
    raw_paths = request.query_params.get("paths")

    target_paths = []
    if raw_paths:
        target_paths = [sanitize_path(p.strip()) for p in raw_paths.split(",") if p.strip()]
    elif raw_path:
        target_paths = [sanitize_path(raw_path.strip())]

    if not target_paths:
        raise HTTPException(status_code=400, detail="Missing path or paths parameter")

    if not is_admin and not auth:
        raise HTTPException(status_code=401, detail="Unauthorized access")

    # Non-admin share visitors may only zip paths inside the shared scope:
    # every target must resolve either as an authorized folder or live in one.
    if not is_admin:
        for target in target_paths:
            clean_target = sanitize_path(str(target).replace("/share_", "").replace("share_", "").strip("/"))
            res = drive.get_directory(clean_target, is_admin=False, auth=auth)
            if res:
                continue
            segments = [s for s in clean_target.strip("/").split("/") if s]
            parent_path = "/" + "/".join(segments[:-1]) if len(segments) > 1 else "/"
            if not drive.get_directory(parent_path, is_admin=False, auth=auth):
                raise HTTPException(status_code=401, detail="Unauthorized access")

    custom_name = request.query_params.get("name")
    default_name, items = drive.collect_items_for_zip(target_paths)

    if not items:
        raise HTTPException(status_code=404, detail="No files found in selected folder(s)")

    zip_base_name = custom_name or default_name
    zip_file_path, final_filename, total_size = await create_zip_archive(items, zip_base_name)

    return FileResponse(
        path=str(zip_file_path),
        filename=final_filename,
        media_type="application/zip",
        background=BackgroundTask(cleanup_temp_zip, zip_file_path),
        headers={
            "Content-Disposition": f'attachment; filename="{urllib.parse.quote(final_filename)}"',
            "Content-Length": str(total_size)
        }
    )


@app.post("/api/downloadZip")
async def api_download_zip(request: Request, _auth: Session = Depends(require_auth)):
    """
    Initiates a bulk ZIP download preparation for selected files & folders.
    """
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    paths = data.get("paths", [])
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return JSONResponse({"status": "No paths provided"}, status_code=400)

    custom_name = data.get("name")
    default_name, items = drive.collect_items_for_zip(paths)
    if not items:
        return JSONResponse({"status": "No files found in selected items to download"}, status_code=404)

    paths_param = ",".join(paths)
    name_param = f"&name={urllib.parse.quote(custom_name)}" if custom_name else ""
    dl_url = f"/downloadZip?paths={urllib.parse.quote(paths_param)}{name_param}"

    return JSONResponse({
        "status": "ok",
        "file_count": len(items),
        "total_size": sum(it.get("size", 0) for it in items),
        "suggested_name": custom_name or default_name,
        "download_url": dl_url
    })



# =========================================================
# Archive Manager API — Browse, Inspect, Extract ZIP Archives
# =========================================================


@app.get("/api/archive/list_formats")
async def api_archive_list_formats(_auth: Session = Depends(require_auth)):
    """Returns the list of archive formats supported by this server."""
    from utils.archive_manager import SUPPORTED_FORMATS
    return JSONResponse({"supported": SUPPORTED_FORMATS})


@app.post("/api/archive/inspect")
async def api_archive_inspect(request: Request, _auth: Session = Depends(require_auth)):
    """
    Inspects an archive file on the drive and returns its full manifest (tree + sizes).

    Body: {"file_path": "<drive_virtual_path>"}
    """
    from utils.directoryHandler import ensure_drive_data
    from utils.archive_manager import (
        inspect_archive, manifest_to_dict,
        ArchiveSecurity, ARCHIVE_TEMP_DIR, cleanup_archive_temp, make_sandbox,
    )
    from config import (
        ARCHIVE_MAX_EXTRACT_SIZE, ARCHIVE_MAX_EXTRACT_FILES,
        ARCHIVE_MAX_NESTING_DEPTH, ARCHIVE_MAX_RATIO,
    )
    import asyncio

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    drive_path = body.get("file_path", "").strip()
    if not drive_path:
        raise HTTPException(status_code=400, detail="Missing file_path")

    drive_path = sanitize_path(drive_path)
    drive = ensure_drive_data()

    try:
        file_obj = drive.get_file(drive_path)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found in drive")

    ext = Path(file_obj.name).suffix.lower()
    if ext not in (".zip",):
        raise HTTPException(status_code=400, detail=f"Unsupported archive format: '{ext}'. Supported: .zip")

    security = ArchiveSecurity(
        max_extract_size=ARCHIVE_MAX_EXTRACT_SIZE,
        max_extract_files=ARCHIVE_MAX_EXTRACT_FILES,
        max_nesting_depth=ARCHIVE_MAX_NESTING_DEPTH,
        max_ratio=ARCHIVE_MAX_RATIO,
    )

    # Resolve local file or download from Telegram to a temp location
    local_path = drive.resolve_local_file_path(file_obj) if hasattr(drive, "resolve_local_file_path") else None
    temp_download: Optional[Path] = None

    if not local_path or not os.path.isfile(local_path):
        # Archive is cloud-only — download to temp for inspection
        if not getattr(file_obj, "file_id", 0) or int(file_obj.file_id) <= 0:
            raise HTTPException(status_code=503, detail="Archive file is not available locally or in Telegram cloud")

        try:
            from utils.clients import get_client
            from config import STORAGE_CHANNEL as _STORAGE_CHANNEL
            ARCHIVE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
            temp_download = ARCHIVE_TEMP_DIR / f"inspect_{secrets.token_hex(8)}{ext}"
            client = get_client()
            msg = await client.get_messages(_STORAGE_CHANNEL, int(file_obj.file_id))
            if not msg:
                raise HTTPException(status_code=404, detail="Telegram message not found for archive")
            await client.download_media(msg, file_name=str(temp_download))
            local_path = str(temp_download)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to download archive for inspection: {e}")

    try:
        manifest = await asyncio.get_running_loop().run_in_executor(
            None, inspect_archive, Path(local_path), security
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"archive inspect error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to inspect archive: {e}")
    finally:
        if temp_download and temp_download.exists():
            try:
                temp_download.unlink(missing_ok=True)
            except Exception:
                pass

    return JSONResponse(manifest_to_dict(manifest))


@app.post("/api/archive/extract")
async def api_archive_extract(request: Request, _auth: Session = Depends(require_auth)):
    """
    Extracts selected members (or all) from an archive into a drive destination folder.
    Each extracted file is queued into the Transfer Manager as an upload job.

    Body:
    {
      "file_path": "<drive_virtual_path>",
      "members": ["path/inside/archive.txt", ...] | null,
      "destination": "<drive_folder_path>",   // defaults to archive's parent folder
      "conflict": "keep_both" | "replace"
    }
    """
    from utils.directoryHandler import ensure_drive_data
    from utils.archive_manager import (
        inspect_archive, extract_archive, make_sandbox,
        cleanup_archive_temp, ArchiveSecurity, ARCHIVE_TEMP_DIR,
        register_download_token,
    )
    from utils.transfer_manager import transfer_manager
    from config import (
        ARCHIVE_MAX_EXTRACT_SIZE, ARCHIVE_MAX_EXTRACT_FILES,
        ARCHIVE_MAX_NESTING_DEPTH, ARCHIVE_MAX_RATIO, STORAGE_CHANNEL as _SC,
    )
    from starlette.background import BackgroundTask
    import asyncio

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    drive_path = sanitize_path(body.get("file_path", "").strip())
    members_raw: Optional[List[str]] = body.get("members")  # None → extract all
    destination_raw: Optional[str] = body.get("destination")
    conflict_mode: str = body.get("conflict", "keep_both")

    if conflict_mode not in ("keep_both", "replace"):
        conflict_mode = "keep_both"

    if not drive_path:
        raise HTTPException(status_code=400, detail="Missing file_path")

    drive = ensure_drive_data()

    try:
        file_obj = drive.get_file(drive_path)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found in drive")

    ext = Path(file_obj.name).suffix.lower()
    if ext not in (".zip",):
        raise HTTPException(status_code=400, detail=f"Unsupported archive format: '{ext}'")

    # Determine destination folder path in drive (defaults to archive's parent)
    if destination_raw:
        dest_folder = sanitize_path(destination_raw.strip())
    else:
        parts = drive_path.strip("/").split("/")
        dest_folder = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"

    security = ArchiveSecurity(
        max_extract_size=ARCHIVE_MAX_EXTRACT_SIZE,
        max_extract_files=ARCHIVE_MAX_EXTRACT_FILES,
        max_nesting_depth=ARCHIVE_MAX_NESTING_DEPTH,
        max_ratio=ARCHIVE_MAX_RATIO,
    )

    # Resolve local file or download from Telegram
    local_path = drive.resolve_local_file_path(file_obj) if hasattr(drive, "resolve_local_file_path") else None
    temp_download: Optional[Path] = None

    if not local_path or not os.path.isfile(local_path):
        if not getattr(file_obj, "file_id", 0) or int(file_obj.file_id) <= 0:
            raise HTTPException(status_code=503, detail="Archive not available locally or in cloud")
        try:
            from utils.clients import get_client
            ARCHIVE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
            temp_download = ARCHIVE_TEMP_DIR / f"extract_{secrets.token_hex(8)}{ext}"
            client = get_client()
            msg = await client.get_messages(_SC, int(file_obj.file_id))
            if not msg:
                raise HTTPException(status_code=404, detail="Telegram message not found for archive")
            await client.download_media(msg, file_name=str(temp_download))
            local_path = str(temp_download)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to download archive: {e}")

    # Safety pass: inspect first to enforce limits before touching disk
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, inspect_archive, Path(local_path), security
        )
    except ValueError as e:
        if temp_download and temp_download.exists():
            temp_download.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))

    # Create sandbox and extract
    sandbox = make_sandbox()
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, extract_archive, Path(local_path), members_raw, sandbox, security
        )
    except Exception as e:
        cleanup_archive_temp(sandbox)
        if temp_download and temp_download.exists():
            temp_download.unlink(missing_ok=True)
        logger.error(f"archive extract error: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")
    finally:
        if temp_download and temp_download.exists():
            try:
                temp_download.unlink(missing_ok=True)
            except Exception:
                pass

    if not result.extracted_files:
        cleanup_archive_temp(sandbox)
        raise HTTPException(
            status_code=400,
            detail="No files were extracted. " + "; ".join(result.skipped[:5])
        )

    # Queue each extracted file as an upload transfer job
    import uuid
    batch_id = f"archive_{secrets.token_hex(6)}"
    transfer_ids: List[str] = []

    for file_path in result.extracted_files:
        try:
            rel = file_path.relative_to(sandbox)
            relative_str = str(rel).replace("\\", "/")
            tx_id = f"arc_{uuid.uuid4().hex[:12]}"
            item = await transfer_manager.queue_upload(
                file_path=str(file_path),
                id=tx_id,
                target_path=dest_folder,
                filename=file_path.name,
                file_size=file_path.stat().st_size,
                conflict=conflict_mode,
                relative_path=relative_str,
                batch_id=batch_id,
            )
            transfer_ids.append(item.id)
        except Exception as e:
            logger.warning(f"Failed to queue extracted file {file_path.name}: {e}")

    # Build download token for direct-browser access (skips Telegram upload)
    file_map = {
        str(f.relative_to(sandbox)).replace("\\", "/"): f
        for f in result.extracted_files
    }
    dl_token = register_download_token(sandbox, file_map)

    return JSONResponse({
        "status": "ok",
        "batch_id": batch_id,
        "transfer_ids": transfer_ids,
        "extracted_count": len(result.extracted_files),
        "skipped": result.skipped[:20],
        "errors": result.errors[:10],
        "download_token": dl_token,
    })


@app.get("/api/archive/download")
async def api_archive_download(request: Request, _auth: Session = Depends(require_auth)):
    """
    Streams a single extracted file back to the browser using a short-lived token.
    Query params: token=<token>&member=<archive-relative-path>
    """
    from utils.archive_manager import resolve_download_token
    from starlette.responses import StreamingResponse
    import mimetypes

    token = request.query_params.get("token", "")
    member = request.query_params.get("member", "")

    if not token or not member:
        raise HTTPException(status_code=400, detail="Missing token or member parameter")

    file_path = resolve_download_token(token, member)
    if not file_path:
        raise HTTPException(status_code=404, detail="Invalid or expired download token")

    filename = file_path.name
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    def _iter_file():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(512 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{urllib.parse.quote(filename)}"',
            "Content-Length": str(file_path.stat().st_size),
        },
    )


# =========================================================
# Google-Grade Thumbnail & Media Optimization Service
# =========================================================

import collections
import io

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class ThumbnailService:
    def __init__(self, max_ram_items: int = None, max_disk_mb: int = None):
        from utils.extra import is_low_memory_env
        low_mem = is_low_memory_env()
        if max_ram_items is None:
            max_ram_items = int(os.getenv("THUMB_MAX_RAM_ITEMS", "30" if low_mem else "100"))
        if max_disk_mb is None:
            max_disk_mb = int(os.getenv("THUMB_CACHE_MAX_MB", "150" if low_mem else "500"))

        self.ram_cache: collections.OrderedDict[Union[int, str], bytes] = collections.OrderedDict()
        self.max_ram_items = max_ram_items
        self.max_disk_bytes = max_disk_mb * 1024 * 1024
        self.cache_dir = Path("./cache/thumbs")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(4)
        self.in_flight: dict[Union[int, str], asyncio.Future] = {}
        # Initial disk prune check on startup
        self.prune_disk_if_needed()

    def get_ram(self, key: Union[int, str]) -> bytes | None:
        if key in self.ram_cache:
            self.ram_cache.move_to_end(key)
            return self.ram_cache[key]
        return None

    def put_ram(self, key: Union[int, str], data: bytes):
        self.ram_cache[key] = data
        self.ram_cache.move_to_end(key)
        if len(self.ram_cache) > self.max_ram_items:
            self.ram_cache.popitem(last=False)

    def prune_disk_if_needed(self):
        """Auto-prunes thumbnails when disk usage exceeds 500MB (deletes oldest files first)."""
        try:
            files = list(self.cache_dir.glob("*.jpg"))
            if not files:
                return
            total_size = sum(f.stat().st_size for f in files if f.exists())
            if total_size > self.max_disk_bytes:
                logger.info(f"Thumbnail cache ({total_size / (1024*1024):.1f}MB) exceeded limit ({self.max_disk_bytes / (1024*1024):.1f}MB). Auto-pruning oldest files...")
                files.sort(key=lambda f: f.stat().st_mtime)
                target_delete_count = max(1, int(len(files) * 0.3))
                for f in files[:target_delete_count]:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
                logger.info(f"Auto-pruned {target_delete_count} oldest thumbnails from disk.")
        except Exception as e:
            logger.warning(f"Error during thumbnail disk pruning: {e}")

    async def get_or_generate_local(self, local_path: str, cache_key: str) -> bytes | None:
        """
        Fast on-the-fly thumbnail generation for physical files on local disk.
        Caches results in RAM and on disk under cache/thumbs/{cache_key}.jpg.
        """
        if not PIL_AVAILABLE:
            return None

        # 1. RAM cache
        ram_data = self.get_ram(cache_key)
        if ram_data:
            return ram_data

        # 2. Disk cache
        disk_file = self.cache_dir / f"{cache_key}.jpg"
        if disk_file.exists() and disk_file.stat().st_size > 0:
            try:
                data = disk_file.read_bytes()
                self.put_ram(cache_key, data)
                return data
            except Exception:
                pass

        # 3. Generate thumbnail from local image
        try:
            p = Path(local_path)
            if not p.is_file():
                return None
            ext = p.suffix.lower().lstrip(".")
            if ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "ico", "tiff", "tif", "avif"]:
                with Image.open(local_path) as img:
                    if img.mode in ("RGBA", "LA", "P"):
                        bg = Image.new("RGB", img.size, (255, 255, 255))
                        if img.mode == "P":
                            img = img.convert("RGBA")
                        if "A" in img.mode:
                            bg.paste(img, mask=img.split()[-1])
                        else:
                            bg.paste(img)
                        img = bg
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                    out_buf = io.BytesIO()
                    img.save(out_buf, format="JPEG", quality=80, optimize=True)
                    thumb_bytes = out_buf.getvalue()
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    disk_file.write_bytes(thumb_bytes)
                    self.put_ram(cache_key, thumb_bytes)
                    self.prune_disk_if_needed()
                    return thumb_bytes
        except Exception as e:
            logger.warning(f"Failed local thumbnail generation for {local_path}: {e}")
        return None

    async def get_or_fetch(self, file_id: int, file_name: str) -> bytes | None:
        file_id = int(file_id)
        # 1. Check RAM Cache
        ram_data = self.get_ram(file_id)
        if ram_data:
            return ram_data

        # 2. Check Disk Cache
        disk_file = self.cache_dir / f"{file_id}.jpg"
        if disk_file.exists() and disk_file.stat().st_size > 0:
            try:
                data = disk_file.read_bytes()
                self.put_ram(file_id, data)
                return data
            except Exception:
                pass

        # 3. Singleflight Request Coalescing (Deduplicate concurrent MTProto calls)
        loop = asyncio.get_running_loop()
        if file_id in self.in_flight:
            return await self.in_flight[file_id]

        future = loop.create_future()
        self.in_flight[file_id] = future

        try:
            async with self.semaphore:
                # Re-check after acquiring semaphore
                if disk_file.exists() and disk_file.stat().st_size > 0:
                    data = disk_file.read_bytes()
                    self.put_ram(file_id, data)
                    future.set_result(data)
                    return data

                from utils.clients import get_client
                client = get_client()
                msg = await client.get_messages(STORAGE_CHANNEL, file_id)

                if not msg:
                    future.set_result(None)
                    return None

                ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
                is_image = ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "ico", "tiff", "tif", "heic", "heif", "avif"]
                is_video = ext in ["mp4", "mkv", "webm", "mov", "avi", "3gp", "ts", "flv", "wmv", "m4v"]

                thumb_target = None
                if msg.photo:
                    thumb_target = getattr(msg.photo, "file_id", msg.photo)
                elif msg.document and msg.document.thumbs:
                    thumb_target = getattr(msg.document.thumbs[0], "file_id", msg.document.thumbs[0])
                elif msg.video and msg.video.thumbs:
                    thumb_target = getattr(msg.video.thumbs[0], "file_id", msg.video.thumbs[0])
                elif msg.animation and msg.animation.thumbs:
                    thumb_target = getattr(msg.animation.thumbs[0], "file_id", msg.animation.thumbs[0])
                elif is_image and msg.document and getattr(msg.document, "file_size", 0) <= 25 * 1024 * 1024:
                    thumb_target = getattr(msg.document, "file_id", msg.document)
                elif is_image:
                    thumb_target = msg

                if thumb_target:
                    try:
                        buf = await client.download_media(thumb_target, in_memory=True)
                        if buf and hasattr(buf, "getbuffer") and buf.getbuffer().nbytes > 0:
                            buf.seek(0)
                            with Image.open(buf) as img:
                                if img.mode in ("RGBA", "LA", "P"):
                                    bg = Image.new("RGB", img.size, (255, 255, 255))
                                    if img.mode == "P":
                                        img = img.convert("RGBA")
                                    if "A" in img.mode:
                                        bg.paste(img, mask=img.split()[-1])
                                    else:
                                        bg.paste(img)
                                    img = bg
                                elif img.mode != "RGB":
                                    img = img.convert("RGB")
                                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                                out_buf = io.BytesIO()
                                img.save(out_buf, format="JPEG", quality=80, optimize=True)
                                thumb_bytes = out_buf.getvalue()
                                self.cache_dir.mkdir(parents=True, exist_ok=True)
                                disk_file.write_bytes(thumb_bytes)
                                self.put_ram(file_id, thumb_bytes)
                                self.prune_disk_if_needed()
                                future.set_result(thumb_bytes)
                                return thumb_bytes
                    except Exception as err:
                        logger.warning(f"Failed in-memory thumbnail conversion for msg {file_id}: {err}")

                future.set_result(None)
                return None
        except Exception as e:
            logger.warning(f"Error extracting thumbnail for msg {file_id}: {e}")
            if not future.done():
                future.set_result(None)
            return None
        finally:
            self.in_flight.pop(file_id, None)


THUMB_SERVICE = ThumbnailService(max_ram_items=300, max_disk_mb=50)


@app.get("/thumbnail")
async def get_thumbnail(request: Request):
    from utils.directoryHandler import ensure_drive_data
    from utils.auth import rate_limit_public_media
    rate_limit_public_media(request, "public_thumbnail", 600, 60)
    drive = ensure_drive_data()

    path = request.query_params.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path parameter")

    clean_path = sanitize_path(path.replace("/share_", "").replace("share_", "").strip("/"))
    auth = request.query_params.get("auth")
    is_admin = is_admin_authenticated(request)

    if not is_admin:
        if not auth:
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            folder_path = (
                "/" + "/".join(clean_path.strip("/").split("/")[:-1])
                if len(clean_path.strip("/").split("/")) > 1
                else "/"
            )
            folder_res = drive.get_directory(folder_path, is_admin=False, auth=auth)
            if not folder_res:
                raise HTTPException(status_code=401, detail="Unauthorized")
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        file = drive.get_file(clean_path, is_admin=is_admin)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    cache_identifier = str(file.file_id) if (getattr(file, "file_id", 0) and file.file_id > 0) else getattr(file, "id", "thumb")
    etag = f'"thumb-{cache_identifier}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"})

    # 1. Try Telegram thumbnail if valid message ID
    if getattr(file, "file_id", 0) and int(file.file_id) > 0:
        thumb_data = await THUMB_SERVICE.get_or_fetch(file.file_id, file.name)
        if thumb_data:
            return Response(
                content=thumb_data,
                media_type="image/jpeg",
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=31536000, immutable",
                },
            )

    # 2. Try local image thumbnail
    local_path = drive.resolve_local_file_path(file)
    if local_path and os.path.isfile(local_path):
        thumb_data = await THUMB_SERVICE.get_or_generate_local(local_path, getattr(file, "id", cache_identifier))
        if thumb_data:
            return Response(
                content=thumb_data,
                media_type="image/jpeg",
                headers={
                    "ETag": etag,
                    "Cache-Control": "public, max-age=31536000, immutable",
                },
            )

    raise HTTPException(status_code=404, detail="Thumbnail not available")


# Api Routes


@app.post("/api/checkPassword")
async def api_check_password(request: Request):
    """
    Direct password verification endpoint for CLI tools (tgdrive_backup.py) and automation scripts.
    Protected by rate limiting and constant-time password verification.
    """
    from utils.auth import rate_limit_check_password
    rate_limit_check_password(request)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    submitted_password = str(data.get("password") or data.get("pass") or "").strip()
    if not submitted_password or not ADMIN_PASSWORD or not verify_password(submitted_password, ADMIN_PASSWORD):
        await asyncio.sleep(0.3)
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    client_ip = get_client_ip(request)

    # Two-factor enforcement: if 2FA is active (ADMIN_EMAIL set), remote requests must provide valid OTP
    if bool(ADMIN_EMAIL):
        is_local = client_ip in ("127.0.0.1", "::1", "localhost", "testclient")
        if not is_local:
            submitted_otp = str(data.get("otp") or "").strip()
            if not submitted_otp or not verify_otp(submitted_otp):
                return JSONResponse(
                    {"status": "otp_required", "detail": "Two-factor authentication code (OTP) required for remote login"},
                    status_code=401,
                )

    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    token = create_session(ip=client_ip, previous_token=old_token)
    from utils.auth import is_secure_cookie
    is_https = is_secure_cookie(request)

    resp = JSONResponse({"status": "ok", "token": token})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_TTL_SECONDS,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/login")
async def api_login(request: Request):
    """
    Step 1 of web authentication.

    - ADMIN_EMAIL configured (recommended): verify email + password, then send a
      single-use OTP via Email/Telegram. Session is created only at /api/verifyOtp.
    - ADMIN_EMAIL not configured: fall back to password-only login and create the
      session immediately. This matches the trust level of /api/checkPassword
      (used by the CLI) and prevents permanent lockout on fresh deployments.
    """
    rate_limit_login(request)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    submitted_email = (data.get("email") or "").strip().lower()
    submitted_password = data.get("password") or data.get("pass") or ""

    if bool(ADMIN_EMAIL):
        # ---- Two-factor mode: email + password -> OTP ----
        # Validate both fields together to avoid email enumeration
        email_ok = secrets.compare_digest(
            submitted_email.encode(), ADMIN_EMAIL.strip().lower().encode()
        )
        password_ok = bool(ADMIN_PASSWORD) and verify_password(str(submitted_password), str(ADMIN_PASSWORD))

        if not (email_ok and password_ok):
            # Uniform delay to prevent timing-based enumeration
            await asyncio.sleep(0.5)
            return JSONResponse({"status": "Invalid email or password"}, status_code=401)

        # Credentials correct — generate and send OTP
        otp_state = get_otp_status()
        if otp_state["pending"] and not otp_state.get("can_resend", True):
            return JSONResponse(
                {"status": "Please wait before requesting a new code."},
                status_code=429,
            )

        otp = create_pending_otp()
        delivery_channels = []

        # 1. Deliver OTP via Telegram Bot directly to STORAGE_CHANNEL
        try:
            from utils.clients import multi_clients
            bot_client = multi_clients.get(1) or (list(multi_clients.values())[0] if multi_clients else None)
            if bot_client and STORAGE_CHANNEL:
                otp_msg = (
                    f"🔐 **TG Drive Verification Code**\n\n"
                    f"Your 6-digit login code is:\n`{otp}`\n\n"
                    f"⏱️ Expires in **5 minutes** (single-use).\n"
                    f"Requested for: `{submitted_email}`"
                )
                await bot_client.send_message(int(STORAGE_CHANNEL), otp_msg)
                delivery_channels.append("Telegram Channel")
                logger.info(f"OTP verification code sent to Telegram Storage Channel ({STORAGE_CHANNEL})")
        except Exception as te:
            logger.warning(f"Telegram Bot OTP send failed: {te}")

        # 2. Deliver OTP via Email (SMTP)
        if email_service.is_configured:
            try:
                await email_service.send_otp(ADMIN_EMAIL, otp)
                delivery_channels.append("Email")
                logger.info("OTP verification code sent via Email")
            except Exception as ee:
                logger.warning(f"SMTP OTP delivery skipped/failed: {ee}")

        # Log confirmation without exposing the raw secret code unless in explicit debug mode
        if os.getenv("LOG_OTP_CONSOLE", "").strip().lower() in ("true", "1", "yes"):
            logger.info(f"🔑 [DEBUG VERIFICATION CODE]: {otp} (for {submitted_email})")
        else:
            masked_email = (submitted_email[:2] + "***@" + submitted_email.split("@")[-1]) if "@" in submitted_email else "***"
            logger.info(f"🔑 Verification code dispatched successfully for {masked_email}")

        # If at least one channel delivered the OTP:
        if delivery_channels:
            msg_text = f"Verification code sent to {' & '.join(delivery_channels)} ({submitted_email}). Check your Telegram Storage Channel or Email inbox."
            return JSONResponse({"status": "otp_sent", "message": msg_text})

        # Fallback if no delivery channel succeeded
        if os.getenv("LOG_OTP_CONSOLE", "").strip().lower() in ("true", "1", "yes"):
            return JSONResponse({"status": "otp_sent", "message": f"Verification code generated (debug mode): {otp}"})
        return JSONResponse({"status": "otp_sent", "message": "Verification code generated. Please configure Telegram bot or SMTP to receive codes."})

    # ---- Single-factor mode: no ADMIN_EMAIL configured ----
    password_ok = bool(ADMIN_PASSWORD) and verify_password(str(submitted_password), str(ADMIN_PASSWORD))
    if not password_ok:
        await asyncio.sleep(0.5)
        return JSONResponse({"status": "Invalid email or password"}, status_code=401)

    logger.warning("ADMIN_EMAIL is not configured — issuing session from password alone. Set ADMIN_EMAIL to enable OTP two-factor login.")

    client_ip = get_client_ip(request)
    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    token = create_session(ip=client_ip, previous_token=old_token)
    from utils.auth import is_secure_cookie

    resp = JSONResponse({"status": "ok", "mode": "password_only"})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_TTL_SECONDS,
        secure=is_secure_cookie(request),
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/verifyOtp")
async def api_verify_otp(request: Request):
    """
    Step 2 of 2: Verify the OTP submitted by the user.
    On success, creates a session and sets the HttpOnly session cookie.
    """
    rate_limit_otp_verify(request)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    submitted_otp = str(data.get("otp") or "").strip()
    if not submitted_otp:
        return JSONResponse({"status": "OTP is required"}, status_code=400)

    otp_state = get_otp_status()
    if not otp_state["pending"]:
        return JSONResponse({"status": "No pending verification. Please log in again."}, status_code=400)
    if otp_state["expired"]:
        return JSONResponse({"status": "Verification code has expired. Please log in again."}, status_code=400)
    if otp_state["locked"]:
        return JSONResponse({"status": "Too many incorrect attempts. Please log in again."}, status_code=429)

    if not verify_otp(submitted_otp):
        remaining = get_otp_status().get("remaining_attempts", 0)
        if remaining == 0:
            return JSONResponse(
                {"status": "Too many incorrect attempts. Please log in again."},
                status_code=429,
            )
        return JSONResponse(
            {"status": f"Incorrect code. {remaining} attempt(s) remaining."},
            status_code=401,
        )

    # OTP verified — create session bound to client IP with rotation
    client_ip = get_client_ip(request)
    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    token = create_session(ip=client_ip, previous_token=old_token)
    from utils.auth import is_secure_cookie
    is_https = is_secure_cookie(request)
    
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return resp


@app.post("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and validate_session(token, ip=get_client_ip(request)) is not None:
        # Valid session presented -> destroy it
        invalidate_session(token)
    elif not token:
        # No cookie at all: clear the browser cookie but do NOT destroy server-side
        # sessions. This prevents cross-site "logout CSRF" from killing the admin's
        # active sessions via a cookieless forged POST.
        pass

    from utils.auth import is_secure_cookie
    is_https = is_secure_cookie(request)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_https,
    )
    return resp


@app.post("/api/createNewFolder")
async def api_new_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", "/"))
    folder_name = sanitize_path(data.get("name", "")).strip("/")

    logger.info(f"createNewFolder path={safe_path} name={folder_name}")
    target_dir = drive.get_directory(safe_path)
    if target_dir and hasattr(target_dir, "contents"):
        for id in target_dir.contents:
            f = target_dir.contents[id]
            if f.type == "folder":
                if f.name.lower() == folder_name.lower():
                    return JSONResponse(
                        {
                            "status": "Folder with the name already exist in current directory"
                        }
                    )

    drive.new_folder(safe_path, folder_name)

    # Record change event for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        # Find the newly created folder to get its ID
        new_folder_id = None
        target = drive.get_directory(safe_path)
        if target and hasattr(target, "contents"):
            for item_id, item in target.contents.items():
                if getattr(item, "type", "") == "folder" and getattr(item, "name", "").lower() == folder_name.lower():
                    new_folder_id = item_id
                    break
        if new_folder_id:
            ver = await record_change_async("FOLDER_CREATED", new_folder_id, "folder")
            if ver > 0:
                broadcast_sync_event("FOLDER_CREATED", new_folder_id, "folder", ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (new_folder): {sync_err}")

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok"})


def _strip_internal_ids(node):
    """Recursively remove Telegram-internal message IDs and other internal
    metadata from serialized drive data before serving it to non-admin visitors."""
    if isinstance(node, dict):
        for key in ("file_id", "auth_hashes", "device", "display_path", "human_path"):
            node.pop(key, None)
        for v in node.values():
            _strip_internal_ids(v)
    elif isinstance(node, list):
        for v in node:
            _strip_internal_ids(v)


@app.post("/api/getDirectory")
async def api_get_directory(request: Request):
    from utils.directoryHandler import ensure_drive_data, _count_total_drive_items, sync_drive_data_from_telegram
    drive = ensure_drive_data()

    try:
        data = await request.json()
    except Exception:
        data = {}

    auth = data.get("auth")
    raw_path = data.get("path", "/")
    path = sanitize_path(raw_path) if not (raw_path.startswith("/tags/") or "/search_" in raw_path or raw_path.startswith("/share_") or raw_path == "/trash" or raw_path == "/recent") else raw_path
    is_admin = is_admin_authenticated(request)

    # On fresh startup/render container reboot, if drive is empty, auto-sync from Telegram backup
    if is_admin and _count_total_drive_items(drive) == 0:
        try:
            from utils.clients import is_telegram_ready
            if is_telegram_ready():
                synced = await sync_drive_data_from_telegram(force=True)
                if synced:
                    drive = ensure_drive_data()
        except Exception as sync_err:
            logger.debug(f"Auto-sync on empty directory check: {sync_err}")

    logger.info(f"getFolder path={path} is_admin={is_admin}")

    # Enforce authorization: non-admin users can ONLY view /share_ paths with valid auth token
    if not is_admin:
        if not path.startswith("/share_") and not path.startswith("share_") and not auth:
            raise HTTPException(status_code=401, detail="Authentication required")

    breadcrumbs = drive.get_breadcrumbs(path)

    if path == "/trash":
        if not is_admin:
            raise HTTPException(status_code=401, detail="Unauthorized access to trash")
        trash_data = {"contents": drive.get_trashed_files_folders()}
        folder_data = convert_class_to_dict(trash_data, isObject=False, showtrash=True)

    elif path == "/recent":
        if not is_admin:
            raise HTTPException(status_code=401, detail="Unauthorized access to recent")
        recent_data = {"contents": drive.get_recent_files(limit=50)}
        folder_data = convert_class_to_dict(recent_data, isObject=False, showtrash=False)

    elif path.startswith("/tags/"):
        if not is_admin:
            raise HTTPException(status_code=401, detail="Unauthorized access to tags")
        tag_name = urllib.parse.unquote(path.replace("/tags/", "").strip("/"))
        tagged_data = {"contents": drive.get_tagged_items(tag_name)}
        folder_data = convert_class_to_dict(tagged_data, isObject=False, showtrash=False)

    elif "/search_" in path or path == "/search":
        if not is_admin:
            raise HTTPException(status_code=401, detail="Unauthorized access to search")
        query = urllib.parse.unquote(path.split("_", 1)[1]) if "/search_" in path else ""
        search_data = {"contents": drive.search_file_folder(query)}
        folder_data = convert_class_to_dict(search_data, isObject=False, showtrash=False)

    elif "/share_" in path or path.startswith("share_"):
        share_path = sanitize_path(path.replace("/share_", "").replace("share_", "").strip("/"))
        res = drive.get_directory(share_path, is_admin=is_admin, auth=auth)
        if not res:
            return JSONResponse({"status": "Folder not found or access expired"}, status_code=404)
        if isinstance(res, tuple):
            folder_data, auth_home_path = res
        else:
            folder_data, auth_home_path = res, None
        auth_home_path = auth_home_path.replace("//", "/") if auth_home_path else None
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
        if not is_admin:
            _strip_internal_ids(folder_data)
            # Never reveal drive structure above the shared scope
            breadcrumbs = []
        return JSONResponse(
            {"status": "ok", "data": folder_data, "breadcrumbs": breadcrumbs, "auth_home_path": auth_home_path}
        )

    else:
        if not is_admin:
            raise HTTPException(status_code=401, detail="Unauthorized")
        folder_data = drive.get_directory(path, is_admin=True)
        if not folder_data:
            return JSONResponse({"status": "Folder not found"}, status_code=404)
        if isinstance(folder_data, tuple):
            folder_data = folder_data[0]
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)

    total_files, total_bytes = drive.get_drive_stats()
    return JSONResponse({
        "status": "ok",
        "data": folder_data,
        "breadcrumbs": breadcrumbs,
        "auth_home_path": None,
        "stats": {
            "total_files": total_files,
            "total_bytes": total_bytes
        }
    })


@app.post("/api/moveFileFolder")
async def move_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    src_path = sanitize_path(data.get("src_path", ""))
    dest_path = sanitize_path(data.get("dest_path", "/"))

    logger.info(f"moveFileFolder src={src_path} dest={dest_path}")
    try:
        drive.move_file_folder(src_path, dest_path)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Error moving file/folder: {e}")
        return JSONResponse({"status": str(e)}, status_code=400)


@app.post("/api/copyFileFolder")
async def copy_file_folder_api(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    src_path = sanitize_path(data.get("src_path", ""))
    dest_path = sanitize_path(data.get("dest_path", "/")) if data.get("dest_path") else None

    logger.info(f"copyFileFolder src={src_path} dest={dest_path}")
    try:
        new_id = drive.copy_file_folder(src_path, dest_path)
        return JSONResponse({"status": "ok", "new_id": new_id})
    except Exception as e:
        logger.error(f"Error copying file/folder: {e}")
        return JSONResponse({"status": str(e)}, status_code=400)



@app.post("/api/checkFileExists")
async def check_file_exists(request: Request, _auth: Session = Depends(require_auth)):
    """
    Checks if a file with the given name already exists in the target folder.
    Used for pre-upload conflict resolution prompts (Replace vs Keep Both vs Cancel).
    """
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()
    data = await request.json()
    target_path = sanitize_path(data.get("path", "/"))
    filename = str(data.get("filename", "")).strip()
    relative_path = data.get("relative_path")

    if relative_path and str(relative_path).strip():
        clean_rel = str(relative_path).replace("\\", "/").strip("/")
        parts = [p for p in clean_rel.split("/") if p and p not in (".", "..")]
        if len(parts) > 1:
            dir_chain = "/".join(parts[:-1])
            folder_res = drive.get_directory(f"{target_path.rstrip('/')}/{dir_chain}", is_admin=True)
            folder_obj = folder_res[0] if isinstance(folder_res, tuple) else folder_res
        else:
            folder_res = drive.get_directory(target_path, is_admin=True)
            folder_obj = folder_res[0] if isinstance(folder_res, tuple) else folder_res
        if parts and parts[-1]:
            filename = parts[-1]
    else:
        folder_res = drive.get_directory(target_path, is_admin=True)
        folder_obj = folder_res[0] if isinstance(folder_res, tuple) else folder_res

    if not filename:
        return JSONResponse({"exists": False})

    exists = False
    existing_size = 0
    existing_id = None

    if folder_obj and hasattr(folder_obj, "contents"):
        for item in folder_obj.contents.values():
            if getattr(item, "type", "") == "file" and getattr(item, "name", "").lower() == filename.lower():
                exists = True
                existing_size = getattr(item, "size", 0)
                existing_id = getattr(item, "id", None)
                break

    return JSONResponse({
        "status": "ok",
        "exists": exists,
        "filename": filename,
        "existing_size": existing_size,
        "existing_id": existing_id,
        "path": target_path
    })


@app.post("/api/createFolderTree")
async def create_folder_tree(request: Request, _auth: Session = Depends(require_auth)):
    """Pre-creates a nested folder hierarchy (including empty directories) under a base path."""
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()
    data = await request.json()
    base_path = sanitize_path(data.get("base_path", "/"))
    folder_paths = data.get("folders", [])

    if not isinstance(folder_paths, list):
        folder_paths = [str(folder_paths)] if folder_paths else []

    created_paths = drive.ensure_folder_tree(base_path, folder_paths)
    return JSONResponse({
        "status": "ok",
        "created_count": len(created_paths),
        "paths": created_paths
    })


SAVE_PROGRESS = {}


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    path: str = Form(...),
    id: str = Form(...),
    total_size: str = Form(...),
    conflict: str = Form("keep_both"),
    relative_path: Optional[str] = Form(None),
    batch_id: Optional[str] = Form(None),
    _auth: Session = Depends(require_auth),
):
    global SAVE_PROGRESS

    safe_path = sanitize_path(path)
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    raw_filename = file.filename or "uploaded_file"
    safe_filename = "".join(c for c in raw_filename if c not in ('\x00', '\r', '\n', '/', '\\')).strip()

    # Handle relative_path for nested folder tree preservation
    clean_rel_path = None
    if relative_path and str(relative_path).strip():
        clean_rel = str(relative_path).replace("\\", "/").strip("/")
        parts = [p for p in clean_rel.split("/") if p and p not in (".", "..")]
        if parts:
            clean_rel_path = "/".join(parts)
            if len(parts) > 1:
                dir_chain = "/".join(parts[:-1])
                safe_path = drive.resolve_or_create_folder_hierarchy(safe_path, dir_chain)
            if parts[-1]:
                safe_filename = "".join(c for c in parts[-1] if c not in ('\x00', '\r', '\n', '/', '\\')).strip() or safe_filename

    # Sanitize upload ID and file extensions to prevent path traversal
    safe_id = "".join(c for c in str(id) if c.isalnum() or c in ("-", "_"))[:64]
    if not safe_id:
        safe_id = getRandomID()

    ext = (safe_filename.rsplit(".", 1)[-1] if "." in safe_filename else "bin").lower()
    ext = "".join(c for c in ext if c.isalnum())[:16] or "bin"

    try:
        total_size = int(total_size)
    except (ValueError, TypeError):
        total_size = 0

    SAVE_PROGRESS[safe_id] = ("running", 0, total_size)
    if len(SAVE_PROGRESS) > 200:
        excess = len(SAVE_PROGRESS) - 200
        for old_k in list(SAVE_PROGRESS.keys())[:excess]:
            SAVE_PROGRESS.pop(old_k, None)

    cache_dir = Path("./cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_location = cache_dir / f"{safe_id}.{ext}"

    file_size = 0

    try:
        # Stream in conservative 256KB chunks to prevent RAM accumulation on Render (512MB RAM)
        chunk_size = 256 * 1024
        async with aiofiles.open(file_location, "wb") as buffer:
            while chunk := await file.read(chunk_size):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File size exceeds {MAX_FILE_SIZE} bytes limit",
                    )
                await buffer.write(chunk)
                SAVE_PROGRESS[safe_id] = ("running", file_size, total_size)
    except HTTPException:
        SAVE_PROGRESS[safe_id] = ("error", file_size, total_size)
        file_location.unlink(missing_ok=True)  # Delete partially written file
        raise
    except Exception as write_err:
        SAVE_PROGRESS[safe_id] = ("error", file_size, total_size)
        file_location.unlink(missing_ok=True)
        logger.error(f"Upload {safe_id} failed while streaming to disk: {write_err}")
        raise HTTPException(status_code=500, detail="Failed to store uploaded file")
    finally:
        # Immediately close Starlette's SpooledTemporaryFile to free /tmp disk and RAM buffers
        try:
            await file.close()
        except Exception:
            pass

    SAVE_PROGRESS[safe_id] = ("completed", file_size, file_size)

    # Queue in TransferManager (handles bounded concurrency, retry, persistence, speed/ETA tracking)
    from utils.transfer_manager import transfer_manager
    await transfer_manager.queue_upload(
        file_path=str(file_location),
        id=safe_id,
        target_path=safe_path,
        filename=safe_filename,
        file_size=file_size,
        conflict=conflict,
        relative_path=clean_rel_path,
        batch_id=batch_id,
    )

    from utils.extra import clean_memory
    clean_memory()

    return JSONResponse({"id": safe_id, "status": "ok", "target_path": safe_path})


@app.post("/api/getSaveProgress")
async def get_save_progress(request: Request, _auth: Session = Depends(require_auth)):
    global SAVE_PROGRESS

    data = await request.json()

    logger.info(f"getSaveProgress id={data.get('id')}")
    try:
        progress = SAVE_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getUploadProgress")
async def get_upload_progress(request: Request, _auth: Session = Depends(require_auth)):
    from utils.uploader import PROGRESS_CACHE
    from utils.transfer_manager import transfer_manager

    data = await request.json()
    item_id = str(data.get("id", ""))
    logger.info(f"getUploadProgress id={item_id}")

    # Check TransferManager first for richer metrics
    transfer_info = transfer_manager.get_transfer(item_id)
    if transfer_info:
        # Compatibility tuple: (status, current, total, filename)
        compat_tuple = (
            "running" if transfer_info["state"] in ("queued", "preparing", "uploading") else (
                "completed" if transfer_info["state"] == "completed" else (
                    "cancelled" if transfer_info["state"] == "cancelled" else (
                        "waiting" if transfer_info["state"] == "retrying" else "error"
                    )
                )
            ),
            transfer_info["transferred"],
            transfer_info["size"],
            transfer_info["filename"]
        )
        return JSONResponse({"status": "ok", "data": compat_tuple, "transfer": transfer_info})

    try:
        progress = PROGRESS_CACHE[item_id]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getActiveUploads")
async def get_active_uploads(request: Request, _auth: Session = Depends(require_auth)):
    from utils.transfer_manager import transfer_manager
    from utils.uploader import PROGRESS_CACHE

    all_data = transfer_manager.get_all_transfers(filter_type="upload")
    active = []
    for t in all_data.get("transfers", []):
        if t["state"] in ("queued", "preparing", "uploading", "retrying"):
            active.append({
                "id": t["id"],
                "status": "waiting" if t["state"] == "retrying" else "running",
                "current": t["transferred"],
                "total": t["size"],
                "filename": t["filename"],
                "speed": t["speed"],
                "speed_formatted": t["speed_formatted"],
                "eta": t["eta"],
                "eta_formatted": t["eta_formatted"],
                "percentage": t["percentage"],
                "state": t["state"]
            })

    # Fallback to PROGRESS_CACHE if transfer manager has no active uploads
    if not active:
        for upload_id, prog in list(PROGRESS_CACHE.items()):
            status = prog[0]
            current = prog[1]
            total = prog[2]
            filename = prog[3] if len(prog) > 3 else "File"
            if status in ("running", "waiting"):
                active.append({
                    "id": upload_id,
                    "status": status,
                    "current": current,
                    "total": total,
                    "filename": filename
                })

    return JSONResponse({"status": "ok", "active": active, "stats": all_data.get("stats", {})})


@app.post("/api/cancelUpload")
async def cancel_upload(request: Request, _auth: Session = Depends(require_auth)):
    from utils.transfer_manager import transfer_manager
    from utils.uploader import STOP_TRANSMISSION
    from utils.downloader import STOP_DOWNLOAD

    data = await request.json()

    upload_id = str(data.get("id") or "").strip()
    if not upload_id:
        return JSONResponse({"status": "Upload id is required"}, status_code=400)

    logger.info(f"cancelUpload id={upload_id}")
    if upload_id not in STOP_TRANSMISSION:
        STOP_TRANSMISSION.append(upload_id)
    if upload_id not in STOP_DOWNLOAD:
        STOP_DOWNLOAD.append(upload_id)
    await transfer_manager.cancel_transfer(upload_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/renameFileFolder")
async def rename_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", ""))
    safe_name = sanitize_path(data.get("name", "")).strip("/")

    logger.info(f"renameFileFolder path={safe_path} name={safe_name}")

    # Capture old name for change tracking before mutation
    _old_name = None
    _entity_id = None
    _entity_type = "file"
    try:
        _clean = safe_path.strip("/")
        _file_id = _clean.split("/")[-1] if "/" in _clean else _clean
        _item = drive.find_item_by_id(_file_id)
        if _item:
            _old_name = getattr(_item, "name", None)
            _entity_id = _file_id
            _entity_type = getattr(_item, "type", "file")
    except Exception:
        pass

    drive.rename_file_folder(safe_path, safe_name)

    # Record change event for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        if _entity_id and _old_name:
            op = "FOLDER_RENAMED" if _entity_type == "folder" else "FILE_RENAMED"
            ver = await record_change_async(op, _entity_id, _entity_type, old_name=_old_name, new_name=safe_name)
            if ver > 0:
                broadcast_sync_event(op, _entity_id, _entity_type, ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (rename): {sync_err}")

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok"})


@app.post("/api/tagFileFolder")
async def tag_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    path = sanitize_path(data.get("path", ""))
    action = data.get("action", "add")  # 'add' or 'remove'
    tag = data.get("tag", "").strip()

    if not path or not tag:
        return JSONResponse({"status": "Path and tag are required"}, status_code=400)

    logger.info(f"tagFileFolder path={path} action={action} tag={tag}")
    if action == "add":
        tags = drive.add_tag(path, tag)
    else:
        tags = drive.remove_tag(path, tag)

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok", "tags": tags})



@app.post("/api/trashFileFolder")
async def trash_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", ""))
    trash_val = bool(data.get("trash", True))

    logger.info(f"trashFileFolder path={safe_path} trash={trash_val}")

    # Capture entity info for change tracking before mutation
    _trash_entity_id = None
    _trash_entity_type = "file"
    try:
        _t_clean = safe_path.strip("/")
        _t_fid = _t_clean.split("/")[-1] if "/" in _t_clean else _t_clean
        _t_item = drive.find_item_by_id(_t_fid)
        if _t_item:
            _trash_entity_id = _t_fid
            _trash_entity_type = getattr(_t_item, "type", "file")
    except Exception:
        pass

    drive.trash_file_folder(safe_path, trash_val)

    # Record change event for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        if _trash_entity_id:
            op = "FILE_TRASHED" if (trash_val and _trash_entity_type == "file") else ("FILE_RESTORED" if not trash_val and _trash_entity_type == "file" else ("FOLDER_TRASHED" if trash_val else "FOLDER_RESTORED"))
            ver = await record_change_async(op, _trash_entity_id, _trash_entity_type)
            if ver > 0:
                broadcast_sync_event(op, _trash_entity_id, _trash_entity_type, ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (trash): {sync_err}")

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok"})


@app.post("/api/syncDriveData")
@app.get("/api/syncDriveData")
async def api_sync_drive_data(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import sync_drive_data_from_telegram
    updated = await sync_drive_data_from_telegram(force=True)
    return JSONResponse({"status": "ok", "updated": updated})


@app.post("/api/deleteFileFolder")
async def delete_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    from utils.clients import get_client
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", ""))
    logger.info(f"deleteFileFolder path={safe_path}")

    # Capture entity info for change tracking before mutation
    _del_entity_id = None
    _del_entity_type = "file"
    try:
        _d_clean = safe_path.strip("/")
        _d_fid = _d_clean.split("/")[-1] if "/" in _d_clean else _d_clean
        _d_item = drive.find_item_by_id(_d_fid)
        if _d_item:
            _del_entity_id = _d_fid
            _del_entity_type = getattr(_d_item, "type", "file")
    except Exception:
        pass

    deleted_msg_ids = drive.delete_file_folder(safe_path)

    # Record change event for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        if _del_entity_id:
            op = "FILE_DELETED" if _del_entity_type == "file" else "FOLDER_DELETED"
            ver = await record_change_async(op, _del_entity_id, _del_entity_type)
            if ver > 0:
                broadcast_sync_event(op, _del_entity_id, _del_entity_type, ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (delete): {sync_err}")

    # Delete message(s) from Telegram Storage Channel
    if deleted_msg_ids:
        try:
            client = get_client()
            if client and STORAGE_CHANNEL:
                for i in range(0, len(deleted_msg_ids), 100):
                    chunk = deleted_msg_ids[i:i + 100]
                    try:
                        await client.delete_messages(STORAGE_CHANNEL, message_ids=chunk)
                    except Exception as chunk_err:
                        logger.warning(f"Telegram chunk delete warning: {chunk_err}")
                logger.info(f"Deleted {len(deleted_msg_ids)} message(s) from Telegram Storage Channel.")
        except Exception as e:
            logger.warning(f"Failed to delete message(s) from Telegram Storage Channel: {e}")

    # Immediately trigger asynchronous Telegram backup
    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok", "deleted_msg_count": len(deleted_msg_ids)})

@app.post("/api/search")
async def api_search_drive(request: Request, _auth: Session = Depends(require_auth)):
    """
    Deep drive-wide search with multi-criteria filtering.
    Accepts both frontend key aliases (type/location/date_range) and
    engine-native keys (item_type/search_root/date_filter) for compatibility.
    """
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    try:
        data = await request.json()
    except Exception:
        data = {}

    query = str(data.get("query", "")).strip()

    # Resolve key aliases (frontend style takes precedence)
    item_type = data.get("type") or data.get("item_type") or "all"
    search_root = data.get("location") or data.get("search_root") or "/"
    date_filter = data.get("date_range") or data.get("date_filter")
    min_size = data.get("min_size")
    max_size = data.get("max_size")
    date_after = data.get("date_after")
    date_before = data.get("date_before")
    extension = data.get("extension")

    try:
        min_size = int(min_size) if min_size is not None and str(min_size).isdigit() else None
    except Exception:
        min_size = None

    try:
        max_size = int(max_size) if max_size is not None and str(max_size).isdigit() else None
    except Exception:
        max_size = None

    raw_results = drive.search_file_folder(
        query=query,
        search_root=str(search_root),
        item_type=str(item_type),
        min_size=min_size,
        max_size=max_size,
        date_filter=date_filter,
        date_after=date_after,
        date_before=date_before,
        extension=extension,
    )

    converted = convert_class_to_dict({"contents": raw_results}, isObject=False, showtrash=False)
    breadcrumbs = [{"name": "My Drive", "path": "/", "id": "root"},
                   {"name": "Search Results", "path": f"/search_{urllib.parse.quote(query)}" if query else "/search"}]
    total_files, total_bytes = drive.get_drive_stats()

    return JSONResponse({
        "status": "ok",
        "query": query,
        "filters": {
            "search_root": search_root,
            "item_type": item_type,
            "min_size": min_size,
            "max_size": max_size,
            "date_filter": date_filter,
            "date_after": date_after,
            "date_before": date_before,
            "extension": extension,
        },
        "count": len(converted.get("contents", {})),
        "data": converted,
        "breadcrumbs": breadcrumbs,
        "stats": {
            "total_files": total_files,
            "total_bytes": total_bytes
        }
    })


@app.post("/api/bulkDelete")
async def bulk_delete_api(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    from utils.clients import get_client
    drive = ensure_drive_data()

    data = await request.json()
    raw_paths = data.get("paths", [])
    paths = [sanitize_path(p) for p in raw_paths if p]
    logger.info(f"bulkDelete {len(paths)} path(s)")
    if not paths:
        return JSONResponse({"status": "ok", "deleted_count": 0})

    # Capture entity info for change tracking before mutation
    _bulk_del_entities = []
    try:
        for bp in paths:
            _b_clean = bp.strip("/")
            _b_fid = _b_clean.split("/")[-1] if "/" in _b_clean else _b_clean
            _b_item = drive.find_item_by_id(_b_fid)
            if _b_item:
                _bulk_del_entities.append((_b_fid, getattr(_b_item, "type", "file")))
    except Exception:
        pass

    deleted_msg_ids = drive.bulk_delete(paths)

    # Record change events for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        for _eid, _etype in _bulk_del_entities:
            op = "FILE_DELETED" if _etype == "file" else "FOLDER_DELETED"
            ver = await record_change_async(op, _eid, _etype)
            if ver > 0:
                broadcast_sync_event(op, _eid, _etype, ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (bulk_delete): {sync_err}")

    # Delete message(s) from Telegram Storage Channel
    if deleted_msg_ids:
        try:
            client = get_client()
            if client and STORAGE_CHANNEL:
                # Delete in chunks of 100 to respect Telegram API limits
                for i in range(0, len(deleted_msg_ids), 100):
                    chunk = deleted_msg_ids[i:i + 100]
                    try:
                        await client.delete_messages(STORAGE_CHANNEL, message_ids=chunk)
                    except Exception as chunk_err:
                        logger.warning(f"Telegram chunk delete warning: {chunk_err}")
                logger.info(f"Bulk deleted {len(deleted_msg_ids)} message(s) from Telegram Storage Channel.")
        except Exception as e:
            logger.warning(f"Failed to bulk delete messages from Telegram Storage Channel: {e}")

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok", "deleted_count": len(paths), "deleted_msg_count": len(deleted_msg_ids)})


@app.post("/api/bulkTrash")
async def bulk_trash_api(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    raw_paths = data.get("paths", [])
    paths = [sanitize_path(p) for p in raw_paths if p]
    trash = bool(data.get("trash", True))
    logger.info(f"bulkTrash {len(paths)} path(s) to trash={trash}")

    # Capture entity info for change tracking before mutation
    _bulk_trash_entities = []
    try:
        for bp in paths:
            _bt_clean = bp.strip("/")
            _bt_fid = _bt_clean.split("/")[-1] if "/" in _bt_clean else _bt_clean
            _bt_item = drive.find_item_by_id(_bt_fid)
            if _bt_item:
                _bulk_trash_entities.append((_bt_fid, getattr(_bt_item, "type", "file")))
    except Exception:
        pass

    for path in paths:
        drive.trash_file_folder(path, trash)

    # Record change events for sync engine
    try:
        from utils.sync import record_change_async, broadcast_sync_event
        for _eid, _etype in _bulk_trash_entities:
            op = ("FILE_TRASHED" if _etype == "file" else "FOLDER_TRASHED") if trash else ("FILE_RESTORED" if _etype == "file" else "FOLDER_RESTORED")
            ver = await record_change_async(op, _eid, _etype)
            if ver > 0:
                broadcast_sync_event(op, _eid, _etype, ver, "admin")
    except Exception as sync_err:
        logger.debug(f"Sync tracking note (bulk_trash): {sync_err}")

    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok", "processed_count": len(paths)})


@app.post("/api/getFileInfoFromUrl")
async def getFileInfoFromUrl(request: Request, _auth: Session = Depends(require_auth)):
    data = await request.json()
    url = str(data.get("url") or "").strip()
    if not url:
        return JSONResponse({"status": "URL is required"}, status_code=400)

    logger.info(f"getFileInfoFromUrl url={url}")
    try:
        file_info = await get_file_info_from_url(url)
        return JSONResponse({"status": "ok", "data": file_info})
    except Exception as e:
        return JSONResponse({"status": str(e)}, status_code=400)


@app.post("/api/startFileDownloadFromUrl")
async def startFileDownloadFromUrl(request: Request, _auth: Session = Depends(require_auth)):
    from utils.transfer_manager import transfer_manager

    data = await request.json()
    url = str(data.get("url") or "").strip()
    if not url:
        return JSONResponse({"status": "URL is required"}, status_code=400)

    safe_path = sanitize_path(data.get("path", "/"))
    safe_name = sanitize_path(data.get("filename", "downloaded_file")).strip("/")
    single_threaded = bool(data.get("singleThreaded", False))

    logger.info(f"startFileDownloadFromUrl filename={safe_name} path={safe_path}")
    try:
        id = getRandomID()
        await transfer_manager.queue_download(
            url=url,
            id=id,
            target_path=safe_path,
            filename=safe_name,
            single_threaded=single_threaded,
        )
        return JSONResponse({"status": "ok", "id": id})
    except Exception as e:
        return JSONResponse({"status": str(e)}, status_code=400)


@app.post("/api/getFileDownloadProgress")
async def getFileDownloadProgress(request: Request, _auth: Session = Depends(require_auth)):
    from utils.downloader import DOWNLOAD_PROGRESS
    from utils.transfer_manager import transfer_manager

    data = await request.json()
    item_id = str(data.get("id", ""))
    logger.info(f"getFileDownloadProgress id={item_id}")

    transfer_info = transfer_manager.get_transfer(item_id)
    if transfer_info:
        compat_tuple = (
            "running" if transfer_info["state"] in ("queued", "preparing", "downloading") else (
                "completed" if transfer_info["state"] == "completed" else (
                    "cancelled" if transfer_info["state"] == "cancelled" else (
                        "waiting" if transfer_info["state"] == "retrying" else "error"
                    )
                )
            ),
            transfer_info["transferred"],
            transfer_info["size"],
        )
        return JSONResponse({"status": "ok", "data": compat_tuple, "transfer": transfer_info})

    try:
        progress = DOWNLOAD_PROGRESS[item_id]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


# ---------------------------------------------------------------------------
# Transfer Manager Dedicated REST APIs
# ---------------------------------------------------------------------------

@app.get("/api/transfers")
@app.post("/api/getTransfers")
async def api_get_transfers(request: Request, _auth: Session = Depends(require_auth)):
    """Returns all active, queued, and historical transfers with real-time stats."""
    from utils.transfer_manager import transfer_manager
    filter_type = request.query_params.get("type")
    filter_state = request.query_params.get("state")
    if request.method == "POST":
        try:
            body = await request.json()
            filter_type = filter_type or body.get("type")
            filter_state = filter_state or body.get("state")
        except Exception:
            pass

    return JSONResponse(transfer_manager.get_all_transfers(filter_type=filter_type, filter_state=filter_state))


@app.get("/api/transfers/{transfer_id}")
async def api_get_single_transfer(transfer_id: str, request: Request, _auth: Session = Depends(require_auth)):
    """Returns detailed real-time state for a specific transfer ID."""
    from utils.transfer_manager import transfer_manager
    info = transfer_manager.get_transfer(transfer_id)
    if not info:
        raise HTTPException(status_code=404, detail="Transfer not found")
    return JSONResponse({"status": "ok", "transfer": info})


@app.post("/api/transfers/{transfer_id}/cancel")
async def api_cancel_single_transfer(transfer_id: str, request: Request, _auth: Session = Depends(require_auth)):
    """Cancels an active or queued transfer."""
    from utils.transfer_manager import transfer_manager
    success = await transfer_manager.cancel_transfer(transfer_id)
    if not success:
        return JSONResponse({"status": "Transfer not found or already terminated"}, status_code=404)
    return JSONResponse({"status": "ok", "message": f"Transfer {transfer_id} cancelled"})


@app.post("/api/transfers/{transfer_id}/retry")
async def api_retry_single_transfer(transfer_id: str, request: Request, _auth: Session = Depends(require_auth)):
    """Retries a failed or cancelled transfer."""
    from utils.transfer_manager import transfer_manager
    item = await transfer_manager.retry_transfer(transfer_id)
    if not item:
        return JSONResponse({"status": "Transfer not found"}, status_code=404)
    return JSONResponse({"status": "ok", "transfer": item.to_dict()})


@app.post("/api/transfers/{transfer_id}/remove")
async def api_remove_single_transfer(transfer_id: str, request: Request, _auth: Session = Depends(require_auth)):
    """Removes a transfer from history."""
    from utils.transfer_manager import transfer_manager
    success = await transfer_manager.remove_transfer(transfer_id)
    if not success:
        return JSONResponse({"status": "Transfer not found"}, status_code=404)
    return JSONResponse({"status": "ok", "message": f"Transfer {transfer_id} removed from history"})


@app.post("/api/transfers/clear")
async def api_clear_finished_transfers(request: Request, _auth: Session = Depends(require_auth)):
    """Clears all completed, failed, and cancelled transfers from history."""
    from utils.transfer_manager import transfer_manager
    count = await transfer_manager.clear_finished_transfers()
    return JSONResponse({"status": "ok", "cleared_count": count})


# ---------------------------------------------------------------------------
# Duplicate Detection & Management API
# ---------------------------------------------------------------------------

@app.api_route("/api/duplicates/status", methods=["GET", "POST"])
async def api_duplicates_status(request: Request, _auth: Session = Depends(require_auth)):
    """Returns duplicate scan status, progress, and recoverable storage statistics."""
    from utils.duplicate_manager import duplicate_manager
    return JSONResponse(duplicate_manager.get_status())


@app.post("/api/duplicates/scan")
async def api_duplicates_trigger_scan(request: Request, _auth: Session = Depends(require_auth)):
    """Initiates an asynchronous background duplicate hash scan."""
    from utils.duplicate_manager import duplicate_manager
    started = duplicate_manager.start_background_scan()
    status = duplicate_manager.get_status()
    return JSONResponse({
        "status": "ok" if started else "already_running",
        "message": "Duplicate scan started in background" if started else "Duplicate scan is already running",
        "scan_status": status
    })


@app.post("/api/duplicates/list")
async def api_duplicates_list(request: Request, _auth: Session = Depends(require_auth)):
    """Returns grouped duplicate files matching search query and category filters."""
    from utils.duplicate_manager import duplicate_manager
    try:
        data = await request.json()
    except Exception:
        data = {}

    query = str(data.get("query", "") or "").strip()
    category = str(data.get("category", "all") or "all").strip().lower()
    sort_by = str(data.get("sort_by", "recoverable_size") or "recoverable_size").strip()

    result = duplicate_manager.get_duplicate_groups(
        query=query,
        mime_category=category,
        sort_by=sort_by
    )
    return JSONResponse(result)


@app.post("/api/duplicates/delete")
async def api_duplicates_delete(request: Request, _auth: Session = Depends(require_auth)):
    """
    Safely deletes or trashes selected duplicate files.
    Enforces the retention safety invariant (cannot delete all copies of a duplicate group).
    """
    from utils.duplicate_manager import duplicate_manager
    try:
        data = await request.json()
    except Exception:
        data = {}

    target_uuids = data.get("target_uuids", [])
    if not isinstance(target_uuids, list) or not target_uuids:
        raise HTTPException(status_code=400, detail="target_uuids list is required")

    soft_delete = not bool(data.get("permanent", False))

    try:
        result = duplicate_manager.delete_duplicates(
            target_file_uuids=target_uuids,
            soft_delete=soft_delete
        )
        return JSONResponse(result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Duplicate deletion error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete duplicates: {e}")



@app.post("/api/getFolderShareAuth")
async def getFolderShareAuth(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", "/"))

    logger.info(f"getFolderShareAuth path={safe_path}")

    try:
        auth = drive.get_folder_auth(safe_path)
        return JSONResponse({"status": "ok", "auth": auth})
    except Exception:
        return JSONResponse({"status": "not found"})


# ---------------------------------------------------------------------------
# Properties & Details REST APIs
# ---------------------------------------------------------------------------

@app.get("/api/files/{file_id}/properties")
@app.post("/api/getFileProperties")
async def api_get_file_properties(request: Request, file_id: Optional[str] = None):
    from utils.directoryHandler import ensure_drive_data
    from utils.properties import PropertiesFormatter
    drive = ensure_drive_data()
    is_admin = is_admin_authenticated(request)

    target_id = file_id
    if not target_id:
        try:
            body = await request.json()
            target_id = body.get("id") or body.get("file_id")
        except Exception:
            pass
    if not target_id:
        target_id = request.query_params.get("id") or request.query_params.get("file_id")

    if not target_id:
        raise HTTPException(status_code=400, detail="Missing file ID")

    auth_token = request.query_params.get("auth") or request.query_params.get("token")
    if not is_admin and not auth_token:
        try:
            body = await request.json()
            auth_token = body.get("auth") or body.get("token")
        except Exception:
            pass

    file_obj = drive.find_item_by_id(target_id)
    if not file_obj or getattr(file_obj, "type", "") != "file":
        raise HTTPException(status_code=404, detail="File not found")

    if not is_admin:
        if not auth_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.shareManager import get_share_by_token
        share_rec = get_share_by_token(auth_token)
        if not share_rec:
            raise HTTPException(status_code=401, detail="Invalid or expired share token")
        share_target = share_rec.get("target_id_path", "")
        file_full_path = (getattr(file_obj, "path", "").rstrip("/") + "/" + file_obj.id).replace("//", "/")
        if not file_full_path.startswith(share_target) and file_obj.id != share_rec.get("target_id"):
            raise HTTPException(status_code=401, detail="File not accessible with this share token")

    props = PropertiesFormatter.get_file_properties(file_obj, drive, is_admin=is_admin, request_base_url=str(request.base_url))
    return JSONResponse(props)


@app.get("/api/folders/{folder_id}/properties")
@app.post("/api/getFolderProperties")
async def api_get_folder_properties(request: Request, folder_id: Optional[str] = None):
    from utils.directoryHandler import ensure_drive_data
    from utils.properties import PropertiesFormatter
    drive = ensure_drive_data()
    is_admin = is_admin_authenticated(request)

    target_id = folder_id
    if not target_id:
        try:
            body = await request.json()
            target_id = body.get("id") or body.get("folder_id")
        except Exception:
            pass
    if not target_id:
        target_id = request.query_params.get("id") or request.query_params.get("folder_id")

    if not target_id:
        raise HTTPException(status_code=400, detail="Missing folder ID")

    auth_token = request.query_params.get("auth") or request.query_params.get("token")
    if not is_admin and not auth_token:
        try:
            body = await request.json()
            auth_token = body.get("auth") or body.get("token")
        except Exception:
            pass

    folder_obj = drive.find_item_by_id(target_id)
    if not folder_obj or getattr(folder_obj, "type", "") != "folder":
        raise HTTPException(status_code=404, detail="Folder not found")

    if not is_admin:
        if not auth_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.shareManager import get_share_by_token
        share_rec = get_share_by_token(auth_token)
        if not share_rec:
            raise HTTPException(status_code=401, detail="Invalid or expired share token")
        share_target = share_rec.get("target_id_path", "")
        folder_full_path = (getattr(folder_obj, "path", "").rstrip("/") + "/" + folder_obj.id).replace("//", "/")
        if not folder_full_path.startswith(share_target) and folder_obj.id != share_rec.get("target_id"):
            raise HTTPException(status_code=401, detail="Folder not accessible with this share token")

    props = PropertiesFormatter.get_folder_properties(folder_obj, drive, is_admin=is_admin, request_base_url=str(request.base_url))
    return JSONResponse(props)


@app.get("/api/files/{file_id}/activity")
@app.post("/api/getFileActivity")
async def api_get_file_activity(request: Request, file_id: Optional[str] = None):
    from utils.directoryHandler import ensure_drive_data
    from utils.properties import ActivityTracker
    drive = ensure_drive_data()
    is_admin = is_admin_authenticated(request)

    target_id = file_id
    if not target_id:
        try:
            body = await request.json()
            target_id = body.get("id") or body.get("file_id")
        except Exception:
            pass
    if not target_id:
        target_id = request.query_params.get("id") or request.query_params.get("file_id")

    if not target_id:
        raise HTTPException(status_code=400, detail="Missing file ID")

    if not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")

    file_obj = drive.find_item_by_id(target_id)
    if not file_obj or getattr(file_obj, "type", "") != "file":
        raise HTTPException(status_code=404, detail="File not found")

    timeline = ActivityTracker.get_grouped_timeline(file_obj)
    return JSONResponse({"id": target_id, "type": "file", "activity": timeline})


@app.get("/api/folders/{folder_id}/activity")
@app.post("/api/getFolderActivity")
async def api_get_folder_activity(request: Request, folder_id: Optional[str] = None):
    from utils.directoryHandler import ensure_drive_data
    from utils.properties import ActivityTracker
    drive = ensure_drive_data()
    is_admin = is_admin_authenticated(request)

    target_id = folder_id
    if not target_id:
        try:
            body = await request.json()
            target_id = body.get("id") or body.get("folder_id")
        except Exception:
            pass
    if not target_id:
        target_id = request.query_params.get("id") or request.query_params.get("folder_id")

    if not target_id:
        raise HTTPException(status_code=400, detail="Missing folder ID")

    if not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")

    folder_obj = drive.find_item_by_id(target_id)
    if not folder_obj or getattr(folder_obj, "type", "") != "folder":
        raise HTTPException(status_code=404, detail="Folder not found")

    timeline = ActivityTracker.get_grouped_timeline(folder_obj)
    return JSONResponse({"id": target_id, "type": "folder", "activity": timeline})


@app.post("/api/properties/enrich")
async def api_enrich_properties(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data
    from utils.properties import MetadataWorker
    drive = ensure_drive_data()
    data = await request.json()
    item_id = data.get("id")
    if not item_id:
        raise HTTPException(status_code=400, detail="Missing item ID")

    item = drive.find_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if getattr(item, "type", "") == "file":
        dl_path = Path(f"./downloads/{item.file_id}_{item.name}")
        if dl_path.exists():
            MetadataWorker._process_item(item, dl_path)
            drive.save()
        else:
            MetadataWorker.enqueue(item)

    return JSONResponse({"status": "ok", "message": "Enrichment scheduled"})


# ==========================================
# Secure Share System (files & folders)
# ==========================================

SHARE_UNLOCK_COOKIE = "shu"


def _share_unlock_cookie_name(token: str) -> str:
    return f"{SHARE_UNLOCK_COOKIE}_{token[:12]}"


def _share_base_url(request: Request) -> str:
    host = request.headers.get("host") or request.url.netloc
    proto_header = request.headers.get("x-forwarded-proto") or request.url.scheme
    scheme = proto_header.split(",")[0].strip() if proto_header else "http"
    return f"{scheme}://{host}"


def _validate_or_none(token: str):
    from utils import shareManager
    return shareManager.validate_share(token)


@app.post("/api/share/create")
async def api_share_create(request: Request, _auth: Session = Depends(require_auth)):
    """Create a secure share link for a file or folder (admin only)."""
    from utils import shareManager
    from utils.auth import hash_password
    from utils.directoryHandler import ensure_drive_data

    data = await request.json()
    target_id_path = sanitize_path(str(data.get("target", ""))).strip("/")
    if not target_id_path:
        return JSONResponse({"status": "error", "error": "invalid_target"}, status_code=400)

    drive = ensure_drive_data()
    parts = [p for p in target_id_path.split("/") if p]
    node, parent = drive.contents.get("/"), None
    for seg in parts:
        nxt = getattr(node, "contents", {}).get(seg)
        if nxt is None or getattr(nxt, "trash", False):
            return JSONResponse({"status": "error", "error": "target_not_found"}, status_code=404)
        parent, node = node, nxt

    item_type = getattr(node, "type", "")
    if item_type not in ("file", "folder"):
        return JSONResponse({"status": "error", "error": "unsupported_target"}, status_code=400)

    # Expiry
    expires_at = None
    hours = data.get("expires_in_hours")
    if hours not in (None, "", 0, "0"):
        try:
            h = float(hours)
            if h <= 0 or h > 87600:  # cap at 10 years
                raise ValueError
            expires_at = time.time() + h * 3600
        except (ValueError, TypeError):
            return JSONResponse({"status": "error", "error": "invalid_expiry"}, status_code=400)

    # Optional password (stored as PBKDF2 hash — never plaintext)
    password_hash = None
    raw_pwd = str(data.get("password") or "").strip()
    if raw_pwd:
        if len(raw_pwd) < 6 or len(raw_pwd) > 128:
            return JSONResponse({"status": "error", "error": "invalid_password"}, status_code=400)
        password_hash = hash_password(raw_pwd)

    rec = shareManager.create_share(
        target_id_path="/".join(parts),
        item_type=item_type,
        name=getattr(node, "name", parts[-1]),
        expires_at=expires_at,
        password_hash=password_hash,
        allow_download=bool(data.get("allow_download", True)),
        allow_preview=bool(data.get("allow_preview", True)),
    )

    url = f"{_share_base_url(request)}/s/{rec['token']}"
    logger.info(f"Share created type={item_type} name={rec['name']} expiry={expires_at}")
    return JSONResponse({"status": "ok", "url": url, "share": shareManager.public_record(rec, include_token=True)})


@app.post("/api/share/revoke")
async def api_share_revoke(request: Request, _auth: Session = Depends(require_auth)):
    """Permanently revoke a share link."""
    from utils import shareManager
    data = await request.json()
    token = str(data.get("token", "")).strip()
    if not token:
        return JSONResponse({"status": "error", "error": "missing_token"}, status_code=400)
    ok = shareManager.revoke_share(token)
    if not ok:
        return JSONResponse({"status": "error", "error": "not_found"}, status_code=404)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(_share_unlock_cookie_name(token), path="/")
    return resp


@app.post("/api/share/regenerate")
async def api_share_regenerate(request: Request, _auth: Session = Depends(require_auth)):
    """Rotate a share onto a brand-new unguessable token; the old link dies instantly."""
    from utils import shareManager
    data = await request.json()
    token = str(data.get("token", "")).strip()
    rec = shareManager.regenerate_share(token)
    if not rec:
        return JSONResponse({"status": "error", "error": "not_found"}, status_code=404)
    resp = JSONResponse({
        "status": "ok",
        "url": f"{_share_base_url(request)}/s/{rec['token']}",
        "share": shareManager.public_record(rec, include_token=True),
    })
    resp.delete_cookie(_share_unlock_cookie_name(token), path="/")
    return resp


@app.post("/api/share/delete")
async def api_share_delete(request: Request, _auth: Session = Depends(require_auth)):
    """Permanently delete a share record completely from the store."""
    from utils import shareManager
    data = await request.json()
    token = str(data.get("token", "")).strip()
    if not token:
        return JSONResponse({"status": "error", "error": "missing_token"}, status_code=400)
    ok = shareManager.delete_share(token)
    if not ok:
        return JSONResponse({"status": "error", "error": "not_found"}, status_code=404)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(_share_unlock_cookie_name(token), path="/")
    return resp


@app.post("/api/share/clear_inactive")
async def api_share_clear_inactive(request: Request, _auth: Session = Depends(require_auth)):
    """Bulk delete all revoked and expired shares."""
    from utils import shareManager
    count = shareManager.clear_inactive_shares()
    return JSONResponse({"status": "ok", "deleted_count": count})


@app.post("/api/share/update")
async def api_share_update(request: Request, _auth: Session = Depends(require_auth)):
    """Update settings on an existing active share (permissions, password, expiration)."""
    from utils import shareManager
    data = await request.json()
    token = str(data.get("token", "")).strip()
    if not token:
        return JSONResponse({"status": "error", "error": "missing_token"}, status_code=400)

    password_hash = None
    clear_password = bool(data.get("clear_password", False))
    raw_pwd = data.get("password")
    if raw_pwd and not clear_password:
        if len(raw_pwd) < 4:
            return JSONResponse({"status": "error", "error": "password_too_short"}, status_code=400)
        password_hash = hash_password(raw_pwd)

    expires_at = None
    if "expires_in_hours" in data:
        raw_hours = data.get("expires_in_hours")
        if raw_hours not in (None, "", "null"):
            try:
                h = float(raw_hours)
                if h > 0:
                    expires_at = time.time() + (h * 3600.0)
            except (ValueError, TypeError):
                pass

    rec = shareManager.update_share_settings(
        token=token,
        expires_at=expires_at if "expires_in_hours" in data else None,
        password_hash=password_hash,
        clear_password=clear_password,
        allow_download=data.get("allow_download"),
        allow_preview=data.get("allow_preview"),
    )
    if not rec:
        return JSONResponse({"status": "error", "error": "not_found"}, status_code=404)

    return JSONResponse({
        "status": "ok",
        "share": shareManager.public_record(rec, include_token=True),
    })


@app.post("/api/share/list")
async def api_share_list(request: Request, _auth: Session = Depends(require_auth)):
    """List every active share (admin manage panel)."""
    from utils import shareManager
    return JSONResponse({"status": "ok", "shares": shareManager.list_shares()})


@app.get("/s/{token}")
@app.get("/s/{token}/")
async def shared_item_page(token: str):
    """Dedicated public page for a shared file or folder."""
    return FileResponse("website/share.html")


def _share_state_payload(rec: dict, rel: str = ""):
    """Build the public payload for an unlocked share. No Telegram IDs are included."""
    from utils import shareManager
    if rec.get("type") == "folder":
        node, parent = shareManager.resolve_within_scope(rec, rel)
        if node is None or getattr(node, "type", "") != "folder":
            return None
        children = shareManager.list_folder_children(node, rel) if rel else shareManager.list_folder_children(node, "")
        crumbs = shareManager.build_breadcrumbs(rec, rel)
        return {
            "type": "folder",
            "name": getattr(node, "name", rec.get("name")),
            "rel": rel,
            "children": children,
            "breadcrumbs": crumbs,
            "allow_download": bool(rec.get("allow_download")),
            "allow_preview": bool(rec.get("allow_preview")),
            "expires_at": rec.get("expires_at"),
        }
    else:
        node, parent, _ = shareManager.resolve_scope_root(rec)
        if node is None or getattr(node, "type", "") != "file":
            return None
        name = getattr(node, "name", rec.get("name"))
        return {
            "type": "file",
            "name": name,
            "size": int(getattr(node, "size", 0) or 0),
            "date": getattr(node, "upload_date", ""),
            "preview_kind": shareManager.preview_kind(name),
            "mime": shareManager.guess_mime(name),
            "allow_download": bool(rec.get("allow_download")),
            "allow_preview": bool(rec.get("allow_preview")),
            "expires_at": rec.get("expires_at"),
        }


@app.post("/api/share/meta")
async def api_share_meta(request: Request):
    """Public metadata for a share token. Handles locked / invalid / revoked / expired states."""
    from utils import shareManager
    rate_limit_public_media(request, "share_meta", 120, 60)

    try:
        data = await request.json()
    except Exception:
        data = {}
    token = str(data.get("token", "")).strip()
    rel = str(data.get("rel", ""))

    rec, err = _validate_or_none(token)
    if err:
        status_code = 410 if err in ("expired", "revoked") else 404
        return JSONResponse({"status": "error", "error": err}, status_code=status_code)

    # Reject unsafe relative paths outright (fail closed, never fall back to root)
    if rel and not shareManager.sanitize_rel(rel):
        return JSONResponse({"status": "error", "error": "gone"}, status_code=404)

    if rec.get("password_hash"):
        cookie_val = request.cookies.get(_share_unlock_cookie_name(token))
        if not shareManager.verify_unlock_cookie(token, cookie_val):
            return JSONResponse({
                "status": "locked",
                "type": rec.get("type"),
                "has_password": True,
                "expires_at": rec.get("expires_at"),
            })

    payload = _share_state_payload(rec, rel)
    if payload is None:
        return JSONResponse({"status": "error", "error": "gone"}, status_code=404)
    payload["status"] = "ok"
    shareManager.touch_access(rec)
    return JSONResponse(payload)


@app.post("/api/share/unlock")
async def api_share_unlock(request: Request):
    """Verify the password for a protected share and set a signed unlock cookie."""
    from utils import shareManager
    from utils.auth import verify_password
    # Spoof-resistant limiter: header rotation cannot reset the brute-force bucket
    rate_limit_strict(request, "share_unlock", 10, 60)

    try:
        data = await request.json()
    except Exception:
        data = {}
    token = str(data.get("token", "")).strip()
    password = str(data.get("password", ""))

    rec, err = _validate_or_none(token)
    if err:
        status_code = 410 if err in ("expired", "revoked") else 404
        return JSONResponse({"status": "error", "error": err}, status_code=status_code)
    if not rec.get("password_hash"):
        return JSONResponse({"status": "ok"})

    if not verify_password(password, rec["password_hash"]):
        return JSONResponse({"status": "error", "error": "bad_password"}, status_code=401)

    resp = JSONResponse({"status": "ok"})
    cookie_value = shareManager.make_unlock_cookie_value(token)
    if cookie_value:
        resp.set_cookie(
            _share_unlock_cookie_name(token),
            cookie_value,
            max_age=shareManager.UNLOCK_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=is_secure_cookie(request),
            path="/",
        )
    logger.info(f"Share unlocked: {rec.get('name')}")
    return resp


@app.get("/share/{token}/file")
@app.get("/share/{token}/file/")
@app.get("/share/{token}/file/{rel:path}")
async def share_file_stream(request: Request, token: str, rel: str = ""):
    """Stream a shared file inline (preview) or as attachment (download).
    The Telegram message ID stays entirely server-side."""
    from utils import shareManager
    rate_limit_public_media(request, "share_file", 120, 60)

    rec, err = _validate_or_none(token)
    if err:
        status_code = 410 if err in ("expired", "revoked") else 404
        raise HTTPException(status_code=status_code, detail=f"Link {err}")
    if rec.get("password_hash"):
        cookie_val = request.cookies.get(_share_unlock_cookie_name(token))
        if not shareManager.verify_unlock_cookie(token, cookie_val):
            raise HTTPException(status_code=401, detail="Password required")

    wants_download = request.query_params.get("dl") in ("1", "true", "yes")
    if wants_download and not rec.get("allow_download"):
        raise HTTPException(status_code=403, detail="Download unavailable")
    if not wants_download and not rec.get("allow_preview"):
        raise HTTPException(status_code=403, detail="Preview unavailable")

    node, _parent = shareManager.resolve_within_scope(rec, rel)
    if node is None or getattr(node, "type", "") != "file" or getattr(node, "trash", False):
        raise HTTPException(status_code=404, detail="File not found")

    file_id = getattr(node, "file_id", None)
    name = getattr(node, "name", "file")
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()
    local_path = drive.resolve_local_file_path(node)

    if local_path and os.path.isfile(local_path):
        from utils.streamer import local_file_streamer
        response = await local_file_streamer(local_path, name, request)
    elif file_id and int(file_id) > 0:
        try:
            response = await media_streamer(STORAGE_CHANNEL, file_id, name, request)
        except Exception as e:
            logger.error(f"Error streaming shared file '{name}' (msg {file_id}): {e}")
            raise HTTPException(status_code=404, detail="File unavailable on Telegram")
    else:
        raise HTTPException(status_code=404, detail="File source offline or not synced")

    disposition = "attachment" if wants_download else "inline"
    # Never render active content (SVG/HTML/XML/JS) same-origin from shares —
    # force download so stored markup can never execute on the app origin.
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("svg", "html", "htm", "xhtml", "xml", "js"):
        disposition = "attachment"
        response.headers["Content-Type"] = "application/octet-stream"

    # RFC 6266 / RFC 5987 standard for Unicode and special characters
    safe_ascii = name.encode("ascii", "replace").decode("ascii").replace('"', '\\"')
    quoted_utf8 = urllib.parse.quote(name)
    response.headers["Content-Disposition"] = f"{disposition}; filename=\"{safe_ascii}\"; filename*=UTF-8''{quoted_utf8}"
    shareManager.touch_access(rec)
    return response


@app.get("/share/{token}/thumb")
@app.get("/share/{token}/thumb/")
@app.get("/share/{token}/thumb/{rel:path}")
async def share_file_thumb(request: Request, token: str, rel: str = ""):
    """Thumbnail for shared items (preview permission required)."""
    from utils import shareManager
    rate_limit_public_media(request, "share_thumb", 600, 60)

    rec, err = _validate_or_none(token)
    if err:
        status_code = 410 if err in ("expired", "revoked") else 404
        raise HTTPException(status_code=status_code, detail=f"Link {err}")
    if rec.get("password_hash"):
        cookie_val = request.cookies.get(_share_unlock_cookie_name(token))
        if not shareManager.verify_unlock_cookie(token, cookie_val):
            raise HTTPException(status_code=401, detail="Password required")
    if not rec.get("allow_preview"):
        raise HTTPException(status_code=403, detail="Preview unavailable")

    node, _parent = shareManager.resolve_within_scope(rec, rel)
    if node is None or getattr(node, "type", "") != "file" or getattr(node, "trash", False):
        raise HTTPException(status_code=404, detail="Not found")

    thumb_data = None
    file_id = getattr(node, "file_id", 0)
    if file_id and int(file_id) > 0:
        try:
            thumb_data = await THUMB_SERVICE.get_or_fetch(int(file_id), getattr(node, "name", ""))
        except Exception as e:
            logger.warning(f"Failed to fetch thumb for shared node {getattr(node, 'name', '')}: {e}")

    if not thumb_data:
        from utils.directoryHandler import ensure_drive_data
        drive = ensure_drive_data()
        local_path = drive.resolve_local_file_path(node)
        if local_path and os.path.isfile(local_path):
            thumb_data = await THUMB_SERVICE.get_or_generate_local(local_path, getattr(node, "id", "share_thumb"))

    if not thumb_data:
        raise HTTPException(status_code=404, detail="No thumbnail")

    return Response(
        content=thumb_data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/share/{token}/zip")
@app.get("/share/{token}/zip/")
@app.get("/share/{token}/zip/{rel:path}")
async def share_folder_zip(request: Request, token: str, rel: str = ""):
    """Download a shared folder or subfolder as a ZIP archive."""
    from utils import shareManager
    from starlette.background import BackgroundTask
    from utils.zipper import create_zip_archive, cleanup_temp_zip

    rate_limit_public_media(request, "share_zip", 60, 60)

    rec, err = _validate_or_none(token)
    if err:
        status_code = 410 if err in ("expired", "revoked") else 404
        raise HTTPException(status_code=status_code, detail=f"Link {err}")
    if rec.get("password_hash"):
        cookie_val = request.cookies.get(_share_unlock_cookie_name(token))
        if not shareManager.verify_unlock_cookie(token, cookie_val):
            raise HTTPException(status_code=401, detail="Password required")

    if not rec.get("allow_download"):
        raise HTTPException(status_code=403, detail="Download unavailable")

    node, _parent = shareManager.resolve_within_scope(rec, rel)
    if node is None or getattr(node, "type", "") != "folder" or getattr(node, "trash", False):
        raise HTTPException(status_code=404, detail="Folder not found")

    base_name, items = shareManager.collect_share_items_for_zip(node, rec.get("name") or "Shared")
    if not items:
        raise HTTPException(status_code=404, detail="No files found in shared folder")

    try:
        zip_file_path, final_filename, total_size = await create_zip_archive(items, base_name)
    except Exception as e:
        logger.error(f"Error creating ZIP archive for share {token}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create zip archive")

    quoted_utf8 = urllib.parse.quote(final_filename)
    safe_ascii = final_filename.encode("ascii", "replace").decode("ascii").replace('"', '\\"')

    shareManager.touch_access(rec)
    return FileResponse(
        path=str(zip_file_path),
        filename=final_filename,
        media_type="application/zip",
        background=BackgroundTask(cleanup_temp_zip, zip_file_path),
        headers={
            "Content-Disposition": f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{quoted_utf8}',
            "Cache-Control": "no-cache",
        },
    )



# ==========================================
# Real-Time Cloud Sync Engine Status API
# ==========================================

SYNC_ENGINE_STATUS = {
    "state": "idle",  # "idle", "scanning", "syncing_folders", "syncing_files", "completed"
    "source": "",
    "current_item": "",
    "current_index": 0,
    "total_items": 0,
    "remaining_items": 0,
    "current_bytes": 0,
    "total_bytes": 0,
    "speed_str": "",
    "folders_total": 0,
    "folders_created": 0,
    "files_total": 0,
    "files_uploaded": 0,
    "files_skipped": 0,
    "updated_at": time.time(),
    "pending_queue": [],
    "completed_list": [],
    "logs": []
}


@app.post("/api/getSyncStatus")
async def getSyncStatus(request: Request, _auth: Session = Depends(require_auth)):
    # If completed_list is empty, reconstruct from existing logs
    if not SYNC_ENGINE_STATUS.get("completed_list") and SYNC_ENGINE_STATUS.get("logs"):
        reconstructed = []
        for log in reversed(SYNC_ENGINE_STATUS["logs"]):
            msg = log.get("msg", "")
            if "]: " in msg and " (" in msg:
                try:
                    fpart = msg.split("]: ", 1)[1]
                    fname = fpart.rsplit(" (", 1)[0].strip()
                    fsize = fpart.rsplit(" (", 1)[1].rstrip(")").strip()
                    reconstructed.append({
                        "name": fname,
                        "path": SYNC_ENGINE_STATUS.get("current_path") or "/",
                        "size": fsize,
                        "time": log.get("time", "")
                    })
                except Exception:
                    pass
        SYNC_ENGINE_STATUS["completed_list"] = reconstructed[:100]

    # Combine with TransferManager concurrent uploads & future queue
    active_concurrent = []
    future_queue = []
    completed_uploads = list(SYNC_ENGINE_STATUS.get("completed_list") or [])

    try:
        from utils.transfer_manager import transfer_manager, TransferType, TransferState, TransferItem
        for t in list(transfer_manager.store.transfers.values()):
            if t.type == TransferType.UPLOAD or getattr(t.type, "value", str(t.type)) == "upload":
                d = t.to_dict()
                st = t.state.value if isinstance(t.state, TransferState) else str(t.state)
                if st in ("preparing", "uploading", "retrying"):
                    active_concurrent.append({
                        "id": d["id"],
                        "name": d["filename"],
                        "path": d.get("relative_path") or d.get("target_path") or "/",
                        "size": d["size"],
                        "transferred": d["transferred"],
                        "percentage": d["percentage"],
                        "speed": d.get("speed_formatted") or "0 B/s",
                        "eta": d.get("eta_formatted") or "--",
                        "state": st
                    })
                elif st == "queued":
                    future_queue.append({
                        "id": d["id"],
                        "name": d["filename"],
                        "path": d.get("relative_path") or d.get("target_path") or "/",
                        "size": TransferItem._format_bytes(d["size"]) if d.get("size") else "Queued",
                        "state": "queued"
                    })
                elif st == "completed":
                    if not any(c.get("name") == d["filename"] for c in completed_uploads):
                        completed_uploads.insert(0, {
                            "name": d["filename"],
                            "path": d.get("relative_path") or d.get("target_path") or "/",
                            "size": TransferItem._format_bytes(d["size"]) if d.get("size") else "Done",
                            "time": "Recent"
                        })
    except Exception:
        pass

    # If sync engine has an active item, include it in active_concurrent if not already present
    cur_item = SYNC_ENGINE_STATUS.get("current_item")
    if cur_item and not any(a["name"] == cur_item for a in active_concurrent):
        active_concurrent.insert(0, {
            "id": "sync_stream_active",
            "name": cur_item,
            "path": SYNC_ENGINE_STATUS.get("current_path") or "/",
            "size": SYNC_ENGINE_STATUS.get("current_size") or "--",
            "transferred": SYNC_ENGINE_STATUS.get("current_bytes") or 0,
            "percentage": SYNC_ENGINE_STATUS.get("current_percent") or 0,
            "speed": SYNC_ENGINE_STATUS.get("speed_str") or "Transferring...",
            "eta": "--",
            "state": "uploading"
        })

    # Merge sync engine pending_queue into future_queue
    for p in (SYNC_ENGINE_STATUS.get("pending_queue") or []):
        p_name = p.get("name", "")
        if p_name and not any(f["name"] == p_name for f in future_queue):
            future_queue.append({
                "id": p_name,
                "name": p_name,
                "path": p.get("path") or "/",
                "size": p.get("size") or "Queued",
                "state": "queued"
            })

    data_payload = dict(SYNC_ENGINE_STATUS)
    data_payload["active_concurrent"] = active_concurrent
    data_payload["future_queue"] = future_queue
    data_payload["completed_uploads"] = completed_uploads[:100]

    try:
        from utils.directoryHandler import ensure_drive_data
        total_files, total_bytes = ensure_drive_data().get_drive_stats()
        data_payload["drive_stats"] = {
            "total_files": total_files,
            "total_bytes": total_bytes
        }
    except Exception:
        pass

    return JSONResponse({"status": "ok", "data": data_payload})


@app.post("/api/updateSyncStatus")
async def updateSyncStatus(request: Request, _auth: Session = Depends(require_auth)):
    data = await request.json()

    status_update = data.get("sync_data", {})
    
    # Handle completed item appending
    completed_item = status_update.pop("completed_item", None)
    if completed_item:
        SYNC_ENGINE_STATUS["completed_list"].insert(0, completed_item)
        if len(SYNC_ENGINE_STATUS["completed_list"]) > 100:
            SYNC_ENGINE_STATUS["completed_list"] = SYNC_ENGINE_STATUS["completed_list"][:100]

    # Handle pending queue update or item popping
    if "pending_queue" in status_update:
        queue = status_update.pop("pending_queue")
        if isinstance(queue, list):
            # Cap memory: telemetry queues never need more than a rolling window
            SYNC_ENGINE_STATUS["pending_queue"] = queue[:200]
    elif "current_item" in status_update and SYNC_ENGINE_STATUS["pending_queue"]:
        cur = status_update["current_item"]
        SYNC_ENGINE_STATUS["pending_queue"] = [
            item for item in SYNC_ENGINE_STATUS["pending_queue"] if item.get("name") != cur
        ]

    SYNC_ENGINE_STATUS.update(status_update)
    SYNC_ENGINE_STATUS["updated_at"] = time.time()

    log_msg = data.get("log")
    if log_msg:
        log_time = time.strftime("%H:%M:%S")
        SYNC_ENGINE_STATUS["logs"].append({
            "time": log_time,
            "msg": log_msg
        })
        if len(SYNC_ENGINE_STATUS["logs"]) > 100:
            SYNC_ENGINE_STATUS["logs"] = SYNC_ENGINE_STATUS["logs"][-100:]

        # Auto-extract completed file name and size from log message if not explicitly sent
        if "]: " in log_msg and " (" in log_msg:
            try:
                fpart = log_msg.split("]: ", 1)[1]
                fname = fpart.rsplit(" (", 1)[0].strip()
                fsize = fpart.rsplit(" (", 1)[1].rstrip(")").strip()
                # Check if already present at head
                if not SYNC_ENGINE_STATUS["completed_list"] or SYNC_ENGINE_STATUS["completed_list"][0].get("name") != fname:
                    SYNC_ENGINE_STATUS["completed_list"].insert(0, {
                        "name": fname,
                        "path": SYNC_ENGINE_STATUS.get("current_path") or "/",
                        "size": fsize,
                        "time": log_time
                    })
                    if len(SYNC_ENGINE_STATUS["completed_list"]) > 100:
                        SYNC_ENGINE_STATUS["completed_list"] = SYNC_ENGINE_STATUS["completed_list"][:100]
            except Exception:
                pass

    return JSONResponse({"status": "ok"})


@app.get("/api/admin/integrityReport")
async def api_admin_integrity_report(session: Session = Depends(require_auth)):
    """
    Administrative Data Integrity Diagnostic Endpoint.
    Scans the metadata tree, verifies file/folder structure and Telegram message mapping validity.
    Does NOT mutate data.
    """
    from utils.directoryHandler import ensure_drive_data
    from utils.clients import get_client_status

    drive = ensure_drive_data()
    if not drive or not hasattr(drive, "contents") or "/" not in drive.contents:
        return JSONResponse({"status": "error", "message": "Drive metadata not loaded"}, status_code=503)

    total_folders = 0
    total_files = 0
    total_bytes = 0
    missing_file_ids = []
    visited_folders = set()
    cyclic_detected = False

    def scan_folder(folder_obj, current_path: str = "/"):
        nonlocal total_folders, total_files, total_bytes, cyclic_detected
        contents = getattr(folder_obj, "contents", {})
        if not isinstance(contents, dict):
            return

        for item_id, item in contents.items():
            item_type = getattr(item, "type", "folder" if hasattr(item, "contents") else "file")
            name = getattr(item, "name", "unnamed")
            item_path = f"{current_path.rstrip('/')}/{name}"

            if item_type == "folder":
                total_folders += 1
                if item_id in visited_folders:
                    cyclic_detected = True
                    continue
                visited_folders.add(item_id)
                scan_folder(item, item_path)
            else:
                total_files += 1
                f_size = getattr(item, "size", 0)
                try:
                    total_bytes += int(f_size)
                except Exception:
                    pass

                f_id = getattr(item, "file_id", None)
                if not f_id:
                    missing_file_ids.append({"path": item_path, "id": item_id})

    try:
        scan_folder(drive.contents["/"], "/")
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Scan failed: {e}"}, status_code=500)

    bak_exists = os.path.exists("cache/drive.data.bak")
    bak_mtime = os.path.getmtime("cache/drive.data.bak") if bak_exists else None

    return JSONResponse({
        "status": "ok",
        "timestamp": time.time(),
        "integrity": {
            "tree_valid": not cyclic_detected and len(missing_file_ids) == 0,
            "cyclic_references": cyclic_detected,
            "total_folders": total_folders,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "missing_file_ids_count": len(missing_file_ids),
            "sample_missing_files": missing_file_ids[:10],
            "backup_synchronized": bak_exists,
            "backup_mtime": bak_mtime,
        },
        "telegram": get_client_status()
    })


@app.post("/api/admin/reloadDriveData")
async def api_admin_reload_drive_data(_session: Session = Depends(require_auth)):
    """Forces in-memory DRIVE_DATA reload from the disk cache (cache/drive.data)."""
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data(force_reload=True)
    try:
        from utils.properties import FolderStatsCalculator
        FolderStatsCalculator.invalidate_cache()
    except Exception:
        pass
    root_items = [c.name for c in drive.contents.get("/", getattr(drive, "contents", {})).contents.values()]
    return JSONResponse({"status": "ok", "message": "Drive data reloaded from disk", "roots": root_items})


@app.get("/api/telegram/status")
async def api_telegram_status():
    """
    Returns the health and concurrency state of all Telegram bots/clients.
    No auth required — safe to poll from monitoring tools.

    Response includes:
      - clients: list of each bot with connected/flooded/load info
      - gate:    current tg_gate pacing + concurrency counters
    """
    from utils.clients import multi_clients, work_loads, get_client_status
    from utils import tg_gate

    gate_stats = tg_gate.stats()

    clients_info = []
    for idx, client in multi_clients.items():
        c_key = str(getattr(client, "name", None) or id(client))
        cooldown = round(tg_gate.get_client_cooldown(c_key), 1)
        clients_info.append({
            "index": idx,
            "name": getattr(client, "name", f"client_{idx}"),
            "connected": getattr(client, "is_connected", lambda: False)(),
            "flooded": cooldown > 0,
            "flood_cooldown_s": cooldown,
            "work_load": work_loads.get(idx, 0),
        })

    return JSONResponse({
        "clients": clients_info,
        "gate": gate_stats,
        "summary": get_client_status(),
    })


@app.get("/api/admin/scanChannelMessages")
async def api_admin_scan_channel_messages(_session: Session = Depends(require_auth)):
    from utils.clients import multi_clients, get_client
    from config import STORAGE_CHANNEL
    client = get_client()
    messages = []
    try:
        msg_ids = list(range(1, 50))
        res = await client.get_messages(STORAGE_CHANNEL, msg_ids)
        for msg in res:
            if not msg or getattr(msg, "empty", False):
                continue
            media = getattr(msg, "document", None) or getattr(msg, "photo", None) or getattr(msg, "video", None) or getattr(msg, "audio", None)
            fname = getattr(media, "file_name", None) if media else None
            fsize = getattr(media, "file_size", 0) if media else 0
            messages.append({
                "id": msg.id,
                "date": str(msg.date),
                "media_type": type(media).__name__ if media else None,
                "file_name": fname,
                "file_size": fsize
            })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    return JSONResponse({"status": "ok", "count": len(messages), "messages": messages})


@app.post("/api/admin/restoreFromManifest")
async def api_admin_restore_from_manifest(_session: Session = Depends(require_auth)):
    """Restores full drives, folders, and files hierarchy from the latest sync manifest."""
    import utils.directoryHandler as dh
    from utils.directoryHandler import Folder, File, NewDriveData, sanitize_name

    manifest_path = Path.home() / ".tgdrive_sync_manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Backup manifest not found")

    import json
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    verified_folders = manifest.get("__verified_folders__", [])
    file_entries = {k: v for k, v in manifest.items() if k != "__verified_folders__"}

    root_folder = Folder("/", "/")
    root_folder.id = "root"
    used_ids = ["root"]
    drive = NewDriveData({"/": root_folder}, used_ids)
    folder_cache = {"": root_folder, "/": root_folder}

    def get_or_create_folder(folder_path_str: str) -> Folder:
        clean = folder_path_str.replace("\\", "/").strip("/")
        if not clean:
            return root_folder
        if clean in folder_cache:
            return folder_cache[clean]

        parts = [p for p in clean.split("/") if p]
        curr_node = root_folder
        curr_path = ""

        for part in parts:
            curr_path = f"{curr_path}/{part}".strip("/")
            if curr_path in folder_cache:
                curr_node = folder_cache[curr_path]
                continue

            clean_part = sanitize_name(part)
            found = None
            if hasattr(curr_node, "contents") and isinstance(curr_node.contents, dict):
                for child in curr_node.contents.values():
                    if getattr(child, "type", "") == "folder" and getattr(child, "name", "") == clean_part and not getattr(child, "trash", False):
                        found = child
                        break

            if found:
                curr_node = found
            else:
                parent_loc = "/" if curr_node.id == "root" else (curr_node.path.rstrip("/") + "/" + curr_node.id).replace("//", "/")
                new_f = Folder(clean_part, parent_loc)
                if new_f.id not in drive.used_ids:
                    drive.used_ids.append(new_f.id)
                curr_node.contents[new_f.id] = new_f
                curr_node = new_f

            folder_cache[curr_path] = curr_node

        return curr_node

    for fpath in verified_folders:
        get_or_create_folder(fpath)

    total_files = 0
    total_bytes = 0
    for file_path_str, file_info in file_entries.items():
        clean_fp = file_path_str.replace("\\", "/").strip("/")
        parts = clean_fp.split("/")
        fname = parts[-1]
        folder_part = "/".join(parts[:-1])

        parent_folder = get_or_create_folder(folder_part)
        f_size = file_info.get("size", 0)
        last_synced = file_info.get("last_synced")
        parent_loc = "/" if parent_folder.id == "root" else (parent_folder.path.rstrip("/") + "/" + parent_folder.id).replace("//", "/")

        f_obj = File(fname, file_id=0, size=f_size, path=parent_loc)
        if f_obj.id not in drive.used_ids:
            drive.used_ids.append(f_obj.id)
        if last_synced:
            f_obj.upload_date = last_synced
            f_obj.uploaded_at = last_synced
            f_obj.modified_at = last_synced
            f_obj.accessed_at = last_synced

        parent_folder.contents[f_obj.id] = f_obj
        total_files += 1
        total_bytes += f_size

    drive.save()
    dh.DRIVE_DATA = drive
    try:
        from utils.properties import FolderStatsCalculator
        FolderStatsCalculator.invalidate_cache()
    except Exception:
        pass

    try:
        await dh._execute_backup()
    except Exception as be:
        logger.warning(f"Note on updating Telegram backup message: {be}")

    root_items = [c.name for c in root_folder.contents.values()]
    return JSONResponse({
        "status": "ok",
        "message": "Data restored from last backup successfully",
        "roots": root_items,
        "total_folders": len(folder_cache) - 1,
        "total_files": total_files,
        "total_bytes": total_bytes
    })


@app.post("/api/admin/eraseAllData")
async def api_admin_erase_all_data(
    request: Request,
    session: Session = Depends(require_auth)
):
    """
    Completely erases everything to start fresh from 0 data:
      1. Deletes all file/backup messages in Telegram STORAGE_CHANNEL
      2. Resets drive.data to clean empty root ('/': Folder('/', '/'), used_ids: [])
      3. Clears local sync manifest, transfer history, shares, duplicate index, and thumbnails
      4. Uploads fresh initial empty drive.data backup to Telegram storage channel
    """
    from utils.directoryHandler import ensure_drive_data, Folder, backup_drive_data
    import utils.directoryHandler as dh
    from utils.clients import get_client
    from pathlib import Path

    deleted_telegram_msgs = 0
    client = None
    try:
        client = get_client()
    except Exception as ce:
        logger.warning(f"Telegram client not immediately available for message deletion: {ce}")

    if client and STORAGE_CHANNEL:
        try:
            for start_id in range(1, 200, 100):
                id_batch = list(range(start_id, start_id + 100))
                try:
                    msgs = await client.get_messages(STORAGE_CHANNEL, id_batch)
                    valid_ids = [m.id for m in msgs if m and not getattr(m, "empty", False)]
                    if valid_ids:
                        await client.delete_messages(STORAGE_CHANNEL, valid_ids)
                        deleted_telegram_msgs += len(valid_ids)
                except Exception as batch_err:
                    logger.warning(f"Batch delete error: {batch_err}")
        except Exception as e:
            logger.warning(f"Error deleting Telegram messages: {e}")

    # 1. Reset drive structure to pristine empty state
    drive = ensure_drive_data(force_reload=True)
    drive.contents = {"/": Folder("/", "/")}
    drive.used_ids = []
    drive.isUpdated = True
    drive.last_modified = 0.0
    drive.save()
    dh.DRIVE_DATA = drive

    # 2. Invalidate stats cache & duplicate cache
    try:
        from utils.properties import FolderStatsCalculator
        FolderStatsCalculator.invalidate_cache()
    except Exception:
        pass

    try:
        from utils.duplicate_detector import DuplicateDetector
        DuplicateDetector.invalidate_cache()
    except Exception:
        pass

    # 3. Clear local sync manifest and JSON backups
    manifest_path = Path.home() / ".tgdrive_sync_manifest.json"
    if manifest_path.exists():
        try:
            manifest_path.unlink(missing_ok=True)
            logger.info("Local sync manifest removed.")
        except Exception:
            pass

    for extra_file in [
        Path("cache/tgdrive_backup.json"),
        Path("data/shares.json"),
        Path("data/duplicate_hashes.json"),
        Path("data/transfers.json")
    ]:
        try:
            if extra_file.exists():
                extra_file.unlink(missing_ok=True)
        except Exception:
            pass

    # 4. Clear thumbnail cache
    thumbs_dir = Path("cache/thumbs")
    if thumbs_dir.exists():
        for f in thumbs_dir.glob("*.jpg"):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    logger.info("Successfully completed full drive and Telegram storage erase. Drive has 0 files, 0 folders, 0 bytes.")
    return JSONResponse({
        "status": "ok",
        "message": "Everything has been erased. Drive is reset to 0 data and ready for fresh uploads.",
        "total_files": 0,
        "total_folders": 0,
        "total_bytes": 0,
        "deleted_telegram_messages": deleted_telegram_msgs
    })


@app.post("/api/admin/purgeChannel")
async def api_admin_purge_channel(
    request: Request,
    session: Session = Depends(require_auth)
):
    """
    Deletes messages inside the Telegram STORAGE_CHANNEL across all configured bot and premium clients.
    """
    from utils.clients import multi_clients, premium_clients
    all_clients = list(multi_clients.values()) + list(premium_clients.values())
    if not all_clients:
        raise HTTPException(status_code=503, detail="No active Telegram clients are currently connected")
    if not STORAGE_CHANNEL:
        raise HTTPException(status_code=400, detail="STORAGE_CHANNEL not configured")

    deleted_ids = set()
    errors = []

    # First discover all valid message IDs in channel
    valid_ids = []
    primary_client = all_clients[0]
    for start_id in range(1, 200, 50):
        id_batch = list(range(start_id, start_id + 50))
        try:
            msgs = await primary_client.get_messages(STORAGE_CHANNEL, id_batch)
            if not isinstance(msgs, list):
                msgs = [msgs]
            batch_valids = [m.id for m in msgs if m and not getattr(m, "empty", False)]
            valid_ids.extend(batch_valids)
        except Exception as e:
            errors.append(f"get_messages range {start_id}-{start_id+50}: {e}")

    # Now attempt deletion across all clients
    for mid in valid_ids:
        deleted = False
        for client in all_clients:
            try:
                await client.delete_messages(STORAGE_CHANNEL, message_ids=mid)
                deleted_ids.add(mid)
                deleted = True
                break
            except Exception as e:
                pass
        if not deleted:
            errors.append(f"Message {mid} could not be deleted (bot needs 'Delete Messages' admin rights in channel)")

    logger.info(f"Purged {len(deleted_ids)} messages from Telegram STORAGE_CHANNEL. Errors: {len(errors)}")
    return JSONResponse({
        "status": "ok",
        "message": f"Successfully deleted {len(deleted_ids)} messages from Telegram storage channel.",
        "deleted_count": len(deleted_ids),
        "total_scanned_messages": len(valid_ids),
        "errors": errors[:10]  # Return up to first 10 for clarity
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)


