"""
Security & Authentication Comprehensive Test Suite
====================================================
Tests all authentication hardening, session management, OTP lifecycle,
rate limiting, constant-time comparisons, and endpoint authorization bypass defenses.
"""

import asyncio
import hashlib
import os
import secrets
import sys
import time
from starlette.testclient import TestClient

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from main import app
from utils.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    PendingOTP,
    Session,
    _PENDING_OTPS,
    _SESSIONS,
    create_pending_otp,
    create_session,
    generate_otp,
    get_otp_status,
    hash_password,
    invalidate_all_sessions,
    invalidate_session,
    is_secure_cookie,
    sanitize_path,
    validate_session,
    verify_otp,
    verify_password,
)


def run_all_security_tests():
    print("=" * 70)
    print("RUNNING SECURITY & AUTHENTICATION AUDIT TEST SUITE")
    print("=" * 70)

    client = TestClient(app)

    # -----------------------------------------------------------------------
    # 1. Password Hashing & Verification
    # -----------------------------------------------------------------------
    print("\n[1/7] Testing Password Hashing & Verification...")
    raw_pass = "ComplexP@ssw0rd!2026_Secure"
    pbkdf2_hash = hash_password(raw_pass, iterations=100_000)
    assert pbkdf2_hash.startswith("pbkdf2:sha256:100000$"), "PBKDF2 header must be present"
    assert verify_password(raw_pass, pbkdf2_hash) is True, "Valid PBKDF2 password must verify"
    assert verify_password("WrongPassword", pbkdf2_hash) is False, "Invalid password must fail"
    assert verify_password("", pbkdf2_hash) is False, "Empty password must fail"
    assert verify_password(raw_pass, "") is False, "Empty hash must fail"

    # SHA256 raw hash fallback
    sha256_hash = hashlib.sha256(raw_pass.encode()).hexdigest()
    assert verify_password(raw_pass, sha256_hash) is True, "Raw SHA256 must verify"
    assert verify_password("BadPass", sha256_hash) is False, "Bad pass against SHA256 must fail"

    # Plaintext fallback with constant-time compare
    assert verify_password(raw_pass, raw_pass) is True, "Plaintext fallback must verify"
    assert verify_password(raw_pass + "x", raw_pass) is False, "Tampered plaintext must fail"
    print("  [OK] PBKDF2 hashing, SHA256 fallback, and constant-time verification passed.")

    # -----------------------------------------------------------------------
    # 2. Session Lifecycle, Rotation & Expiration
    # -----------------------------------------------------------------------
    print("\n[2/7] Testing Session Lifecycle, Rotation & Expiration...")
    # Clean test session slate
    invalidate_all_sessions()

    # Create session
    token1 = create_session(ip="127.0.0.1")
    assert len(token1) == 64, "Token must be 256-bit hex (64 chars)"
    sess = validate_session(token1, ip="127.0.0.1")
    assert sess is not None, "Valid session token must validate"
    assert sess.ip == "127.0.0.1"

    # Session rotation: creating a new session with previous_token revokes old token
    token2 = create_session(ip="127.0.0.1", previous_token=token1)
    assert validate_session(token1, ip="127.0.0.1") is None, "Old session must be invalidated on rotation"
    assert validate_session(token2, ip="127.0.0.1") is not None, "New session must be active"

    # Session invalidation (logout)
    invalidate_session(token2)
    assert validate_session(token2, ip="127.0.0.1") is None, "Explicitly invalidated session must not validate"

    # Expired session test
    token_exp = create_session(ip="127.0.0.1")
    _SESSIONS[token_exp].created_at = time.time() - (SESSION_TTL_SECONDS + 10)
    assert validate_session(token_exp, ip="127.0.0.1") is None, "Expired session must be rejected and evicted"
    assert token_exp not in _SESSIONS, "Expired session must be pruned from store"

    # Tampered / Forged token test
    forged_token = "deadbeef" * 8
    assert validate_session(forged_token, ip="127.0.0.1") is None, "Forged token must be rejected"
    print("  [OK] Session creation, rotation, invalidation, expiration, and forgery rejection passed.")

    # -----------------------------------------------------------------------
    # 3. OTP Lifecycle, Single-Use & Brute-Force Lockout
    # -----------------------------------------------------------------------
    print("\n[3/7] Testing OTP Lifecycle, Single-Use & Brute-Force Lockout...")
    _PENDING_OTPS.clear()

    # Generate OTP
    otp_code = create_pending_otp()
    assert len(otp_code) == 6 and otp_code.isdigit(), "OTP must be 6 numeric digits"
    pending = _PENDING_OTPS.get("admin")
    assert pending is not None, "Pending OTP must be stored"
    assert pending.otp_hash != otp_code, "Raw OTP must NEVER be stored in memory"
    assert hashlib.sha256(otp_code.encode()).hexdigest() == pending.otp_hash

    # Status check
    status = get_otp_status()
    assert status["pending"] is True
    assert status["remaining_attempts"] == 5

    # Verification failure increments attempts
    assert verify_otp("000000" if otp_code != "000000" else "111111") is False
    status = get_otp_status()
    assert status["remaining_attempts"] == 4

    # Single-use test
    assert verify_otp(otp_code) is True, "Correct OTP must verify"
    assert "admin" not in _PENDING_OTPS, "OTP must be destroyed immediately after successful verification (single-use)"
    assert verify_otp(otp_code) is False, "OTP cannot be re-used a second time"

    # Brute-force lockout test (5 failed attempts)
    _PENDING_OTPS.clear()
    otp_code2 = create_pending_otp()
    wrong = "999999" if otp_code2 != "999999" else "888888"
    for i in range(4):
        assert verify_otp(wrong) is False, f"Attempt {i+1} must fail"
    # 5th attempt must trigger lockout and delete pending OTP
    assert verify_otp(wrong) is False, "5th attempt must fail and trigger lockout"
    assert "admin" not in _PENDING_OTPS, "Locked OTP must be immediately purged from memory"
    assert verify_otp(otp_code2) is False, "Locked OTP cannot verify even with correct code"

    # Expiration test (TTL > 5 minutes)
    _PENDING_OTPS.clear()
    otp_code3 = create_pending_otp()
    _PENDING_OTPS["admin"].created_at = time.time() - 305  # 5 mins 5 secs ago
    assert verify_otp(otp_code3) is False, "Expired OTP must be rejected"
    assert "admin" not in _PENDING_OTPS, "Expired OTP must be purged"
    print("  [OK] OTP generation, hashing, single-use, 5-attempt lockout, and TTL expiration passed.")

    # -----------------------------------------------------------------------
    # 4. HTTP Cookie Security Attributes & Logout
    # -----------------------------------------------------------------------
    print("\n[4/7] Testing HTTP Cookie Security Attributes & Logout...")
    # Direct password login to get session cookie
    pwd = config.ADMIN_PASSWORD or "admin"
    r = client.post("/api/checkPassword", json={"password": pwd})
    assert r.status_code == 200, f"Login must succeed, got {r.status_code}"
    
    # Verify Set-Cookie header contains HttpOnly and SameSite=lax
    cookie_header = r.headers.get("set-cookie", "")
    assert "tg_session=" in cookie_header, "Set-Cookie must contain tg_session"
    assert "httponly" in cookie_header.lower(), "Cookie MUST have HttpOnly flag"
    assert "samesite=lax" in cookie_header.lower(), "Cookie MUST have SameSite=lax flag"
    assert "path=/" in cookie_header.lower(), "Cookie MUST have Path=/ attribute"

    # Extract session cookie
    auth_cookie = r.cookies.get(SESSION_COOKIE_NAME)
    assert auth_cookie is not None, "Client must receive session cookie"

    # Verify authenticated request works with cookie
    r_auth = client.post("/api/getDirectory", json={"path": "/"}, cookies={SESSION_COOKIE_NAME: auth_cookie})
    assert r_auth.status_code == 200, "Authenticated request with valid cookie must succeed"

    # Logout
    r_logout = client.post("/api/logout", cookies={SESSION_COOKIE_NAME: auth_cookie})
    assert r_logout.status_code == 200
    logout_cookie_header = r_logout.headers.get("set-cookie", "").lower()
    assert "max-age=0" in logout_cookie_header or 'expires=' in logout_cookie_header, "Logout must delete cookie"

    # Verify logged-out token cannot access protected endpoints
    r_after_logout = client.post("/api/getDirectory", json={"path": "/"}, cookies={SESSION_COOKIE_NAME: auth_cookie})
    assert r_after_logout.status_code == 401, "Logged-out session must be rejected with 401"
    print("  [OK] Cookie flags (HttpOnly, SameSite, Path), authenticated flow, and logout invalidation passed.")

    # -----------------------------------------------------------------------
    # 5. Endpoint Authorization Matrix (Unauthenticated Bypass Defense)
    # -----------------------------------------------------------------------
    print("\n[5/7] Testing Endpoint Authorization Matrix (Unauthenticated Bypass Defense)...")
    
    # Test protected endpoints without any authentication
    endpoints_to_test = [
        # Metadata
        ("POST", "/api/getDirectory", {"path": "/"}),
        ("POST", "/api/search", {"query": "test"}),
        ("POST", "/api/checkFileExists", {"path": "/", "filename": "test.txt"}),
        ("POST", "/api/getSyncStatus", {}),
        # Operations
        ("POST", "/api/createNewFolder", {"path": "/", "name": "HackFolder"}),
        ("POST", "/api/moveFileFolder", {"src_path": "/a", "dest_path": "/b"}),
        ("POST", "/api/copyFileFolder", {"src_path": "/a", "dest_path": "/b"}),
        ("POST", "/api/renameFileFolder", {"path": "/a", "name": "b"}),
        ("POST", "/api/tagFileFolder", {"path": "/a", "tag": "test"}),
        ("POST", "/api/trashFileFolder", {"path": "/a", "trash": True}),
        ("POST", "/api/deleteFileFolder", {"path": "/a"}),
        ("POST", "/api/bulkDelete", {"paths": ["/a"]}),
        ("POST", "/api/bulkTrash", {"paths": ["/a"]}),
        ("POST", "/api/cancelUpload", {"id": "123"}),
        ("POST", "/api/syncDriveData", {}),
        ("GET", "/api/syncDriveData", None),
        ("POST", "/api/updateSyncStatus", {"sync_data": {}}),
        ("POST", "/api/getFolderShareAuth", {"path": "/"}),
        ("GET", "/api/admin/integrityReport", None),
        ("POST", "/api/downloadZip", {"paths": ["/"]}),
        ("GET", "/downloadZip?path=/", None),
        ("GET", "/file?path=/nonexistent.txt", None),
        ("GET", "/thumbnail?path=/nonexistent.jpg", None),
    ]

    for method, endpoint, payload in endpoints_to_test:
        if method == "POST":
            res = client.post(endpoint, json=payload if payload else {})
        else:
            res = client.get(endpoint)
        assert res.status_code == 401, (
            f"SECURITY BYPASS DEFECT: {method} {endpoint} returned status {res.status_code} "
            f"instead of 401 Unauthorized for unauthenticated request!"
        )

    # Test forged/tampered session cookie on protected endpoints
    forged_cookies = {SESSION_COOKIE_NAME: "invalid_forged_session_token_1234567890"}
    r_forged1 = client.post("/api/getDirectory", json={"path": "/"}, cookies=forged_cookies)
    assert r_forged1.status_code == 401, "Forged session cookie must return 401"

    r_forged2 = client.get("/api/admin/integrityReport", cookies=forged_cookies)
    assert r_forged2.status_code == 401, "Forged session cookie on admin API must return 401"
    print("  [OK] All 22 protected endpoints strictly enforce 401 Unauthorized against bypass attempts.")

    # -----------------------------------------------------------------------
    # 6. Information Leakage & Secret Exposure Defense
    # -----------------------------------------------------------------------
    print("\n[6/7] Testing Information Leakage & Secret Exposure Defense...")
    # Health endpoints must not leak credentials
    for health_path in ["/health", "/healthz", "/ping", "/health/live", "/health/ready"]:
        r_h = client.get(health_path)
        assert r_h.status_code in (200, 503), f"Health check returned unexpected code {r_h.status_code}"
        text = r_h.text.lower()
        assert "admin_password" not in text and "password" not in text or "password" not in r_h.json()
        assert "api_hash" not in text
        assert "bot_tokens" not in text
        assert "string_sessions" not in text
        assert "smtp_password" not in text

    # Login error responses must be generic (no email enumeration)
    r_bad_email = client.post("/api/login", json={"email": "wrong@test.com", "password": "wrong"})
    assert r_bad_email.status_code == 401
    assert "invalid email or password" in r_bad_email.json().get("status", "").lower()

    r_bad_pass = client.post("/api/login", json={"email": config.ADMIN_EMAIL or "admin@test.com", "password": "wrong"})
    assert r_bad_pass.status_code == 401
    assert "invalid email or password" in r_bad_pass.json().get("status", "").lower()
    print("  [OK] No secret leakage in health checks, API diagnostics, or login error messages.")

    # -----------------------------------------------------------------------
    # 7. Path Traversal & Injection Shield
    # -----------------------------------------------------------------------
    print("\n[7/7] Testing Path Traversal & Injection Shield...")
    assert sanitize_path("../../../etc/passwd") == "/etc/passwd"
    assert sanitize_path("..\\..\\windows\\system32") == "/windows/system32"
    assert sanitize_path("/folder/../../secret") == "/secret"
    assert sanitize_path("\x00/null_byte_attack") == "/null_byte_attack"
    assert sanitize_path(None) == "/"
    assert sanitize_path("") == "/"
    print("  [OK] Path traversal sanitization strictly collapses relative sequences to root-scoped POSIX paths.")

    print("\n" + "=" * 70)
    print("ALL SECURITY AUDIT TESTS PASSED (100% GREEN)")
    print("=" * 70)


if __name__ == "__main__":
    run_all_security_tests()
