from utils.downloader import (
    download_file,
    get_file_info_from_url,
)
import asyncio
import secrets
import time
from collections import defaultdict
from pathlib import Path
from contextlib import asynccontextmanager
import aiofiles
from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form, Response
from fastapi.responses import FileResponse, JSONResponse
from config import ADMIN_PASSWORD, MAX_FILE_SIZE, STORAGE_CHANNEL
from utils.clients import initialize_clients
from utils.directoryHandler import getRandomID
from utils.extra import auto_ping_website, convert_class_to_dict, reset_cache_dir
from utils.streamer import media_streamer
from utils.uploader import start_file_uploader
from utils.logger import Logger
import urllib.parse


# Brute-force protection: rate limit password verification attempts
LOGIN_ATTEMPTS = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 60


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOCKOUT_WINDOW_SECONDS]
    return len(LOGIN_ATTEMPTS[ip]) < MAX_LOGIN_ATTEMPTS


def record_failed_attempt(ip: str):
    LOGIN_ATTEMPTS[ip].append(time.time())


def clear_attempts(ip: str):
    LOGIN_ATTEMPTS.pop(ip, None)


def is_admin_authenticated(request: Request, data: dict = None, password: str = None) -> bool:
    """Verifies admin access using timing-safe comparison against ADMIN_PASSWORD."""
    if not ADMIN_PASSWORD:
        return False

    candidate = password
    if not candidate and data:
        candidate = data.get("password") or data.get("pass")

    if not candidate:
        candidate = (
            request.query_params.get("password")
            or request.query_params.get("token")
            or request.cookies.get("tg_session")
        )

    if not candidate:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[7:].strip()

    if candidate and secrets.compare_digest(str(candidate), str(ADMIN_PASSWORD)):
        return True

    return False


# Startup Event
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reset the cache directory, delete cache files
    reset_cache_dir()

    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "admin":
        logger.warning(
            "⚠️ SECURITY WARNING: ADMIN_PASSWORD is set to default 'admin' or empty! "
            "Please update ADMIN_PASSWORD in your environment variables for security."
        )

    # Initialize the clients
    await initialize_clients()

    # Start the website auto ping task
    asyncio.create_task(auto_ping_website())

    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
logger = Logger(__name__)


# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.api_route("/", methods=["GET", "HEAD"])
async def home_page():
    return FileResponse("website/home.html")


@app.api_route("/health", methods=["GET", "HEAD", "POST"])
@app.api_route("/ping", methods=["GET", "HEAD", "POST"])
async def health_check():
    return JSONResponse({"status": "ok", "message": "TG Drive is active"})


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
    return FileResponse(f"website/static/{file_path}")



@app.get("/file")
async def dl_file(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    path = request.query_params.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path parameter")

    clean_path = ("/" + (path or "").replace("/share_", "").replace("share_", "").strip("/")).replace("//", "/")
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


# =========================================================
# Google-Grade Thumbnail & Media Optimization Service
# =========================================================

import collections

class ThumbnailService:
    def __init__(self, max_ram_items: int = 300, max_disk_mb: int = 50):
        self.ram_cache: collections.OrderedDict[int, bytes] = collections.OrderedDict()
        self.max_ram_items = max_ram_items
        self.max_disk_bytes = max_disk_mb * 1024 * 1024
        self.cache_dir = Path("./cache/thumbs")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(4)
        self.in_flight: dict[int, asyncio.Future] = {}

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
        try:
            files = list(self.cache_dir.glob("*.jpg"))
            total_size = sum(f.stat().st_size for f in files if f.exists())
            if total_size > self.max_disk_bytes:
                files.sort(key=lambda f: f.stat().st_mtime)
                for f in files[: len(files) // 2]:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass

    async def get_or_fetch(self, file_id: int, file_name: str) -> bytes | None:
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

                thumb_target = None
                ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
                is_image = ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "tiff"]

                if msg.photo:
                    thumb_target = msg.photo
                elif msg.document and msg.document.thumbs:
                    thumb_target = msg.document.thumbs[0]
                elif msg.video and msg.video.thumbs:
                    thumb_target = msg.video.thumbs[0]
                elif msg.animation and msg.animation.thumbs:
                    thumb_target = msg.animation.thumbs[0]
                elif is_image and msg.document and msg.document.file_size and msg.document.file_size < 3 * 1024 * 1024:
                    thumb_target = msg.document

                if thumb_target:
                    temp_path = await client.download_media(thumb_target, file_name=str(disk_file))
                    if temp_path and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                        data = disk_file.read_bytes()
                        self.put_ram(file_id, data)
                        self.prune_disk_if_needed()
                        future.set_result(data)
                        return data

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
    drive = ensure_drive_data()

    path = request.query_params.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path parameter")

    clean_path = ("/" + (path or "").replace("/share_", "").replace("share_", "").strip("/")).replace("//", "/")
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
async def check_password(request: Request):
    client_ip = request.client.host if request.client else "unknown"

    if not check_rate_limit(client_ip):
        logger.warning(f"Rate limit triggered for IP {client_ip} on /api/checkPassword")
        return JSONResponse(
            {"status": "Too many failed attempts. Please wait 1 minute before trying again."},
            status_code=429,
        )

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "Invalid payload"}, status_code=400)

    password = data.get("password") or data.get("pass", "")
    if password and secrets.compare_digest(str(password), str(ADMIN_PASSWORD)):
        clear_attempts(client_ip)
        resp = JSONResponse({"status": "ok"})
        resp.set_cookie(
            key="tg_session",
            value=ADMIN_PASSWORD,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
        )
        return resp

    record_failed_attempt(client_ip)
    return JSONResponse({"status": "Invalid password"}, status_code=401)


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(key="tg_session")
    return resp


