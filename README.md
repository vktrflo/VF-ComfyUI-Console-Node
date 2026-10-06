# ComfyUI-Console-Node

Mirror the ComfyUI server console (stdout/stderr + `logging`) into a node on
the canvas. Filter it, color-code it by level, pause/clear it, copy lines —
and keep a rotating copy on disk for grep.

## Install

Copy this repository into `<ComfyUI>/custom_nodes/ComfyUI-Console-Node` (or
run `python tools/deploy.py [target]`, default target
`E:/comfyui_instances/SMALL_DESKTOP/ComfyUI/custom_nodes`). Restart ComfyUI.

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

## Behavior notes

- In-place updates (`\r`, e.g. tqdm progress bars) stream live and **replace
  the previous line** in the viewer — the bar you see is the current state,
  not a wall of updates. The disk log keeps only finished lines.
- ANSI escape codes from the console (colors, links) are stripped.

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

Note for test authors: pytest restores `sys.stdout`/`sys.stderr` between the
setup and call phases, so swapping those streams inside a *fixture* does not
reach the test body. Swap them inside the test body (see
`tests/test_capture.py::swap_streams`).
