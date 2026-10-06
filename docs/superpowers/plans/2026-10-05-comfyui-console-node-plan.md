# ComfyUI Console Node — Implementation Plan

> **For Hermes:** Use the `subagent-driven-development` skill to implement this plan task-by-task. Fresh subagent per task, spec compliance review, then code quality review.

**Goal:** A ComfyUI custom-node pack (`ComfyUI-Console-Node`) that mirrors the ComfyUI Python server console (stdout/stderr + `logging`) into a filterable, live-updating viewer node on the canvas, with a rotating on-disk copy for post-mortem grep.

**Architecture:** One Python package (`comfyui_console_node/`) + one Lit/DOM-widget JS file. `capture.py` installs a `StreamProxy` over `sys.stdout`/`sys.stderr` and attaches a root-`logging` handler; both paths funnel lines through `_publish()` into `storage.py` (rotating file) and `state.py` (ring buffer + per-viewer asyncio queues). `routes.py` exposes one SSE endpoint (`GET /console_node/log/stream`) that replays the ring then streams new lines; `web/console_node.js` renders them in a `node.addDOMWidget` area. No workflow inputs/outputs (`is_output_node=True` terminal node, like PreviewImage).

**Tech Stack:** Python stdlib only (runtime), aiohttp (provided by ComfyUI), ComfyUI V3 node API (`comfy_api.latest`), plain-JS DOM widget (no build step). Dev: pytest + pytest-asyncio + aiohttp (for route tests).

**Repo:** `E:\projects\ComfyUI-Console-Node` — already `git init`ed (branch `main`); spec committed at `docs/superpowers/specs/2026-10-05-comfyui-console-node-design.md`.

**Environment notes (Windows, git-bash):**
- Shell is bash (MSYS). Native tools get forward-slash native paths (`E:/projects/...`), never `/e/...`.
- Python dir is `python` (3.14.x) on PATH; `python3` is missing. `uv` is installed — use it for the dev venv.
- Run tests as `.venv/Scripts/python.exe -m pytest -q` from the repo root.
- Do NOT import `folder_paths`, `server`, or `comfy_api` at module import time in any library module — only inside functions/try-blocks (keeps the package importable and testable without ComfyUI).

---

## Global Constraints (apply to every task)

1. Runtime dependencies: **stdlib only**. `aiohttp` may be imported (ComfyUI ships it) but add it to dev extras, not project deps.
2. Never let a logging/capture failure crash the caller: every write into the pipeline is wrapped so exceptions cannot propagate to the printing code.
3. All captured lines are dicts: `{"ts": float, "level": "INFO|WARN|ERROR|STDOUT|STDERR", "source": "internal|external", "text": str}` — single line, no trailing newline.
4. Our own diagnostics go through `capture.emit_notice(...)` (tagged `internal`) — never bare `print()` from our code.
5. Tests must pass without ComfyUI installed (comfy modules stubbed/monkeypatched).
6. Commit after every task. Commit message convention: `feat: ...` / `test: ...` / `chore: ...` / `docs: ...`.

---

### Task 1: Project scaffolding + dev venv

**Objective:** Create the project skeleton, dev venv with test deps, and a passing smoke test.

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md` (stub — full pass in Task 11)
- Create: `conftest.py`
- Create: `comfyui_console_node/__init__.py`
- Create: `tests/test_smoke.py`

**Step 1: Write the files**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "comfyui-console-node"
version = "0.1.0"
description = "Mirror the ComfyUI server console into a node on the canvas"
requires-python = ">=3.10"
dependencies = []
license = "MIT"

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "aiohttp"]

[tool.setuptools]
packages = ["comfyui_console_node"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
```

`LICENSE`: MIT license text, `Copyright (c) 2026 Console Node contributors`.

`README.md` (stub):
```markdown
# ComfyUI-Console-Node

Mirrors the ComfyUI server console into a node on the canvas. Full docs TBD (Task 11).
```

`conftest.py`:
```python
"""Make the repo root importable for tests regardless of invocation directory."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

`comfyui_console_node/__init__.py`:
```python
"""Console capture pipeline for the ComfyUI Console Node pack."""
```

`tests/test_smoke.py`:
```python
def test_package_imports():
    import comfyui_console_node  # noqa: F401
```

**Step 2: Create the venv and install dev deps**

Run:
```bash
cd E:/projects/ComfyUI-Console-Node
uv venv
uv pip install --python .venv/Scripts/python.exe pytest pytest-asyncio aiohttp
```
Expected: `.venv` created; 3 packages installed (plus transitive deps).

**Step 3: Run the smoke test**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `1 passed`.

**Step 4: Commit**

```bash
git add pyproject.toml .gitignore LICENSE README.md conftest.py comfyui_console_node/__init__.py tests/test_smoke.py
git commit -m "chore: project scaffolding and dev venv"
```

---

### Task 2: constants.py

**Objective:** Env-overridable defaults used across the pack.

**Files:**
- Create: `comfyui_console_node/constants.py`
- Test: `tests/test_constants.py`

**Step 1: Write the failing test**

`tests/test_constants.py`:
```python
import importlib

from comfyui_console_node import constants


def test_default_values_are_sane():
    assert constants.BUFFER_DEFAULT > 0
    assert constants.CLIENT_QUEUE_MAX > 0
    assert constants.ROTATE_BYTES > 0
    assert constants.MAX_BACKUPS >= 1
    assert constants.LOG_DIR_NAME == "console-node"
    assert constants.LOG_FILE_NAME == "console.log"
    assert constants.ROUTE_STREAM == "/console_node/log/stream"
    assert constants.HEARTBEAT_SECONDS > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("COMFYUI_CONSOLE_NODE_BUFFER", "123")
    reloaded = importlib.reload(constants)
    try:
        assert reloaded.BUFFER_DEFAULT == 123
    finally:
        monkeypatch.delenv("COMFYUI_CONSOLE_NODE_BUFFER")
        importlib.reload(constants)
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_constants.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `constants`.

**Step 3: Write the implementation**

