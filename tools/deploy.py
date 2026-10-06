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
