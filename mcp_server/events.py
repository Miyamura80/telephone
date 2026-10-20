"""Serve the MCP Events methods (``events/list|subscribe|unsubscribe``) on ``/mcp``.

The 1.x Python SDK validates every request against a closed set of spec
methods, so it rejects ``events/*`` before any handler could run. This thin
ASGI layer sits between the auth middleware and FastMCP: it answers the three
events methods itself and replays every other request to FastMCP untouched.
It also advertises ``capabilities.events`` in the initialize result.

Temporary by design: SDK v2 (protocol 2026-07-28) has a first-class extension
API (``MethodBinding``), which is also what ChatGPT needs to negotiate events.
Migrating to it replaces this module.
"""

from __future__ import annotations

import json
from typing import Any

from anyio.to_thread import run_sync
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from models.telephone_events import SubscribeParams, UnsubscribeParams
from services.line_events import line_events
from src.utils.current_user import current_user

_METHODS = frozenset({"events/list", "events/subscribe", "events/unsubscribe"})
# Every /mcp POST is buffered to check its method; cap it so an authenticated
# client cannot exhaust memory. Far above any real MCP request.
MAX_BODY_BYTES = 4 * 1024 * 1024
_INVALID_PARAMS = -32602
_INVALID_REQUEST = -32600


def advertise_events(mcp: FastMCP) -> None:
    """Add ``events`` to the server capabilities sent in the initialize result."""
    server = mcp._mcp_server
    original = server.get_capabilities

    def get_capabilities(notification_options, experimental_capabilities):
        caps = original(notification_options, experimental_capabilities)
        # ServerCapabilities allows extra keys; `events` is not in the 1.x model.
        return caps.model_copy(update={"events": {"delivery": ["webhook"]}})

    server.get_capabilities = get_capabilities  # ty: ignore[invalid-assignment]


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    user = current_user()
    owner = user.user_id if user is not None else "anonymous"
    if method == "events/list":
        return line_events.list_events().model_dump(by_alias=True)
    if method == "events/subscribe":
        return line_events.subscribe(
            SubscribeParams.model_validate(params), owner
        ).model_dump(by_alias=True)
    line_events.unsubscribe(UnsubscribeParams.model_validate(params).id, owner)
    return {}


async def _handle(rpc: dict[str, Any]) -> dict[str, Any]:
    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc.get("id")}
    params = rpc.get("params") or {}
    if not isinstance(params, dict):
        reply["error"] = {
            "code": _INVALID_REQUEST,
            "message": "params must be an object",
        }
        return reply
    try:
        # subscribe resolves DNS for the SSRF guard; keep it off the loop.
        reply["result"] = await run_sync(_dispatch, rpc["method"], params)
    except ValueError as exc:
        # Covers pydantic ValidationError, EventsError and TelephoneError, plus
        # stray ValueError/UnicodeError from URL parsing: always -32602.
        reply["error"] = {"code": _INVALID_PARAMS, "message": str(exc)}
    return reply


class EventsJsonRpc:
    """ASGI wrapper that answers ``events/*`` POSTs and passes the rest through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/mcp"
        ):
            await self.app(scope, receive, send)
            return
        body = await _read_body(receive)
        if body is None:
            await _send_json(send, 413, {"error": "request body too large"})
            return
        rpc = _parse(body)
        if rpc is None:
            await self.app(scope, _replay(body, receive), send)
            return
        await _send_json(send, 200, await _handle(rpc))


async def _send_json(send: Send, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _read_body(receive: Receive) -> bytes | None:
    """Read the whole body, or None once it exceeds ``MAX_BODY_BYTES``."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _parse(body: bytes) -> dict[str, Any] | None:
    """Return the request if it is a single JSON-RPC call to an events method."""
    try:
        rpc = json.loads(body)
    except ValueError:
        return None
    if not isinstance(rpc, dict) or "id" not in rpc:
        return None
    method = rpc.get("method")
    if isinstance(method, str) and method in _METHODS:
        return rpc
    return None


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