`comfyui_console_node/constants.py`:
```python
"""Defaults for Console Node, overridable via environment variables."""

import os

from pathlib import Path


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BUFFER_DEFAULT = _int_env("COMFYUI_CONSOLE_NODE_BUFFER", 2000)
CLIENT_QUEUE_MAX = _int_env("COMFYUI_CONSOLE_NODE_CLIENT_QUEUE", 5000)
ROTATE_BYTES = _int_env("COMFYUI_CONSOLE_NODE_ROTATE_BYTES", 5 * 1024 * 1024)
MAX_BACKUPS = _int_env("COMFYUI_CONSOLE_NODE_MAX_BACKUPS", 3)
HIDE_INTERNAL = os.environ.get("COMFYUI_CONSOLE_NODE_HIDE_INTERNAL", "1").strip() != "0"

LOG_DIR_NAME = "console-node"
LOG_FILE_NAME = "console.log"

ROUTE_PREFIX = "/console_node"
ROUTE_STREAM = ROUTE_PREFIX + "/log/stream"
HEARTBEAT_SECONDS = 15

DEFAULT_DEPLOY_TARGET = Path("D:/VectorFlow/custom_nodes")
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_constants.py -q`
Expected: `2 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/constants.py tests/test_constants.py
git commit -m "feat: constants with env overrides"
```

---

### Task 3: state.py — ring buffer + SSE client fanout

**Objective:** Thread-safe process-wide state: bounded ring of log lines, one asyncio queue per connected viewer, cross-thread delivery.

**Files:**
- Create: `comfyui_console_node/state.py`
- Test: `tests/test_state.py`

**Step 1: Write the failing test**

`tests/test_state.py`:
```python
import asyncio

import pytest

from comfyui_console_node import state


@pytest.fixture(autouse=True)
def clean():
    state.reset_for_tests()
    yield
    state.reset_for_tests()


def test_build_line_fields():
    line = state.build_line("WARN", "hello", "internal")
    assert line["level"] == "WARN"
    assert line["text"] == "hello"
    assert line["source"] == "internal"
    assert isinstance(line["ts"], float)


def test_ring_is_bounded():
    state.reset_for_tests(buffer_size=3)
    for i in range(5):
        state.enqueue(state.build_line("INFO", f"line-{i}"))
    texts = [line["text"] for line in state.ring()]
    assert texts == ["line-2", "line-3", "line-4"]


def test_enqueue_without_loop_is_safe():
    state.enqueue(state.build_line("INFO", "no loop"))
    assert len(state.ring()) == 1


async def test_client_receives_lines():
    state.set_loop(asyncio.get_running_loop())
    client = state.register_client()
    try:
        state.enqueue(state.build_line("INFO", "one"))
        state.enqueue(state.build_line("ERROR", "two"))
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
        first = client.queue.get_nowait()
        second = client.queue.get_nowait()
        assert first["text"] == "one"
        assert second["text"] == "two"
        assert first["level"] == "INFO"
    finally:
        state.unregister_client(client)
    assert state.client_count() == 0


async def test_slow_client_drops_oldest_and_flags():
    state.set_loop(asyncio.get_running_loop())
    client = state.register_client()
    client.queue = asyncio.Queue(maxsize=2)
    try:
        for i in range(4):
            state.enqueue(state.build_line("INFO", f"line-{i}"))
        await asyncio.sleep(0)
        remaining = []
        while not client.queue.empty():
            remaining.append(client.queue.get_nowait()["text"])
        assert remaining == ["line-2", "line-3"]
        assert client.dropped is True
    finally:
        state.unregister_client(client)
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py -q`
Expected: FAIL — `ImportError` for `state`.

**Step 3: Write the implementation**

`comfyui_console_node/state.py`:
```python
"""Process-wide ring buffer and SSE client fanout.

`enqueue` may be called from any thread (anything that prints); deliveries
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


def build_line(level: str, text: str, source: str = "external") -> dict:
    """Create a LogLine dict: {ts, level, source, text}."""
    return {"ts": time.time(), "level": level, "source": source, "text": text}


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
    with _lock:
        if (not _ring_overflow_noticed) and _ring.maxlen is not None and len(_ring) >= _ring.maxlen:
            _ring_overflow_noticed = True
            _ring.append(build_line("WARN", OVERFLOW_NOTICE, source="internal"))
        _ring.append(line)
        clients = list(_clients)
    loop = _loop
    if loop is None or loop.is_closed():
        return
    for client in clients:
        try:
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
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py -q`
Expected: `5 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/state.py tests/test_state.py
git commit -m "feat: ring buffer and SSE client fanout state"
```

---

### Task 4: storage.py — rotating disk log

**Objective:** Human-readable rotating log at `<user_dir>/console-node/console.log`, 5 MB × 3 backups by default, failures raised to the caller (capture handles them).

**Files:**
- Create: `comfyui_console_node/storage.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

`tests/test_storage.py`:
```python
import pytest

from comfyui_console_node import constants, state, storage


@pytest.fixture(autouse=True)
def clean():
    yield
    storage.close()


def test_append_formats_line(tmp_path):
    storage.init(tmp_path)
    line = {"ts": 1728000000.0, "level": "ERROR", "source": "external", "text": "boom"}
    storage.append(line)
    content = (tmp_path / constants.LOG_FILE_NAME).read_text(encoding="utf-8")
    assert content.startswith("[")
    assert "[ERROR] boom" in content
    assert content.endswith("\n")


def test_init_creates_directories(tmp_path):
    target = tmp_path / "deep" / "nested"
    path = storage.init(target)
    assert path.exists()


def test_rotation_and_backup_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "ROTATE_BYTES", 120)
    monkeypatch.setattr(constants, "MAX_BACKUPS", 2)
    storage.init(tmp_path)
    for i in range(60):
        storage.append(state.build_line("INFO", f"row-{i:03d}"))
    names = sorted(p.name for p in tmp_path.iterdir())
    assert constants.LOG_FILE_NAME in names
    assert f"{constants.LOG_FILE_NAME}.1" in names
    assert f"{constants.LOG_FILE_NAME}.2" in names
    assert f"{constants.LOG_FILE_NAME}.3" not in names


def test_append_after_close_raises(tmp_path):
    storage.init(tmp_path)
    storage.close()
    with pytest.raises(RuntimeError):
        storage.append(state.build_line("INFO", "after close"))


