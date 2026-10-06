import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from comfyui_console_node import routes, state


@pytest.fixture(autouse=True)
def clean():
    state.reset_for_tests()
    yield
    state.reset_for_tests()


def make_app():
    table = web.RouteTableDef()
    assert routes.register_routes(table) is True
    app = web.Application()
    app.add_routes(table)
    return app


@pytest.fixture
async def client():
    server = TestServer(make_app())
    http = TestClient(server)
    await http.start_server()
    yield http
    await http.close()


async def read_chunk(response, timeout=2.0):
    return await asyncio.wait_for(response.content.read(65536), timeout)


async def test_backlog_replays_ring(client):
    state.enqueue(state.build_line("INFO", "alpha"))
    state.enqueue(state.build_line("ERROR", "beta"))
    response = await client.get("/console_node/log/stream")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    text = (await read_chunk(response)).decode("utf-8")
    assert "event: backlog" in text
    assert "alpha" in text and "beta" in text
    response.close()


async def test_backlog_filter(client):
    state.enqueue(state.build_line("INFO", "alpha"))
    state.enqueue(state.build_line("INFO", "beta"))
    response = await client.get("/console_node/log/stream?filter=alpha")
    text = (await read_chunk(response)).decode("utf-8")
    assert "alpha" in text
    assert "beta" not in text
    response.close()


async def test_invalid_filter_rejected(client):
    response = await client.get("/console_node/log/stream?filter=(")
    assert response.status == 400
    response.close()


async def test_live_lines_stream(client):
    response = await client.get("/console_node/log/stream")
    await read_chunk(response)  # backlog frame (empty)
    state.enqueue(state.build_line("WARN", "live-one"))
    text = (await read_chunk(response)).decode("utf-8")
    assert "event: line" in text
    assert "live-one" in text
    response.close()


async def test_client_removed_on_disconnect(client, monkeypatch):
    monkeypatch.setattr(routes, "HEARTBEAT_SECONDS", 0.05)
    response = await client.get("/console_node/log/stream")
    await read_chunk(response)
    assert state.client_count() == 1
    response.close()
    for _ in range(20):
        if state.client_count() == 0:
            break
        await asyncio.sleep(0.05)
    assert state.client_count() == 0
