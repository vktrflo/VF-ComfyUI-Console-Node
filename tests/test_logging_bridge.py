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
