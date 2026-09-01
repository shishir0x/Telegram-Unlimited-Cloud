"""
Telegram Rate-Limit Coordinator (FloodWait Shield)
==================================================
Central coordination point for every message-sending operation that talks to
the STORAGE_CHANNEL (uploads, metadata backups, bot-mode copies).

Techniques implemented:
  1. Global send concurrency limiter   — bounds simultaneous send_document calls
  2. Per-client cooldown registry      — a flooded client is skipped by the picker
                                         until its FloodWait expires (+ safety buffer)
  3. Global cooldown gate              — ANY FloodWait pauses ALL senders briefly,
                                         preventing the "thundering herd" cascade
                                         where parallel tasks keep hammering while
                                         one task sleeps
  4. Adaptive pacing (AIMD)            — inter-send gap grows +STEP per FloodWait
                                         (capped), decays multiplicatively after
                                         successful sends; floor = BASE_GAP
  5. Per-client minimum spacing        — each bot tracks its own last-send timestamp
                                         so multiple bots can pipeline sends fully
                                         in parallel without global serialization
  6. Jitter                            — randomized sleeps avoid synchronized retry
                                         storms across workers

No external dependencies. Import and use from anywhere.
"""

import asyncio
import os
import random
import time

import config

# ---------------------------------------------------------------------------
# Tunables (env-overridable, sane defaults)
from utils.extra import is_low_memory_env

_bot_count = len(getattr(config, "BOT_TOKENS", []))
_default_concurrency = min(2, _bot_count or 1) if is_low_memory_env() else max(4, _bot_count)
SEND_CONCURRENCY = max(1, int(os.getenv("TG_SEND_CONCURRENCY", str(_default_concurrency))))
BASE_GAP = float(os.getenv("TG_BASE_GAP", "0.2"))      # min seconds between sends PER CLIENT (was 0.5)
FLOOD_STEP = float(os.getenv("TG_FLOOD_STEP", "1.0"))  # pacing added per FloodWait event (was 2.0)
PACING_MAX = float(os.getenv("TG_PACING_MAX", "90"))   # ceiling for adaptive delay
PACING_DECAY = 0.65                                    # multiplicative decay on success (was 0.55, faster recovery)
CLIENT_BUFFER = 0.5                                    # extra seconds after FloodWait value (was 2.0)
MAX_GLOBAL_WAIT = float(os.getenv("TG_MAX_WAIT", "900"))  # give up waiting after 15 min

# ---------------------------------------------------------------------------
# State (single event loop -> plain attrs are safe)
# ---------------------------------------------------------------------------
_semaphore: asyncio.Semaphore | None = None     # lazily bound to running loop
_semaphore_loop = None                           # the loop that created _semaphore
_flood_until: dict = {}                          # client_key (str) -> epoch ts
_global_until: float = 0.0                       # epoch ts everyone must wait until
_last_send_ts: dict = {}                         # PER-CLIENT: client_key -> last send monotonic ts
_pace: float = 0.0                               # current adaptive extra delay (shared, AIMD)


def _sem() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    try:
        cur_loop = asyncio.get_running_loop()
    except RuntimeError:
        cur_loop = None
    # Recreate semaphore if missing or bound to a different (stale) event loop.
    # We track the creating loop ourselves; Python 3.10+ removed asyncio.Semaphore._loop.
    if _semaphore is None or _semaphore_loop is not cur_loop:
        _semaphore = asyncio.Semaphore(SEND_CONCURRENCY)
        _semaphore_loop = cur_loop
    return _semaphore


def note_flood(client_key=None, wait_seconds: float = 0.0, has_alternatives: bool = False) -> None:
    """
    Report a Telegram FloodWait.
        client_key       — identifier of the offending client (str/int/Client)
        wait_seconds     — Telegram's requested wait (fw.value)
        has_alternatives — whether other active clients can immediately take over
    """
    global _global_until, _pace
    now = time.monotonic()
    wait_s = max(1.0, float(wait_seconds))

    if client_key is not None:
        key = str(client_key)
        _flood_until[key] = now + wait_s + CLIENT_BUFFER + random.uniform(0.1, 0.5)

    if has_alternatives:
        # Other bots are ready: brief 0.2s pause so next bot claims the slot smoothly
        global_pause = now + 0.2
    else:
        # All bots rate-limited: pause queue until earliest bot cooldown expires
        global_pause = now + min(wait_s + 0.5, wait_s * 0.8 + 1.0)

    _global_until = max(_global_until, global_pause)
    _pace = min(PACING_MAX, _pace + FLOOD_STEP)


