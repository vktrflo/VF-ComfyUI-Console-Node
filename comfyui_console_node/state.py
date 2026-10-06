"""Process-wide ring buffer and SSE client fanout.

``enqueue`` may be called from any thread (anything that prints); deliveries
hop onto the aiohttp event loop via ``loop.call_soon_threadsafe``.
"""

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

from . import constants

_lock = threading.RLock()
_ring: deque = deque(maxlen=constants.BUFFER_DEFAULT)
_clients: List["Client"] = []
_loop: Optional[asyncio.AbstractEventLoop] = None
_proxy_installed = False
_ring_overflow_noticed = False

DROP_NOTICE = "console-node: viewer fell behind; oldest buffered lines were dropped"
OVERFLOW_NOTICE = "console-node: ring buffer is full; oldest lines are being dropped"


def build_line(level: str, text: str, source: str = "external", cr: bool = False) -> dict:
    """Create a LogLine dict: {ts, level, source, text, cr}.

    ``cr`` marks an in-place terminal update (progress bar): viewers replace
    the previous ``cr`` line; the disk log keeps only finished lines.
    """
    return {"ts": time.time(), "level": level, "source": source, "text": text, "cr": cr}


@dataclass
class Client:
    queue: "asyncio.Queue" = field(default_factory=lambda: asyncio.Queue(maxsize=constants.CLIENT_QUEUE_MAX))
    dropped: bool = False


def set_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _loop
    _loop = loop


def ring() -> list:
    with _lock:
        return list(_ring)


def register_client() -> "Client":
    client = Client()
    with _lock:
        _clients.append(client)
    return client


def unregister_client(client: "Client") -> None:
    with _lock:
        try:
            _clients.remove(client)
        except ValueError:
            pass


def client_count() -> int:
    with _lock:
        return len(_clients)


def enqueue(line: dict) -> None:
    global _ring_overflow_noticed
    notice = None
    with _lock:
        if (not _ring_overflow_noticed) and _ring.maxlen is not None and len(_ring) >= _ring.maxlen:
            _ring_overflow_noticed = True
            notice = build_line("WARN", OVERFLOW_NOTICE, source="internal")
        if _ring and _ring[-1].get("cr"):
            # An open in-place update is overwritten, mirroring the terminal.
            _ring[-1] = line
        else:
            _ring.append(line)
        clients = list(_clients)
    loop = _loop
    if loop is None or loop.is_closed():
        return
    for client in clients:
        try:
            if notice is not None:
                loop.call_soon_threadsafe(_deliver, client, notice)
            loop.call_soon_threadsafe(_deliver, client, line)
        except RuntimeError:
            pass


def _drop_oldest(queue: "asyncio.Queue") -> None:
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass


def _deliver(client: "Client", line: dict) -> None:
    """Runs on the event loop thread; never raises."""
    queue = client.queue
    if queue.full():
        _drop_oldest(queue)
        if not client.dropped:
            client.dropped = True
            try:
                queue.put_nowait(build_line("WARN", DROP_NOTICE, source="internal"))
            except asyncio.QueueFull:
                pass
    try:
        queue.put_nowait(line)
    except asyncio.QueueFull:
        _drop_oldest(queue)
        try:
            queue.put_nowait(line)
        except asyncio.QueueFull:
            pass


def is_proxy_installed() -> bool:
    return _proxy_installed


def mark_proxy_installed() -> None:
    global _proxy_installed
    _proxy_installed = True


def reset_for_tests(buffer_size: Optional[int] = None) -> None:
    """Restore a clean slate; used by the test suite only."""
    global _ring, _clients, _proxy_installed, _ring_overflow_noticed, _loop
    with _lock:
        _ring = deque(maxlen=buffer_size if buffer_size is not None else constants.BUFFER_DEFAULT)
        _clients = []
        _proxy_installed = False
        _ring_overflow_noticed = False
    _loop = None
