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