def note_success(client_key=None) -> None:
    """Report a successful send: decay adaptive pacing toward the base gap."""
    global _pace
    _pace = max(0.0, _pace * PACING_DECAY - 0.1)
    # Update per-client last send timestamp
    if client_key is not None:
        _last_send_ts[str(client_key)] = time.monotonic()


def client_available(client_key) -> bool:
    """True if this client's per-client cooldown has expired."""
    key = str(client_key)
    return time.monotonic() >= _flood_until.get(key, 0.0)


def get_client_cooldown(client_key) -> float:
    """Remaining cooldown in seconds for a specific client."""
    key = str(client_key)
    return max(0.0, _flood_until.get(key, 0.0) - time.monotonic())


def next_client_wake(keys) -> float:
    """Earliest monotonic time any of the given client keys becomes usable."""
    str_keys = [str(k) for k in keys]
    times = [_flood_until[k] for k in str_keys if k in _flood_until]
    return min(times) if times else time.monotonic()


def get_global_cooldown() -> float:
    """Remaining global pause duration in seconds."""
    return max(0.0, _global_until - time.monotonic())


async def wait_turn(client_key=None) -> None:
    """
    Must be awaited while HOLDING the semaphore, immediately before sending.
    Enforces:
      1. Global flood gate (if all bots flooded)
      2. Per-client minimum spacing (BASE_GAP) — each bot tracks independently
         so parallel bots don't block each other
      3. Adaptive pacing decay (AIMD)
      4. Jitter to de-synchronize concurrent workers

    With per-client tracking, Bot1 spacing at 0.2s does NOT force Bot2 to wait —
    all 4 bots can pipeline sends fully in parallel.
    """
    global _last_send_ts, _global_until
    c_key = str(client_key) if client_key is not None else "__global__"

    while True:
        now = time.monotonic()

        # 1. Global flood gate (only blocks when ALL bots are flooded)
        global_remaining = _global_until - now
        if global_remaining > 0:
            await asyncio.sleep(min(global_remaining, 30) * random.uniform(0.9, 1.1))
            continue

        # 2. Per-client spacing: enforce BASE_GAP + adaptive pace per bot
        last_ts = _last_send_ts.get(c_key, 0.0)
        client_wake = last_ts + BASE_GAP + _pace
        client_remaining = client_wake - now
        if client_remaining > 0:
            await asyncio.sleep(min(client_remaining, 30) * random.uniform(0.9, 1.1))
            continue

        break

    # Record send time for this specific client
    _last_send_ts[c_key] = time.monotonic()


async def acquire() -> asyncio.Semaphore:
    """Acquire a send slot (bounds concurrency). Await, use, then release()."""
    await _sem().acquire()
    return _sem()


def release(sem: asyncio.Semaphore) -> None:
    sem.release()


class send_slot:
    """Async context manager combining concurrency limit + per-client turn waiting.

    Usage:
        async with tg_gate.send_slot(client_key=client.name):
            await client.send_document(...)

    The client_key enables per-bot spacing so all 4 bots pipeline independently.
    Backward-compatible: omitting client_key falls back to a shared "__global__" key.
    """

    def __init__(self, client_key=None):
        self.client_key = client_key

    async def __aenter__(self):
        await _sem().acquire()
        await wait_turn(self.client_key)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _sem().release()
        return False


def stats() -> dict:
    """Non-sensitive snapshot for health endpoints / debugging."""
    now = time.monotonic()
    return {
        "send_concurrency": SEND_CONCURRENCY,
        "active_slots": SEND_CONCURRENCY - getattr(_sem(), "_value", SEND_CONCURRENCY),
        "available_slots": getattr(_sem(), "_value", SEND_CONCURRENCY),
        "flooded_clients": sum(1 for t in _flood_until.values() if t > now),
        "global_cooldown_s": round(max(0.0, _global_until - now), 1),
        "adaptive_pace_s": round(_pace, 2),
        "base_gap_s": BASE_GAP,
        "flood_step_s": FLOOD_STEP,
        "client_buffer_s": CLIENT_BUFFER,
        "per_client_spacing": {
            k: round(max(0.0, v + BASE_GAP + _pace - now), 2)
            for k, v in _last_send_ts.items()
        },
    }
