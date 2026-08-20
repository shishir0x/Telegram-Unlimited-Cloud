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


@app.get("/")
async def home_page():
    return FileResponse("website/home.html")


@app.get("/stream")
async def stream_page():
    return FileResponse("website/VideoPlayer.html")


@app.get("/static/{file_path:path}")
async def static_files(file_path):
    if "apiHandler.js" in file_path:
        with open(Path("website/static/js/apiHandler.js")) as f:
            content = f.read()
            content = content.replace("MAX_FILE_SIZE__SDGJDG", str(MAX_FILE_SIZE))
        return Response(content=content, media_type="application/javascript")
    return FileResponse(f"website/static/{file_path}")


@app.get("/file")
async def dl_file(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    path = request.query_params.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path parameter")

    auth = request.query_params.get("auth")
    is_admin = is_admin_authenticated(request)

    # If not admin, verify if access is authorized through folder share token
    if not is_admin:
        if not auth:
            raise HTTPException(status_code=401, detail="Unauthorized access to file")
        try:
            folder_path = (
                "/" + "/".join(path.strip("/").split("/")[:-1])
                if len(path.strip("/").split("/")) > 1
                else "/"
            )
            folder_res = DRIVE_DATA.get_directory(folder_path, is_admin=False, auth=auth)
            if not folder_res:
                raise HTTPException(status_code=401, detail="Unauthorized access to file")
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized access to file")

    try:
        file = DRIVE_DATA.get_file(path)
        return await media_streamer(STORAGE_CHANNEL, file.file_id, file.name, request)
    except Exception as e:
        logger.error(f"Error streaming file '{path}': {e}")
        raise HTTPException(status_code=404, detail="File not found")


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

    password = data.get("pass", "")
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
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"createNewFolder {data}")
    folder_data = DRIVE_DATA.get_directory(data["path"]).contents
    for id in folder_data:
        f = folder_data[id]
        if f.type == "folder":
            if f.name == data["name"]:
                return JSONResponse(
                    {
                        "status": "Folder with the name already exist in current directory"
                    }
                )

    DRIVE_DATA.new_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/getDirectory")
async def api_get_directory(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()
    is_admin = is_admin_authenticated(request, data=data)
    auth = data.get("auth")
    path = data.get("path", "/")

    logger.info(f"getFolder path={path} is_admin={is_admin}")

    # Protected paths: trash & search & normal drive require admin authentication
    if not is_admin:
        if not path.startswith("/share_"):
            return JSONResponse({"status": "Invalid password"}, status_code=401)

        share_path = path.split("_", 1)[1]
        res = DRIVE_DATA.get_directory(share_path, is_admin=False, auth=auth)
        if not res:
            return JSONResponse({"status": "Unauthorized folder access"}, status_code=403)
        folder_data, auth_home_path = res
        auth_home_path = auth_home_path.replace("//", "/") if auth_home_path else None
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
        return JSONResponse(
            {"status": "ok", "data": folder_data, "auth_home_path": auth_home_path}
        )

    # Admin access paths
    if path == "/trash":
        trash_data = {"contents": DRIVE_DATA.get_trashed_files_folders()}
        folder_data = convert_class_to_dict(trash_data, isObject=False, showtrash=True)

    elif "/search_" in path:
        query = urllib.parse.unquote(path.split("_", 1)[1])
        search_data = {"contents": DRIVE_DATA.search_file_folder(query)}
        folder_data = convert_class_to_dict(search_data, isObject=False, showtrash=False)

    elif "/share_" in path:
        share_path = path.split("_", 1)[1]
        folder_data, auth_home_path = DRIVE_DATA.get_directory(share_path, is_admin=True, auth=auth)
        auth_home_path = auth_home_path.replace("//", "/") if auth_home_path else None
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
        return JSONResponse(
            {"status": "ok", "data": folder_data, "auth_home_path": auth_home_path}
        )

    else:
        folder_data = DRIVE_DATA.get_directory(path, is_admin=True)
        folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)

    return JSONResponse({"status": "ok", "data": folder_data, "auth_home_path": None})


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
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"renameFileFolder {data}")
    DRIVE_DATA.rename_file_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/trashFileFolder")
async def trash_file_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"trashFileFolder {data}")
    DRIVE_DATA.trash_file_folder(data["path"], data["trash"])
    return JSONResponse({"status": "ok"})


@app.post("/api/deleteFileFolder")
async def delete_file_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"deleteFileFolder {data}")
    DRIVE_DATA.delete_file_folder(data["path"])
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
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if not is_admin_authenticated(request, data=data):
        return JSONResponse({"status": "Invalid password"}, status_code=401)

    logger.info(f"getFolderShareAuth {data}")

    try:
        auth = DRIVE_DATA.get_folder_auth(data["path"])
        return JSONResponse({"status": "ok", "auth": auth})
    except Exception:
        return JSONResponse({"status": "not found"})
