"""aiohttp SSE endpoint that streams captured console lines to the canvas."""

import asyncio
import json
import re

from aiohttp import web

from . import state
from .constants import HEARTBEAT_SECONDS, ROUTE_STREAM


def _compile_filter(request) -> "re.Pattern | None":
    raw = request.query.get("filter")
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error as exc:
        raise web.HTTPBadRequest(text=f"Invalid filter regex: {exc}") from exc


def _matches(line: dict, pattern) -> bool:
    return pattern is None or pattern.search(line["text"]) is not None


async def _send_event(response, event: str, payload) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    await response.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))


async def _stream(request):
    pattern = _compile_filter(request)

    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    state.set_loop(asyncio.get_running_loop())

    client = state.register_client()
    try:
        backlog = [line for line in state.ring() if _matches(line, pattern)]
        await _send_event(response, "backlog", backlog)
        while True:
            try:
                line = await asyncio.wait_for(client.queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            if _matches(line, pattern):
                await _send_event(response, "line", line)
    except ConnectionResetError:
        pass
    finally:
        state.unregister_client(client)
    return response


def register_routes(routes=None) -> bool:
    """Attach the SSE route to ComfyUI's aiohttp app; False without a server."""
    if routes is None:
        try:
            from server import PromptServer
        except ImportError:
            return False
        routes = PromptServer.instance.routes

    @routes.get(ROUTE_STREAM)
    async def console_node_stream(request):
        return await _stream(request)

    return True
