# ComfyUI Console Node — Design Spec

**Date:** 2026-10-05
**Target repo:** `E:\projects\ComfyUI-Console-Node`
**Status:** Approved design; plan written at `docs/superpowers/plans/2026-10-05-comfyui-console-node-plan.md`

**Revision 1.1 (2026-10-05):** Capture design amended during plan-stage research against the live ComfyUI install:
- ComfyUI replaces `sys.stdout`/`sys.stderr` with its own `LogInterceptor`s and binds `logging` handlers at startup — **before** any custom node loads. The stdout proxy alone would therefore miss all `logging` output (model loads, warnings, "Prompt executed", …), so capture also attaches a handler to the root and `comfy` loggers; exact duplicates across the two paths are suppressed.
- Console lines printed before this pack loads are recovered at startup from `app.logger.get_logs()` when the running ComfyUI provides it (small-desktop builds keep a rolling console deque); otherwise the seed step is a silent no-op.
- `\r` progress chunks collapse to their final overwrite state, matching how the terminal displays them.
- The node/extension use the V3 API (`comfy_api.latest`), the same mechanism as the working `ComfyUI-Prompt-Cast` pack.

**Revision 1.2 (2026-10-05):** Live-testing fixes (smoke run on the real server):
- `\r` in-place updates (tqdm progress bars) are now streamed **live** with a `cr` flag instead of being collapsed until the next newline. Viewers replace the previous open `cr` line (terminal overwrite semantics); the ring applies the same replace rule; the disk log keeps only finished lines. This is what made sampling progress invisible until 100% before.
- ANSI escape sequences (colors, OSC links) are stripped from captured text.
- The canvas widget is a **growable** DOM widget (no `computeSize` override; `getMinHeight` option) so it fills the node exactly — the earlier fixed height left a gap at the bottom of the node.

## Purpose

A ComfyUI custom-node pack that mirrors the ComfyUI Python server's stdout/stderr to a node on the canvas. The node is a debugging display: filterable, level-colored, with a small rotating on-disk log so users can `tail` history outside the canvas. UI-only — no workflow outputs.

## Goals

- Capture **everything** the ComfyUI Python server prints (startup banner through end of session, idle + execution).
- Stream new lines live to the node with auto-scroll; reconnect-safe.
- Provide a user-configurable filter (regex/substring) and level filter (INFO/WARN/ERROR/ALL).
- Persist all lines to a rotating file under `ComfyUI/user/console-node/console.log` so history is recoverable from disk.
- Be invisible when normal: capture failures must never crash ComfyUI; proxy install must be idempotent under node hot-reload.

## Non-Goals

- Browser DevTools / frontend JS console capture. (Server terminal only.)
- Persisting or wiring log content into the workflow as a STRING output.
- Cross-process or cross-machine log aggregation.
- Log rotation policies beyond a simple size-based ring (no compression, no archival).
- In-canvas editing or searching across more than the in-memory buffer.

## Architecture

