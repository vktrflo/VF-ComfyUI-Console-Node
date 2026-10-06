import asyncio

import pytest

from comfyui_console_node import state


@pytest.fixture(autouse=True)
def clean():
    state.reset_for_tests()
    yield
    state.reset_for_tests()


def test_build_line_fields():
    line = state.build_line("WARN", "hello", "internal")
    assert line["level"] == "WARN"
    assert line["text"] == "hello"
    assert line["source"] == "internal"
    assert isinstance(line["ts"], float)


def test_ring_is_bounded():
    state.reset_for_tests(buffer_size=3)
    for i in range(5):
        state.enqueue(state.build_line("INFO", f"line-{i}"))
    texts = [line["text"] for line in state.ring()]
    assert texts == ["line-2", "line-3", "line-4"]


def test_enqueue_without_loop_is_safe():
    state.enqueue(state.build_line("INFO", "no loop"))
    assert len(state.ring()) == 1


async def test_client_receives_lines():
    state.set_loop(asyncio.get_running_loop())
    client = state.register_client()
    try:
        state.enqueue(state.build_line("INFO", "one"))
        state.enqueue(state.build_line("ERROR", "two"))
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
        first = client.queue.get_nowait()
        second = client.queue.get_nowait()
        assert first["text"] == "one"
        assert second["text"] == "two"
        assert first["level"] == "INFO"
    finally:
        state.unregister_client(client)
    assert state.client_count() == 0


async def test_slow_client_drops_oldest_and_flags():
    state.set_loop(asyncio.get_running_loop())
    client = state.register_client()
    client.queue = asyncio.Queue(maxsize=2)
    try:
        for i in range(4):
            state.enqueue(state.build_line("INFO", f"line-{i}"))
        await asyncio.sleep(0)
        remaining = []
        while not client.queue.empty():
            remaining.append(client.queue.get_nowait()["text"])
        assert remaining == ["line-2", "line-3"]
        assert client.dropped is True
    finally:
        state.unregister_client(client)
