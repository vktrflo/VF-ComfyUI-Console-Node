"""One-shot startup wiring: storage, capture, seed, loop, routes."""

import asyncio
from pathlib import Path

from . import capture, state, storage
from .constants import LOG_DIR_NAME

_seeded = False


def resolve_user_dir() -> Path:
    """ComfyUI user directory (where server/user data lives)."""
    try:
        from folder_paths import get_user_directory

        directory = get_user_directory()
        if directory:
            return Path(directory)
    except Exception:
        pass
    return Path.home()


def _seed_from_app_logger() -> int:
    """Recover console lines printed before this pack loaded.

    ComfyUI (small-desktop builds) keeps a rolling deque of recent console
    output in ``app.logger``; seed the ring with it so early startup lines
    are visible on the canvas. Silently a no-op elsewhere.
    """
    try:
        from app import logger as app_logger

        entries = list(app_logger.get_logs() or [])
    except Exception:
        return 0
    recovered = 0
    for entry in entries:
        try:
            chunk = entry.get("m", "")
        except AttributeError:
            continue
        for raw in str(chunk).split("\n"):
            text = raw.rstrip("\r")
            if "\r" in text:
                text = text.split("\r")[-1]
            if not text:
                continue
            line = state.build_line(capture.classify("stdout", text), text)
            try:
                storage.append(line)
            except Exception:
                pass
            state.enqueue(line)
            recovered += 1
    return recovered


def setup() -> None:
    """Wire everything; safe to call more than once."""
    global _seeded
    capture.install_proxy()
    try:
        storage.init(resolve_user_dir() / LOG_DIR_NAME)
    except Exception as exc:
        capture.emit_notice(f"console-node: could not open disk log: {exc}", level="WARN")
    capture.attach_logging()

    recovered = 0
    if not _seeded:
        recovered = _seed_from_app_logger()
        _seeded = True

    try:
        state.set_loop(asyncio.get_running_loop())
    except RuntimeError:
        pass

    from . import routes

    if not routes.register_routes():
        capture.emit_notice("console-node: ComfyUI server not reachable; SSE endpoint not registered", level="WARN")

    if recovered:
        capture.emit_notice(f"console-node: capture started; recovered {recovered} earlier console lines")
    else:
        capture.emit_notice("console-node: capture started")
