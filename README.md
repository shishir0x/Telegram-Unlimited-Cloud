# TG Cloud Drive - Unlimited Cloud Storage Powered by Telegram

A modern, fast, and feature-rich Google Drive alternative that uses Telegram as a secure, unlimited cloud storage backend. Built with a sleek Material Design 3 interface, in-app media & document previewing, drag-and-drop file organization, directory navigation, secure password authentication, and automated database backups.

---

## ✨ Features

- **📂 Folder & File Organization**: Create, rename, move, and organize folders and files with ease.
- **👁️ In-Tab Media & Document Previews**: Preview images, stream videos/audio, read PDFs, and view syntax-highlighted code files directly in your browser tab without force-downloading.
- **🔗 Shareable Links**: Generate instant share links for files and folders with secure token authorization.
- **🗑️ Trash & Recovery**: Full bin/trash support with restore and permanent deletion capabilities.
- **🖱️ Drag & Drop**: Move files between folders or upload files directly by dragging them onto the browser.
- **⚡ High-Speed Streaming**: Powered by Pyrogram and `tgcrypto` for high-throughput downloads and smooth media playback.
- **💾 Automated Telegram Backups**: Keeps your file tree and metadata continuously backed up in your Telegram storage channel.
- **🤖 Telegram Bot Integration**: Upload files directly from Telegram by sending them to your bot.
- **🔄 Auto Keep-Alive**: Built-in pinger keeps free cloud hosting instances awake.

---

## 🛠️ Tech Stack

- **Backend**: Python 3, FastAPI, Uvicorn, Pyrogram, `tgcrypto`
- **Frontend**: HTML5, CSS3 (Material Design 3 / Glassmorphism), Vanilla JavaScript (SPA router)
- **Storage**: Telegram Cloud Channels via Telegram MTProto API

---

## ⚙️ Environment Variables

### Required Variables

| Variable | Description | Example |
| :--- | :--- | :--- |
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) | `12345678` |
| `API_HASH` | Telegram API Hash from [my.telegram.org](https://my.telegram.org) | `1bbcc5eb55b66965...` |
| `BOT_TOKENS` | Telegram Bot token(s) from [@BotFather](https://t.me/BotFather) | `1234567890:AAH...` |
| `STORAGE_CHANNEL` | Telegram Storage Channel ID | `-1001234567890` |
| `DATABASE_BACKUP_MSG_ID` | Message ID of a backup document in the storage channel | `37` |

> **Note**: The bot(s) listed in `BOT_TOKENS` must be added as **Administrators** with full permissions in your `STORAGE_CHANNEL`.

### Optional Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ADMIN_PASSWORD` | `admin` | Password for accessing the web admin panel |
| `STRING_SESSIONS` | *None* | Premium Pyrogram string session(s) for uploading files up to 4GB |
| `DATABASE_BACKUP_TIME` | `60` | Interval in seconds between automatic database backups |
| `WEBSITE_URL` | *None* | Public web URL (e.g., `https://your-app.onrender.com`) for auto-ping keepalive |
| `MAIN_BOT_TOKEN` | *None* | Dedicated bot token for interactive Telegram Bot Mode |
| `TELEGRAM_ADMIN_IDS` | *None* | Comma-separated list of Telegram User IDs authorized for Bot Mode |

---

## 🚀 Deployment Guide

### Deploying on Render (Free Web Service)

1. Go to the **[Render Dashboard](https://dashboard.render.com/)**.
2. Click **New +** → **Web Service** → Connect your repository: `shishir0x/Telegram-Unlimited-Cloud`.
3. Set the configuration:
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Under **Environment Variables**, add the variables listed above.
5. Click **Create Web Service**.

---

### Running Locally

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
   Create a `.env` file in the project root:
   ```env
   API_ID=your_api_id
   API_HASH=your_api_hash
   BOT_TOKENS=your_bot_token
   STORAGE_CHANNEL=-100xxxxxxxxxx
   DATABASE_BACKUP_MSG_ID=1
   ADMIN_PASSWORD=your_secure_password
   ```

4. **Start the server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
   Open `http://localhost:8000` in your browser.

---

## 🤖 Telegram Bot Mode

Upload files to your drive straight from Telegram:
1. Set `MAIN_BOT_TOKEN` and your user ID in `TELEGRAM_ADMIN_IDS`.
2. Open your Telegram bot and send or forward any file.
3. Use `/set_folder` to pick the target upload folder, or `/current_folder` to check the current destination.

---

## 📄 License

This project is open source and available under the [MIT License](LICENSE).
