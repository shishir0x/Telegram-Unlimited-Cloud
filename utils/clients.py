import asyncio, config
from pathlib import Path
from utils import fast_crypto
fast_crypto.patch_pyrogram()
from pyrogram import Client
from utils.directoryHandler import backup_drive_data, loadDriveData, auto_sync_telegram_loop, auto_sync_database_loop
from utils.logger import Logger
import os
import sys
import signal

logger = Logger(__name__)

multi_clients = {}
premium_clients = {}
work_loads = {}
premium_work_loads = {}
main_bot = None
_retry_task = None


async def initialize_clients():
    global multi_clients, work_loads, premium_clients, premium_work_loads
    logger.info("Initializing Clients")

    session_cache_path = Path(f"./cache")
    session_cache_path.mkdir(parents=True, exist_ok=True)

    all_tokens = dict((i, t) for i, t in enumerate(config.BOT_TOKENS, start=1))
    all_sessions = dict(
        (i, s) for i, s in enumerate(config.STRING_SESSIONS, start=len(all_tokens) + 1)
    )

    _client_flood_until = {}

    async def start_client(client_id, token, client_type):
        try:
            logger.info(f"Starting - {client_type.title()} Client {client_id}")

            if client_type == "bot":
                # Use persistent session SQLite database in ./cache/ to eliminate
                # repeated auth.ImportBotAuthorization calls on every server start/reload.
                try:
                    client = Client(
                        name=str(client_id),
                        workdir=str(session_cache_path.resolve()),
                        api_id=config.API_ID,
                        api_hash=config.API_HASH,
                        bot_token=token,
                        in_memory=False,
                        max_concurrent_transmissions=8,
                        workers=16,
                        no_updates=True,
                    )
                    client.loop = asyncio.get_running_loop()
                    await client.start()
                except Exception as start_err:
                    if "database is locked" in str(start_err).lower():
                        logger.warning(
                            f"Session cache locked for Bot Client {client_id} (another server process is active). "
                            f"Falling back to ephemeral session for this client..."
                        )
                        client = Client(
                            name=f"{client_id}_ephemeral",
                            api_id=config.API_ID,
                            api_hash=config.API_HASH,
                            bot_token=token,
                            in_memory=True,
                            max_concurrent_transmissions=8,
                            workers=16,
                            no_updates=True,
                        )
                        client.loop = asyncio.get_running_loop()
                        await client.start()
                    else:
                        raise start_err

                # Verify storage channel access and admin status
                if config.STORAGE_CHANNEL:
                    try:
                        me = await client.get_me()
                        chat = await client.get_chat(config.STORAGE_CHANNEL)
                        logger.info(f"✅ Bot Client {client_id} (@{me.username or me.id}) has access to storage channel '{chat.title or config.STORAGE_CHANNEL}'.")
                    except Exception as ch_err:
                        logger.warning(
                            f"⚠️ Bot Client {client_id} cannot access STORAGE_CHANNEL ({config.STORAGE_CHANNEL}): {ch_err}. "
                            f"Ensure this bot is added to the channel as an Administrator with 'Post Messages' permission."
                        )

                multi_clients[client_id] = client
                work_loads[client_id] = 0
                _client_flood_until.pop(client_id, None)
            elif client_type == "user":
                client = await Client(
                    name=str(client_id),
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=token,
                    sleep_threshold=config.SLEEP_THRESHOLD,
                    in_memory=True,
                    max_concurrent_transmissions=8,
                    workers=16,
                    no_updates=True,
                ).start()

                if config.STORAGE_CHANNEL:
                    try:
                        chat = await client.get_chat(config.STORAGE_CHANNEL)
                        logger.info(f"✅ User Client {client_id} has access to storage channel '{chat.title or config.STORAGE_CHANNEL}'.")
                    except Exception as ch_err:
                        logger.warning(f"⚠️ User Client {client_id} cannot access STORAGE_CHANNEL ({config.STORAGE_CHANNEL}): {ch_err}.")

                premium_clients[client_id] = client
                premium_work_loads[client_id] = 0

            logger.info(f"Started - {client_type.title()} Client {client_id}")
            return True
        except Exception as e:
            err_str = str(e)
            if "FLOOD_WAIT" in err_str or getattr(e, "value", None) is not None:
                import re, time
                val = getattr(e, "value", None)
                if val is None:
                    m = re.search(r"wait\s+(\d+)\s*seconds", err_str, re.IGNORECASE)
                    val = int(m.group(1)) if m else 1800
                _client_flood_until[client_id] = time.time() + val + 5
                try:
                    from utils import tg_gate
                    tg_gate.note_flood(client_id, val)
                except Exception:
                    pass
                logger.warning(
                    f"⚠️ {client_type.title()} Client {client_id} in FloodWait for {val}s. "
                    f"Will not retry until cooldown expires at {time.strftime('%H:%M:%S', time.localtime(_client_flood_until[client_id]))}."
                )
            else:
                logger.error(f"Failed To Start {client_type.title()} Client - {client_id} Error: {e}")
            return False

    # Stagger client startup sequentially to avoid Telegram FloodWait on parallel connections
    for client_id, token in all_tokens.items():
        success = await start_client(client_id, token, "bot")
        if success:
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(1.5)

    for client_id, token in all_sessions.items():
        success = await start_client(client_id, token, "user")
        if success:
            await asyncio.sleep(1.0)

    # Background retry worker with smart cooldown adherence (NEVER re-triggers FloodWait prematurely)
    async def retry_pending_clients():
        import time
        while len(multi_clients) < len(all_tokens):
            await asyncio.sleep(15)
            now = time.time()
            for cid, tok in all_tokens.items():
                if cid not in multi_clients:
                    cooldown = _client_flood_until.get(cid, 0)
                    if now < cooldown:
                        # Cooldown still active; wait quietly without pinging Telegram servers
                        continue
                    logger.info(f"Cooldown expired for Bot Client {cid}. Retrying connection...")
                    await start_client(cid, tok, "bot")
                    await asyncio.sleep(2.0)
            if len(multi_clients) > 0 and not getattr(config, "_drive_data_loaded", False):
                try:
                    await loadDriveData()
                    config._drive_data_loaded = True
                    asyncio.create_task(backup_drive_data())
                    asyncio.create_task(auto_sync_telegram_loop())
                    asyncio.create_task(auto_sync_database_loop())
                except Exception:
                    pass

    _retry_task = asyncio.create_task(retry_pending_clients())

    if len(multi_clients) == 0:
        logger.warning("⚠️ Bot clients encountered temporary FloodWait. Server is active and will auto-reconnect in the background.")
    else:
        logger.info(f"✅ {len(multi_clients)} Telegram Bot Client(s) successfully initialized.")

    if len(premium_clients) == 0:
        logger.info("No Premium Clients Were Initialized")

    # Load the drive data (only if at least one client connected successfully)
    if len(multi_clients) > 0 or len(premium_clients) > 0:
        if not getattr(config, "_drive_data_loaded", False):
            try:
                await loadDriveData()
                config._drive_data_loaded = True
                # Start the backup drive data task and auto-sync loop
                asyncio.create_task(backup_drive_data())
                asyncio.create_task(auto_sync_telegram_loop())
                asyncio.create_task(auto_sync_database_loop())
            except Exception as e:
                logger.warning(f"Initial drive data load deferred until bot connection: {e}")
    else:
        logger.warning("No clients ready at startup; drive data load deferred to retry worker.")


