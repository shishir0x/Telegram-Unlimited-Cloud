"""
Authentication Core — Session & OTP Management
================================================
Provides:
    - Cryptographically secure session token management (in-memory store)
    - Time-limited, single-use OTP store with attempt limiting
    - FastAPI dependency `require_auth` for protecting endpoints
    - Rate-limit helpers for login, OTP request, and OTP verification

Design principles:
    - Passwords are compared using secrets.compare_digest (constant-time)
    - Sessions are random 256-bit tokens, never derived from user data
    - OTPs are 6-digit codes generated from os.urandom (cryptographic source)
    - All stores are in-memory; process restart invalidates all sessions/OTPs
    - No sensitive values are ever logged

Thread-safety: Python's asyncio event loop is single-threaded, so dict
operations on the stores are effectively atomic for the async use case.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import Cookie, HTTPException, Request, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (sourced from environment via config.py at startup)
# ---------------------------------------------------------------------------
# Configurable session duration in hours (default 12 hours)
SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "12"))
SESSION_TTL_SECONDS: int = SESSION_HOURS * 60 * 60         # default 12 hours
SESSION_INACTIVITY_TTL: int = 4 * 60 * 60                  # 4 hours idle = expire
OTP_TTL_SECONDS: int = 5 * 60                             # 5 minutes
OTP_MAX_ATTEMPTS: int = 5                                  # wrong OTP attempts before lockout
OTP_COOLDOWN_SECONDS: int = 60                            # minimum gap between OTP re-sends

# Rate limiting (very simple in-memory sliding window)
_RATE_WINDOWS: dict[str, dict] = {}        # { "category:ip": {"count": N, "window_start": T} }
LOGIN_RATE_LIMIT = (10, 60)                # 10 attempts per 60 seconds per IP
OTP_REQUEST_RATE_LIMIT = (5, 60)          # 5 OTP requests per 60 seconds per IP
OTP_VERIFY_RATE_LIMIT = (10, 60)          # 10 verify attempts per 60 seconds per IP

_SESSION_FILE = Path("./cache/auth_sessions.json")


def hash_password(plaintext: str, iterations: int = 100_000) -> str:
    """
    Hash a plaintext password using PBKDF2-HMAC-SHA256 with a cryptographically secure random salt.
    Format: pbkdf2:sha256:{iterations}${salt_hex}${hash_hex}
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, iterations)
    return f"pbkdf2:sha256:{iterations}${salt.hex()}${dk.hex()}"


def verify_password(plaintext: str, stored_hash_or_plaintext: str) -> bool:
    """
    Constant-time password verification supporting PBKDF2-HMAC-SHA256 hashes,
    SHA-256 hashes, and plaintext passwords (from .env) with timing-attack immunity.
    """
    if not plaintext or not stored_hash_or_plaintext:
        return False

    # Check for PBKDF2 format: pbkdf2:sha256:100000$salt$hash
    if stored_hash_or_plaintext.startswith("pbkdf2:sha256:"):
        try:
            parts = stored_hash_or_plaintext.split("$")
            header = parts[0]
            iterations = int(header.split(":")[2])
            salt = bytes.fromhex(parts[1])
            expected_hash = parts[2]
            computed_dk = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, iterations)
            return secrets.compare_digest(computed_dk.hex(), expected_hash)
        except Exception as e:
            logger.warning(f"Error parsing PBKDF2 password format: {e}")
            return False

    # Check for raw SHA-256 hash (64 hex characters)
    if len(stored_hash_or_plaintext) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored_hash_or_plaintext):
        computed_sha = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        if secrets.compare_digest(computed_sha.lower(), stored_hash_or_plaintext.lower()):
            return True

    # Fallback to direct constant-time plaintext comparison (e.g. from .env ADMIN_PASSWORD)
    return secrets.compare_digest(plaintext.encode("utf-8"), stored_hash_or_plaintext.encode("utf-8"))


