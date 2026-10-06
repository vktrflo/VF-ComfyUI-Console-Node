import io
import sys

import pytest

from comfyui_console_node import capture, state


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    yield
    state.reset_for_tests()
    capture.reset_for_tests()


def make_sink():
    seen = []

    def sink(level, text, source="external", via_logging=False, cr=False):
        seen.append((level, text, source))

    return seen, sink


@pytest.fixture
def sink(monkeypatch):
    seen, recorder = make_sink()
    monkeypatch.setattr(capture, "_publish", recorder)
    return seen


def swap_streams(monkeypatch):
    """Swap sys.stdout/stderr in the CALL phase.

    Swaps made in a fixture are reverted by pytest's capture between the
    setup and call phases, so these must happen inside the test body.
    """
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err


def install_test_proxy(monkeypatch):
    out, err = swap_streams(monkeypatch)
    assert capture.install_proxy() is True
    return out, err


@pytest.mark.parametrize(
    ("stream_name", "text", "expected"),
    [
        ("stdout", "Traceback (most recent call last):", "ERROR"),
        ("stdout", "Exception in thread main", "ERROR"),
        ("stdout", "WARNING: deprecated config option", "WARN"),
        ("stdout", "INFO: started server", "INFO"),
        ("stdout", "regular output", "STDOUT"),
        ("stdout", "[INFO] setup plugin", "INFO"),
        ("stderr", "no tokens here", "STDERR"),
    ],
)
def test_classify(stream_name, text, expected):
    assert capture.classify(stream_name, text) == expected


def test_proxy_buffers_and_emits_complete_lines(monkeypatch, sink):
    install_test_proxy(monkeypatch)
    sys.stdout.write("hello ")
    sys.stdout.write("world\nsecond")
    sys.stdout.write(" line\n")
    assert sink == [
        ("STDOUT", "hello world", "external"),
        ("STDOUT", "second line", "external"),
    ]


def test_proxy_passes_through_to_original(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    monkeypatch.setattr(capture.storage, "append", lambda line: None)
    out, _ = swap_streams(monkeypatch)
    capture.install_proxy()
    sys.stdout.write("echo me\n")
    assert out.getvalue() == "echo me\n"


def test_install_is_idempotent(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    swap_streams(monkeypatch)
    assert capture.install_proxy() is True
    first = sys.stdout
    assert isinstance(first, capture.StreamProxy)
    assert capture.install_proxy() is False
    assert sys.stdout is first


def test_carriage_returns_emit_live(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    seen = []
    monkeypatch.setattr(
        capture,
        "_publish",
        lambda level, text, source="external", via_logging=False, cr=False: seen.append((level, text, cr)),
    )
    swap_streams(monkeypatch)
    assert capture.install_proxy() is True
    sys.stdout.write("Loading 10%\r")
    sys.stdout.write("Loading 20%\r")
    sys.stdout.write("done\n")
    sys.stdout.write("crlf line\r\n")
    assert seen == [
        ("STDOUT", "Loading 10%", True),
        ("STDOUT", "Loading 20%", True),
        ("STDOUT", "done", False),
        ("STDOUT", "crlf line", False),
    ]


def test_ansi_codes_stripped(monkeypatch, sink):
    install_test_proxy(monkeypatch)
    sys.stdout.write("\x1b[32m[INFO]\x1b[0m hello\n")
    assert sink == [("INFO", "[INFO] hello", "external")]


def test_emit_notice_is_tagged_internal(monkeypatch, sink):
    install_test_proxy(monkeypatch)
    capture.emit_notice("console-node: hello")
    assert sink == [("STDOUT", "console-node: hello", "internal")]


def test_disk_failure_notices_once_then_recovers(monkeypatch):
    state.reset_for_tests()
    capture.reset_for_tests()
    direct = []
    monkeypatch.setattr(capture, "_direct_write", direct.append)
    failing = {"on": True}

    def flaky(line):
        if failing["on"]:
            raise OSError("disk full")

    monkeypatch.setattr(capture.storage, "append", flaky)
    capture._publish("STDOUT", "one", "external")
    capture._publish("STDOUT", "two", "external")
    assert len(direct) == 1
    assert "disk log unavailable" in direct[0]
    failing["on"] = False
    capture._publish("STDOUT", "three", "external")
    assert len(direct) == 2
    assert "recovered" in direct[1]


def test_publish_routes_to_storage_and_ring(monkeypatch, tmp_path):
    state.reset_for_tests()
    capture.reset_for_tests()
    from comfyui_console_node import storage

    storage.init(tmp_path)
    try:
        capture._publish("ERROR", "kaboom", "external")
        assert state.ring()[-1]["text"] == "kaboom"
        assert state.ring()[-1]["level"] == "ERROR"
        content = (tmp_path / "console.log").read_text(encoding="utf-8")
        assert "kaboom" in content
    finally:
        storage.close()
