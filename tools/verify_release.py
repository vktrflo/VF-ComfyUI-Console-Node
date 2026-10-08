#!/usr/bin/env python3
"""Verify the repo is ready to publish to the Comfy Registry.

    python tools/verify_release.py

Checks, in order of how badly a failure would hurt:

  1. pyproject.toml parses and carries the registry metadata
  2. the test suite passes
  3. every image the README references exists, and none are orphaned
  4. the simulated registry archive contains the runtime files and excludes
     the development ones
"""

import fnmatch
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTEST = REPO / ".venv" / "Scripts" / "python.exe"
PYTHON = sys.executable

failures: list[str] = []
notes: list[str] = []


def check(ok, label, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


# --- 1. registry metadata ----------------------------------------------------
print("\n1. pyproject.toml")
try:
    with open(REPO / "pyproject.toml", "rb") as fh:
        meta = tomllib.load(fh)
    check(True, "parses as TOML")
except Exception as exc:
    check(False, "parses as TOML", str(exc))
    meta = {}

if meta:
    project = meta.get("project", {})
    comfy = meta.get("tool", {}).get("comfy", {})
    urls = project.get("urls", {})

    check(bool(project.get("name")), "[project].name set", project.get("name", ""))
    check(
        bool(re.fullmatch(r"[a-z0-9._-]+", project.get("name", "x"))),
        "[project].name is registry-safe (lowercase)",
        project.get("name", ""),
    )
    check(
        bool(re.fullmatch(r"\d+\.\d+\.\d+", project.get("version", ""))),
        "[project].version is semver",
        project.get("version", ""),
    )
    check(bool(project.get("description")), "[project].description set")
    check(bool(urls.get("Repository")), "[project.urls].Repository set", urls.get("Repository", ""))
    check(bool(comfy.get("PublisherId")), "[tool.comfy].PublisherId set", comfy.get("PublisherId", ""))
    check("TODO" not in str(comfy.get("PublisherId", "")), "PublisherId has no TODO placeholder")
    check(bool(comfy.get("DisplayName")), "[tool.comfy].DisplayName set", comfy.get("DisplayName", ""))

    icon = comfy.get("Icon", "")
    if check(bool(icon), "[tool.comfy].Icon set"):
        icon_path = REPO / "icon.png"
        check(icon_path.exists(), "icon.png exists in repo")
        # The URL must match the repo/branch or the registry will 404.
        expected = f"raw.githubusercontent.com/{re.sub(r'^https://github.com/', '', urls.get('Repository', ''))}/main/icon.png"
        check(expected in icon, "Icon URL points at this repo's main branch", icon)

# --- 2. tests ----------------------------------------------------------------
print("\n2. tests")
if PYTEST.exists():
    proc = subprocess.run([str(PYTEST), "-m", "pytest", "-q"], cwd=REPO, capture_output=True, text=True)
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr).strip() else ""
    check(proc.returncode == 0, "pytest passes", tail)
else:
    notes.append("pytest skipped: no .venv/Scripts/python.exe")

# --- 3. README images --------------------------------------------------------
print("\n3. README media")
readme = (REPO / "README.md").read_text(encoding="utf-8")
refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme)
local = [r for r in refs if not r.startswith(("http://", "https://"))]
missing = [r for r in local if not (REPO / r).exists()]
check(not missing, f"all {len(local)} referenced images exist", ", ".join(missing))

on_disk = {f"docs/media/{p.name}" for p in (REPO / "docs" / "media").glob("*.gif")} if (REPO / "docs" / "media").exists() else set()
orphans = sorted(on_disk - set(local))
check(not orphans, "no unreferenced GIF in docs/media", ", ".join(orphans))

# --- 4. simulated registry archive ------------------------------------------
print("\n4. registry archive (git ls-files minus .comfyignore)")
tracked = subprocess.run(
    ["git", "ls-files", "--cached"], cwd=REPO, capture_output=True, text=True, check=True,
).stdout.split()
untracked = subprocess.run(
    ["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, capture_output=True, text=True, check=True,
).stdout.split()

# `comfy node publish` packages git-tracked files, so anything still untracked
# would be silently missing from the published archive.
if untracked:
    check(False, f"{len(untracked)} file(s) untracked -- they will NOT be published until committed",
          ", ".join(sorted(untracked)[:8]) + (" ..." if len(untracked) > 8 else ""))
else:
    check(True, "no untracked files pending commit")

# What the archive would contain once everything is committed.
shipped = sorted(set(tracked) | set(untracked))

patterns = []
comfyignore = REPO / ".comfyignore"
if comfyignore.exists():
    for line in comfyignore.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)


def ignored(path: str) -> bool:
    """Approximate gitignore matching for the subset .comfyignore uses.

    A trailing-slash pattern is a DIRECTORY match and must be tested against
    path prefixes. Testing it against individual path components (the obvious
    first attempt) silently fails: 'docs/superpowers/' never matches the
    component 'docs', so the whole directory would ship to the registry.
    """
    parts = path.split("/")
    for raw in patterns:
        pat = raw.rstrip("/")
        if raw.endswith("/"):
            if path == pat or path.startswith(pat + "/"):
                return True
            continue
        # A pattern with no slash matches the basename at any depth.
        if "/" not in pat and fnmatch.fnmatch(parts[-1], pat):
            return True
        # Otherwise match the whole path or any directory prefix of it.
        if fnmatch.fnmatch(path, pat):
            return True
        if any(fnmatch.fnmatch("/".join(parts[: i + 1]), pat) for i in range(len(parts))):
            return True
    return False


shipped = [p for p in shipped if not ignored(p)]

required = [
    "pyproject.toml", "README.md", "LICENSE", "__init__.py", "icon.png",
    "web/console_node.js",
    "comfyui_console_node/__init__.py", "comfyui_console_node/nodes.py",
    "comfyui_console_node/bootstrap.py", "comfyui_console_node/capture.py",
    "comfyui_console_node/routes.py", "comfyui_console_node/state.py",
    "comfyui_console_node/storage.py", "comfyui_console_node/constants.py",
]
absent = [r for r in required if r not in shipped]
check(not absent, f"all {len(required)} runtime files ship", ", ".join(absent))

excluded = ["tests", "tools", "conftest.py", ".recording", ".development"]
leaked = [e for e in excluded if any(s == e or s.startswith(e + "/") for s in shipped)]
check(not leaked, "development files excluded", ", ".join(leaked))

media_in_archive = sorted(s for s in shipped if s.startswith("docs/media/"))
check(len(media_in_archive) == len(on_disk), "docs/media GIFs ship with the archive",
      f"{len(media_in_archive)} of {len(on_disk)}")

size = sum((REPO / p).stat().st_size for p in shipped if (REPO / p).exists())
print(f"       archive: {len(shipped)} files, {size / 1024:.0f} KB")
for p in sorted(shipped):
    print(f"         {p}")

# --- summary -----------------------------------------------------------------
print("\n" + "=" * 60)
for n in notes:
    print(f"note: {n}")
if failures:
    print(f"FAILED {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all checks passed")