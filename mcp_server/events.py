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
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from models.telephone_events import SubscribeParams, UnsubscribeParams
from services.line_events import EventsError, line_events
from services.switchboard import TelephoneError
from src.utils.current_user import current_user

_METHODS = frozenset({"events/list", "events/subscribe", "events/unsubscribe"})
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
    except ValidationError as exc:
        reply["error"] = {"code": _INVALID_PARAMS, "message": str(exc)}
    except (EventsError, TelephoneError) as exc:
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
        rpc = _parse(body)
        if rpc is None:
            await self.app(scope, _replay(body, receive), send)
            return
        payload = json.dumps(await _handle(rpc)).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _parse(body: bytes) -> dict[str, Any] | None:
    """Return the request if it is a single JSON-RPC call to an events method."""
    try:
        rpc = json.loads(body)
    except ValueError:
        return None
    if isinstance(rpc, dict) and rpc.get("method") in _METHODS and "id" in rpc:
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
