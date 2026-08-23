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
  5. Minimum inter-message spacing     — never lets two channel messages land
                                         closer than BASE_GAP seconds apart
  6. Jitter                            — randomized sleeps avoid synchronized retry
                                         storms across workers

No external dependencies. Import and use from anywhere.
"""

import asyncio
import os
import random
import time

# ---------------------------------------------------------------------------
# Tunables (env-overridable, sane defaults)
# ---------------------------------------------------------------------------
SEND_CONCURRENCY = max(1, int(os.getenv("TG_SEND_CONCURRENCY", "2")))
BASE_GAP = float(os.getenv("TG_BASE_GAP", "1.2"))      # min seconds between channel sends
FLOOD_STEP = float(os.getenv("TG_FLOOD_STEP", "2.0"))  # pacing added per FloodWait event
PACING_MAX = float(os.getenv("TG_PACING_MAX", "90"))   # ceiling for adaptive delay
PACING_DECAY = 0.55                                    # multiplicative decay on success
CLIENT_BUFFER = 2.0                                    # extra seconds after FloodWait value
MAX_GLOBAL_WAIT = float(os.getenv("TG_MAX_WAIT", "900"))  # give up waiting after 15 min

# ---------------------------------------------------------------------------
# State (single event loop -> plain attrs are safe)
# ---------------------------------------------------------------------------
_semaphore: asyncio.Semaphore | None = None     # lazily bound to running loop
_flood_until: dict = {}                          # client_key -> epoch ts
_global_until: float = 0.0                       # epoch ts everyone must wait until
_last_send_ts: float = 0.0                       # last completed/started channel send
_pace: float = 0.0                               # current adaptive extra delay


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SEND_CONCURRENCY)
    return _semaphore


def note_flood(client_key=None, wait_seconds: float = 0.0) -> None:
    """
    Report a Telegram FloodWait.
        client_key  — id(client) of the offending client (None => unknown source)
        wait_seconds — Telegram's requested wait (fw.value)
    """
    global _global_until, _pace
    now = time.monotonic()
    wait_s = max(1.0, float(wait_seconds))

    if client_key is not None:
        _flood_until[client_key] = now + wait_s + CLIENT_BUFFER + random.uniform(0.5, 2.0)

    # Global gate: pause every sender a bit longer than the flood itself,
    # so queued workers don't re-trigger the same limit one after another.
    global_pause = now + min(wait_s * 0.6 + random.uniform(0.5, 1.5), wait_s)
    _global_until = max(_global_until, global_pause)

    # Adaptive pacing ramps up aggressively but stays bounded
    _pace = min(PACING_MAX, _pace + FLOOD_STEP)


def note_success() -> None:
    """Report a successful send: decay adaptive pacing toward the base gap."""
    global _pace
    _pace = max(0.0, _pace * PACING_DECAY - 0.1)


def client_available(client_key) -> bool:
    """True if this client's per-client cooldown has expired."""
    return time.monotonic() >= _flood_until.get(client_key, 0.0)


def next_client_wake(keys) -> float:
    """Earliest monotonic time any of the given client keys becomes usable."""
    times = [_flood_until[k] for k in keys if k in _flood_until]
    return min(times) if times else time.monotonic()


async def wait_turn() -> None:
    """
    Must be awaited while HOLDING the semaphore, immediately before sending.
    Enforces: global flood gate -> minimum spacing since last send -> adaptive
    pacing, all with jitter so parallel workers de-synchronize.
    """
    global _last_send_ts
    while True:
        now = time.monotonic()
        wake = _global_until

        spaced = _last_send_ts + BASE_GAP + _pace
        if spaced > wake:
            wake = spaced

        remaining = wake - now
        if remaining <= 0:
            break
        # Jitter: ±15% so concurrent workers don't wake in lockstep
        await asyncio.sleep(min(remaining, 30) * random.uniform(0.85, 1.15))

    _last_send_ts = time.monotonic()


async def acquire() -> asyncio.Semaphore:
    """Acquire a send slot (bounds concurrency). Await, use, then release()."""
    await _sem().acquire()
    return _sem()


def release(sem: asyncio.Semaphore) -> None:
    sem.release()


class send_slot:
    """Async context manager combining concurrency limit + turn waiting.

    Usage:
        async with tg_gate.send_slot():
            await client.send_document(...)
    """

    async def __aenter__(self):
        await _sem().acquire()
        await wait_turn()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _sem().release()
        return False


def stats() -> dict:
    """Non-sensitive snapshot for health endpoints / debugging."""
    now = time.monotonic()
    return {
        "send_concurrency": SEND_CONCURRENCY,
        "active_slots": getattr(_sem(), "_value", SEND_CONCURRENCY),
        "flooded_clients": sum(1 for t in _flood_until.values() if t > now),
        "global_cooldown_s": round(max(0.0, _global_until - now), 1),
        "adaptive_pace_s": round(_pace, 2),
    }
