"""MCP Events (``events/list|subscribe|unsubscribe``) as an SDK v2 extension.

Served through the SDK's first-class extension API: each method is a
``MethodBinding`` on the ``io.modelcontextprotocol/events`` extension (the
SEP-3415 draft identifier), which the SDK advertises under
``capabilities.extensions``. ChatGPT's implementation looks for a top-level
``capabilities.events`` instead, so a small middleware stamps that too.

The handlers are thin: they parse params, call ``services.line_events`` off
the event loop (subscribe resolves DNS and may POST a verification challenge),
and translate its errors into JSON-RPC errors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from anyio.to_thread import run_sync
from mcp import MCPError
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.types import INVALID_PARAMS, RequestParams
from pydantic import ConfigDict, ValidationError

from models.telephone_events import SubscribeParams, UnsubscribeParams
from services.line_events import CallbackError, line_events, subscription_id
from src.utils.current_user import current_user

EVENTS_EXTENSION = "io.modelcontextprotocol/events"
# The code ChatGPT expects when callback verification fails (SEP-3415 drafts
# renumber it; ChatGPT is the client that exists today).
CALLBACK_ENDPOINT_ERROR = -32015
_ADVERTISED = {"delivery": ["webhook"]}


class _AnyParams(RequestParams):
    """Accept any params object; the handlers validate it themselves so a bad
    request gets a precise message instead of a generic schema error."""

    model_config = ConfigDict(extra="allow")


def _owner() -> str:
    user = current_user()
    return user.user_id if user is not None else "anonymous"


def _raw(params: _AnyParams | None) -> dict[str, Any]:
    if params is None:
        return {}
    raw = params.model_dump(by_alias=True, exclude_none=True)
    raw.pop("_meta", None)
    return raw


def _list(_raw_params: dict[str, Any], _owner_id: str) -> dict[str, Any]:
    return line_events.list_events().model_dump(by_alias=True)


def _subscribe(raw: dict[str, Any], owner: str) -> dict[str, Any]:
    params = SubscribeParams.model_validate(raw)
    return line_events.subscribe(params, owner).model_dump(by_alias=True)


def _unsubscribe(raw: dict[str, Any], owner: str) -> dict[str, Any]:
    params = UnsubscribeParams.model_validate(raw)
    sub_id = params.id
    if sub_id is None:
        assert params.name and params.arguments and params.delivery
        sub_id = subscription_id(
            owner,
            params.delivery.url,
            params.name,
            params.arguments.line,
            params.arguments.seat,
        )
    line_events.unsubscribe(sub_id, owner)
    return {}


def _bind(
    method: str, fn: Callable[[dict[str, Any], str], dict[str, Any]]
) -> MethodBinding:
    async def handler(
        _ctx: ServerRequestContext[Any, Any], params: _AnyParams | None
    ) -> HandlerResult:
        owner = _owner()
        try:
            return await run_sync(fn, _raw(params), owner)
        except CallbackError as exc:
            raise MCPError(
                CALLBACK_ENDPOINT_ERROR,
                "Callback endpoint error",
                {"reason": exc.reason},
            ) from exc
        except (ValidationError, ValueError) as exc:
            # pydantic errors, EventsError and TelephoneError (bad seat, unknown
            # line), and stray URL-parsing errors: all bad params.
            raise MCPError(INVALID_PARAMS, str(exc)) from exc

    return MethodBinding(method=method, params_type=_AnyParams, handler=handler)


class LineEventsExtension(Extension):
    identifier = EVENTS_EXTENSION

    def settings(self) -> dict[str, Any]:
        return dict(_ADVERTISED)

    def methods(self) -> Sequence[MethodBinding]:
        return (
            _bind("events/list", _list),
            _bind("events/subscribe", _subscribe),
            _bind("events/unsubscribe", _unsubscribe),
        )


async def advertise_top_level_events(
    ctx: ServerRequestContext[Any, Any], call_next: CallNext
) -> HandlerResult:
    """Also stamp ``capabilities.events`` for clients that read the pre-SEP key."""
    result = await call_next(ctx)
    if ctx.method in ("initialize", "server/discover") and isinstance(result, dict):
        caps = result.setdefault("capabilities", {})
        if isinstance(caps, dict):
            caps.setdefault("events", dict(_ADVERTISED))
    return result
