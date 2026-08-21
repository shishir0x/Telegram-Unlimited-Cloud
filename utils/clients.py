import asyncio, config
from pathlib import Path
from pyrogram import Client
from utils.directoryHandler import backup_drive_data, loadDriveData
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


async def initialize_clients():
    global multi_clients, work_loads, premium_clients, premium_work_loads
    logger.info("Initializing Clients")

    session_cache_path = Path(f"./cache")
    session_cache_path.mkdir(parents=True, exist_ok=True)

    all_tokens = dict((i, t) for i, t in enumerate(config.BOT_TOKENS, start=1))
    all_sessions = dict(
        (i, s) for i, s in enumerate(config.STRING_SESSIONS, start=len(all_tokens) + 1)
    )

    async def start_client(client_id, token, client_type):
        try:
            logger.info(f"Starting - {client_type.title()} Client {client_id}")

            if client_type == "bot":
                client = Client(
                    name=str(client_id),
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    bot_token=token,
                    workdir=session_cache_path,
                    max_concurrent_transmissions=8,
                    workers=16,
                    no_updates=True,
                )
                client.loop = asyncio.get_running_loop()
                await client.start()
                try:
                    await client.send_message(
                        config.STORAGE_CHANNEL,
                        f"Started - {client_type.title()} Client {client_id}",
                    )
                except Exception:
                    pass
                multi_clients[client_id] = client
                work_loads[client_id] = 0
            elif client_type == "user":
                client = await Client(
                    name=str(client_id),
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=token,
                    sleep_threshold=config.SLEEP_THRESHOLD,
                    workdir=session_cache_path,
                    max_concurrent_transmissions=8,
                    workers=16,
                    no_updates=True,
                ).start()
                try:
                    await client.send_message(
                        config.STORAGE_CHANNEL,
                        f"Started - {client_type.title()} Client {client_id}",
                    )
                except Exception:
                    pass
                premium_clients[client_id] = client
                premium_work_loads[client_id] = 0

            logger.info(f"Started - {client_type.title()} Client {client_id}")
            return True
        except Exception as e:
            logger.error(
                f"Failed To Start {client_type.title()} Client - {client_id} Error: {e}"
            )
            return False

    # Stagger client startup sequentially to avoid Telegram FloodWait on parallel connections
    for client_id, token in all_tokens.items():
        success = await start_client(client_id, token, "bot")
        if success:
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(2.0)

    for client_id, token in all_sessions.items():
        success = await start_client(client_id, token, "user")
        if success:
            await asyncio.sleep(1.0)

    # Background retry worker for any clients that encountered temporary FloodWait
    async def retry_pending_clients():
        while len(multi_clients) < len(all_tokens):
            await asyncio.sleep(30)
            for cid, tok in all_tokens.items():
                if cid not in multi_clients:
                    logger.info(f"Retrying Bot Client {cid} connection...")
                    await start_client(cid, tok, "bot")
                    await asyncio.sleep(2.0)
            if len(multi_clients) > 0 and not getattr(config, "_drive_data_loaded", False):
                try:
                    await loadDriveData()
                    config._drive_data_loaded = True
                    asyncio.create_task(backup_drive_data())
                except Exception:
                    pass

    asyncio.create_task(retry_pending_clients())

    if len(multi_clients) == 0:
        logger.warning("⚠️ Bot clients encountered temporary FloodWait. Server is active and will auto-reconnect in the background.")
    else:
        logger.info(f"✅ {len(multi_clients)} Telegram Bot Client(s) successfully initialized.")

    if len(premium_clients) == 0:
        logger.info("No Premium Clients Were Initialized")

    # Load the drive data
    try:
        await loadDriveData()
        config._drive_data_loaded = True
        # Start the backup drive data task
        asyncio.create_task(backup_drive_data())
    except Exception as e:
        logger.warning(f"Initial drive data load deferred until bot connection: {e}")


def get_client(premium_required=False) -> Client:
    global multi_clients, work_loads, premium_clients, premium_work_loads

    if premium_required:
        index = min(premium_work_loads, key=premium_work_loads.get)
        premium_work_loads[index] += 1
        return premium_clients[index]

    index = min(work_loads, key=work_loads.get)
    work_loads[index] += 1
    return multi_clients[index]
