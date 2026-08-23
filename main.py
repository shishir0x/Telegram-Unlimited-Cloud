from utils.downloader import (
    download_file,
    get_file_info_from_url,
)
import os
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
    start_cleanup_task,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    get_client_ip,
    is_admin_authenticated,
    verify_password,
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

    # Initialize Telegram clients in the background so server starts immediately (<100ms)
    asyncio.create_task(initialize_clients())

    # Start the website auto ping task
    asyncio.create_task(auto_ping_website())

    # Start background session/OTP cleanup task
    start_cleanup_task()

    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    logger.info(f"🌐 Local Browser: http://localhost:8000")
    logger.info(f"📱 Mobile Devices on Wi-Fi: http://{local_ip}:8000")

    yield

    # Graceful shutdown: cleanly disconnect Telegram clients
    try:
        from utils.clients import stop_clients
        await stop_clients()
    except Exception as e:
        logger.warning(f"Shutdown cleanup error: {e}")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
logger = Logger(__name__)


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
    """Readiness probe: verifies Telegram connectivity and metadata readiness."""
    from utils.clients import is_telegram_ready
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()
    is_ready = is_telegram_ready() and drive is not None
    if is_ready:
        return JSONResponse({"status": "ready", "telegram_ready": True, "drive_loaded": True}, status_code=200)
    return JSONResponse({"status": "initializing", "telegram_ready": is_telegram_ready(), "drive_loaded": drive is not None}, status_code=503)


@app.get("/stream")
async def stream_page():
    return FileResponse("website/VideoPlayer.html")


@app.get("/static/{file_path:path}")
async def static_files(file_path):
    if "apiHandler.js" in file_path:
        with open(Path("website/static/js/apiHandler.js"), "r", encoding="utf-8") as f:
            content = f.read()
            content = content.replace("MAX_FILE_SIZE__SDGJDG", str(MAX_FILE_SIZE))
        return Response(content=content, media_type="application/javascript")

    # Path traversal shield: resolved target must remain inside website/static
    webroot = Path("website/static").resolve()
    try:
        target = (webroot / file_path).resolve()
        if not str(target).startswith(str(webroot)):
            raise HTTPException(status_code=404, detail="Not found")
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(target)



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
        file = drive.get_file(clean_path)
        return await media_streamer(STORAGE_CHANNEL, file.file_id, file.name, request)
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
    def __init__(self, max_ram_items: int = 500, max_disk_mb: int = int(os.getenv("THUMB_CACHE_MAX_MB", "500"))):
        self.ram_cache: collections.OrderedDict[int, bytes] = collections.OrderedDict()
        self.max_ram_items = max_ram_items
        self.max_disk_bytes = max_disk_mb * 1024 * 1024
        self.cache_dir = Path("./cache/thumbs")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(4)
        self.in_flight: dict[int, asyncio.Future] = {}
        # Initial disk prune check on startup
        self.prune_disk_if_needed()

    def get_ram(self, file_id: int) -> bytes | None:
        if file_id in self.ram_cache:
            self.ram_cache.move_to_end(file_id)
            return self.ram_cache[file_id]
        return None

    def put_ram(self, file_id: int, data: bytes):
        self.ram_cache[file_id] = data
        self.ram_cache.move_to_end(file_id)
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
                # Sort by last modified time (oldest first)
                files.sort(key=lambda f: f.stat().st_mtime)
                # Remove oldest 30% of thumbnails to bring disk space safely below threshold
                target_delete_count = max(1, int(len(files) * 0.3))
                for f in files[:target_delete_count]:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
                logger.info(f"Auto-pruned {target_delete_count} oldest thumbnails from disk.")
        except Exception as e:
            logger.warning(f"Error during thumbnail disk pruning: {e}")

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
                is_image = ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "tiff"]
                is_video = ext in ["mp4", "mkv", "webm", "mov", "avi", "3gp", "ts", "flv"]

                thumb_target = None
                if msg.photo:
                    thumb_target = msg.photo
                elif msg.document and msg.document.thumbs:
                    thumb_target = msg.document.thumbs[0]
                elif msg.video and msg.video.thumbs:
                    thumb_target = msg.video.thumbs[0]
                elif msg.animation and msg.animation.thumbs:
                    thumb_target = msg.animation.thumbs[0]
                elif is_image and msg.document:
                    thumb_target = msg.document
                elif is_image:
                    thumb_target = msg

                if thumb_target:
                    try:
                        buf = await client.download_media(thumb_target, in_memory=True)
                        if buf and hasattr(buf, "getbuffer") and buf.getbuffer().nbytes > 0:
                            buf.seek(0)
                            with Image.open(buf) as img:
                                img = img.convert("RGB")
                                img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                                out_buf = io.BytesIO()
                                img.save(out_buf, format="JPEG", quality=75, optimize=True)
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
        file = drive.get_file(clean_path)
        if not file or not file.file_id:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    etag = f'"thumb-{file.file_id}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"})

    thumb_data = await THUMB_SERVICE.get_or_fetch(file.file_id, file.name)
    if thumb_data:
        return Response(
            content=thumb_data,
            media_type="image/jpeg",
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=31536000, immutable"
            }
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

        # If at least one channel delivered the OTP:
        if delivery_channels:
            msg_text = f"Verification code sent to {' & '.join(delivery_channels)}."
            return JSONResponse({"status": "otp_sent", "message": msg_text})

        # Dev/fallback mode: log code to console
        logger.warning("[CONSOLE OTP] Verification code generated (check server logs)")
        return JSONResponse({"status": "otp_sent", "message": "Verification code generated."})

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
    asyncio.create_task(backup_drive_data(loop=False))
    return JSONResponse({"status": "ok"})


