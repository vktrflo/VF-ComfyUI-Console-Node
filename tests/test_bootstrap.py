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
    yield tmp_path
    state.reset_for_tests()
    capture.reset_for_tests()


def swap_streams(monkeypatch):
    """Swap sys.stdout/stderr in the CALL phase.

    Swaps made in a fixture are reverted by pytest's capture between the
    setup and call phases, so these must happen inside the test body.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    return out


def test_setup_wires_pipeline(clean, monkeypatch):
    swap_streams(monkeypatch)
    bootstrap.setup()
    log_path = clean / "console-node" / "console.log"
    assert log_path.exists()
    assert isinstance(sys.stdout, capture.StreamProxy)
    content = log_path.read_text(encoding="utf-8")
    assert "capture started" in content


def test_setup_is_idempotent_for_proxy(clean, monkeypatch):
    swap_streams(monkeypatch)
    bootstrap.setup()
    wrapped = sys.stdout
    bootstrap.setup()
    assert sys.stdout is wrapped


def test_setup_recovers_pre_load_lines(clean, monkeypatch):
    swap_streams(monkeypatch)
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


def test_setup_without_app_logger_still_starts(clean, monkeypatch):
    swap_streams(monkeypatch)
    bootstrap.setup()
    texts = [line["text"] for line in state.ring()]
    assert any("capture started" in t for t in texts)
