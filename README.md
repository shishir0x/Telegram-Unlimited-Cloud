# TG Cloud Drive - Unlimited Cloud Storage Powered by Telegram

A modern, fast, and feature-rich Google Drive alternative that uses Telegram as a secure, unlimited cloud storage backend. Built with a sleek Material Design 3 interface, in-app media & document previewing, drag-and-drop file organization, directory navigation, secure password authentication, and automated database backups.

---

## ✨ Features

- **📂 Folder & File Organization**: Create, rename, move, and organize folders and files with ease.
- **👁️ In-Tab Media & Document Previews**: Preview images, stream videos/audio, read PDFs, and view syntax-highlighted code files directly in your browser tab without force-downloading.
- **📦 Folder ZIP Downloads**: OneDrive-style multi-folder and file selection zipped and downloaded directly in one click.
- **🔗 Shareable Links**: Generate instant share links for files and folders with secure token authorization.
- **🗑️ Trash & Recovery**: Full bin/trash support with restore and permanent deletion capabilities.
- **🖱️ Drag & Drop**: Move files between folders or upload files directly by dragging them onto the browser.
- **⚡ High-Speed Streaming**: Powered by Pyrogram and `tgcrypto` for high-throughput downloads and smooth media playback.
- **💾 Automated Telegram Backups**: Keeps your file tree and metadata continuously backed up in your Telegram storage channel.
- **🤖 Telegram Bot Integration**: Upload files directly from Telegram by sending them to your bot.
- **🔄 Auto Keep-Alive**: Built-in pinger keeps free cloud hosting instances awake.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn, Pyrogram, `tgcrypto`
- **Frontend**: HTML5, Vanilla CSS3 (Material Design 3 / Glassmorphism), Vanilla JavaScript SPA
- **Storage**: Telegram Cloud Channels via Telegram MTProto API

---

## ⚙️ Environment Variables

Copy `.env.example` to `.env` and fill in your values.

### Required Core Variables

| Variable | Required | Purpose | Example |
| :--- | :---: | :--- | :--- |
| `API_ID` | **Yes** | Telegram API ID from [my.telegram.org](https://my.telegram.org) | `12345678` |
| `API_HASH` | **Yes** | Telegram API Hash from [my.telegram.org](https://my.telegram.org) | `1bbcc5eb55b66965...` |
| `BOT_TOKENS` | **Yes** | Telegram Bot token(s) from [@BotFather](https://t.me/BotFather) (comma-separated for multi-bot pool) | `1234567890:AAH...` |
| `STORAGE_CHANNEL` | **Yes** | Telegram Storage Channel ID (with `-100` prefix) | `-1001234567890` |
| `DATABASE_BACKUP_MSG_ID` | **Yes** | Message ID of a pinned backup document in the storage channel | `1` |
| `ADMIN_PASSWORD` | **Yes** | Web Admin Panel password (must be a strong, non-default password) | `YourStrongSecret!2026` |

> [!IMPORTANT]
> The bot(s) listed in `BOT_TOKENS` must be added as **Administrators** with full posting rights in your `STORAGE_CHANNEL`.

### Optional & Security Variables

| Variable | Default | Purpose |
| :--- | :---: | :--- |
| `ADMIN_EMAIL` | *None* | Admin email for 2FA One-Time Password (OTP) verification |
| `SESSION_HOURS` | `12` | Session lifetime in hours |
| `SESSION_SECRET_KEY` | *Auto* | 64-char hex key for session signing (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `RESEND_API_KEY` | *None* | Resend API key for cloud OTP delivery (recommended over SMTP) |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP host for email delivery fallback |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | *None* | SMTP username / email |
| `SMTP_PASSWORD` | *None* | SMTP App Password |
| `STRING_SESSIONS` | *None* | Premium Pyrogram string session(s) unlocking 4 GB single file limits |
| `DATABASE_BACKUP_TIME`| `60` | Interval in seconds between automatic database backups |
| `WEBSITE_URL` | *None* | Public web URL for auto-ping keepalive |
| `MAIN_BOT_TOKEN` | *None* | Dedicated bot token for interactive Telegram Bot Mode |
| `TELEGRAM_ADMIN_IDS` | *None* | Comma-separated list of Telegram User IDs authorized for Bot Mode |

---

## 🚀 Running Locally

1. **Clone the repository**:
   ```bash
   git clone https://github.com/shishir0x/Telegram-Unlimited-Cloud.git
   cd Telegram-Unlimited-Cloud
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env and enter your API_ID, API_HASH, BOT_TOKENS, etc.
   ```

4. **Start the server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
   Open `http://localhost:8000` in your browser.

---

## 🐳 Docker Deployment

Run with Docker Compose:
```bash
docker compose up -d --build
```
Check health:
```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

---

## 💾 Backup & Disaster Recovery

- **What is backed up?** Metadata file tree (`drive.data`) containing folder hierarchies, file names, sizes, device origins, and Telegram message IDs.
- **Where is it stored?** Local atomic cache (`cache/drive.data`), emergency local backup (`cache/drive.data.bak`), and continuously posted to `STORAGE_CHANNEL` at message ID `DATABASE_BACKUP_MSG_ID`.
- **How to restore?**
  1. If local disk is lost, the server automatically downloads the latest pickled metadata from `DATABASE_BACKUP_MSG_ID` on startup.
  2. If the Telegram message is missing, the server falls back to `cache/drive.data.bak`.

---

## 🔧 Troubleshooting

- **Telegram FloodWait:** Multiple parallel uploads might hit Telegram rate limits. The application handles this automatically with backoff retries.
- **401 Unauthorized on API:** Verify your `ADMIN_PASSWORD` or ensure your session cookie `tg_session` is active.
- **Missing file previews:** Ensure your bot is still an active Administrator in the `STORAGE_CHANNEL`.

---

## 📄 License & Security

- Licensed under the [MIT License](LICENSE).
- Security policy and vulnerability disclosure: see [SECURITY.md](SECURITY.md).