@app.post("/api/getDirectory")
async def api_get_directory(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    try:
        data = await request.json()
    except Exception:
        data = {}

    auth = data.get("auth")
    raw_path = data.get("path", "/")
    path = sanitize_path(raw_path) if not (raw_path.startswith("/tags/") or "/search_" in raw_path or raw_path.startswith("/share_") or raw_path == "/trash" or raw_path == "/recent") else raw_path
    is_admin = is_admin_authenticated(request)

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
async def api_check_file_exists(request: Request, _auth: Session = Depends(require_auth)):
    """
    Checks if a file with the given name already exists in the target folder.
    Used for pre-upload conflict resolution prompts (Replace vs Keep Both vs Cancel).
    """
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    target_path = sanitize_path(data.get("path", "/"))
    filename = str(data.get("filename", "")).strip()

    if not filename:
        return JSONResponse({"exists": False})

    folder_res = drive.get_directory(target_path, is_admin=True)
    folder_obj = folder_res[0] if isinstance(folder_res, tuple) else folder_res

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


SAVE_PROGRESS = {}


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    path: str = Form(...),
    id: str = Form(...),
    total_size: str = Form(...),
    conflict: str = Form("keep_both"),
    _auth: Session = Depends(require_auth),
):
    global SAVE_PROGRESS

    safe_path = sanitize_path(path)

    # Sanitize upload ID and file extensions to prevent path traversal
    safe_id = "".join(c for c in str(id) if c.isalnum() or c in ("-", "_"))[:64]
    if not safe_id:
        safe_id = getRandomID()

    raw_filename = file.filename or "uploaded_file"
    safe_filename = "".join(c for c in raw_filename if c not in ('\x00', '\r', '\n', '/', '\\')).strip()
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
        async with aiofiles.open(file_location, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):  # Read file in chunks of 1MB
                SAVE_PROGRESS[safe_id] = ("running", file_size, total_size)
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File size exceeds {MAX_FILE_SIZE} bytes limit",
                    )
                await buffer.write(chunk)
    except HTTPException:
        SAVE_PROGRESS[safe_id] = ("error", file_size, total_size)
        file_location.unlink(missing_ok=True)  # Delete partially written file
        raise
    except Exception as write_err:
        SAVE_PROGRESS[safe_id] = ("error", file_size, total_size)
        file_location.unlink(missing_ok=True)
        logger.error(f"Upload {safe_id} failed while streaming to disk: {write_err}")
        raise HTTPException(status_code=500, detail="Failed to store uploaded file")

    SAVE_PROGRESS[safe_id] = ("completed", file_size, file_size)

    asyncio.create_task(
        start_file_uploader(file_location, safe_id, safe_path, safe_filename, file_size, conflict=conflict)
    )

    return JSONResponse({"id": safe_id, "status": "ok"})


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

    data = await request.json()

    logger.info(f"getUploadProgress id={data.get('id')}")

    try:
        progress = PROGRESS_CACHE[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getActiveUploads")
async def get_active_uploads(request: Request, _auth: Session = Depends(require_auth)):
    from utils.uploader import PROGRESS_CACHE

    active = []
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

    return JSONResponse({"status": "ok", "active": active})


@app.post("/api/cancelUpload")
async def cancel_upload(request: Request, _auth: Session = Depends(require_auth)):
    from utils.uploader import STOP_TRANSMISSION
    from utils.downloader import STOP_DOWNLOAD

    data = await request.json()

    upload_id = str(data.get("id") or "").strip()
    if not upload_id:
        return JSONResponse({"status": "Upload id is required"}, status_code=400)

    logger.info(f"cancelUpload id={upload_id}")
    STOP_TRANSMISSION.append(upload_id)
    STOP_DOWNLOAD.append(upload_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/renameFileFolder")
async def rename_file_folder(request: Request, _auth: Session = Depends(require_auth)):
    from utils.directoryHandler import ensure_drive_data, backup_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    safe_path = sanitize_path(data.get("path", ""))
    safe_name = sanitize_path(data.get("name", "")).strip("/")

    logger.info(f"renameFileFolder path={safe_path} name={safe_name}")
    drive.rename_file_folder(safe_path, safe_name)
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
    drive.trash_file_folder(safe_path, trash_val)
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
    deleted_msg_ids = drive.delete_file_folder(safe_path)

    # Delete message(s) from Telegram Storage Channel
    if deleted_msg_ids:
        try:
            client = get_client()
            await client.delete_messages(STORAGE_CHANNEL, message_ids=deleted_msg_ids)
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

    deleted_msg_ids = drive.bulk_delete(paths)

    # Delete message(s) from Telegram Storage Channel
    if deleted_msg_ids:
        try:
            client = get_client()
            # Delete in chunks of 100 to respect Telegram API limits
            for i in range(0, len(deleted_msg_ids), 100):
                chunk = deleted_msg_ids[i:i + 100]
                await client.delete_messages(STORAGE_CHANNEL, message_ids=chunk)
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

    for path in paths:
        drive.trash_file_folder(path, trash)

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
        asyncio.create_task(
            download_file(url, id, safe_path, safe_name, single_threaded)
        )
        return JSONResponse({"status": "ok", "id": id})
    except Exception as e:
        return JSONResponse({"status": str(e)}, status_code=400)


@app.post("/api/getFileDownloadProgress")
async def getFileDownloadProgress(request: Request, _auth: Session = Depends(require_auth)):
    from utils.downloader import DOWNLOAD_PROGRESS

    data = await request.json()

    logger.info(f"getFileDownloadProgress id={data.get('id')}")

    try:
        progress = DOWNLOAD_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


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
            # e.g. "Streaming [370/7059]: Screenshot 2026-08-13 005059.png (47.83 KB)"
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

    try:
        from utils.directoryHandler import ensure_drive_data
        total_files, total_bytes = ensure_drive_data().get_drive_stats()
        SYNC_ENGINE_STATUS["drive_stats"] = {
            "total_files": total_files,
            "total_bytes": total_bytes
        }
    except Exception:
        pass

    return JSONResponse({"status": "ok", "data": SYNC_ENGINE_STATUS})


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

