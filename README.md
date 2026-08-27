# 🚀 Telegram Unlimited Drive (TG Drive)

**Production-Ready, Google Drive–Class Cloud Storage Powered by Unlimited Telegram Infrastructure**

TG Drive turns Telegram's high-speed cloud infrastructure into your own private, unlimited personal cloud storage. It delivers a full-featured, responsive web interface packed with instant media streaming, rich previews, folder hierarchies, advanced search, tag management, duplicate file cleaners, batch ZIP downloads, tokenized sharing, and automatic cloud backup synchronization.

---

## 🌟 Key Features

### 🗂️ Google Drive–Class File & Folder Management
- **Hierarchical Directory Tree**: Create unlimited nested folders with automatic path resolution (`mkdir -p` semantics).
- **List & Grid View**: Switch smoothly between detailed list view and responsive visual grid view.
- **Drag & Drop Organization**: Drag files and folders directly from your desktop to upload, or drag items into folders and breadcrumbs to move them.
- **Multi-Select & Bulk Actions**: Select multiple items with `Ctrl` / `Shift` / lasso select to download as ZIP, move, tag, trash, or permanently delete.
- **Context Menus & Hotkeys**: Full right-click menu support and keyboard shortcuts (`Ctrl+A`, `Delete`, `Enter`, `Esc`, `F2`).
- **Recycle Bin / Trash**: Safe soft-delete system with instant restore and one-click trash purge.

### 🎬 Media Streaming & Rich In-Browser Previews
- **Instant Video & Audio Streaming**: Stream 4K/1080p MP4, MKV, WebM, MOV, and MP3/FLAC/WAV with full HTTP 206 Partial Content range seeking.
- **Dynamic Thumbnail Engine**: Generates sharp JPEG thumbnails on the fly (via Pillow) with dual-layer RAM & disk LRU caching and HTTP ETag 304 validation.
- **Document & PDF Reader**: View PDF documents directly in-browser with page navigation and zoom.
- **Syntax-Highlighted Code Viewer**: Read source code in 30+ languages (Python, JS, TS, HTML, CSS, Rust, Go, C++, SQL, YAML, JSON, Markdown) with line numbering and copy tools.
- **Full-Resolution Image Lightbox**: High-definition image viewer with pan, zoom, rotate, and full-screen modes.

### ⚡ Resilient Transfer Manager & Multi-Bot Pool
- **Multi-Bot Concurrency**: Distribute upload and download workloads across multiple Telegram bot tokens in parallel to maximize throughput and eliminate rate limits.
- **Telegram Premium Session Support**: Add `STRING_SESSIONS` to unlock **up to 4.0 GB** single-file uploads (standard bots support up to 2.0 GB).
- **In-Memory Pyrogram Engine**: Eliminates SQLite database lock conflicts on serverless, Docker, and Render container environments.
- **Visual Transfer Queue**: Monitor live upload/download speed, estimated completion time, progress bars, pause/cancel, and auto-retry upon network failure.

### 🔍 Deep Search, Tagging & Duplicate Cleanup
- **Power Search with Operators**: Filter by filename, type (`type:pdf`, `type:video`, `type:image`, `type:code`), extension (`ext:zip`), size (`size:>100mb`), and date range (`after:2026-01-01`).
- **Custom Color Tagging**: Assign custom colored tags to files and folders to build quick-access filters.
- **Automated Duplicate Cleaner**: Fast SHA256-based duplicate file scanner that detects identical files across your entire drive and lets you trash redundant copies with one click.
- **Activity & Telemetry History**: Track item creation date, modification time, file size, access counters, and SHA256 checksums in the Inspector panel.

### 🔗 Secure Tokenized Sharing
- **Public & Private Share Links**: Generate shareable links for individual files or entire folders.
- **Scoped Guest Access**: Shared folder links grant guest access only within the shared directory scope — upper directory trees and internal Telegram file IDs are strictly isolated.
- **Bulk ZIP Downloads**: Download single folders or multi-file selections packaged as a single ZIP archive on the fly.

### 🛡️ Enterprise-Grade Security & 2FA
- **Two-Factor Authentication (2FA)**: Two-step login requiring Admin Password + 6-digit email OTP (via Resend API or SMTP).
- **PBKDF2 Password Hashing**: Military-grade cryptographic password protection with constant-time verification.
- **Secure HttpOnly Cookie Sessions**: Cryptographically signed session tokens with `HttpOnly`, `SameSite=Lax`, and `Secure` attributes.
- **Brute-Force & Rate-Limit Shield**: Automated lockouts for consecutive failed login and OTP attempts.
- **Path Traversal & Injection Defense**: Comprehensive path sanitization and strict Content Security Policy (CSP).

