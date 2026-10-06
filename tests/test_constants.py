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