def get_client(premium_required=False) -> Client:
    """Returns the least-loaded client that is NOT currently in a Telegram FloodWait cooldown.
    Automatically rotates across healthy bots and falls back gracefully."""
    global multi_clients, work_loads, premium_clients, premium_work_loads
    from utils import tg_gate

    def _client_key(cl):
        return getattr(getattr(cl, "me", None), "id", None) or getattr(cl, "name", None)

    # 1. Premium client requested
    if premium_required:
        if premium_clients:
            usable = [
                (cid, cl) for cid, cl in premium_clients.items()
                if tg_gate.client_available(cid) and tg_gate.client_available(_client_key(cl))
            ]
            if usable:
                cid, cl = min(usable, key=lambda item: premium_work_loads.get(item[0], 0))
                premium_work_loads[cid] = premium_work_loads.get(cid, 0) + 1
                return cl
            if premium_work_loads:
                cid = min(premium_work_loads, key=premium_work_loads.get)
                premium_work_loads[cid] = premium_work_loads.get(cid, 0) + 1
                return premium_clients[cid]
        logger.warning("Premium client requested but none active; falling back to standard client.")

    # 2. Standard multi-client pool (Flood-Safe selection)
    if multi_clients:
        usable = [
            (cid, cl) for cid, cl in multi_clients.items()
            if tg_gate.client_available(cid) and tg_gate.client_available(_client_key(cl))
        ]
        if usable:
            cid, cl = min(usable, key=lambda item: work_loads.get(item[0], 0))
            work_loads[cid] = work_loads.get(cid, 0) + 1
            return cl

        # If all standard bots are currently in cooldown, fallback to any available premium client
        if premium_clients:
            usable_prem = [
                (cid, cl) for cid, cl in premium_clients.items()
                if tg_gate.client_available(cid) and tg_gate.client_available(_client_key(cl))
            ]
            if usable_prem:
                cid, cl = min(usable_prem, key=lambda item: premium_work_loads.get(item[0], 0))
                premium_work_loads[cid] = premium_work_loads.get(cid, 0) + 1
                return cl

        # All clients in cooldown: return least loaded client so caller can handle or wait
        all_pool = {**multi_clients, **premium_clients}
        all_loads = {**work_loads, **premium_work_loads}
        if all_pool and all_loads:
            cid = min(all_loads, key=all_loads.get)
            all_loads[cid] = all_loads.get(cid, 0) + 1
            return all_pool[cid]

    if premium_clients and premium_work_loads:
        cid = min(premium_work_loads, key=premium_work_loads.get)
        premium_work_loads[cid] = premium_work_loads.get(cid, 0) + 1
        return premium_clients[cid]

    raise RuntimeError("No active Telegram clients are currently connected. Please verify bot tokens in .env.")