def test_write_failure_propagates(tmp_path):
    storage.init(tmp_path)

    class BrokenHandle:
        def write(self, text):
            raise OSError("disk full")

        def flush(self):
            raise OSError("disk full")

        def close(self):
            pass

    storage._handle = BrokenHandle()
    with pytest.raises(OSError):
        storage.append(state.build_line("INFO", "will fail"))
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_storage.py -q`
Expected: FAIL — `ImportError` for `storage`.

**Step 3: Write the implementation**

`comfyui_console_node/storage.py`:
```python
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
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_storage.py -q`
Expected: `5 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/storage.py tests/test_storage.py
git commit -m "feat: rotating disk log storage"
```

---

### Task 5: capture.py — stdout/stderr proxy + publish pipeline

**Objective:** `StreamProxy` over `sys.stdout`/`sys.stderr` (idempotent install) that forwards to the original stream, splits complete lines, handles `\r` progress overwrites, classifies levels, and publishes into storage + state with disk-failure notices.

**Files:**
- Create: `comfyui_console_node/capture.py` (part 1 of 2 — logging handler comes in Task 6)
- Test: `tests/test_capture.py`

**Step 1: Write the failing test**

`tests/test_capture.py`:
```python
import io
import sys

import pytest

from comfyui_console_node import capture, state


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    yield
    state.reset_for_tests()
    capture.reset_for_tests()


def make_sink():
    seen = []
    def sink(level, text, source="external", via_logging=False):
        seen.append((level, text, source))
    return seen, sink


@pytest.fixture
def sink(monkeypatch):
    seen, recorder = make_sink()
    monkeypatch.setattr(capture, "_publish", recorder)
    return seen


@pytest.fixture
def installed(monkeypatch, sink):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert capture.install_proxy() is True
    return sink


@pytest.mark.parametrize(
    ("stream_name", "text", "expected"),
    [
        ("stdout", "Traceback (most recent call last):", "ERROR"),
        ("stdout", "Exception in thread main", "ERROR"),
        ("stdout", "WARNING: deprecated config option", "WARN"),
        ("stdout", "INFO: started server", "INFO"),
        ("stdout", "regular output", "STDOUT"),
        ("stderr", "no tokens here", "STDERR"),
    ],
)
def test_classify(stream_name, text, expected):
    assert capture.classify(stream_name, text) == expected


def test_proxy_buffers_and_emits_complete_lines(installed):
    sys.stdout.write("hello ")
    sys.stdout.write("world\nsecond")
    sys.stdout.write(" line\n")
    assert installed == [
        ("STDOUT", "hello world", "external"),
        ("STDOUT", "second line", "external"),
    ]


