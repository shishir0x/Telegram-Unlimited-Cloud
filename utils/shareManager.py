"""
Secure Share Manager
====================
Cryptographically-secure, unguessable share tokens for files AND folders with
optional expiration, password protection, and granular download/preview rights.

Design principles:
    - Tokens are 256-bit `secrets.token_urlsafe(32)` values: impossible to guess,
      no enumeration possible (unlike the legacy 6-char auth hashes).
    - Internal Telegram message IDs (`file.file_id`) NEVER appear in any share
      payload; clients only ever see drive-level random IDs and relative keys.
    - Passwords are stored as PBKDF2-HMAC-SHA256 hashes (reuses utils.auth).
    - Unlock state is an HMAC-signed cookie derived from the admin secret, so it
      survives restarts without storing session state and cannot be forged.
    - Scope enforcement is purely ID-walk based: a share exposes exactly its
      target subtree, nothing above or beside it.
"""

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logger import Logger

logger = Logger(__name__)

SHARES_FILE = Path("./cache/shares.json")
SHARE_TOKEN_BYTES = 32          # 256-bit tokens
DEFAULT_SHARE_HOURS = 24 * 7    # default expiry offered to callers (may pass None)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_shares() -> Dict[str, dict]:
    if not SHARES_FILE.exists():
        return {}
    try:
        return json.loads(SHARES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Could not load shares store: {e}")
        backup = SHARES_FILE.with_suffix(".json.bak")
        if backup.exists():
            try:
                return json.loads(backup.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}


def _save_shares(shares: Dict[str, dict]) -> None:
    try:
        SHARES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SHARES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(shares, indent=2), encoding="utf-8")
        if SHARES_FILE.exists():
            try:
                import shutil
                shutil.copy2(SHARES_FILE, SHARES_FILE.with_suffix(".json.bak"))
            except Exception:
                pass
        os.replace(tmp, SHARES_FILE)
    except Exception as e:
        logger.error(f"Could not save shares store: {e}")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def generate_share_token() -> str:
    """Cryptographically secure, unguessable share token (256 bits of entropy)."""
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def _unlock_secret() -> bytes:
    """Dedicated random 32-byte signing key, persisted in ./cache. Deliberately
    NOT derived from ADMIN_PASSWORD (avoids offline brute-force from a sampled
    cookie). Deleting the key file invalidates all outstanding unlock cookies."""
    global _SIGNING_KEY
    if _SIGNING_KEY is not None:
        return _SIGNING_KEY
    key_file = SHARES_FILE.parent / "share_secret.key"
    try:
        if key_file.exists():
            _SIGNING_KEY = bytes.fromhex(key_file.read_text(encoding="utf-8").strip())
        else:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            raw = secrets.token_bytes(32)
            key_file.write_text(raw.hex(), encoding="utf-8")
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
            _SIGNING_KEY = raw
    except Exception as e:
        logger.warning(f"Share signing key unavailable ({e}); unlock cookies disabled.")
        _SIGNING_KEY = b""
    return _SIGNING_KEY


_SIGNING_KEY: Optional[bytes] = None
UNLOCK_COOKIE_MAX_AGE = 7 * 86400


def make_unlock_cookie_value(token: str) -> str:
    """HMAC-signed unlock proof embedding an issuance time: '<iat>.<mac>'."""
    secret = _unlock_secret()
    if not secret:
        return ""
    iat = int(time.time())
    mac = hmac.new(secret, f"unlock:{token}:{iat}".encode(), hashlib.sha256).hexdigest()
    return f"{iat}.{mac}"


