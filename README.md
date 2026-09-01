# TG Drive — Enterprise Architecture & Operator Manual

**A distributed, Telegram-backed cloud storage platform providing a Google Drive–grade web interface, multi-bot MTProto transfer multiplexing, and real-time multi-client synchronization.**

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/framework-FastAPI%200.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Database](https://img.shields.io/badge/database-PostgreSQL%20%7C%20SQLite-4169E1.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Core Subsystems](#core-subsystems)
   - [MTProto Multi-Bot Multiplexer & Flood-Wait Router](#1-mtproto-multi-bot-multiplexer--flood-wait-router)
   - [Transfer Manager State Machine](#2-transfer-manager-state-machine)
   - [Low-Memory Container Engine (Render 512MB RAM Ceiling)](#3-low-memory-container-engine-render-512mb-ram-ceiling)
   - [Real-Time Distributed Synchronization Engine](#4-real-time-distributed-synchronization-engine)
   - [Database Connection Pooling & TCP Keepalives](#5-database-connection-pooling--tcp-keepalives)
3. [Component Hierarchy & Directory Structure](#component-hierarchy--directory-structure)
4. [Environment Configuration Reference](#environment-configuration-reference)
5. [Installation & Deployment](#installation--deployment)
   - [Local Development](#local-development)
   - [Production Docker Deployment](#production-docker-deployment)
   - [Render Cloud Deployment](#render-cloud-deployment)
6. [API Specification](#api-specification)
7. [Testing & Quality Assurance](#testing--quality-assurance)
8. [Troubleshooting & Runbook](#troubleshooting--runbook)
9. [License](#license)

---

## System Architecture

TG Drive decouples **data storage** from **metadata indexing**:
- **Data Plane:** Files are chunked and streamed directly to a private Telegram storage channel over the MTProto protocol via a pool of bot accounts and optional Telegram Premium user sessions.
- **Control Plane:** Metadata (folder hierarchies, permissions, MIME types, content hashes, and versioned audit logs) is persisted in a relational database (PostgreSQL with PgBouncer compatibility in production, SQLite in local development).
- **Synchronization Plane:** An event-sourced changelog broadcasts atomic mutations over WebSockets to all connected browser clients, with automatic HTTP polling fallback.

```
                                 ┌────────────────────────────────────────┐
                                 │       Client Browser / Web App         │
                                 │   (HTML5 SPA · WebSockets · Range Req) │
                                 └──────────────────┬─────────────────────┘
                                                    │
                                                    │ HTTPS / WSS
                                                    ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI ASGI Application Layer (Uvicorn)                                                              │
│                                                                                                       │
│   ┌──────────────────────┐   ┌──────────────────────┐   ┌─────────────────────────────────────────┐   │
│   │ Security Middleware  │   │  Auth & Session Core │   │ Transfer Manager Subsystem              │   │
│   │ · CSP / CORS / HSTS  │──▶│  · PBKDF2-HMAC-SHA256│──▶│ · Bounded Concurrency Workers (2-4)     │   │
│   │ · IP-Bound Sessions  │   │  · 2FA Email / TG OTP│   │ · Resilient Exponential Backoff Retry   │   │
│   └──────────────────────┘   └──────────────────────┘   └────────────────────┬────────────────────┘   │
│                                                                              │                        │
│   ┌──────────────────────────────────────────────────────────────────────┐   │                        │
│   │ Memory Safety & Host OS Reclamation Layer                            │   │                        │
│   │ · MALLOC_ARENA_MAX=2 (prevents glibc virtual fragmentation)          │   │                        │
│   │ · clean_memory() with ctypes.CDLL("libc.so.6").malloc_trim(0)        │   │                        │
│   │ · 256KB bounded upload chunking · Auto 60s background cycle          │   │                        │
│   └──────────────────────────────────────────────────────────────────────┘   │                        │
│                 │                                                            │                        │
│                 ▼                                                            ▼                        │
│   ┌───────────────────────────┐                         ┌─────────────────────────────────────────┐   │
│   │ Directory Tree & Metadata │                         │ MTProto Telegram Gateway (tg_gate)      │   │
│   │ · In-memory cache + DB    │                         │ · Multi-Bot Token Round-Robin Pool      │   │
│   │ · Cycle-safe recursion    │                         │ · Per-Bot FloodWait Rate-Limiter        │   │
│   │ · Atomic JSON mirror      │                         │ · Telegram Premium 4GB Session Router   │   │
│   └─────────────┬─────────────┘                         └────────────────────┬────────────────────┘   │
└─────────────────┼────────────────────────────────────────────────────────────┼────────────────────────┘
                  │                                                            │
                  ▼                                                            ▼
    ┌───────────────────────────┐                                ┌───────────────────────────┐
    │ PostgreSQL / SQLite       │                                │ Telegram Cloud Network    │
    │ · PgBouncer NullPool      │                                │ · MTProto Distributed DC │
    │ · TCP Keepalives (idle 30)│                                │ · Storage Channel Backend │
    │ · sync_version changelog  │                                │ · Infinite Free Capacity  │
    └───────────────────────────┘                                └───────────────────────────┘
```

---

## Core Subsystems

### 1. MTProto Multi-Bot Multiplexer & Flood-Wait Router

To maximize throughput and bypass Telegram's strict per-account rate limits (FloodWait), the backend deploys a multiplexed bot pool:

- **Load Distribution:** Up to $N$ bot tokens (`BOT_TOKENS=tok1,tok2,tok3,tok4`) are initialized into independent Pyrogram MTProto clients during startup.
- **Intelligent Flood Routing (`utils/tg_gate.py`):** When Telegram issues a `FloodWait(seconds)` error on Bot $A$, `tg_gate` marks Bot $A$ as cooling down until $t_{\text{now}} + \text{seconds}$, and immediately routes subsequent transmission tasks to Bot $B$, Bot $C$, or Bot $D$.
- **Per-Bot Concurrency Gates:** Active API calls per bot are throttled via an asynchronous semaphore and minimum inter-request spacing dictionary to eliminate burst-induced 429/420 errors.
- **Premium 4GB Support:** Standard bot accounts are limited by Telegram to 2GB per file. Providing Pyrogram user string sessions (`STRING_SESSIONS=...`) unlocks 4GB per file; uploads larger than 2GB automatically route exclusively through premium-capable sessions.
- **Real-Time Health Monitoring:** Inspect active bot connections, channel access status, and cooldown timers via `GET /api/telegram/status`.

### 2. Transfer Manager State Machine

File transfers (both uploads to Telegram and remote downloads from web URLs) are orchestrated by a persistent, background state machine in `utils/transfer_manager.py`:

```
                       ┌──────────────┐
                       │   QUEUED     │
                       └──────┬───────┘
                              │ Worker picks up task
                              ▼
                       ┌──────────────┐
              ┌───────▶│  UPLOADING   │──────────────┐
              │        └──────┬───────┘              │
              │               │                      │
       Transient Error        │ Success              │ Cancelled by user
       Retry Budget Left      ▼                      ▼
              │        ┌──────────────┐       ┌──────────────┐
              └────────┤   RETRYING   │       │  CANCELLED   │
                       └──────┬───────┘       └──────────────┘
                              │
                              │ Max retries exceeded
                              ▼
                       ┌──────────────┐
                       │    FAILED    │
                       └──────────────┘
```

- **Exponential Backoff:** Transient network drops and rate limits are retried with randomized exponential jitter:
  $$\Delta t = \min\left(60.0, 2^{\text{retry} - 1} \times 2.0 + \text{Uniform}(0.5, 2.0)\right)$$
- **Crash Recovery:** Transfer states are written to disk (`cache/transfers.json`). Upon an ungraceful server restart or crash, interrupted jobs in `UPLOADING` state are cleanly recovered and transitioned back to `QUEUED`.
- **Deduplication:** Multiple concurrent submissions of identical jobs are coalesced into a single active transmission.

### 3. Low-Memory Container Engine (Render 512MB RAM Ceiling)

Free and starter tiers on container hosts like **Render.com** enforce a hard **512MB RAM ceiling** without swap space. Exceeding this limit triggers an immediate kernel `SIGKILL` (exit code 137). The system incorporates deep memory optimizations to ensure deterministic sub-150MB operation:

1. **Glibc Arena Limiting (`MALLOC_ARENA_MAX=2`):** By default, glibc on Linux creates $8 \times N_{\text{cores}}$ independent 64MB memory arenas. On a multi-core cloud host, this fragments virtual address space and causes memory retention. Restricting arenas to 2 keeps heap growth linear and compact.
2. **Dynamic Host OS Page Trimming (`clean_memory()`):** Python's pymalloc retains freed memory pages inside its internal arenas. In `utils/extra.py`, `clean_memory()` executes `gc.collect()` followed by `ctypes.CDLL("libc.so.6").malloc_trim(0)`, compelling the C runtime to release unused heap pages directly back to the Linux kernel.
3. **Bounded Chunk Streaming:** In `main.py`, upload streams read in **256KB chunks** (reduced from legacy 8MB buffers), and invoke `await file.close()` in `finally:` blocks to instantly unspool memory buffers.
4. **Adaptive Concurrency & Thread Scaling:**
   - On Render (`RENDER=true` or `LOW_MEMORY_MODE=1`), Pyrogram worker threads are capped at **2 per bot** (down from 16), reducing idle thread stacks from 64 to 8–16.
   - TransferManager worker pools scale to **2 upload / 2 download workers**.
   - `ThumbnailService` in-memory RAM cache is scaled down to 30 items.
5. **Pillow Decompression Bomb Safeguard:** Uploaded images larger than 6000px bypass raw decompression; JPEG thumbnails employ `img.draft("RGB", (320, 320))` to decode directly at 1/8th scale without allocating full-sized pixel matrices in RAM.
6. **Automatic 60s Background Reclamation:** An asynchronous lifecycle task executes memory cleanup every 60 seconds.

### 4. Real-Time Distributed Synchronization Engine

When running multiple instances (e.g. Local developer server + Production Render container) against the same database, consistency is preserved through an event-sourced architecture:

- **Atomic Changelog (`sync_changelog`):** Every mutating operation (creation, rename, move, trash, restore, delete) increments a monotonically increasing global integer version (`sync_version`) within the same database transaction.
- **WebSocket Push (`/api/sync/ws`):** Once committed, mutations emit lightweight JSON notifications (`FILE_CREATED`, `FOLDER_MOVED`, etc.) to active WebSockets managed by `WebSocketManager`.
- **Client Catch-Up (`GET /api/sync/changes?since=V`):** Clients that disconnect or miss WebSocket packets supply their last acknowledged version to fetch missing changes and reconcile their UI state without a full reload.
- **Cross-Tab Synchronization:** Browser tabs communicate via the `BroadcastChannel` API to prevent duplicate fetch requests across open windows.

### 5. Database Connection Pooling & TCP Keepalives

Production PostgreSQL deployments using transaction poolers (e.g. Supabase port 6543, Neon, or PgBouncer) drop idle connections, causing `server closed the connection unexpectedly` errors. TG Drive eliminates this via:

- **`NullPool` Architecture:** Prevents application-side retention of stale sockets in server-side pooled environments.
- **TCP Keepalive Probing:** Connect arguments explicitly configure operating system socket keepalives:
  ```python
  "keepalives": 1,
  "keepalives_idle": 30,      # Probe after 30 seconds of inactivity
  "keepalives_interval": 10,   # Probe every 10 seconds
  "keepalives_count": 5        # Disconnect after 5 failed probes
  ```
- **Resilient Context Managers:** `get_db_session()` traps disconnects, performs automatic transaction rollbacks, and safely releases handles.

---

## Component Hierarchy & Directory Structure

```
d:/Portfolio/Tele Unlim/
├── main.py                        # FastAPI application entry point, routes & lifespan
├── config.py                      # Config parser, URL normalizer & env validator
├── Dockerfile                     # Multi-stage Python 3.12-slim build (MALLOC_ARENA_MAX=2)
├── docker-compose.yml             # Local production container orchestration
├── requirements.txt               # Locked Python dependencies
├── alembic/                       # Database schema migration revisions
├── database/
│   ├── connection.py              # SQLAlchemy engine, PgBouncer NullPool & session manager
│   ├── models.py                  # Declarative ORM schemas (Folder, File, SyncVersion, ChangeLog)
│   └── repository.py              # Atomic database queries and sync change persistence
├── utils/
│   ├── extra.py                   # clean_memory(), malloc_trim(), cycle-safe compute_folder_stats()
│   ├── clients.py                 # Multi-bot Pyrogram initialization & thread pool management
│   ├── tg_gate.py                 # Rate-limit gating, FloodWait cooldown tracking & slot manager
│   ├── transfer_manager.py        # Asynchronous state-machine queue (upload/download workers)
│   ├── uploader.py                # Direct Telegram MTProto document uploader
│   ├── downloader.py              # URL fetcher & multi-threaded chunk downloader (TechZDL)
│   ├── streamer/
│   │   ├── __init__.py            # HTTP Range (206) media streaming & local file streamer
│   │   ├── custom_dl.py           # MTProto ByteStreamer yielding direct DC chunks
│   │   └── file_properties.py     # FileId parsing & DC resolution
│   ├── properties.py              # EXIF, ID3, PDF & video metadata extractor + worker
│   ├── duplicate_manager.py       # Streaming SHA-256 duplicate scanner with cycle guards
│   ├── archive_manager.py         # Memory-bounded zip inspection & extraction sandbox
│   ├── zipper.py                  # On-the-fly zip packaging with stale cache pruner
│   ├── auth.py                    # Session management, constant-time verification & OTP engine
│   ├── sync.py                    # ChangeTracker mutation recorder & SyncService
│   ├── sync_routes.py             # Sync REST endpoints & WebSocket handler
│   ├── websocket_manager.py       # Active socket registry & dead connection pruner
│   └── logger.py                  # Standardized colorized console & file logging
└── website/
    ├── home.html                  # Core single-page interface shell
    ├── share.html                 # Tokenized public download portal
    └── static/
        ├── css/home.css           # Desktop & mobile responsive styling
        └── js/                    # Client modular state, transfer manager & sync client
```

---

## Environment Configuration Reference

Create a `.env` file in the root directory. Required parameters are indicated below:

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `TELEGRAM_API_ID` | **Yes** | — | Telegram API ID from [my.telegram.org](https://my.telegram.org/auth). |
| `TELEGRAM_API_HASH` | **Yes** | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org/auth). |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Comma-separated list of Bot tokens from [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_CHAT_ID` | **Yes** | — | Channel ID (`-100...`) or `@channel_name` where files are stored. |
| `ADMIN_PASSWORD` | **Yes** | — | Secure administrative panel password (min. 8 characters). |
| `DATABASE_URL` | No | SQLite | PostgreSQL connection URL (`postgresql://...`). Falls back to SQLite if omitted. |
| `STRING_SESSIONS` | No | `""` | Comma-separated Pyrogram session strings for Telegram Premium (enables 4GB uploads). |
| `ADMIN_EMAIL` | No | `""` | Email for 2FA OTP codes. If omitted, password-only mode is active. |
| `RESEND_API_KEY` | No | `""` | Resend API key for OTP delivery. |
| `SMTP_HOST` | No | `smtp.gmail.com` | SMTP relay server for OTP delivery. |
| `SMTP_PORT` | No | `587` | SMTP port (`587` for STARTTLS, `465` for SSL). |
| `SMTP_USER` | No | `""` | SMTP authentication username. |
| `SMTP_PASSWORD` | No | `""` | SMTP password or app-specific password. |
| `SECRET_KEY` | No | Auto | 256-bit cryptographic secret for session tokens. |
| `CORS_ORIGINS` | No | `*` | Allowed CORS origins for web requests. |
| `SESSION_HOURS` | No | `12` | Session lifetime in hours before re-authentication is required. |
| `LOW_MEMORY_MODE` | No | `0` | Force memory-constrained mode (`1` or `true`) on non-Render hosts. |
| `WEBSITE_URL` | No | `""` | Public URL for the built-in keepalive pinger (prevents free tier sleep). |

---

## Installation & Deployment

### Local Development

1. **Clone and create environment:**
   ```bash
   git clone https://github.com/shishir0x/Telegram-Unlimited-Cloud.git
   cd Telegram-Unlimited-Cloud
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Populate TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_PASSWORD
   ```

3. **Run database migrations:**
   ```bash
   alembic upgrade head
   ```

4. **Launch development server:**
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### Production Docker Deployment

The included multi-stage `Dockerfile` configures `MALLOC_ARENA_MAX=2`, creates a non-root application user, and incorporates healthcheck probes:

```bash
# Build and launch daemonized container
docker compose up -d --build

# Monitor runtime logs
docker compose logs -f

# Verify container liveness probe
curl -f http://localhost:8000/health/live
```

### Render Cloud Deployment

1. Create a new **Web Service** linked to your repository.
2. Select **Docker** environment (or Native Python 3.12).
3. Under **Environment Variables**, supply your credentials:
   - `RENDER=true` (automatically set by Render)
   - `DATABASE_URL` (points to your shared PostgreSQL instance)
   - Set other required Telegram and Auth variables.
4. Set **Health Check Path** to `/health/live`.

---

## API Specification

All authenticated endpoints require the `tg_session` cookie issued upon successful login/OTP verification.

### System & Health

| Method | Route | Description | Auth |
| :--- | :--- | :--- | :---: |
| `GET` | `/health/live` | Process liveness probe | None |
| `GET` | `/health/ready` | Readiness probe (validates Telegram & Database connections) | None |
| `GET` | `/api/telegram/status` | Bot pool operational health, active clients & FloodWait status | Admin |

### File & Directory Operations

| Method | Route | Description | Auth |
| :--- | :--- | :--- | :---: |
| `POST` | `/api/getDirectory` | Fetches directory contents, breadcrumbs, and stats | Admin |
| `POST` | `/api/createNewFolder` | Creates a new directory at specified path | Admin |
| `POST` | `/api/upload` | Multipart file upload stream (chunked to TransferManager) | Admin |
| `GET` | `/file` | Streams file content with HTTP 206 Range support | Cookie / Token |
| `GET` | `/thumbnail` | Generates / returns cached JPEG thumbnail with ETag | Cookie / Token |
| `POST` | `/api/renameFileFolder` | Renames an existing file or directory | Admin |
| `POST` | `/api/moveFileFolder` | Moves entities across the folder hierarchy | Admin |
| `POST` | `/api/trashFileFolder` | Soft-deletes entities to the recycle bin | Admin |
| `POST` | `/api/deleteFileFolder` | Permanently removes an entity and cleans storage | Admin |

### Transfer Subsystem

| Method | Route | Description | Auth |
| :--- | :--- | :--- | :---: |
| `GET` | `/api/transfers` | Returns active, queued, retrying, and completed transfers | Admin |
| `GET` | `/api/transfers/{id}` | Fetches individual transfer progress, speed, and ETA | Admin |
| `POST` | `/api/transfers/{id}/cancel` | Aborts an active transfer and unlinks cache artifacts | Admin |
| `POST` | `/api/transfers/{id}/retry` | Manually triggers immediate retry of a failed job | Admin |
| `POST` | `/api/transfers/clear` | Purges completed and cancelled items from the ledger | Admin |

### Synchronization

| Method | Route | Description | Auth |
| :--- | :--- | :--- | :---: |
| `GET` | `/api/sync/status` | Returns current database version and timestamp | Admin |
| `GET` | `/api/sync/changes?since=V` | Returns ordered mutations recorded since version `V` | Admin |
| `WS` | `/api/sync/ws` | Bidirectional WebSocket connection for live mutation push | Admin |

---

## Testing & Quality Assurance

TG Drive includes comprehensive automated test suites covering concurrency, database integrity, transfer pipelines, and properties enrichment:

```bash
# Verify Python syntax and AST compilation across entire repository
python -c "import ast, os; [ast.parse(open(os.path.join(r, f), encoding='utf-8').read()) for r, _, fs in os.walk('.') if not any(x in r for x in ['.git', '__pycache__', 'venv']) for f in fs if f.endswith('.py')]; print('AST syntax check passed.')"

# Run Transfer Manager test suite (state machine, retry backoff, concurrency semaphore)
python -m unittest test_transfer_manager.py

# Run Google Drive-Style Properties & Details test suite
python test_properties_system.py

# Run Database Schema & CRUD tests
python -m unittest test_phase1_database.py

# Run Synchronization Engine test suite
python -m unittest test_phase2_sync.py
```

---

## Troubleshooting & Runbook

### 1. Render Container Exits with Error 137 (`SIGKILL`)
- **Root Cause:** Container exceeded the 512MB RAM ceiling.
- **Remediation:** Verify `MALLOC_ARENA_MAX=2` is present in the container environment. Confirm `LOW_MEMORY_MODE=1` or `RENDER=true` is set. Check that `/api/upload` is receiving files through standard streaming rather than buffering the entire payload in memory.

### 2. Database Disconnect (`server closed the connection unexpectedly`)
- **Root Cause:** PostgreSQL pooler terminated idle TCP socket.
- **Remediation:** Verify that [database/connection.py](file:///d:/Portfolio/Tele%20Unlim/database/connection.py) has TCP keepalives enabled (`keepalives_idle=30`). If connecting to Supabase, ensure port `6543` (transaction mode) is specified in `DATABASE_URL` with `NullPool` enabled.

### 3. Telegram `FloodWait` (Rate Limited)
- **Root Cause:** Telegram MTProto API temporarily throttled transmissions.
- **Remediation:** Check `/api/telegram/status` to review active bot backoffs. Ensure multiple bot tokens (`BOT_TOKENS=tok1,tok2,...`) are configured so the gateway can fail over to unthrottled bots automatically.

### 4. Admin Authentication Refused at Startup
- **Root Cause:** `ADMIN_PASSWORD` matches an insecure default value (`admin`, `password`, `123456`).
- **Remediation:** Set a unique, complex password with at least 8 characters in your `.env` file.

---

## License

This project is open-source software licensed under the terms of the [MIT License](LICENSE).