def sanitize_path(raw_path: Optional[str]) -> str:
    """
    Strict path traversal shield. Normalizes slashes, eliminates null bytes, 
    resolves relative path sequences (../, ..\\), and ensures clean root formatting.
    """
    if not raw_path:
        return "/"
    
    import posixpath
    # Strip null bytes and control chars
    clean = str(raw_path).replace("\x00", "").replace("\r", "").replace("\n", "").strip()
    # Normalize backslashes
    clean = clean.replace("\\", "/")
    if not clean.startswith("/"):
        clean = "/" + clean

    # Resolve canonical POSIX path
    norm = posixpath.normpath(clean)
    # Collapse any multiple leading slashes into single /
    import re
    norm = re.sub(r"^/+", "/", norm)
    if not norm:
        norm = "/"
    return norm


def get_client_ip(request: Request) -> str:
    """
    Extracts the client IP address, safe for reverse-proxied deployments (Render, Cloudflare, Nginx, etc.).

    Prioritizes CDN/Proxy headers (CF-Connecting-IP, True-Client-IP, X-Real-IP) and the
    original client IP (first entry) of X-Forwarded-For.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()[:64]

    true_ip = request.headers.get("True-Client-IP")
    if true_ip:
        return true_ip.strip()[:64]

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()[:64]

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        hops = [h.strip() for h in forwarded[:512].split(",") if h.strip()]
        if hops:
            return hops[0][:64]

    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Session:
    token: str
    ip: str = "unknown"
    csrf_token: str = field(default_factory=lambda: secrets.token_hex(16))
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        now = time.time()
        age = now - self.created_at
        idle = now - self.last_seen
        return age > SESSION_TTL_SECONDS or idle > SESSION_INACTIVITY_TTL

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "ip": self.ip,
            "csrf_token": self.csrf_token,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            token=d["token"],
            ip=d.get("ip", "unknown"),
            csrf_token=d.get("csrf_token", secrets.token_hex(16)),
            created_at=d.get("created_at", time.time()),
            last_seen=d.get("last_seen", time.time()),
        )


@dataclass
class PendingOTP:
    otp_hash: str                          # sha256 hex of the OTP — raw value never stored
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_sent: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > OTP_TTL_SECONDS

    @property
    def is_locked(self) -> bool:
        return self.attempts >= OTP_MAX_ATTEMPTS

    @property
    def can_resend(self) -> bool:
        return time.time() - self.last_sent > OTP_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
# Single-admin system: only one session and at most one pending OTP exist at
# a time.  We use dicts to allow future multi-user extension without rework.

_SESSIONS: dict[str, Session] = {}        # token -> Session
_PENDING_OTPS: dict[str, PendingOTP] = {} # "admin" key -> PendingOTP


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _check_rate_limit(category: str, ip: str, limit: int, window_seconds: int) -> None:
    """
    Sliding-window rate limiter with automatic stale window cleanup.
    Raises HTTP 429 if caller exceeds `limit` requests within `window_seconds`.
    """
    key = f"{category}:{ip}"
    now = time.time()

    # Periodic cleanup of expired rate windows to prevent unbounded memory growth
    if len(_RATE_WINDOWS) > 300:
        stale_keys = [
            k for k, v in _RATE_WINDOWS.items()
            if now - v.get("window_start", 0) > 300
        ]
        for k in stale_keys:
            _RATE_WINDOWS.pop(k, None)
        if len(_RATE_WINDOWS) > 1000:
            sorted_keys = sorted(_RATE_WINDOWS.keys(), key=lambda k: _RATE_WINDOWS[k].get("window_start", 0))
            for k in sorted_keys[: len(_RATE_WINDOWS) - 500]:
                _RATE_WINDOWS.pop(k, None)

    entry = _RATE_WINDOWS.get(key)
    if entry is None or now - entry["window_start"] > window_seconds:
        _RATE_WINDOWS[key] = {"count": 1, "window_start": now}
        return

    entry["count"] += 1
    if entry["count"] > limit:
        retry_after = max(1, int(window_seconds - (now - entry["window_start"])) + 1)
        logger.warning(f"Rate limit exceeded: {category} from {ip} ({entry['count']} reqs / {window_seconds}s)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_login(request: Request) -> None:
    """Call this at the start of the login endpoint."""
    ip = get_client_ip(request)
    _check_rate_limit("login", ip, *LOGIN_RATE_LIMIT)


def rate_limit_check_password(request: Request) -> None:
    """Call this at the start of checkPassword endpoint to protect CLI and direct password checks."""
    ip = get_client_ip(request)
    _check_rate_limit("check_password", ip, 15, 60)


def rate_limit_otp_request(request: Request) -> None:
    """Call this at the start of the OTP send endpoint."""
    ip = get_client_ip(request)
    _check_rate_limit("otp_request", ip, *OTP_REQUEST_RATE_LIMIT)


def rate_limit_otp_verify(request: Request) -> None:
    """Call this at the start of the OTP verify endpoint."""
    ip = get_client_ip(request)
    _check_rate_limit("otp_verify", ip, *OTP_VERIFY_RATE_LIMIT)


def rate_limit_public_media(request: Request, category: str, limit: int, window_seconds: int = 60) -> None:
    """
    Rate limiter for unauthenticated public endpoints (/file, /thumbnail, /downloadZip)
    that accept share tokens. Prevents brute-force enumeration of short share tokens
    while allowing generous budgets for legitimate gallery browsing.
    """
    ip = get_client_ip(request)
    _check_rate_limit(category, ip, limit, window_seconds)


def rate_limit_strict(request: Request, category: str, limit: int, window_seconds: int = 60) -> None:
    """
    Spoof-resistant rate limiter for security-sensitive public endpoints
    (e.g. share password unlocks). Buckets on the raw socket peer address
    COMBINED with any proxy-supplied IP, so clients rotating X-Forwarded-For /
    X-Real-IP headers cannot keep resetting their own bucket.
    """
    socket_ip = request.client.host if request.client else "unknown"
    identity = f"{socket_ip}|{get_client_ip(request)}"
    _check_rate_limit(category, identity, limit, window_seconds)


# ---------------------------------------------------------------------------
# OTP management
# ---------------------------------------------------------------------------

def _hash_otp(otp: str) -> str:
    """One-way hash of the OTP code so we never store raw values."""
    return hashlib.sha256(otp.encode()).hexdigest()


def generate_otp() -> str:
    """Return a cryptographically secure 6-digit numeric OTP."""
    digits = string.digits
    return "".join(secrets.choice(digits) for _ in range(6))


def create_pending_otp() -> str:
    """
    Generate an OTP, store its hash, and return the plaintext OTP.
    The plaintext is returned exactly once and must be emailed immediately.
    """
    otp = generate_otp()
    _PENDING_OTPS["admin"] = PendingOTP(
        otp_hash=_hash_otp(otp),
        last_sent=time.time(),
    )
    return otp


SECURE_COOKIES_ENV: str = os.getenv("SECURE_COOKIES", "").strip().lower()


def is_secure_cookie(request: Request) -> bool:
    """
    Determines if session cookies should have the Secure attribute.
    Respects explicit SECURE_COOKIES environment variable, request.url.is_secure,
    and X-Forwarded-Proto / X-Forwarded-Ssl headers from reverse proxies.
    """
    if SECURE_COOKIES_ENV in ("true", "1", "yes"):
        return True
    if SECURE_COOKIES_ENV in ("false", "0", "no"):
        return False

    proto = request.headers.get("x-forwarded-proto", "").lower()
    first_proto = proto.split(",")[0].strip() if proto else ""
    if first_proto == "https" or request.headers.get("x-forwarded-ssl", "").lower() == "on":
        return True

    return bool(getattr(request.url, "is_secure", False)) or request.url.scheme == "https"


def verify_otp(submitted_otp: str) -> bool:
    """
    Validates the submitted OTP against the stored hash.
    Increments attempt counter. Deletes OTP on success (single-use) or on lockout.
    Returns True on success, False on failure.
    """
    pending = _PENDING_OTPS.get("admin")
    if pending is None:
        return False

    if pending.is_expired:
        _PENDING_OTPS.pop("admin", None)
        return False

    if pending.is_locked:
        _PENDING_OTPS.pop("admin", None)
        return False

    pending.attempts += 1

    if not secrets.compare_digest(pending.otp_hash, _hash_otp(submitted_otp)):
        if pending.is_locked:
            _PENDING_OTPS.pop("admin", None)
        return False

    # OTP correct — consume it (single-use)
    _PENDING_OTPS.pop("admin", None)
    return True


def get_otp_status() -> dict:
    """Return non-sensitive status info about the current pending OTP."""
    pending = _PENDING_OTPS.get("admin")
    if pending is None:
        return {"pending": False}
    return {
        "pending": True,
        "expired": pending.is_expired,
        "locked": pending.is_locked,
        "can_resend": pending.can_resend,
        "remaining_attempts": max(0, OTP_MAX_ATTEMPTS - pending.attempts),
    }


# ---------------------------------------------------------------------------
# Session management (Disk-persisted & IP-bound)
# ---------------------------------------------------------------------------

def _save_sessions_to_disk() -> None:
    """Atomically save active, unexpired sessions to cache/auth_sessions.json."""
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {tok: sess.to_dict() for tok, sess in _SESSIONS.items() if not sess.is_expired}
        temp_file = _SESSION_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temp_file, _SESSION_FILE)
    except Exception as e:
        logger.warning(f"Could not persist sessions to disk: {e}")


# Debounced async disk writer — avoids blocking the event loop on every session op
_save_task: Optional[asyncio.Task] = None


def _schedule_save_sessions() -> None:
    """Schedule a debounced async session save (2 s delay, collapses multiple calls)."""
    global _save_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. startup); write synchronously
        _save_sessions_to_disk()
        return

    if _save_task and not _save_task.done():
        _save_task.cancel()

    async def _deferred_save():
        try:
            await asyncio.sleep(2)  # debounce: coalesce rapid saves
            await loop.run_in_executor(None, _save_sessions_to_disk)
        except asyncio.CancelledError:
            pass

    _save_task = loop.create_task(_deferred_save())


def _load_sessions_from_disk() -> None:
    """Load active sessions on startup so restarts do not log out users."""
    if not _SESSION_FILE.exists():
        return
    try:
        content = _SESSION_FILE.read_text(encoding="utf-8")
        data = json.loads(content)
        for tok, d in data.items():
            sess = Session.from_dict(d)
            if not sess.is_expired:
                _SESSIONS[tok] = sess
        logger.info(f"Restored {len(_SESSIONS)} active session(s) from disk")
    except Exception as e:
        logger.warning(f"Could not load sessions from disk: {e}")


# Initialize sessions from disk on module import
_load_sessions_from_disk()


def create_session(ip: str = "unknown", previous_token: Optional[str] = None) -> str:
    """
    Create a new session bound to client IP.
    Rotates session by invalidating previous_token if provided.
    Prunes expired sessions, persists to disk and returns the new session token.
    Allows multi-device logins (e.g. PC + Mobile) simultaneously.
    """
    # Invalidate previous session token on re-login / rotation
    if previous_token and previous_token in _SESSIONS:
        _SESSIONS.pop(previous_token, None)

    # Evict expired sessions first
    expired = [tok for tok, s in _SESSIONS.items() if s.is_expired]
    for tok in expired:
        _SESSIONS.pop(tok, None)

    token = secrets.token_hex(32)  # 256-bit random token
    sess = Session(token=token, ip=ip)
    _SESSIONS[token] = sess
    _schedule_save_sessions()
    return token


def invalidate_session(token: str) -> None:
    """Destroy a specific active session."""
    if token in _SESSIONS:
        _SESSIONS.pop(token, None)
        _schedule_save_sessions()
        logger.info("Session invalidated")


ENFORCE_IP_BINDING: bool = os.getenv("ENFORCE_IP_BINDING", "false").strip().lower() in ("true", "1", "yes")


def validate_session(token: str, ip: str = None) -> Optional[Session]:
    """
    Look up and validate a session token.
    Returns the Session object or None if invalid or expired.
    """
    if not token:
        return None

    session = _SESSIONS.get(token)
    if session is None:
        return None

    if session.is_expired:
        _SESSIONS.pop(token, None)
        _schedule_save_sessions()
        logger.info("Expired session evicted")
        return None

    # IP Binding verification: only enforce hard failure if explicitly enabled in env
    if ENFORCE_IP_BINDING and ip and session.ip and session.ip != "unknown":
        local_ips = {"127.0.0.1", "::1", "localhost", "testclient"}
        is_both_local = (session.ip in local_ips and ip in local_ips)
        if not is_both_local and session.ip != ip:
            logger.warning(f"Session IP mismatch: session registered to {session.ip}, request from {ip}")
            return None

    # Update last seen timestamp and track latest client IP
    if ip and ip != "unknown":
        session.ip = ip

    session.last_seen = time.time()
    return session


def invalidate_all_sessions() -> None:
    """Destroy all active sessions (used on global logout or password change)."""
    _SESSIONS.clear()
    _schedule_save_sessions()
    logger.info("All sessions invalidated")


def is_admin_authenticated(request: Request) -> bool:
    """Check if request has a valid session cookie matching client IP."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    ip = get_client_ip(request)
    return validate_session(token, ip=ip) is not None


