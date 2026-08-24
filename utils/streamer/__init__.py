import math, mimetypes
from fastapi.responses import StreamingResponse, Response
from utils.logger import Logger
from utils.streamer.custom_dl import ByteStreamer
from utils.streamer.file_properties import get_name
from utils.clients import (
    get_client,
)
from urllib.parse import quote

logger = Logger(__name__)

class_cache = {}


async def media_streamer(channel: int, message_id: int, file_name: str, request):
    global class_cache

    range_header = request.headers.get("Range", 0)

    from utils.clients import multi_clients, premium_clients

    # Try preferred client, falling back across all connected clients
    primary_client = get_client()
    client_candidates = [primary_client]
    for c in list(multi_clients.values()) + list(premium_clients.values()):
        if c not in client_candidates:
            client_candidates.append(c)

    file_id = None
    tg_connect = None
    last_err = None

    for client in client_candidates:
        try:
            if client in class_cache:
                current_streamer = class_cache[client]
            else:
                current_streamer = ByteStreamer(client)
                class_cache[client] = current_streamer

            file_id = await current_streamer.get_file_properties(channel, message_id)
            tg_connect = current_streamer
            break
        except Exception as e:
            last_err = e
            continue

    if not tg_connect or not file_id:
        logger.error(f"Failed to retrieve file properties for message {message_id} in channel {channel}: {last_err}")
        raise Exception("FileNotFound")

    file_size = file_id.file_size

    if range_header:
        try:
            range_spec = str(range_header).replace("bytes=", "").split(",")[0]
            from_bytes_str, until_bytes_str = range_spec.split("-", 1)
            from_bytes = int(from_bytes_str) if from_bytes_str else 0
            until_bytes = int(until_bytes_str) if until_bytes_str else file_size - 1
        except (ValueError, TypeError):
            return Response(
                status_code=416,
                content="416: Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return Response(
            status_code=416,
            content="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 512 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    disposition = "attachment"
    mime_type = mimetypes.guess_type(file_name.lower())[0] or "application/octet-stream"

    # Safely previewable extensions that cannot execute arbitrary JS in browser document origin
    safe_previewable_exts = (
        ".pdf", ".txt", ".md", ".py", ".ts", ".css",
        ".json", ".csv", ".tsv", ".log", ".yaml", ".yml", ".sh", ".bat",
        ".c", ".cpp", ".h", ".java", ".rs", ".go", ".sql", ".ini", ".env", ".cfg"
    )

    is_media = (
        "video/" in mime_type
        or "audio/" in mime_type
        or ("image/" in mime_type and not file_name.lower().endswith(".svg"))
        or "application/pdf" in mime_type
        or file_name.lower().endswith(safe_previewable_exts)
    )

    if is_media:
        disposition = "inline"
        if file_name.lower().endswith((".txt", ".log", ".ini", ".env", ".cfg")):
            mime_type = "text/plain; charset=utf-8"
        elif file_name.lower().endswith((".py", ".sh", ".bat", ".c", ".cpp", ".h", ".java", ".rs", ".go", ".sql", ".yaml", ".yml", ".ts", ".css")):
            mime_type = "text/plain; charset=utf-8"
        elif file_name.lower().endswith(".md"):
            mime_type = "text/markdown; charset=utf-8"
        elif file_name.lower().endswith(".pdf"):
            mime_type = "application/pdf"

    # Stored XSS defense: never render active browser markup (.html, .htm, .svg, .xml, .xhtml, .js)
    # same-origin inline in the browser, which could steal cookies/tokens.
    ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
    if ext in ("svg", "html", "htm", "xhtml", "xml", "js"):
        disposition = "attachment"
        mime_type = "application/octet-stream"

    # RFC 6266 / RFC 5987 standard for Unicode and special character filename protection
    safe_ascii = file_name.encode("ascii", "replace").decode("ascii").replace('"', '\\"')
    quoted_utf8 = quote(file_name)

    return StreamingResponse(
        status_code=206 if range_header else 200,
        content=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{safe_ascii}"; filename*=UTF-8\'\'{quoted_utf8}',
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
        },
        media_type=mime_type,
    )