@app.post("/api/createNewFolder")
async def api_new_folder(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"createNewFolder {data}")
    target_dir = drive.get_directory(data["path"])
    if target_dir and hasattr(target_dir, "contents"):
        for id in target_dir.contents:
            f = target_dir.contents[id]
            if f.type == "folder":
                if f.name == data["name"]:
                    return JSONResponse(
                        {
                            "status": "Folder with the name already exist in current directory"
                        }
                    )

    drive.new_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/getDirectory")
async def api_get_directory(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()
    is_admin = is_admin_authenticated(request, data=data)
    auth = data.get("auth")
    path = data.get("path", "/")

    logger.info(f"getFolder path={path} is_admin={is_admin}")

    breadcrumbs = drive.get_breadcrumbs(path)

    # Protected paths: trash & search & normal drive require admin authentication
    if not is_admin:
        if not path.startswith("/share_"):
            return JSONResponse({"status": "Invalid password"}, status_code=401)

        share_path = path.split("_", 1)[1]
        res = drive.get_directory(share_path, is_admin=False, auth=auth)
        if not res:
            return JSONResponse({"status": "Unauthorized folder access"}, status_code=403)
        if isinstance(res, tuple):
            folder_data, auth_home_path = res
        else:
            folder_data, auth_home_path = res, None
        auth_home_path = auth_home_path.replace("//", "/") if auth_home_path else None
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
        return JSONResponse(
            {"status": "ok", "data": folder_data, "breadcrumbs": breadcrumbs, "auth_home_path": auth_home_path}
        )

    # Admin access paths
    if path == "/trash":
        trash_data = {"contents": drive.get_trashed_files_folders()}
        folder_data = convert_class_to_dict(trash_data, isObject=False, showtrash=True)

    elif "/search_" in path:
        query = urllib.parse.unquote(path.split("_", 1)[1])
        search_data = {"contents": drive.search_file_folder(query)}
        folder_data = convert_class_to_dict(search_data, isObject=False, showtrash=False)

    elif "/share_" in path:
        share_path = path.split("_", 1)[1]
        res = drive.get_directory(share_path, is_admin=True, auth=auth)
        if not res:
            return JSONResponse({"status": "Folder not found"}, status_code=404)
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
        folder_data = drive.get_directory(path, is_admin=True)
        if not folder_data:
            return JSONResponse({"status": "Folder not found"}, status_code=404)
        if isinstance(folder_data, tuple):
            folder_data = folder_data[0]
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)

    total_files, total_bytes = drive.get_drive_stats()
    resp = JSONResponse({
        "status": "ok",
        "data": folder_data,
        "breadcrumbs": breadcrumbs,
        "auth_home_path": None,
        "stats": {
            "total_files": total_files,
            "total_bytes": total_bytes
        }
    })
    if is_admin and ADMIN_PASSWORD:
        resp.set_cookie(
            key="tg_session",
            value=ADMIN_PASSWORD,
            httponly=True,
            samesite="lax",
            max_age=30 * 86400,
        )
    return resp


@app.post("/api/moveFileFolder")
async def move_file_folder(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"moveFileFolder {data}")
    try:
        drive.move_file_folder(data["src_path"], data["dest_path"])
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Error moving file/folder: {e}")
        return JSONResponse({"status": str(e)}, status_code=400)