def verify_unlock_cookie(token: str, value: Optional[str]) -> bool:
    if not value or "." not in value:
        return False
    secret = _unlock_secret()
    if not secret:
        return False
    try:
        iat_str, mac = value.split(".", 1)
        iat = int(iat_str)
        if time.time() - iat > UNLOCK_COOKIE_MAX_AGE:
            return False
        expected = hmac.new(secret, f"unlock:{token}:{iat}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, mac)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Share lifecycle
# ---------------------------------------------------------------------------

def create_share(
    target_id_path: str,
    item_type: str,
    name: str,
    expires_at: Optional[float] = None,
    password_hash: Optional[str] = None,
    allow_download: bool = True,
    allow_preview: bool = True,
) -> dict:
    """Create a fresh share. Returns the stored record."""
    token = generate_share_token()
    rec = {
        "token": token,
        "target": target_id_path.strip("/"),
        "type": "folder" if item_type == "folder" else "file",
        "name": name,
        "created_at": time.time(),
        "expires_at": float(expires_at) if expires_at else None,
        "password_hash": password_hash or None,
        "allow_download": bool(allow_download),
        "allow_preview": bool(allow_preview),
        "revoked": False,
        "access_count": 0,
    }
    shares = _load_shares()
    shares[token] = rec
    _save_shares(shares)
    logger.info(f"Created {rec['type']} share '{rec['name']}' (expires_at={rec['expires_at']})")
    return rec


def regenerate_share(old_token: str) -> Optional[dict]:
    """Atomically rotate a share onto a brand-new unguessable token.
    The old token becomes permanently invalid."""
    shares = _load_shares()
    old = shares.get(old_token)
    if not old:
        return None
    old["revoked"] = True
    new_token = generate_share_token()
    replacement = dict(old)
    replacement["token"] = new_token
    replacement["revoked"] = False
    replacement["created_at"] = time.time()
    shares[new_token] = replacement
    _save_shares(shares)
    logger.info(f"Regenerated share '{old.get('name')}' -> new token issued")
    return replacement


def revoke_share(token: str) -> bool:
    shares = _load_shares()
    rec = shares.get(token)
    if not rec:
        return False
    rec["revoked"] = True
    _save_shares(shares)
    logger.info(f"Revoked share '{rec.get('name')}'")
    return True


def list_shares() -> List[dict]:
    """All shares, newest first. Tokens ARE included (admin-only endpoint)."""
    recs = sorted(_load_shares().values(), key=lambda r: r.get("created_at", 0), reverse=True)
    return [public_record(r, include_token=True) for r in recs]


def get_share_record(token: str) -> Optional[dict]:
    if not token or len(token) > 128:
        return None
    return _load_shares().get(token)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_share(token: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    Returns (record, None) if usable, else (None, reason).
    Reason ∈ {'invalid', 'revoked', 'expired'}.
    """
    rec = get_share_record(token)
    if rec is None:
        return None, "invalid"
    if rec.get("revoked"):
        return None, "revoked"
    exp = rec.get("expires_at")
    if exp and time.time() > float(exp):
        return None, "expired"
    return rec, None


def touch_access(rec: dict) -> None:
    """Best-effort access counter bump."""
    try:
        shares = _load_shares()
        cur = shares.get(rec["token"])
        if cur:
            cur["access_count"] = int(cur.get("access_count", 0)) + 1
            _save_shares(shares)
    except Exception:
        pass


def public_record(rec: dict, include_token: bool = False) -> dict:
    out = {
        "type": rec.get("type"),
        "name": rec.get("name"),
        "created_at": rec.get("created_at"),
        "expires_at": rec.get("expires_at"),
        "revoked": rec.get("revoked", False),
        "has_password": bool(rec.get("password_hash")),
        "allow_download": rec.get("allow_download", True),
        "allow_preview": rec.get("allow_preview", True),
        "access_count": int(rec.get("access_count", 0)),
    }
    if include_token:
        out["token"] = rec["token"]
    return out


# ---------------------------------------------------------------------------
# Scoped subtree resolution (pure ID-walk — no name guessing, no escape)
# ---------------------------------------------------------------------------

def sanitize_rel(rel: str) -> str:
    """Normalize a client-supplied relative path inside a share. Returns '' when unsafe."""
    if not rel:
        return ""
    clean = str(rel).replace("\\", "/").replace("\x00", "")
    parts = [p for p in clean.split("/") if p not in ("", ".",)]
    if any(p == ".." for p in parts):
        return ""
    if len(parts) > 32:
        return ""
    return "/".join(parts)


def resolve_target_drive():
    from utils.directoryHandler import ensure_drive_data
    return ensure_drive_data()


def resolve_scope_root(rec: dict):
    """Resolve the share target object. Returns (node, parent_node_or_None, id_parts).
    Trashed targets resolve to None — a trashed share must stop working."""
    drive = resolve_target_drive()
    parts = [p for p in str(rec.get("target", "")).split("/") if p]
    node = drive.contents.get("/")
    parent = None
    for seg in parts:
        contents = getattr(node, "contents", {})
        nxt = contents.get(seg)
        if nxt is None or getattr(nxt, "trash", False):
            return None, None, parts
        parent, node = node, nxt
    return node, parent, parts


def resolve_within_scope(rec: dict, rel: str):
    """
    Resolve `rel` strictly INSIDE the shared subtree.
    Returns (node, parent) or (None, None). Any attempt outside scope fails closed.
    """
    rel = sanitize_rel(rel)
    if rel and rec.get("type") != "folder":
        # File shares expose exactly one item; ignore any rel beyond it
        return None, None

    drive = resolve_target_drive()
    parts = [p for p in str(rec.get("target", "")).split("/") if p]
    if rel:
        parts += rel.split("/")

    node = drive.contents.get("/")
    parent = None
    for seg in parts:
        contents = getattr(node, "contents", {})
        nxt = contents.get(seg)
        if nxt is None or getattr(nxt, "trash", False):
            return None, None
        parent, node = node, nxt
    return node, parent


def build_breadcrumbs(rec: dict, rel: str) -> List[dict]:
    """Human-readable crumb trail within the share scope: [{name, rel}]."""
    crumbs: List[dict] = [{"name": rec.get("name") or "Shared", "rel": ""}]
    if rec.get("type") != "folder" or not rel:
        return crumbs

    drive = resolve_target_drive()
    base_parts = [p for p in str(rec.get("target", "")).split("/") if p]
    rel_parts = [p for p in sanitize_rel(rel).split("/") if p]

    node = drive.contents.get("/")
    walked: List[str] = []
    for seg in base_parts:
        node = getattr(node, "contents", {}).get(seg)
        if node is None:
            return crumbs
    for seg in rel_parts:
        node = getattr(node, "contents", {}).get(seg)
        if node is None:
            break
        walked.append(seg)
        crumbs.append({"name": getattr(node, "name", seg), "rel": "/".join(walked)})
    return crumbs


def list_folder_children(node, parent_rel: str) -> List[dict]:
    """
    Serialize permitted descendants for the public page.
    Exposes ONLY safe fields: drive-level random id, name, type, size, date.
    Telegram message IDs are deliberately excluded.
    """
    children = []
    for cid, item in getattr(node, "contents", {}).items():
        if getattr(item, "trash", False):
            continue
        t = getattr(item, "type", "")
        if t == "folder":
            children.append({
                "key": cid,
                "name": getattr(item, "name", cid),
                "type": "folder",
                "size": None,
                "date": getattr(item, "upload_date", ""),
            })
        elif t == "file":
            children.append({
                "key": cid,
                "name": getattr(item, "name", cid),
                "type": "file",
                "size": int(getattr(item, "size", 0) or 0),
                "date": getattr(item, "upload_date", ""),
            })
    children.sort(key=lambda c: (c["type"] != "folder", c["name"].lower()))
    return children


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "svg", "avif"}
VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "avi", "m4v", "ogv"}
AUDIO_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"}
PDF_EXTS = {"pdf"}
TEXT_EXTS = {"txt", "md", "json", "csv", "log", "xml", "yml", "yaml", "py", "js",
             "ts", "html", "css", "c", "cpp", "h", "java", "sh", "bat", "ini", "toml"}


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in str(name) else ""


def preview_kind(name: str) -> Optional[str]:
    """Returns 'image' | 'video' | 'audio' | 'pdf' | 'text' | None."""
    e = ext_of(name)
    if e in IMAGE_EXTS:
        return "image"
    if e in VIDEO_EXTS:
        return "video"
    if e in AUDIO_EXTS:
        return "audio"
    if e in PDF_EXTS:
        return "pdf"
    if e in TEXT_EXTS:
        return "text"
    return None


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(str(name))[0] or "application/octet-stream"


def collect_share_items_for_zip(node, root_name: str = "Shared") -> Tuple[str, List[Dict]]:
    """
    Recursively collect all files under a scoped folder node for creating a ZIP archive.
    Returns (suggested_name, items_list).
    Excludes trashed items. Ensures paths are strictly relative within the archive.
    """
    items = []
    
    def _walk(curr_node, current_rel_path: str):
        if getattr(curr_node, "trash", False):
            return
        node_type = getattr(curr_node, "type", "")
        if node_type == "file":
            fid = getattr(curr_node, "file_id", None)
            fname = getattr(curr_node, "name", "file")
            if fid:
                items.append({
                    "file_id": fid,
                    "file_name": fname,
                    "archive_path": f"{current_rel_path}/{fname}".strip("/"),
                    "size": int(getattr(curr_node, "size", 0) or 0),
                })
        elif node_type == "folder":
            contents = getattr(curr_node, "contents", {})
            for cid, child in contents.items():
                if getattr(child, "trash", False):
                    continue
                child_name = getattr(child, "name", cid)
                child_type = getattr(child, "type", "")
                if child_type == "folder":
                    _walk(child, f"{current_rel_path}/{child_name}".strip("/"))
                elif child_type == "file":
                    _walk(child, current_rel_path)

    base_name = getattr(node, "name", root_name) or root_name
    _walk(node, "")
    return base_name, items