### 🔄 Automated Cloud Backup & Disaster Recovery
- **Zero Local Disk Footprint Required**: Metadata (`drive.data`) and files live safely in your Telegram `STORAGE_CHANNEL`.
- **Zero-Configuration Setup (`DATABASE_BACKUP_MSG_ID=0`)**: The server automatically posts, pins, and maintains the backup metadata message in your Telegram channel.
- **Cross-Platform Multi-Format Deserializer**: Cross-compatible with Python 3.8 through 3.14 across Windows, Linux, and macOS. Loads from Protocol 4 Dill/Pickle and human-readable JSON mirrors (`tgdrive_backup.json`).
- **Instant Cloud Recovery**: Fresh container deployments on Render, Railway, or VPS immediately download the latest backup and restore your drive contents into memory on boot.

---

## 🛠️ Architecture & Tech Stack

```
┌────────────────────────────────────────────────────────┐
│               Frontend: Glassmorphic SPA               │
│     Vanilla HTML5 / CSS3 (MD3 Glass) / Vanilla JS      │
└──────────────────────────┬─────────────────────────────┘
                           │ REST API & Streams (Fetch / Range)
┌──────────────────────────▼─────────────────────────────┐
│                 Backend: FastAPI + Uvicorn             │
│   • Auth & OTP Engine      • Transfer Queue Manager    │
│   • Thumbnail LRU Service  • Duplicate Hash Scanner    │
│   • Metadata Directory Tree • ZIP Stream Packer        │
└──────────────────────────┬─────────────────────────────┘
                           │ MTProto API (In-Memory Pyrogram)
┌──────────────────────────▼─────────────────────────────┐
│             Telegram Cloud Infrastructure              │
│   • Multi-Bot Pool         • Premium String Sessions   │
│   • STORAGE_CHANNEL (Files)• Pinned drive.data (Index) │
└────────────────────────────────────────────────────────┘
```

---

## 📋 Environment Configuration

Create a `.env` file in the root directory (or configure environment variables in Render/Railway):

