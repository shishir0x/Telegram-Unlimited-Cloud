# Security Policy

## Supported Versions

We actively maintain and provide security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 2.x     | :white_check_mark: |
| < 2.0   | :x:                |

---

## Reporting a Vulnerability

If you discover a security vulnerability within **Telegram Unlimited Cloud**, please report it responsibly. We appreciate your efforts to improve the security of the application.

### How to Report
- **Email:** Send details to the repository maintainer or open a private security advisory on GitHub.
- **Details to Include:**
  - Description of the vulnerability and its potential impact.
  - Clear step-by-step reproduction steps or a minimal proof of concept (PoC).
  - Affected components or API endpoints.
  - Any proposed remediations or patches.

### Responsible Disclosure Expectations
- Please allow reasonable time for the maintainers to investigate, patch, and release a fix before public disclosure.
- Do not attempt to access, modify, or destroy user data in production instances without explicit permission.
- Avoid Denial of Service (DoS) attacks or automated brute-forcing against live production infrastructure.

---

## Security Architecture & Best Practices

1. **Authentication:** Single-admin with constant-time password verification (`secrets.compare_digest`), cryptographic session cookies (`tg_session`), and optional 2FA One-Time Passwords (OTP).
2. **Access Control:** Public access is strictly isolated to explicit shared folder paths (`/share_<id>`) using cryptographic share tokens; all root, search, management, and private endpoints return `401 Unauthorized`.
3. **Data Integrity:** Metadata persistence (`drive.data`) uses atomic temporary file substitution (`os.replace`) with continuous `.bak` backup copies to prevent data corruption during unexpected restarts.
4. **Input Sanitization:** File and folder names are rigorously sanitized to eliminate directory traversal sequences (`..`), path separators, and null bytes.
5. **Rate Limiting:** Sliding-window rate limiters defend against brute-force attacks on authentication and password verification endpoints.