SAVE_PROGRESS = {}


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    path: str = Form(...),
    password: str = Form(...),
    id: str = Form(...),
    total_size: str = Form(...),
):
    global SAVE_PROGRESS

    if not is_admin_authenticated(None, password=password):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    total_size = int(total_size)
    SAVE_PROGRESS[id] = ("running", 0, total_size)

    ext = file.filename.lower().split(".")[-1]

    cache_dir = Path("./cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_location = cache_dir / f"{id}.{ext}"

    file_size = 0

    async with aiofiles.open(file_location, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):  # Read file in chunks of 1MB
            SAVE_PROGRESS[id] = ("running", file_size, total_size)
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                await buffer.close()
                file_location.unlink()  # Delete the partially written file
                raise HTTPException(
                    status_code=400,
                    detail=f"File size exceeds {MAX_FILE_SIZE} bytes limit",
                )
            await buffer.write(chunk)

    SAVE_PROGRESS[id] = ("completed", file_size, file_size)

    asyncio.create_task(
        start_file_uploader(file_location, id, path, file.filename, file_size)
    )

    return JSONResponse({"id": id, "status": "ok"})


@app.post("/api/getSaveProgress")
async def get_save_progress(request: Request):
    global SAVE_PROGRESS

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getUploadProgress {data}")
    try:
        progress = SAVE_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getUploadProgress")
async def get_upload_progress(request: Request):
    from utils.uploader import PROGRESS_CACHE

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getUploadProgress {data}")

    try:
        progress = PROGRESS_CACHE[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getActiveUploads")
async def get_active_uploads(request: Request):
    from utils.uploader import PROGRESS_CACHE

    data = await request.json()
    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    active = []
    for upload_id, prog in list(PROGRESS_CACHE.items()):
        status = prog[0]
        current = prog[1]
        total = prog[2]
        filename = prog[3] if len(prog) > 3 else "File"
        if status == "running":
            active.append({
                "id": upload_id,
                "status": status,
                "current": current,
                "total": total,
                "filename": filename
            })

    return JSONResponse({"status": "ok", "active": active})


@app.post("/api/cancelUpload")
async def cancel_upload(request: Request):
    from utils.uploader import STOP_TRANSMISSION
    from utils.downloader import STOP_DOWNLOAD

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"cancelUpload {data}")
    STOP_TRANSMISSION.append(data["id"])
    STOP_DOWNLOAD.append(data["id"])
    return JSONResponse({"status": "ok"})


@app.post("/api/renameFileFolder")
async def rename_file_folder(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"renameFileFolder {data}")
    drive.rename_file_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/trashFileFolder")
async def trash_file_folder(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"trashFileFolder {data}")
    drive.trash_file_folder(data["path"], data["trash"])
    return JSONResponse({"status": "ok"})


@app.post("/api/deleteFileFolder")
async def delete_file_folder(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"deleteFileFolder {data}")
    drive.delete_file_folder(data["path"])
    return JSONResponse({"status": "ok"})


@app.post("/api/getFileInfoFromUrl")
async def getFileInfoFromUrl(request: Request):
    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getFileInfoFromUrl {data}")
    try:
        file_info = await get_file_info_from_url(data["url"])
        return JSONResponse({"status": "ok", "data": file_info})
    except Exception as e:
        return JSONResponse({"status": str(e)})


@app.post("/api/startFileDownloadFromUrl")
async def startFileDownloadFromUrl(request: Request):
    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"startFileDownloadFromUrl {data}")
    try:
        id = getRandomID()
        asyncio.create_task(
            download_file(data["url"], id, data["path"], data["filename"], data["singleThreaded"])
        )
        return JSONResponse({"status": "ok", "id": id})
    except Exception as e:
        return JSONResponse({"status": str(e)})


@app.post("/api/getFileDownloadProgress")
async def getFileDownloadProgress(request: Request):
    from utils.downloader import DOWNLOAD_PROGRESS

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getFileDownloadProgress {data}")

    try:
        progress = DOWNLOAD_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except Exception:
        return JSONResponse({"status": "not found"})


@app.post("/api/getFolderShareAuth")
async def getFolderShareAuth(request: Request):
    from utils.directoryHandler import ensure_drive_data
    drive = ensure_drive_data()

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getFolderShareAuth {data}")

    try:
        auth = drive.get_folder_auth(data["path"])
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
async def getSyncStatus(request: Request):
    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

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
                        "path": SYNC_ENGINE_STATUS.get("current_path") or "/OnePlus_Nord_CE4/",
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
async def updateSyncStatus(request: Request):
    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    status_update = data.get("sync_data", {})
    
    # Handle completed item appending
    completed_item = status_update.pop("completed_item", None)
    if completed_item:
        SYNC_ENGINE_STATUS["completed_list"].insert(0, completed_item)
        if len(SYNC_ENGINE_STATUS["completed_list"]) > 100:
            SYNC_ENGINE_STATUS["completed_list"] = SYNC_ENGINE_STATUS["completed_list"][:100]

    # Handle pending queue update or item popping
    if "pending_queue" in status_update:
        SYNC_ENGINE_STATUS["pending_queue"] = status_update.pop("pending_queue")
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
                        "path": SYNC_ENGINE_STATUS.get("current_path") or "/OnePlus_Nord_CE4/",
                        "size": fsize,
                        "time": log_time
                    })
                    if len(SYNC_ENGINE_STATUS["completed_list"]) > 100:
                        SYNC_ENGINE_STATUS["completed_list"] = SYNC_ENGINE_STATUS["completed_list"][:100]
            except Exception:
                pass

    return JSONResponse({"status": "ok"})

