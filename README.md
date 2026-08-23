# Telegram Unlimited Drive

**Unlimited Cloud Storage Powered by Telegram**

TG Drive is a self-hosted, production-ready cloud storage platform that leverages Telegram's infrastructure as an unlimited, secure storage backend. It delivers a Google Drive–class experience through a modern web interface, complete with media previewing, granular file management, secure authentication, and automated disaster recovery.

---

## Overview

Traditional cloud providers impose storage quotas and recurring costs. TG Drive removes both constraints by storing file data in a private Telegram channel via the MTProto API, while retaining full metadata locally for instant directory navigation. The result is a scalable, zero-cost storage layer presented through a polished, professional web application.

### Core Capabilities

| Capability | Description |
| :--- | :--- |
| **File & Folder Management** | Create, rename, move, copy, tag, and organize files and folders through an intuitive interface. |
| **Media & Document Previews** | Preview images, stream video and audio, read PDFs, and view syntax-highlighted source code directly in the browser. |
| **Image Thumbnails** | Server-side generated JPEG thumbnails (via Pillow) enable fast grid browsing, backed by ETag-based immutable caching. |
| **Advanced Search** | Search across the entire drive or within the current folder, with filtering by item type, location, and file size. |
| **Bulk ZIP Downloads** | Select multiple files or entire folders and download them as a single ZIP archive that preserves directory structure. |
| **Shareable Links** | Generate share links for files and folders, secured by token-based authorization. |
| **Trash & Recovery** | Soft-delete support with restore and permanent deletion options. |
| **Bulk Operations** | Multi-select workflows for batch trash, restore, and deletion. |
| **Drag & Drop** | Move items between folders or upload directly by dragging files onto the browser window. |
| **High-Speed Transfers** | Powered by Pyrogram and `tgcrypto` for high-throughput uploads, downloads, and smooth media streaming. |
| **Automated Backups** | File tree and metadata are continuously backed up to the Telegram storage channel, with debounced writes to prevent API rate limits. |
| **Integrity Diagnostics** | An admin-only integrity report scans the metadata tree for cyclic references and missing Telegram message mappings. |
| **Telegram Bot Integration** | Upload files directly from Telegram by sending them to the configured bot. |
| **Keep-Alive Pinger** | Optional built-in pinger keeps free-tier hosting instances active. |

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend** | Python 3.10+, FastAPI, Uvicorn, Pyrogram, `tgcrypto`, Pillow |
| **Frontend** | HTML5, Vanilla CSS3 (Material Design 3 / Glassmorphism), Vanilla JavaScript SPA |
| **Storage** | Telegram Cloud Channels via the Telegram MTProto API |

---

## Configuration

All configuration is managed through environment variables. Begin by copying `.env.example` to `.env` and completing the required values.

### Required Variables

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID obtained from [my.telegram.org](https://my.telegram.org) | `12345678` |
| `API_HASH` | Telegram API Hash obtained from [my.telegram.org](https://my.telegram.org) | `1bbcc5eb55b66965...` |
| `BOT_TOKENS` | Bot token(s) from [@BotFather](https://t.me/BotFather); comma-separated for a multi-bot pool | `1234567890:AAH...` |
| `STORAGE_CHANNEL` | Storage channel ID (with `-100` prefix) | `-1001234567890` |
| `DATABASE_BACKUP_MSG_ID` | Message ID of the pinned backup document in the storage channel | `1` |
| `ADMIN_PASSWORD` | Web admin panel password; must be strong and non-default | `YourStrongSecret!2026` |

> [!IMPORTANT]
> Every bot listed in `BOT_TOKENS` must be added to the `STORAGE_CHANNEL` as an **Administrator** with full posting rights.

### Optional Variables

| Variable | Default | Description |
| :--- | :---: | :--- |
| `ADMIN_EMAIL` | *None* | Admin email address used for two-factor OTP verification |
| `SESSION_HOURS` | `12` | Session lifetime in hours |
| `SESSION_SECRET_KEY` | *Auto* | 64-character hex key for session signing (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `RESEND_API_KEY` | *None* | Resend API key for email OTP delivery (recommended over SMTP) |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP host for email delivery fallback |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | *None* | SMTP username / email address |
| `SMTP_PASSWORD` | *None* | SMTP application password |
| `STRING_SESSIONS` | *None* | Premium Pyrogram string session(s); raises the single-file limit to 4 GB |
| `DATABASE_BACKUP_TIME` | `60` | Interval in seconds between automatic database backups |
| `WEBSITE_URL` | *None* | Public URL used by the auto-ping keep-alive service |
| `MAIN_BOT_TOKEN` | *None* | Dedicated bot token for interactive Telegram Bot Mode |
| `TELEGRAM_ADMIN_IDS` | *None* | Comma-separated Telegram user IDs authorized for Bot Mode |

---

## Installation

### Run Locally

1. **Clone the repository**

   ```bash
   git clone https://github.com/shishir0x/Telegram-Unlimited-Cloud.git
   cd Telegram-Unlimited-Cloud
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the environment**

   ```bash
   cp .env.example .env
   # Edit .env and provide API_ID, API_HASH, BOT_TOKENS, etc.
   ```

4. **Start the server**

   ```bash
   uvicorn main:app --reload --port 8000
   ```

   Open `http://localhost:8000` in your browser.

### Deploy with Docker

```bash
docker compose up -d --build
```

Verify deployment health:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

---

## Backup & Disaster Recovery

TG Drive implements a three-tier persistence strategy to protect metadata against data loss.

- **Scope of backup**: The metadata file tree (`drive.data`) contains folder hierarchies, file names, sizes, device origins, and Telegram message IDs. File contents reside permanently in the storage channel and require no separate backup.
- **Storage locations**:
  1. Primary local cache — `cache/drive.data` (atomic writes)
  2. Emergency local fallback — `cache/drive.data.bak`
  3. Off-site copy — continuously updated at `DATABASE_BACKUP_MSG_ID` in the Telegram storage channel
- **Recovery procedure**:
  1. If the local disk is lost, the server automatically downloads the latest metadata from `DATABASE_BACKUP_MSG_ID` on startup.
  2. If the Telegram message is unavailable, the server falls back to `cache/drive.data.bak`.

---

## Supplementary Tools

- **`tgdrive_backup.py`** — Command-line utility for remote drive backups via the `/api/checkPassword` endpoint.
- **`CAPACITOR_ANDROID_GUIDE.md`** — Step-by-step guide for packaging the web application as a native Android app using Capacitor.

---

## Testing

The repository includes three test suites covering functionality, hardening, and security:

```bash
python test_all_functions.py     # End-to-end functional tests
python test_hardening.py         # Security hardening checks
python test_security_audit.py    # Comprehensive security audit
```

---

## Troubleshooting

| Issue | Resolution |
| :--- | :--- |
| **Telegram FloodWait errors** | Parallel uploads may trigger rate limits; the application handles these automatically with backoff retries. No action required. |
| **401 Unauthorized responses** | Verify the `ADMIN_PASSWORD` value, or confirm the `tg_session` cookie is still valid. |
| **Missing previews or thumbnails** | Ensure every configured bot remains an active Administrator in the `STORAGE_CHANNEL`. |
| **Metadata health concerns** | Authenticated administrators can request `/api/admin/integrityReport` to verify tree validity and backup synchronization status. |

---

## License & Security

- This project is licensed under the [MIT License](LICENSE).
- For security policy and vulnerability disclosure procedures, refer to [SECURITY.md](SECURITY.md).