### Required Variables

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org/auth) | `12345678` |
| `API_HASH` | Telegram API Hash from [my.telegram.org](https://my.telegram.org/auth) | `1bbcc5eb55b6696589...` |
| `BOT_TOKENS` | One or more bot tokens from [@BotFather](https://t.me/BotFather) (comma-separated for multi-bot pool) | `123456:ABC-DEF...,789012:GHI-JKL...` |
| `STORAGE_CHANNEL` | Telegram Channel ID (with `-100` prefix), channel username, or channel URL | `-1002455648178` or `@my_tg_channel` |
| `DATABASE_BACKUP_MSG_ID` | **Set to `0` for new setup** (the bot will auto-create and pin the message), or message ID of an existing backup | `0` or `15` |
| `ADMIN_PASSWORD` | Strong password for the web admin dashboard | `SuperSecretPassword!2026` |

> [!IMPORTANT]
> Make sure your Telegram bot is added to your `STORAGE_CHANNEL` as an **Administrator** with permissions to **Post Messages**, **Edit Messages**, **Pin Messages**, and **Delete Messages**.

### Optional Variables

| Variable | Default | Description |
| :--- | :---: | :--- |
| `ADMIN_EMAIL` | *None* | Admin email address for receiving 2FA OTP verification codes |
| `RESEND_API_KEY` | *None* | Resend API key (`re_...`) for reliable instant OTP email delivery |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP host (if using SMTP instead of Resend) |
| `SMTP_PORT` | `587` | SMTP port (`587` for STARTTLS, `465` for SMTPS) |
| `SMTP_USER` | *None* | SMTP username / email address |
| `SMTP_PASSWORD` | *None* | SMTP App Password (e.g. Gmail 16-character App Password) |
| `FROM_EMAIL` | *SMTP_USER* | Sender email address displayed to recipients |
| `STRING_SESSIONS` | *None* | Telegram Premium Pyrogram String Sessions (enables 4.0 GB file uploads) |
| `DATABASE_BACKUP_TIME` | `60` | Auto-backup interval in seconds |
| `WEBSITE_URL` | *None* | Public web URL for the built-in auto-pinger (keeps free hosting instances awake) |
| `MAIN_BOT_TOKEN` | *None* | Bot token for Telegram Bot Direct Upload Mode |
| `TELEGRAM_ADMIN_IDS` | *None* | Comma-separated Telegram User IDs allowed to use Bot Direct Upload Mode |

---

## 🚀 Setup & Deployment

### Option 1: Run Locally

1. **Clone the repository**:
   ```bash
   git clone https://github.com/shishir0x/Telegram-Unlimited-Cloud.git
   cd Telegram-Unlimited-Cloud
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your `.env`**:
   ```bash
   cp .env.example .env
   # Fill in API_ID, API_HASH, BOT_TOKENS, STORAGE_CHANNEL, ADMIN_PASSWORD, DATABASE_BACKUP_MSG_ID=0
   ```

4. **Launch the server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
   Open [http://localhost:8000](http://localhost:8000) in your browser and sign in with your `ADMIN_PASSWORD`.

---

### Option 2: Deploy to Render (Cloud Hosting)

1. Fork or push this repository to your GitHub account.
2. Go to [Render Dashboard](https://dashboard.render.com/) → click **New +** → **Web Service**.
3. Connect your GitHub repository.
4. Configure the service settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. In the **Environment Variables** section, add:
   - `API_ID`
   - `API_HASH`
   - `BOT_TOKENS`
   - `STORAGE_CHANNEL`
   - `DATABASE_BACKUP_MSG_ID` = `0`
   - `ADMIN_PASSWORD`
   - `ADMIN_EMAIL` *(Optional for 2FA)*
   - `RESEND_API_KEY` *(Optional for OTP)*
6. Click **Create Web Service**.
7. Once deployed, open your Render web URL. Your bot will automatically initialize the channel and load your drive.

---

### Option 3: Deploy with Docker & Docker Compose

1. **Start the containerized stack**:
   ```bash
   docker compose up -d --build
   ```

2. **Check deployment health**:
   ```bash
   curl http://localhost:8000/health/live
   curl http://localhost:8000/health/ready
   ```

3. **View live logs**:
   ```bash
   docker compose logs -f
   ```

---

### Option 4: Build Native Android APK

This repository includes full support for packaging the web application as a standalone Android app via Capacitor.
Refer to [`CAPACITOR_ANDROID_GUIDE.md`](CAPACITOR_ANDROID_GUIDE.md) for step-by-step instructions.

---

## 🧪 Testing & Quality Assurance

The codebase comes equipped with automated test suites verifying security, session integrity, duplicate detection, and file system functionality:

```bash
# Run Security, 2FA & Authorization Audit
python test_security_audit.py

# Run Duplicate Cleaner & Hashing Tests
python test_duplicates.py

# Run End-to-End Functional Test Suite
python test_all_functions.py

# Run Security Hardening & Rate-Limiter Checks
python test_hardening.py
```

---

## ❓ Frequently Asked Questions (FAQ)

<details>
<summary><b>How do I find my STORAGE_CHANNEL ID?</b></summary>

1. Create a private Telegram channel.
2. Add your bot as an **Administrator**.
3. Send any message in the channel and forward it to [@JsonDumpBot](https://t.me/JsonDumpBot) or [@userinfobot](https://t.me/userinfobot) on Telegram.
4. Look for the `forward_from_chat` -> `id` (e.g. `-1002455648178`).
5. Alternatively, copy the channel invite link (`https://t.me/c/2455648178`) and paste it directly into `STORAGE_CHANNEL`.
</details>

<details>
<summary><b>What should I put for DATABASE_BACKUP_MSG_ID?</b></summary>

If you are a new user, simply set `DATABASE_BACKUP_MSG_ID=0`. When the server starts up, it automatically creates a new backup message in your channel, pins it, and manages it for you.
</details>

<details>
<summary><b>What is the maximum file size I can upload?</b></summary>

- Standard Telegram Bot: **2.0 GB** per file.
- Telegram Premium (`STRING_SESSIONS` configured): **4.0 GB** per file.
- There is **no limit** on the total number of files or total storage capacity you can store in your channel.
</details>

<details>
<summary><b>Are my files safe if the Render/host container restarts?</b></summary>

**Yes.** All file contents are permanently stored in Telegram's distributed cloud storage, and your folder directory tree is synced to your pinned `drive.data` message. When the host restarts or re-deploys, TG Drive automatically downloads the latest metadata snapshot and restores everything instantly.
</details>

---

## 📄 License & Contributing

- Distributed under the [MIT License](LICENSE).
- Security policies and vulnerability reporting procedures are outlined in [SECURITY.md](SECURITY.md).
- Contributions, bug reports, and feature pull requests are welcome!