Three components in one Python package, deployed under `D:/VectorFlow/custom_nodes/ComfyUI-Console-Node` (and the ComfyUI portable install per the user's standing deploy convention).

1. **`capture.py` — StreamProxy + logging bridge + level classification**
   - Installs Python `StreamProxy` wrappers over `sys.stdout` and `sys.stderr` exactly once (idempotent via module-global `_installed`).
   - Attaches a `CaptureHandler` to the root and `comfy` loggers exactly once, because ComfyUI's own logging handlers bind their streams before this pack loads and would otherwise bypass the proxy entirely.
   - Buffers partial writes until newline; `\r` segments collapse to their final overwrite state (progress-bar semantics); classifies level from tokens (`ERROR`, `WARNING`, `WARN`, `Traceback`, `INFO`) falling back to the originating stream (stderr → STDERR, stdout → STDOUT).
   - Tags internal lines (`source="internal"`) when the pack itself emitted them so the widget can hide them by default.
   - Hands each parsed line to `state.enqueue(line)` and `storage.append(line)` via a single `_publish()` choke point; exact duplicates arriving from both capture paths within 50 ms are suppressed.

2. **`storage.py` — rotating file writer**
   - Opens `<ComfyUI user dir>/console-node/console.log` on first use; creates the directory.
   - Rotates at 5 MB; keeps `console.log.1`, `.2`, `.3`; deletes oldest on next rotation.
   - On any write failure, falls back to silent drops and emits one `ERROR` line via the **original** stdout (held on the proxy instance) — never recurses.

3. **`routes.py` — SSE endpoint**
   - `GET /console_node/log/stream` returns `text/event-stream`.
   - On connect: sends `event: backlog` with the current in-memory ring (last 2000 lines by default, configurable via env var `COMFYUI_CONSOLE_NODE_BUFFER`).
   - Then sends one `event: line` per new line as it arrives.
   - Honors optional `?filter=<regex>` on the backlog (validates regex; 400 on bad pattern).
   - Auto-disconnects on `ConnectionResetError`/`CancelledError`.

4. **`web/console_node.js` — Lit widget**
   - Lit element that wraps the node body with a scrollable console region.
   - Subscribes via `EventSource`; auto-reconnects with exponential backoff.
   - UI controls: filter (regex/substring), level filter (ALL/INFO/WARN/ERROR), pause, clear, auto-scroll toggle.
   - Color-codes lines by level; clickable timestamps copy the line text to clipboard.
   - Default hides internal-source lines; user can toggle "Show internal" in the widget menu.

5. **`nodes.py` — `ConsoleLogViewer` (V3 API)**
   - `io.Schema(node_id="ConsoleLogViewer", display_name="Console Log Viewer", category="utils/debug", inputs=[], outputs=[], is_output_node=True)`
   - `execute()` is a no-op returning `io.NodeOutput()`; the widget streams over SSE independently of execution.
   - `ConsoleNodeExtension(ComfyExtension)` returns `[ConsoleLogViewer]`; the pack uses the same `comfy_entrypoint()` mechanism as `ComfyUI-Prompt-Cast`.

## Module state ownership

`state.py` is the single source of truth:

- `_ring: deque[LogLine]` — bounded ring, default 2000 entries, configurable via env var.
- `_clients: list[asyncio.Queue]` — one per viewer; SSE response reads from it.
- `_proxy_installed: bool` — idempotency guard.
- `_lock: threading.RLock` — guards ring append + fanout enqueue so StreamProxy (called from arbitrary threads) can write safely while the asyncio loop drains client queues.
- `register_routes(app)` — called from `comfy_entrypoint()`; receives the live aiohttp app so cross-thread `run_coroutine_threadsafe` has the loop handle.

## Data types

```python
from typing import Literal, Optional, TypedDict

LogLevel = Literal["INFO", "WARN", "ERROR", "STDOUT", "STDERR"]
LogSource = Literal["internal", "external"]

class LogLine(TypedDict):
    ts: float          # epoch seconds, level monotonic per system clock
    level: LogLevel
    source: LogSource
    text: str          # single line, no trailing newline
    cr: bool           # True when this is an in-place update (progress bar)

LevelFilter = Literal["ALL", "INFO", "WARN", "ERROR"]

class LineFilter(TypedDict):
    level_min: LevelFilter   # "ALL" shows everything; "INFO" shows all; "WARN" shows WARN+ERROR; "ERROR" shows ERROR only
    regex: Optional[re.Pattern]   # applied to text; None means accept-all
```

## Data flow

### Startup

1. ComfyUI imports `ComfyUI-Console-Node/__init__.py` → calls `comfy_entrypoint()`.
2. `comfy_entrypoint()` → `bootstrap.setup()`:
   - `capture.install_proxy()` — wraps stdout/stderr exactly once, chaining over ComfyUI's own `LogInterceptor`s.
   - `storage.init(<user_dir>/console-node)` — opens the rotating file.
   - `capture.attach_logging()` — attaches the root + `comfy` logger handler.
   - seed — replays `app.logger.get_logs()` (when present) into the ring + disk so pre-load lines are visible.
   - `state.set_loop(...)` — records the running loop for cross-thread fanout.
   - `routes.register_routes()` — attaches the SSE handler to `PromptServer.instance.routes`.
   - emits an internal "capture started" notice.
3. Browser connects to `/console_node/log/stream` → server sends buffered ring as `event: backlog`, then pushes `event: line` per new line.

### Per-line (steady state)

1. `print(...)` writes hit the `StreamProxy`; `logging` records hit the `CaptureHandler` (ComfyUI's own logging handlers pre-date the proxy and bypass it). Diagnostics from the pack itself are tagged `source="internal"`.
2. Both paths normalize the text (buffer until newline; `\r` collapses to its final overwrite) and classify the level.
3. `_publish()` appends the line to the rotating file — internal-source lines **are** persisted to disk for completeness; only the widget hides them by default.
4. `_publish()` appends to the ring and fans out to every viewer queue: oldest evicted on ring overflow; a full viewer queue drops its oldest pending line once, then stays silent.
5. Each connected SSE handler drains its viewer queue and writes `event: line\ndata: <json>\n\n` to its response.

## Error handling

- **Ring overflow**: ring trims oldest (no synthetic entries in the ring); a one-time `WARN` line is delivered to live viewers instead.
- **Client disconnect mid-stream**: SSE handler catches `ConnectionResetError`/`CancelledError`, removes the queue from `_clients`, closes the response. Reconnects auto-replay the ring.
- **Write to disk fails** (full disk, perms): proxy logs one `ERROR` via the original stdout (never recurses), then drops disk writes silently until next successful write. ComfyUI is never crashed by a logging failure.
- **Filter regex invalid on subscribe**: server returns `400`; widget shows red badge on filter input.
- **Multiple `ConsoleLogViewer` nodes**: each subscribes independently; line fanout is shared (one enqueue, N recipients).
- **Reload safety**: `install_proxy()` checks `_proxy_installed` and reuses the existing wrapper if present, so ComfyUI's node hot-reload never stacks stdout calls. `attach_logging()` and the pre-load seed are likewise once-only.
- **Duplicate capture**: if a logging handler is ever created after the proxy is installed, its output would pass both capture paths; an exact-duplicate guard (same text from the non-logging path within 50 ms) suppresses the second copy. A single record delivered to the handler via multiple loggers is deduped by record identity.
- **Pre-load history**: recovered only on ComfyUI builds that keep `app.logger.get_logs()`; otherwise capture starts at pack load and the internal "capture started" notice marks the boundary.
- **Internal-source noise**: proxy tags its own diagnostic lines `source="internal"`; widget hides them by default; togglable via "Show internal" menu item. Internal-source lines are still persisted to the on-disk log.
- **Pause**: pause is implemented client-side in the widget only — the server keeps writing the ring, fanning out, and persisting to disk. The viewer simply stops rendering incoming lines until unpaused. Pause is per-viewer, not global.
- **Level filter**: the widget's level dropdown accepts `ALL` (show every level regardless of `level_min`) in addition to the four log levels. The server-side backlog filter accepts only real levels — `ALL` is a client-only convenience applied after replay.

## Testing

- **Unit (pytest)** (dev deps: `pytest`, `pytest-asyncio`, `aiohttp`):
  - `test_capture.py` — StreamProxy line buffering, `\r` collapse, level classification across INFO/WARN/ERROR/STDOUT/STDERR, idempotent install, disk-failure notice.
  - `test_logging_bridge.py` — record capture across root/`comfy` loggers, level mapping, multiline splitting, cross-path dedupe.
  - `test_storage.py` — rotation, backup pruning, failure propagation.
  - `test_state.py` — ring bounds, fanout, slow-viewer drop.
  - `test_routes.py` — regex validation (400 on bad pattern), backlog framing, live lines, disconnect cleanup.
  - `test_bootstrap.py` — wiring end-to-end, pre-load seed, idempotence.
  - `test_nodes.py` — schema shape, empty execute, extension node list (with `comfy_api` stubbed).
- **Integration**:
  - Spin up a fake aiohttp app in pytest, register the SSE route, push synthetic lines through a wrapped print, assert SSE client receives them in order.
- **Manual smoke** (during implementation, not automated):
  - Drop the node on canvas, run a workflow that intentionally triggers a `ValueError` in a downstream node, confirm the ERROR line appears in red within ~200ms.
  - Restart ComfyUI, reconnect, confirm backlog replays last 2000 lines.
  - Delete console-node dir, confirm directory is recreated on next line.

## File layout

```
ComfyUI-Console-Node/
├── __init__.py                    # comfy_entrypoint() + WEB_DIRECTORY
├── pyproject.toml                 # name, dev deps (pytest, pytest-asyncio, aiohttp); no runtime deps
├── README.md                      # install + usage + limitations
├── conftest.py                    # repo root on sys.path for tests
├── .gitignore
├── LICENSE                        # MIT
├── comfyui_console_node/
│   ├── __init__.py
│   ├── bootstrap.py               # setup() wiring + pre-load seed
│   ├── capture.py                 # StreamProxy, logging bridge, level parser
│   ├── constants.py               # defaults + env overrides
│   ├── nodes.py                   # ConsoleLogViewer (V3) + extension
│   ├── routes.py                  # SSE route registration
│   ├── state.py                   # ring + fanout + lock
│   └── storage.py                 # rotating file writer
├── web/
│   └── console_node.js            # canvas widget
├── docs/superpowers/
│   ├── specs/2026-10-05-comfyui-console-node-design.md   # this file
│   └── plans/2026-10-05-comfyui-console-node-plan.md
├── tests/
│   ├── test_bootstrap.py, test_capture.py, test_constants.py, test_logging_bridge.py,
│   ├── test_nodes.py, test_routes.py, test_routes_bootstrap.py, test_smoke.py,
│   └── test_state.py, test_storage.py
└── tools/
    └── deploy.py                  # copy the pack into D:/VectorFlow/custom_nodes
```

## Dependencies

- Runtime: **none.** Stdlib only (`asyncio`, `logging`, `re`, `collections.deque`, `pathlib`, `threading`); aiohttp is provided by ComfyUI.
- Dev: `pytest`, `pytest-asyncio`, `aiohttp` (route tests).

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `COMFYUI_CONSOLE_NODE_BUFFER` | `2000` | Max lines held in the in-memory ring. |
| `COMFYUI_CONSOLE_NODE_ROTATE_BYTES` | `5242880` | Rotate the on-disk log when it exceeds this size. |
| `COMFYUI_CONSOLE_NODE_MAX_BACKUPS` | `3` | Number of rotated backups to keep. |
| `COMFYUI_CONSOLE_NODE_HIDE_INTERNAL` | `1` | When `1`, widget hides `source="internal"` lines by default. |

## Deployment

- After implementation: deploy to `D:/VectorFlow/custom_nodes/ComfyUI-Console-Node` **and** the ComfyUI portable install per the user's standing convention (both VFUtils custom_nodes locations).
- `git init` happens at `E:\projects\ComfyUI-Console-Node\` per the user's instruction; no remote is set.