def test_proxy_passes_through_to_original(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    capture.install_proxy()
    sys.stdout.write("echo me\n")
    assert out.getvalue() == "echo me\n"


def test_install_is_idempotent(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert capture.install_proxy() is True
    first = sys.stdout
    assert isinstance(first, capture.StreamProxy)
    assert capture.install_proxy() is False
    assert sys.stdout is first


def test_carriage_returns_collapse_to_final_state(installed):
    sys.stdout.write("Loading 10%\rLoading 20%\rLoading 100%")
    assert installed == []
    sys.stdout.write("\n")
    assert installed == [("STDOUT", "Loading 100%", "external")]


def test_emit_notice_is_tagged_internal(installed):
    capture.emit_notice("console-node: hello")
    assert installed == [("STDOUT", "console-node: hello", "internal")]


def test_disk_failure_notices_once_then_recovers(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    direct = []
    monkeypatch.setattr(capture, "_direct_write", direct.append)
    failing = {"on": True}

    def flaky(line):
        if failing["on"]:
            raise OSError("disk full")

    monkeypatch.setattr(capture.storage, "append", flaky)
    capture._publish("STDOUT", "one", "external")
    capture._publish("STDOUT", "two", "external")
    assert len(direct) == 1
    assert "disk log unavailable" in direct[0]
    failing["on"] = False
    capture._publish("STDOUT", "three", "external")
    assert len(direct) == 2
    assert "recovered" in direct[1]


def test_publish_routes_to_storage_and_ring(monkeypatch, tmp_path):
    state.reset_for_tests()
    capture.reset_for_tests()
    from comfyui_console_node import storage
    storage.init(tmp_path)
    try:
        capture._publish("ERROR", "kaboom", "external")
        assert state.ring()[-1]["text"] == "kaboom"
        assert state.ring()[-1]["level"] == "ERROR"
        content = (tmp_path / "console.log").read_text(encoding="utf-8")
        assert "kaboom" in content
    finally:
        storage.close()
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_capture.py -q`
Expected: FAIL — `ImportError` for `capture`.

**Step 3: Write the implementation**

`comfyui_console_node/capture.py`:
```python
"""Console capture: stdout/stderr proxy and (Task 6) logging bridge.

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


# --- logging bridge (implemented fully in Task 6) -------------------------
_attached_handlers: list = []
_logging_attached = False
```

Note: `_logging_attached` and `_attached_handlers` are referenced by `reset_for_tests` — define them near the top of the module instead (before `reset_for_tests` runs), i.e. move the last three lines up next to `_disk_failed`. Keep module import order valid.

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_capture.py -q`
Expected: `14 passed` (6 classify params + 8 others).

**Step 5: Commit**

```bash
git add comfyui_console_node/capture.py tests/test_capture.py
git commit -m "feat: stdout/stderr proxy and publish pipeline"
```

---

### Task 6: capture.py — logging bridge

**Objective:** Attach a `logging.Handler` to the root logger and the `comfy` logger so `logging` output (which bypasses the stdout swap because ComfyUI binds its handlers at startup) is captured, deduped against the proxy path.

**Files:**
- Modify: `comfyui_console_node/capture.py` (replace the placeholder `# --- logging bridge ...` tail block)
- Test: `tests/test_logging_bridge.py`

**Step 1: Write the failing test**

`tests/test_logging_bridge.py`:
```python
import logging

import pytest

from comfyui_console_node import capture, state


@pytest.fixture(autouse=True)
def clean():
    state.reset_for_tests()
    capture.reset_for_tests()
    yield
    state.reset_for_tests()
    capture.reset_for_tests()


@pytest.fixture
def sink(monkeypatch):
    seen = []

    def recorder(level, text, source="external", via_logging=False):
        seen.append((level, text, source, via_logging))

    monkeypatch.setattr(capture, "_publish", recorder)
    return seen


def test_attach_is_idempotent(sink):
    assert capture.attach_logging() is True
    assert capture.attach_logging() is False


def test_records_are_captured_once_despite_two_loggers(sink):
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        assert capture.attach_logging() is True
        logging.getLogger("comfy").info("model loaded")
    finally:
        root.setLevel(old_level)
    assert sink == [("INFO", "model loaded", "external", True)]


def test_level_mapping(sink):
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        assert capture.attach_logging() is True
        logging.getLogger().debug("quiet debug")
        logging.getLogger().warning("careful")
        logging.getLogger().error("broken")
    finally:
        root.setLevel(old_level)
    levels = [entry[0] for entry in sink]
    assert levels == ["INFO", "WARN", "ERROR"]


def test_multiline_records_split_into_lines(sink):
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.ERROR)
    try:
        assert capture.attach_logging() is True
        logging.getLogger().error("first line\nsecond line")
    finally:
        root.setLevel(old_level)
    texts = [entry[1] for entry in sink]
    assert texts == ["first line", "second line"]
    assert all(entry[0] == "ERROR" for entry in sink)


def test_proxy_path_dedupe(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    published = []
    monkeypatch.setattr(capture.storage, "append", lambda line: published.append(line))
    capture._publish("STDOUT", "dup line", "external")
    capture._publish("STDOUT", "dup line", "external", via_logging=True)
    assert len(published) == 1
    capture._publish("INFO", "again", "external", via_logging=True)
    capture._publish("INFO", "again", "external", via_logging=True)
    assert len(published) == 3
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_logging_bridge.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'attach_logging'` (or `CaptureHandler`).

**Step 3: Write the implementation**

In `comfyui_console_node/capture.py`, replace the placeholder tail (`# --- logging bridge (implemented fully in Task 6) ---` and the two placeholder vars) with:

```python
# --- logging bridge -------------------------------------------------------

def _record_level(record: logging.LogRecord) -> str:
    if record.levelno >= logging.ERROR:
        return "ERROR"
    if record.levelno >= logging.WARNING:
        return "WARN"
    return "INFO"


class CaptureHandler(logging.Handler):
    """Funnels logging records into the console-node pipeline."""

    def emit(self, record: logging.LogRecord) -> None:
        global _last_record
        if record is _last_record:
            return  # same record delivered via more than one logger
        _last_record = record
        try:
            text = self.format(record)
        except Exception:
            return
        if not isinstance(text, str):
            return
        source = "internal" if getattr(_internal, "active", False) else "external"
        level = _record_level(record)
        for raw in text.split("\n"):
            raw = raw.rstrip("\r")
            if "\r" in raw:
                raw = raw.split("\r")[-1]
            _publish(level, raw, source, via_logging=True)


def attach_logging() -> bool:
    """Attach the capture handler to root and 'comfy' exactly once."""
    global _logging_attached
    with _publish_lock:
        if _logging_attached:
            return False
        _logging_attached = True
    handler = CaptureHandler(level=logging.DEBUG)
    for logger in (logging.getLogger(), logging.getLogger("comfy")):
        logger.addHandler(handler)
        _attached_handlers.append((logger, handler))
    return True
```

Also move the module-level vars `_attached_handlers: list = []` and `_logging_attached = False` up next to `_disk_failed` (so `reset_for_tests` can reference them regardless of definition order — module executes top-to-bottom; `reset_for_tests` only runs later, so leaving them at the bottom would also work, but keep them with the other globals for clarity).

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_logging_bridge.py tests/test_capture.py -q`
Expected: `19 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/capture.py tests/test_logging_bridge.py
git commit -m "feat: logging bridge with cross-path dedupe"
```

---

### Task 7: routes.py — SSE endpoint

**Objective:** `GET /console_node/log/stream` streams the ring as one `backlog` frame then one `line` frame per new line; `?filter=<regex>` filters; heartbeat keeps proxies happy; `register_routes()` is safe without ComfyUI.

**Files:**
- Create: `comfyui_console_node/routes.py`
- Test: `tests/test_routes.py`, `tests/test_routes_bootstrap.py`

**Step 1: Write the failing tests**

`tests/test_routes_bootstrap.py`:
```python
import importlib.util


def test_routes_import_without_comfyui():
    spec = importlib.util.find_spec("comfyui_console_node.routes")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.register_routes() is False  # no ComfyUI 'server' module here
```

`tests/test_routes.py`:
```python
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from comfyui_console_node import routes, state


@pytest.fixture(autouse=True)
def clean():
    state.reset_for_tests()
    yield
    state.reset_for_tests()


def make_app():
    table = web.RouteTableDef()
    assert routes.register_routes(table) is True
    app = web.Application()
    app.add_routes(table)
    return app


@pytest.fixture
async def client():
    server = TestServer(make_app())
    http = TestClient(server)
    await http.start_server()
    yield http
    await http.close()


async def read_chunk(response, timeout=2.0):
    return await asyncio.wait_for(response.content.read(65536), timeout)


async def test_backlog_replays_ring(client):
    state.enqueue(state.build_line("INFO", "alpha"))
    state.enqueue(state.build_line("ERROR", "beta"))
    response = await client.get("/console_node/log/stream")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    text = (await read_chunk(response)).decode("utf-8")
    assert "event: backlog" in text
    assert "alpha" in text and "beta" in text
    response.close()


async def test_backlog_filter(client):
    state.enqueue(state.build_line("INFO", "alpha"))
    state.enqueue(state.build_line("INFO", "beta"))
    response = await client.get("/console_node/log/stream?filter=alpha")
    text = (await read_chunk(response)).decode("utf-8")
    assert "alpha" in text
    assert "beta" not in text
    response.close()


async def test_invalid_filter_rejected(client):
    response = await client.get("/console_node/log/stream?filter=(")
    assert response.status == 400
    response.close()


async def test_live_lines_stream(client):
    response = await client.get("/console_node/log/stream")
    await read_chunk(response)  # backlog frame (empty)
    state.enqueue(state.build_line("WARN", "live-one"))
    text = (await read_chunk(response)).decode("utf-8")
    assert "event: line" in text
    assert "live-one" in text
    response.close()


async def test_client_removed_on_disconnect(client, monkeypatch):
    monkeypatch.setattr(routes, "HEARTBEAT_SECONDS", 0.05)
    response = await client.get("/console_node/log/stream")
    await read_chunk(response)
    assert state.client_count() == 1
    response.close()
    for _ in range(20):
        if state.client_count() == 0:
            break
        await asyncio.sleep(0.05)
    assert state.client_count() == 0
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_routes.py tests/test_routes_bootstrap.py -q`
Expected: FAIL — `ImportError` for `routes`.

**Step 3: Write the implementation**

`comfyui_console_node/routes.py`:
```python
"""aiohttp SSE endpoint that streams captured console lines to the canvas."""

import asyncio
import json
import re

from aiohttp import web

from . import state
from .constants import HEARTBEAT_SECONDS, ROUTE_STREAM


def _compile_filter(request) -> "re.Pattern | None":
    raw = request.query.get("filter")
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error as exc:
        raise web.HTTPBadRequest(text=f"Invalid filter regex: {exc}") from exc


def _matches(line: dict, pattern) -> bool:
    return pattern is None or pattern.search(line["text"]) is not None


async def _send_event(response, event: str, payload) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    await response.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))


async def _stream(request):
    pattern = _compile_filter(request)

    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    state.set_loop(asyncio.get_running_loop())

    client = state.register_client()
    try:
        backlog = [line for line in state.ring() if _matches(line, pattern)]
        await _send_event(response, "backlog", backlog)
        while True:
            try:
                line = await asyncio.wait_for(client.queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            if _matches(line, pattern):
                await _send_event(response, "line", line)
    except ConnectionResetError:
        pass
    finally:
        state.unregister_client(client)
    return response


def register_routes(routes=None) -> bool:
    """Attach the SSE route to ComfyUI's aiohttp app; False without a server."""
    if routes is None:
        try:
            from server import PromptServer
        except ImportError:
            return False
        routes = PromptServer.instance.routes

    @routes.get(ROUTE_STREAM)
    async def console_node_stream(request):
        return await _stream(request)

    return True
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_routes.py tests/test_routes_bootstrap.py -q`
Expected: `6 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/routes.py tests/test_routes.py tests/test_routes_bootstrap.py
git commit -m "feat: SSE log stream route"
```

---

### Task 8: bootstrap.py — startup wiring + history seed

**Objective:** `setup()` — used by the ComfyUI entrypoint — opens storage under the ComfyUI user dir, installs the proxy + logging handler, seeds the ring from `app.logger.get_logs()` (this fork keeps a rolling console deque; recovers lines printed before the pack loaded), records the running loop, registers routes, and emits a capture-started notice.

**Files:**
- Create: `comfyui_console_node/bootstrap.py`
- Test: `tests/test_bootstrap.py`

**Step 1: Write the failing test**

`tests/test_bootstrap.py`:
```python
import io
import sys
import types

import pytest

from comfyui_console_node import bootstrap, capture, state


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    state.reset_for_tests()
    capture.reset_for_tests()
    monkeypatch.setattr(bootstrap, "_seeded", False)
    monkeypatch.setattr(bootstrap, "resolve_user_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    yield tmp_path
    state.reset_for_tests()
    capture.reset_for_tests()


def test_setup_wires_pipeline(clean):
    bootstrap.setup()
    log_path = clean / "console-node" / "console.log"
    assert log_path.exists()
    assert isinstance(sys.stdout, capture.StreamProxy)
    content = log_path.read_text(encoding="utf-8")
    assert "capture started" in content


def test_setup_is_idempotent_for_proxy(clean):
    bootstrap.setup()
    wrapped = sys.stdout
    bootstrap.setup()
    assert sys.stdout is wrapped


def test_setup_recovers_pre_load_lines(clean, monkeypatch):
    fake_logger = types.ModuleType("app.logger")
    fake_logger.get_logs = lambda: [
        {"t": "2026-01-01T00:00:00", "m": "earlier line\nsecond line\n"}
    ]
    fake_app = types.ModuleType("app")
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.logger", fake_logger)
    bootstrap.setup()
    texts = [line["text"] for line in state.ring()]
    assert "earlier line" in texts
    assert "second line" in texts
    assert any("recovered 2 earlier console lines" in t for t in texts)


def test_setup_without_app_logger_still_starts(clean):
    bootstrap.setup()
    texts = [line["text"] for line in state.ring()]
    assert any("capture started" in t for t in texts)
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bootstrap.py -q`
Expected: FAIL — `ImportError` for `bootstrap`.

**Step 3: Write the implementation**

`comfyui_console_node/bootstrap.py`:
```python
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
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bootstrap.py -q`
Expected: `4 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: bootstrap wiring with pre-load history seed"
```

---

### Task 9: nodes.py + package entrypoint

**Objective:** V3 node `ConsoleLogViewer` (no inputs/outputs, `is_output_node=True`, category `utils/debug`), extension class, and the `comfy_entrypoint()` in the repo-root `__init__.py` that calls `bootstrap.setup()`.

**Files:**
- Create: `comfyui_console_node/nodes.py`
- Modify: `__init__.py` (repo root — currently does not exist; create)
- Test: `tests/test_nodes.py`

**Step 1: Write the failing test**

`tests/test_nodes.py`:
```python
import asyncio
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_fake_comfy_api():
    class ComfyNode:
        pass

    class ComfyExtension:
        async def get_node_list(self):
            return []

    class NodeOutput:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    def Schema(**fields):
        return types.SimpleNamespace(**fields)

    io = types.SimpleNamespace(ComfyNode=ComfyNode, Schema=Schema, NodeOutput=NodeOutput)
    latest = types.ModuleType("comfy_api.latest")
    latest.ComfyExtension = ComfyExtension
    latest.io = io
    package = types.ModuleType("comfy_api")
    package.latest = latest
    return package, latest


@pytest.fixture
def nodes_module(monkeypatch):
    package, latest = make_fake_comfy_api()
    monkeypatch.setitem(sys.modules, "comfy_api", package)
    monkeypatch.setitem(sys.modules, "comfy_api.latest", latest)
    sys.modules.pop("comfyui_console_node.nodes", None)
    module = importlib.import_module("comfyui_console_node.nodes")
    yield module, latest
    sys.modules.pop("comfyui_console_node.nodes", None)


def test_schema_shape(nodes_module):
    module, _ = nodes_module
    schema = module.ConsoleLogViewer.define_schema()
    assert schema.node_id == "ConsoleLogViewer"
    assert schema.display_name == "Console Log Viewer"
    assert schema.category == "utils/debug"
    assert schema.is_output_node is True
    assert schema.inputs == []
    assert schema.outputs == []


def test_execute_returns_empty_output(nodes_module):
    module, _ = nodes_module
    result = module.ConsoleLogViewer.execute()
    assert result.args == ()
    assert result.kwargs == {}


def test_extension_lists_node(nodes_module):
    module, latest = nodes_module
    extension = module.ConsoleNodeExtension()
    assert isinstance(extension, latest.ComfyExtension)
    node_list = asyncio.run(extension.get_node_list())
    assert node_list == [module.ConsoleLogViewer]


def test_entrypoint_shape(monkeypatch):
    package, latest = make_fake_comfy_api()
    monkeypatch.setitem(sys.modules, "comfy_api", package)
    monkeypatch.setitem(sys.modules, "comfy_api.latest", latest)
    spec = importlib.util.spec_from_file_location(
        "console_node_custom_node", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert callable(module.comfy_entrypoint)
    assert module.WEB_DIRECTORY == "./web"
```

**Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_nodes.py -q`
Expected: FAIL — no `comfyui_console_node/nodes.py`, no root `__init__.py`.

**Step 3: Write the implementation**

`comfyui_console_node/nodes.py`:
```python
"""ComfyUI V3 node: an on-canvas viewer for the server console log."""

from comfy_api.latest import ComfyExtension, io


class ConsoleLogViewer(io.ComfyNode):
    """UI-only terminal mirror; the widget streams over SSE independently."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ConsoleLogViewer",
            display_name="Console Log Viewer",
            category="utils/debug",
            inputs=[],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls):
        return io.NodeOutput()


class ConsoleNodeExtension(ComfyExtension):
    async def get_node_list(self):
        return [ConsoleLogViewer]
```

Repo-root `__init__.py`:
```python
"""ComfyUI entrypoint for the Console Node custom node pack."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from .comfyui_console_node.bootstrap import setup
    from .comfyui_console_node.nodes import ConsoleNodeExtension

    setup()
    return ConsoleNodeExtension()
```

**Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_nodes.py -q`
Expected: `4 passed`.

**Step 5: Commit**

```bash
git add comfyui_console_node/nodes.py __init__.py tests/test_nodes.py
git commit -m "feat: console viewer node and pack entrypoint"
```

---

### Task 10: web/console_node.js — canvas widget

**Objective:** DOM widget on `ConsoleLogViewer` nodes: live SSE subscription, level colors, timestamps (click to copy), filter input, level dropdown, pause/clear/follow toggles, internal-lines toggle, max-lines input, reconnect indicator.

**Files:**
- Create: `web/console_node.js`
- Test: `node --check` syntax validation (plus manual canvas smoke in Task 12)

**Step 1: Write the file**

`web/console_node.js`:
```javascript
import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

const NODE_NAME = 'ConsoleLogViewer';
const STREAM_PATH = '/console_node/log/stream';

const LEVEL_RANK = { ERROR: 3, WARN: 2, INFO: 1, STDOUT: 0, STDERR: 0 };
const THRESHOLDS = { ALL: -1, INFO: 1, WARN: 2, ERROR: 3 };

const STYLE = `
.cn-root { display:flex; flex-direction:column; width:100%; height:100%; font-family:ui-monospace, Consolas, monospace; font-size:11px; color:var(--fg-color,#ddd); }
.cn-bar { display:flex; gap:4px; padding:4px 4px 2px 4px; flex-wrap:wrap; align-items:center; }
.cn-bar input, .cn-bar select, .cn-bar button { font-size:11px; background:rgba(0,0,0,0.25); color:inherit; border:1px solid var(--border-color,#444); border-radius:4px; padding:1px 6px; }
.cn-bar button { cursor:pointer; }
.cn-bar button.on { background:#3f6f4f; color:#fff; }
.cn-log { flex:1; overflow-y:auto; padding:4px 6px; margin-top:2px; background:rgba(0,0,0,0.35); border-top:1px solid var(--border-color,#444); white-space:pre-wrap; word-break:break-all; }
.cn-line { display:flex; gap:6px; }
.cn-ts { color:#888; cursor:copy; flex:none; }
.cn-state { color:#888; font-size:10px; margin-left:auto; }
.cn-level-ERROR { color:#ff6b6b; }
.cn-level-WARN { color:#ffb84d; }
.cn-level-STDERR { color:#ffa07a; }
.cn-level-INFO { color:#9fc7ff; }
.cn-level-STDOUT { color:#cfcfcf; }
`;

function injectStyle() {
  if (document.getElementById('cn-style')) return;
  const el = document.createElement('style');
  el.id = 'cn-style';
  el.textContent = STYLE;
  document.head.appendChild(el);
}

function formatTime(ts) {
  try {
    return new Date(ts * 1000).toTimeString().slice(0, 8);
  } catch {
    return '--:--:--';
  }
}

function createConsoleView(node) {
  injectStyle();

  const root = document.createElement('div');
  root.className = 'cn-root';

  const bar = document.createElement('div');
  bar.className = 'cn-bar';

  const levelSelect = document.createElement('select');
  for (const key of Object.keys(THRESHOLDS)) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = key;
    levelSelect.appendChild(option);
  }
  levelSelect.value = 'ALL';

  const filterInput = document.createElement('input');
  filterInput.placeholder = 'filter (regex)';
  filterInput.style.width = '110px';

  const maxInput = document.createElement('input');
  maxInput.type = 'number';
  maxInput.min = '100';
  maxInput.max = '20000';
  maxInput.step = '100';
  maxInput.value = '2000';
  maxInput.style.width = '58px';
  maxInput.title = 'max lines held in the viewer';

  const internalBtn = document.createElement('button');
  internalBtn.textContent = 'int';
  internalBtn.title = 'show internal (console-node) lines';

  const pauseBtn = document.createElement('button');
  pauseBtn.textContent = 'pause';

  const followBtn = document.createElement('button');
  followBtn.textContent = 'follow';
  followBtn.classList.add('on');

  const clearBtn = document.createElement('button');
  clearBtn.textContent = 'clear';

  const stateLabel = document.createElement('span');
  stateLabel.className = 'cn-state';
  stateLabel.textContent = 'connecting…';

  bar.append(levelSelect, filterInput, maxInput, internalBtn, pauseBtn, followBtn, clearBtn, stateLabel);

  const logEl = document.createElement('div');
  logEl.className = 'cn-log';

  root.append(bar, logEl);

  const view = {
    buffer: [],
    maxLines: 2000,
    paused: false,
    follow: true,
    showInternal: false,
    threshold: THRESHOLDS.ALL,
    regex: null,
    es: null,
    root,
    logEl,
  };

  const passes = (line) => {
    if (!view.showInternal && line.source === 'internal') return false;
    if ((LEVEL_RANK[line.level] ?? 0) < view.threshold) return false;
    if (view.regex && !view.regex.test(line.text)) return false;
    return true;
  };

  const buildLine = (line) => {
    const row = document.createElement('div');
    row.className = 'cn-line';
    const ts = document.createElement('span');
    ts.className = 'cn-ts';
    ts.textContent = formatTime(line.ts);
    ts.title = 'click to copy line';
    ts.addEventListener('click', () => {
      const text = `[${formatTime(line.ts)}] ${line.text}`;
      navigator.clipboard?.writeText(text);
    });
    const body = document.createElement('span');
    body.className = 'cn-level-' + (line.level || 'STDOUT');
    body.textContent = line.text;
    row.append(ts, body);
    return row;
  };

  const stick = () => {
    if (view.follow) logEl.scrollTop = logEl.scrollHeight;
  };

  const trim = () => {
    while (view.buffer.length > view.maxLines) view.buffer.shift();
    while (logEl.childElementCount > view.maxLines) logEl.removeChild(logEl.firstChild);
  };

  const appendLine = (line) => {
    view.buffer.push(line);
    trim();
    if (view.paused || !passes(line)) return;
    logEl.appendChild(buildLine(line));
    stick();
  };

  const rebuild = () => {
    logEl.replaceChildren();
    for (const line of view.buffer) {
      if (passes(line)) logEl.appendChild(buildLine(line));
    }
    stick();
  };

  levelSelect.onchange = () => {
    view.threshold = THRESHOLDS[levelSelect.value] ?? -1;
    rebuild();
  };
  filterInput.oninput = () => {
    const source = filterInput.value.trim();
    try {
      view.regex = source ? new RegExp(source, 'i') : null;
      filterInput.style.borderColor = 'var(--border-color,#444)';
    } catch {
      view.regex = null;
      filterInput.style.borderColor = '#f66';
    }
    rebuild();
  };
  maxInput.onchange = () => {
    const value = parseInt(maxInput.value, 10);
    view.maxLines = Number.isFinite(value) ? Math.max(100, Math.min(20000, value)) : 2000;
    maxInput.value = String(view.maxLines);
    trim();
    rebuild();
  };
  internalBtn.onclick = () => {
    view.showInternal = !view.showInternal;
    internalBtn.classList.toggle('on', view.showInternal);
    rebuild();
  };
  pauseBtn.onclick = () => {
    view.paused = !view.paused;
    pauseBtn.classList.toggle('on', view.paused);
    if (!view.paused) rebuild();
  };
  followBtn.onclick = () => {
    view.follow = !view.follow;
    followBtn.classList.toggle('on', view.follow);
    stick();
  };
  clearBtn.onclick = () => {
    view.buffer = [];
    logEl.replaceChildren();
  };

  const connect = () => {
    try { view.es?.close(); } catch { /* ignore */ }
    const es = new EventSource(api.apiURL(STREAM_PATH));
    view.es = es;
    es.onopen = () => { stateLabel.textContent = 'live'; };
    es.onerror = () => { stateLabel.textContent = 'reconnecting…'; };
    es.addEventListener('backlog', (event) => {
      let lines = [];
      try { lines = JSON.parse(event.data); } catch { return; }
      if (!Array.isArray(lines)) return;
      view.buffer = lines;
      trim();
      rebuild();
    });
    es.addEventListener('line', (event) => {
      let line = null;
      try { line = JSON.parse(event.data); } catch { return; }
      if (line) appendLine(line);
    });
  };

  view.destroy = () => {
    try { view.es?.close(); } catch { /* ignore */ }
  };

  connect();
  return view;
}

app.registerExtension({
  name: 'ComfyUI.ConsoleNode',
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_NAME) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      const view = createConsoleView(this);
      const widget = this.addDOMWidget('console_view', 'CONSOLE_VIEW', view.root, { serialize: false });
      widget.computeSize = () => [this.size[0] - 16, Math.max(160, this.size[1] - 70)];
      const removed = this.onRemoved;
      this.onRemoved = function () {
        view.destroy();
        return removed?.apply(this, arguments);
      };
      this.setSize([Math.max(this.size[0] ?? 0, 460), Math.max(this.size[1] ?? 0, 520)]);
      return result;
    };
  },
});
```

**Step 2: Validate syntax**

Run: `node --check web/console_node.js`
Expected: no output, exit 0. (If `node` is missing, note it and continue — syntax will be exercised at canvas smoke in Task 12.)

**Step 3: Commit**

```bash
git add web/console_node.js
git commit -m "feat: canvas console widget with live SSE rendering"
```

---

### Task 11: tools/deploy.py + README

**Objective:** Deploy helper (copy pack into custom_nodes targets) and real README.

**Files:**
- Create: `tools/deploy.py`
- Modify: `README.md`

**Step 1: Write the files**

`tools/deploy.py`:
```python
"""Copy the pack into ComfyUI custom_nodes directories.

Usage: python tools/deploy.py [target_dir ...]
Defaults to the VectorFlow custom_nodes directory used by the local setup.
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK_NAME = "ComfyUI-Console-Node"
SKIP_NAMES = {"__pycache__", ".git", ".venv", "docs", "tests", "tools", ".pytest_cache"}
DEFAULT_TARGETS = [Path("D:/VectorFlow/custom_nodes")]


def deploy(target: Path) -> Path:
    destination = Path(target) / PACK_NAME
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(REPO.iterdir()):
        if item.name in SKIP_NAMES:
            continue
        if item.is_dir():
            shutil.copytree(
                item,
                destination / item.name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        else:
            shutil.copy2(item, destination / item.name)
    return destination


def main(argv):
    targets = [Path(arg) for arg in argv] or DEFAULT_TARGETS
    for target in targets:
        print(f"deployed -> {deploy(target)}")


if __name__ == "__main__":
    main(sys.argv[1:])
```

`README.md` (replace stub — keep it factual, ~60 lines):
```markdown
# ComfyUI-Console-Node

Mirror the ComfyUI server console (stdout/stderr + `logging`) into a node on
the canvas. Filter it, color-code it by level, pause/clear it, copy lines —
and keep a rotating copy on disk for grep.

## Install

Copy this repository into `<ComfyUI>/custom_nodes/ComfyUI-Console-Node` (or
run `python tools/deploy.py [target]`, default target
`D:/VectorFlow/custom_nodes`). Restart ComfyUI.

## Use

Add node → `utils/debug` → **Console Log Viewer**. Drop it anywhere on the
canvas; it does not need to be connected to anything.

- `ALL / INFO / WARN / ERROR` — level filter
- filter box — case-insensitive regex applied to line text
- `int` — show internal console-node lines (hidden by default)
- `pause` — stop rendering (server keeps buffering)
- `follow` — auto-scroll to newest line
- `clear` — empty the viewer
- click a timestamp — copy that line to the clipboard

## Disk log

`<ComfyUI user dir>/console-node/console.log` — plain text
(`[YYYY-MM-DD HH:MM:SS] [LEVEL] text`), rotates at 5 MB keeping 3 backups.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `COMFYUI_CONSOLE_NODE_BUFFER` | 2000 | ring buffer size (lines) |
| `COMFYUI_CONSOLE_NODE_CLIENT_QUEUE` | 5000 | per-viewer queue size (lines) |
| `COMFYUI_CONSOLE_NODE_ROTATE_BYTES` | 5242880 | rotation threshold |
| `COMFYUI_CONSOLE_NODE_MAX_BACKUPS` | 3 | rotated files kept |
| `COMFYUI_CONSOLE_NODE_HIDE_INTERNAL` | 1 | hide internal lines by default |

## Limitations

- Lines printed before this pack loads are only recovered on ComfyUI builds
  that keep a console backlog (`app.logger.get_logs()`, small-desktop builds).
- Writes via `stream.buffer` (binary) are not captured.
- `contextlib.redirect_stdout` temporarily bypasses capture.

## Development

```bash
uv venv
uv pip install --python .venv/Scripts/python.exe pytest pytest-asyncio aiohttp
.venv/Scripts/python.exe -m pytest -q
```
```

**Step 2: Verify deploy works**

Run: `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'tools'); import deploy; print(deploy.REPO)"`
Expected: prints the repo path. (Full deploy happens in Task 12.)

**Step 3: Commit**

```bash
git add tools/deploy.py README.md
git commit -m "chore: deploy helper and README"
```

---

### Task 12: Full suite, deploy, and smoke checklist

**Objective:** Whole suite green, package deployed to the live ComfyUI, manual smoke steps executed/recorded.

**Files:**
- Modify: none (verification task)

**Step 1: Full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (approx. `55+ passed`).

**Step 2: Deploy**

Run: `.venv/Scripts/python.exe tools/deploy.py`
Expected: `deployed -> D:\VectorFlow\custom_nodes\ComfyUI-Console-Node`.
Verify the deployed folder contains `__init__.py`, `comfyui_console_node/`, `web/`, `pyproject.toml`.

**Step 3: ComfyUI restart + canvas smoke (manual / browser-assisted)**

Restart ComfyUI (custom nodes load at startup). Then:

1. Canvas → Add Node → `utils/debug` → **Console Log Viewer** appears.
2. Drop it; the panel shows `live` and a backlog of recent console lines.
3. Run any workflow (or queue the viewer node itself); confirm new lines
   appear in ~200 ms, errors render red, `stream` label returns to `live`
   after a server restart (reconnect + backlog replay).
4. Filter box `dram` narrows the view; `pause` freezes; `clear` empties;
   timestamp click copies a line.
5. Confirm `D:/VectorFlow/user/console-node/console.log` grows while lines stream.
6. `int` toggle reveals console-node internal lines.

Record results (pass/fail + notes) in the task report. If ComfyUI is running
at `10.0.0.164:8188` and browser automation is available, steps 1–2 can be
verified via the browser tool; steps 3–6 need a real queue cycle.

**Step 4: Final commit (only if anything changed during smoke)**

```bash
git status
# if clean, nothing to commit — done
```

---

## Execution notes for subagents

- Read the spec (`docs/superpowers/specs/2026-10-05-comfyui-console-node-design.md`) plus this plan before starting; both live in the repo.
- Task order matters: Tasks 3–6 build state → storage → capture; Task 7–8 depend on both; Task 9 depends on 8; Task 10 is standalone JS; Task 11–12 finish.
- Run tests from the repo root with `.venv/Scripts/python.exe -m pytest -q`; every task's "Run to verify" step defines done.
- Do not add runtime dependencies; do not import ComfyUI modules (`server`, `folder_paths`, `comfy_api`) at top level outside `nodes.py`/`bootstrap.py`'s guarded imports.
- Keep code and tests exactly consistent with this plan; if a deviation is genuinely required, note it in the task report.

---

## Inline-execution deviations (recorded 2026-10-05)

Implemented inline (not via subagents). Three deviations from the code above; the repository code is authoritative:

1. **`state.enqueue` overflow notice** (Task 3): the one-time `WARN` for ring overflow is now delivered to live viewers only — it is no longer appended into the ring (appending it evicted one real line and polluted the backlog on any >2000-line session).
2. **pytest capture interaction** (Tasks 5/8): pytest restores `sys.stdout`/`sys.stderr` at the setup→call phase boundary, so stream swaps made inside fixtures never reach the test body. `tests/test_capture.py` and `tests/test_bootstrap.py` therefore swap streams in the test body via `swap_streams()` / `install_test_proxy()` helpers.
3. **Pass-through test** (Task 5): `test_proxy_passes_through_to_original` stubs `storage.append` so the one-time "disk log unavailable" notice cannot leak into the asserted stream.
4. **Progress bars stream live** (post-smoke fix): `StreamProxy` emits `\r` segments immediately with `cr=True`; `state.enqueue` replaces the previous open `cr` ring entry; `storage.append` skips `cr` lines; the widget replaces the open row in place. ANSI codes stripped at capture. Verified against the live server: tqdm sampling bars update during execution, no longer only at 100%. Follow-up: open `\r` states now emit on every write with per-state dedupe (zero lag, covering `\r`-first tqdm writes and `\r`-last writes) — the first cut emitted each state one refresh late.
5. **Widget sizing** (post-smoke fix): the `computeSize` override was removed — it made the widget fixed-height and left a ~64 px gap at the bottom. The DOM widget is now growable (`getMinHeight: () => 160`), filling remaining node height via the frontend's `distributeSpace` layout (semantics confirmed from frontend 1.39.19 source maps).
6. **Deploy target moved** (post-smoke): the SMALL_DESKTOP instance's custom_nodes directory is now `E:\comfyui_instances\SMALL_DESKTOP\ComfyUI\custom_nodes` (was `D:\VectorFlow\custom_nodes`); `tools/deploy.py`, `README.md`, `constants.py`, and the spec were updated. Earlier paths in this plan are historical.

### Task 12 status

- Full suite: **50 passed**.
- Deployed to `D:\VectorFlow\custom_nodes\ComfyUI-Console-Node` and verified on disk.
- Canvas smoke found two live issues (fixed as deviations 4–5); re-verify after the next ComfyUI restart + browser reload.
