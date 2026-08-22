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


def get_client_ip(request: Request) -> str:
    """Extracts client IP address respecting reverse proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Session:
    token: str
    ip: str = "unknown"
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
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            token=d["token"],
            ip=d.get("ip", "unknown"),
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


# ---------------------------------------------------------------------------
# OTP management
# ---------------------------------------------------------------------------

import hashlib


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


def verify_otp(submitted_otp: str) -> bool:
    """
    Validates the submitted OTP against the stored hash.
    Increments attempt counter. Deletes OTP on success (single-use).
    Returns True on success, False on failure.
    """
    pending = _PENDING_OTPS.get("admin")
    if pending is None:
        return False

    if pending.is_expired:
        del _PENDING_OTPS["admin"]
        return False

    if pending.is_locked:
        return False

    pending.attempts += 1

    if not secrets.compare_digest(pending.otp_hash, _hash_otp(submitted_otp)):
        return False

    # OTP correct — consume it (single-use)
    del _PENDING_OTPS["admin"]
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


def create_session(ip: str = "unknown") -> str:
    """
    Create a new session bound to client IP.
    Prunes expired sessions, persists to disk and returns the new session token.
    Allows multi-device logins (e.g. PC + Mobile) simultaneously.
    """
    # Evict expired sessions first
    expired = [tok for tok, s in _SESSIONS.items() if s.is_expired]
    for tok in expired:
        _SESSIONS.pop(tok, None)

    token = secrets.token_hex(32)  # 256-bit random token
    sess = Session(token=token, ip=ip)
    _SESSIONS[token] = sess
    _save_sessions_to_disk()
    return token


def invalidate_session(token: str) -> None:
    """Destroy a specific active session."""
    if token in _SESSIONS:
        _SESSIONS.pop(token, None)
        _save_sessions_to_disk()
        logger.info("Session invalidated")


def validate_session(token: str, ip: str = None) -> Optional[Session]:
    """
    Look up and validate a session token with IP verification.
    Returns the Session object or None if invalid/expired/IP mismatched.
    """
    session = _SESSIONS.get(token)
    if session is None:
        return None

    if session.is_expired:
        del _SESSIONS[token]
        _save_sessions_to_disk()
        logger.info("Expired session evicted")
        return None

    # IP Binding verification: if both IPs are known, prevent session hijacking from foreign IPs
    if ip and session.ip and session.ip != "unknown":
        local_ips = {"127.0.0.1", "::1", "localhost", "testclient"}
        is_both_local = (session.ip in local_ips and ip in local_ips)
        if not is_both_local and session.ip != ip:
            logger.warning(f"Session IP mismatch: session registered to {session.ip}, request from {ip}")
            return None

    session.last_seen = time.time()
    return session


def invalidate_all_sessions() -> None:
    """Destroy all active sessions (used on global logout or password change)."""
    _SESSIONS.clear()
    _save_sessions_to_disk()
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

        except Exception as e:
            logger.error(f"Error in auth cleanup task: {e}")


def start_cleanup_task() -> asyncio.Task:
    """Start the background cleanup coroutine. Call from app startup."""
    return asyncio.create_task(_cleanup_expired_stores())
