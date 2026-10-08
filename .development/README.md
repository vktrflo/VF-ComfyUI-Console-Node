# Development notes

Internal documentation for **VF-ComfyUI-Console-Node**. Nothing here ships to
users: the directory is excluded from the registry archive by `.comfyignore`,
and the user-facing README deliberately covers only install and use.

## Contents

| Path | What it is |
|---|---|
| [`specs/`](specs/) | Design specs written before implementation |
| [`plans/`](plans/) | Implementation plans and their deviations |
| this file | Build, test, demo-recording and release workflows |

## Test environment

```bash
uv venv
uv pip install --python .venv/Scripts/python.exe pytest pytest-asyncio aiohttp
.venv/Scripts/python.exe -m pytest -q
```

### Gotcha: pytest restores `sys.stdout` / `sys.stderr`

pytest swaps `sys.stdout`/`sys.stderr` between the setup and call phases, so
swapping those streams inside a *fixture* does not reach the test body. Swap
them inside the test body — see `tests/test_capture.py::swap_streams`.

This matters here because the capture module's whole job is replacing those
streams; a fixture-based swap silently tests nothing.

## Regenerating the icon

`icon.png` is generated, not hand-drawn, so it can be re-derived rather than
re-drawn:

```bash
python tools/make_icon.py
```

It is pure stdlib (`zlib` + `struct`) with 3×3 supersampling — no Pillow
dependency is added just to draw 400×400 pixels. Keep it square and ≤400×400;
the registry and ComfyUI-Manager render it at roughly 64px, so high contrast and
few shapes matter more than detail.

## Recording the demo GIFs

The six GIFs in `docs/media/` are captured from a real ComfyUI instance, not
mocked. Demo *content* is synthetic — emitted by a throwaway helper node — but
it travels the genuine capture path (stdout proxy → ring buffer → SSE → canvas
widget), so what you see is the node's real behaviour.

The helper lives only in the recording instance and is deleted afterwards. It
must be a proper V3 extension (ComfyUI skips a custom-node module that returns
no `ComfyExtension`) and register aiohttp routes in `on_load`.

```bash
pwsh -File tools/record_instance.ps1 start   # isolated ComfyUI on :8198
node tools/record_demos.mjs                 # per-demo .webm + meta.json
node tools/encode_demos.mjs                 # crop + palette -> docs/media/*.gif
pwsh -File tools/record_instance.ps1 stop
```

`record_instance.ps1` launches ComfyUI with
`--disable-all-custom-nodes --whitelist-custom-nodes`, so your real custom nodes
and workflows are untouched. Requires Playwright reachable via `PLAYWRIGHT_ROOT`.

### Non-obvious things that cost time

- **SSE backlog races the `live` status.** `onopen` fires *before* the `backlog`
  event, so clearing the viewer the moment state reads `live` gets undone when
  the backlog lands a moment later. Wait for the line count to stabilise first.
- **Measure the lead-in *after* the clear**, not before, or the encoder trims to
  a frame where the viewer still holds the previous demo's lines.
- **`recordVideo.size` must equal the viewport.** Asking for a larger video does
  not upscale the page — Playwright lays content out in the top-left viewport
  region and pads the rest, so the extra pixels are grey.
- **Renumbering frames destroys timing.** Playwright writes variable-frame-rate
  webm; resampling with `fps=` preserves elapsed time, but `setpts=N/FRAME_RATE/TB`
  collapses the clip to its frame-count/12 seconds. `mpdecimate` has the same
  problem unless paired with a matching `setpts` scale.
- **`-filter_complex` chains are joined with `,`, graphs with `;`.** A chain
  starting from an input must also carry the `[0:v]` label, or ffmpeg reports
  `No such filter: ''`.
- **The max-lines control clamps to ≥100.** A demo setting it below 100 shows no
  trim at all; emit more than 100 lines for the cap to be visible.
- Hide the Vue frontend's chrome by hiding each ancestor's siblings, otherwise
  the tab bar, side rail and run toolbar float over the node in every frame.

## Release verification

```bash
python tools/verify_release.py
```

Checks registry metadata (including that `PublisherId` has no leftover TODO and
that the icon URL actually resolves from `main`), runs the test suite, confirms
every README image exists with no orphans, and simulates the published archive
by applying `.comfyignore` to the tracked file list.

It **fails when files are untracked** — `comfy node publish` packages
git-tracked files only, so an uncommitted `icon.png` or GIF would ship silently
missing.

## Publishing

`PublisherId` in `pyproject.toml` is immutable after the first publish and forms
the public node URL, so it is pinned rather than templated. Merge to `main`
before publishing, since `[tool.comfy].Icon` resolves from `main`.

```bash
pip install comfy-cli
comfy node publish
```