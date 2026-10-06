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