def get_active_session_count() -> int:
    return len(_SESSIONS)


# ---------------------------------------------------------------------------
# FastAPI dependency — protects every authenticated endpoint
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "tg_session"


async def require_auth(
    request: Request,
    tg_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Session:
    """
    FastAPI dependency that enforces authentication and IP binding on every request.
    """
    if tg_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={
                "WWW-Authenticate": "Cookie",
                "X-Redirect-To": "/",
            },
        )

    ip = get_client_ip(request)
    session = validate_session(tg_session, ip=ip)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
            headers={
                "WWW-Authenticate": "Cookie",
                "X-Redirect-To": "/",
            },
        )

    return session


# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------

async def _cleanup_expired_stores() -> None:
    """
    Periodically evict expired sessions and OTPs from in-memory stores.
    Run this as a background asyncio task.
    """
    while True:
        await asyncio.sleep(5 * 60)  # Run every 5 minutes
        try:
            now = time.time()

            # Evict expired sessions
            expired_tokens = [t for t, s in _SESSIONS.items() if s.is_expired]
            for t in expired_tokens:
                del _SESSIONS[t]
            if expired_tokens:
                logger.info(f"Cleanup: evicted {len(expired_tokens)} expired session(s)")

            # Evict expired OTP
            pending = _PENDING_OTPS.get("admin")
            if pending and pending.is_expired:
                del _PENDING_OTPS["admin"]
                logger.info("Cleanup: evicted expired pending OTP")

            # Evict old rate limit windows
            old_keys = [k for k, v in _RATE_WINDOWS.items()
                        if now - v["window_start"] > 3600]
            for k in old_keys:
                del _RATE_WINDOWS[k]

            if len(_RATE_WINDOWS) > 1000:
                excess = len(_RATE_WINDOWS) - 1000
                for k in list(_RATE_WINDOWS.keys())[:excess]:
                    _RATE_WINDOWS.pop(k, None)

            from utils.extra import clean_memory
            clean_memory()
        except Exception as e:
            logger.error(f"Error in auth cleanup task: {e}")


def start_cleanup_task() -> asyncio.Task:
    """Start the background cleanup coroutine. Call from app startup."""
    return asyncio.create_task(_cleanup_expired_stores())
