"""Rotating plain-text log file; grep-able history outside the canvas.

Callers (capture.py) treat any exception from ``append`` as a transient
failure and keep the in-memory pipeline alive.
"""

import threading
import time
from pathlib import Path

from . import constants

_lock = threading.RLock()
_path = None
_handle = None
_written = 0


def init(directory) -> Path:
    """(Re)open the log file inside ``directory`` (created if missing)."""
    global _path, _handle, _written
    with _lock:
        if _handle is not None:
            _handle.close()
            _handle = None
        _path = Path(directory)
        _path.mkdir(parents=True, exist_ok=True)
        _path = _path / constants.LOG_FILE_NAME
        _handle = _open()
        try:
            _written = _path.stat().st_size
        except OSError:
            _written = 0
        return _path


def path() -> "Path | None":
    return _path


def close() -> None:
    global _handle
    with _lock:
        if _handle is not None:
            _handle.close()
            _handle = None


def _open():
    return open(_path, "a", encoding="utf-8", errors="replace", newline="\n")


def format_line(line: dict) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(line["ts"]))
    return f"[{stamp}] [{line['level']}] {line['text']}"


def append(line: dict) -> None:
    global _written
    with _lock:
        if line.get("cr"):
            # In-place progress updates are not persisted; the finished line
            # (or the next completed line) is what the disk log keeps.
            return
        if _handle is None:
            raise RuntimeError("console-node storage is not initialized")
        text = format_line(line) + "\n"
        _handle.write(text)
        _handle.flush()
        _written += len(text.encode("utf-8", "replace"))
        if constants.ROTATE_BYTES > 0 and _written >= constants.ROTATE_BYTES:
            _rotate()


def _rotate() -> None:
    global _handle, _written
    _handle.close()
    _handle = None
    backups = max(int(constants.MAX_BACKUPS), 1)
    for index in range(backups - 1, 0, -1):
        candidate = _path.with_name(f"{_path.name}.{index}")
        if candidate.exists():
            candidate.replace(_path.with_name(f"{_path.name}.{index + 1}"))
    if _path.exists():
        _path.replace(_path.with_name(f"{_path.name}.1"))
    _handle = _open()
    _written = 0