def is_telegram_ready() -> bool:
    """Returns True if at least one Telegram bot/user client is connected and ready."""
    return len(multi_clients) > 0 or len(premium_clients) > 0


def get_client_status() -> dict:
    """Returns safe summary status of active Telegram connections."""
    return {
        "bot_clients_active": len(multi_clients),
        "premium_clients_active": len(premium_clients),
        "drive_data_loaded": getattr(config, "_drive_data_loaded", False),
        "telegram_ready": is_telegram_ready(),
    }


async def stop_clients():
    """Gracefully stop all connected Telegram clients and background tasks."""
    global multi_clients, premium_clients, _retry_task
    logger.info("Stopping Telegram clients gracefully...")
    if _retry_task and not _retry_task.done():
        _retry_task.cancel()

    for cid, client in list(multi_clients.items()):
        try:
            if getattr(client, "is_connected", False):
                await client.stop()
        except Exception as e:
            logger.warning(f"Error stopping Bot client {cid}: {e}")

    for cid, client in list(premium_clients.items()):
        try:
            if getattr(client, "is_connected", False):
                await client.stop()
        except Exception as e:
            logger.warning(f"Error stopping Premium client {cid}: {e}")

    multi_clients.clear()
    premium_clients.clear()
    logger.info("All Telegram clients stopped cleanly.")
