"""Console capture: stdout/stderr proxy and logging bridge.

Both paths funnel through ``_publish``, which writes to the rotating disk
log (storage) and the in-memory ring (state). Storage failures must never
propagate to the printing code; they degrade to a one-time notice written
directly to the original console stream.
"""

import logging
import re
import sys
import threading
import time

from . import state, storage

_DEDUPE_WINDOW = 0.05

_internal = threading.local()
_publish_lock = threading.RLock()
_last_publish = {"text": None, "via": None, "when": 0.0}
_last_record = None
_disk_failed = False
_original_streams = {"stdout": None, "stderr": None}
_attached_handlers: list = []
_logging_attached = False

ERROR_PATTERN = re.compile(r"\b(error|exception|traceback|critical)\b", re.IGNORECASE)
WARN_PATTERN = re.compile(r"\b(warn(?:ing)?|deprecat\w*)\b", re.IGNORECASE)
INFO_PATTERN = re.compile(r"(INFO|DEBUG)\b")


def classify(stream_name: str, text: str) -> str:
    """Best-effort level for one line of console text."""
    if ERROR_PATTERN.search(text):
        return "ERROR"
    if WARN_PATTERN.search(text):
        return "WARN"
    if INFO_PATTERN.match(text.lstrip()):
        return "INFO"
    return "STDOUT" if stream_name == "stdout" else "STDERR"


def _direct_write(text: str) -> None:
    """Write straight to the original console, bypassing our pipeline."""
    stream = _original_streams.get("stdout") or sys.__stdout__
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except Exception:
        pass


def _publish(level: str, text: str, source: str, via_logging: bool = False) -> None:
    global _disk_failed
    now = time.monotonic()
    with _publish_lock:
        last = _last_publish
        if (via_logging and last["via"] != "logging" and last["text"] == text
                and (now - last["when"]) < _DEDUPE_WINDOW):
            return
        last["text"] = text
        last["via"] = "logging" if via_logging else "stream"
        last["when"] = now
    line = state.build_line(level, text, source)
    try:
        storage.append(line)
        if _disk_failed:
            _disk_failed = False
            _direct_write("[console-node] disk log recovered\n")
    except Exception:
        if not _disk_failed:
            _disk_failed = True
            _direct_write("[console-node] disk log unavailable; continuing in memory only\n")
    state.enqueue(line)


class StreamProxy:
    """Duck-typed replacement for sys.stdout / sys.stderr.

    Writes pass through to the original stream (the real terminal keeps
    working) while complete lines are mirrored into the pipeline.
    """

    def __init__(self, original, stream_name: str):
        self._original = original
        self._stream_name = stream_name
        self._buffer = ""
        self._buffer_lock = threading.Lock()

    # --- io compatibility -------------------------------------------------
    def write(self, text):
        try:
            self._original.write(text)
        except Exception:
            pass
        self._feed(text)
        return len(text) if isinstance(text, str) else 0

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def writelines(self, lines):
        for chunk in lines:
            self.write(chunk)

    def isatty(self):
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def fileno(self):
        return self._original.fileno()

    def close(self):  # keep the real console open
        pass

    @property
    def closed(self):
        return False

    @property
    def encoding(self):
        return getattr(self._original, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._original, "errors", "replace")

    @property
    def buffer(self):
        return getattr(self._original, "buffer", None)

    # --- capture ----------------------------------------------------------
    def _feed(self, text):
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                return
        with self._buffer_lock:
            self._buffer += text
            while "\n" in self._buffer:
                raw, self._buffer = self._buffer.split("\n", 1)
                self._emit(raw)
            if "\r" in self._buffer:
                # Terminal overwrite semantics: keep only the final state.
                self._buffer = self._buffer.split("\r")[-1]

    def _emit(self, raw: str):
        text = raw.rstrip("\r")
        if "\r" in text:
            text = text.split("\r")[-1]
        source = "internal" if getattr(_internal, "active", False) else "external"
        _publish(classify(self._stream_name, text), text, source)


def install_proxy() -> bool:
    """Wrap sys.stdout/sys.stderr exactly once. Returns True when installed."""
    if state.is_proxy_installed():
        return False
    current_out, current_err = sys.stdout, sys.stderr
    if isinstance(current_out, StreamProxy) or isinstance(current_err, StreamProxy):
        state.mark_proxy_installed()
        return False
    _original_streams["stdout"] = current_out
    _original_streams["stderr"] = current_err
    sys.stdout = StreamProxy(current_out, "stdout")
    sys.stderr = StreamProxy(current_err, "stderr")
    state.mark_proxy_installed()
    return True


def emit_notice(text: str, level: str = "INFO") -> None:
    """Console-node diagnostic; tagged internal so viewers can hide it."""
    _internal.active = True
    try:
        stream = sys.stdout
        stream.write(text + "\n")
        stream.flush()
    except Exception:
        pass
    finally:
        _internal.active = False


def reset_for_tests() -> None:
    global _last_record, _disk_failed, _logging_attached
    with _publish_lock:
        _last_publish.update(text=None, via=None, when=0.0)
        _last_record = None
        _disk_failed = False
        _original_streams["stdout"] = None
        _original_streams["stderr"] = None
    _internal.active = False
    for logger, handler in list(_attached_handlers):
        try:
            logger.removeHandler(handler)
        except Exception:
            pass
    _attached_handlers.clear()
    _logging_attached = False


# --- logging bridge -------------------------------------------------------
# CaptureHandler / attach_logging are implemented in Task 6.
