"""Internal tool-registration factory for `mcp_server.server`.

Builds MCPServer tools from `ServiceEntry` records, branching between two paths:

- **Headless**: sync wrapper, returns the Pydantic output model. The SDK derives
  `outputSchema` from the return annotation and runs the wrapper on a worker
  thread, so a blocking service never stalls the event loop.
- **Enhanced**: async wrapper with `Context`, calls an `@enhance`-registered
  function. Returns a `CallToolResult`, annotated as
  `Annotated[CallToolResult, OutputModel]` so the SDK still publishes (and
  validates against) the service's `outputSchema`.

Expected failures (bad input, a refused seat, a missing scope, an exhausted
quota) are re-raised as `ToolError` so the model reads their message; the SDK
replaces any other exception's text with a generic "Error executing tool".

Don't reach for these helpers from feature code - use `@service` and `@enhance`.
"""

import inspect
from typing import Annotated, Any, NoReturn

from fastapi import HTTPException
from loguru import logger as log
from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from mcp_server.enhancers import EnhancerEntry, get_enhancer
from mcp_server.enhancers.base import EnhancedTool, build_app_meta
from mcp_server.url_elicitation import reraise_with_elicitation
from services import ConnectRequiredError, ServiceEntry
from src.utils.current_user import current_user


def _check_scopes() -> None:
    """Enforce ``services:execute`` scope for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    """
    user = current_user()
    if user is None:
        return

    # Circular import: api_server.server mounts mcp_server.server, which
    # imports this module - api_server.* must only load at call time.
    from api_server.auth.scopes import SERVICES_EXECUTE, check_scopes  # noqa: PLC0415

    if not check_scopes([SERVICES_EXECUTE], user.scopes):
        raise PermissionError(
            "Insufficient permissions: 'services:execute' scope required."
        )


def _check_quota() -> None:
    """Enforce daily quota for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    Called *after* input validation so malformed requests don't burn quota.
    """
    user = current_user()
    if user is None:
        return

    # Circular import: api_server.server mounts mcp_server.server, which
    # imports this module - api_server.* must only load at call time.
    from api_server.billing.limits import ensure_daily_limit  # noqa: PLC0415

    ensure_daily_limit(user.user_id)


def _meter(entry: ServiceEntry) -> None:
    """Meter an authenticated MCP tool call: paywall if priced, else quota.

    Priced services require a valid ``X-PAYMENT`` header (bound to a ContextVar
    by the /mcp middleware) that is verified and settled before the tool runs;
    a missing/invalid payment surfaces as a structured ``MCPError`` carrying the
    x402 challenge. When the paywall is inactive (x402 disabled) a priced
    service still falls back to the daily quota, so it is never unlimited. Free
    services stay on the daily quota. Skips silently when no authenticated user
    is bound (CLI / stdio), which is never paywalled.

    Synchronous: headless tools call it directly; the async enhanced path must
    offload it to a thread (see ``_meter_async``) so the facilitator round-trip
    never blocks the shared event loop.
    """
    user = current_user()
    if user is None:
        return

    charged = False
    if entry.price is not None:
        charged = _enforce_paywall(entry, user)
    if not charged:
        _check_quota()


async def _meter_async(entry: ServiceEntry) -> None:
    """Run :func:`_meter` off the event loop for the async (enhanced) path.

    ``_meter`` does blocking DB and (for priced tools) blocking facilitator I/O
    via a thread bridge; calling it directly inside an async ``tool_fn`` would
    block the loop for every other /mcp and HTTP request. ``anyio`` copies the
    current context so ``current_user``/``current_payment_header`` still resolve.
    """
    from anyio.to_thread import run_sync  # noqa: PLC0415

    await run_sync(_meter, entry)


# Failures the calling model should read and act on. Everything else is a bug,
# and the SDK keeps its text on the server.
_MODEL_VISIBLE = (ValueError, PermissionError, HTTPException)


def _raise_for_model(exc: Exception) -> NoReturn:
    message = exc.detail if isinstance(exc, HTTPException) else exc
    raise ToolError(str(message)) from exc


def _enforce_paywall(entry: ServiceEntry, user) -> bool:
    """Enforce the x402 paywall for a priced tool; translate to ``MCPError``.

    Returns ``enforce_payment``'s ``charged`` flag (False when x402 is disabled,
    so the caller falls back to quota).
    """
    price = entry.price
    if price is None:
        return False

    # Circular import: api_server.* loads only at call time (see _check_quota).
    from api_server.billing.paywall import (  # noqa: PLC0415
        PaymentRequiredError,
        enforce_payment,
    )
    from src.payments.request_context import current_payment_header  # noqa: PLC0415

    try:
        return enforce_payment(
            user_id=user.user_id,
            route=entry.name,
            price=price,
            asset=entry.asset,
            payment_header=current_payment_header(),
            mutating=entry.mutating,
        )
    except PaymentRequiredError as exc:
        # -32402: an app-defined analogue of HTTP 402 for the MCP transport. The
        # x402 `accepts` challenge rides in `data` so the client can pay + retry.
        raise MCPError(-32402, "Payment required", exc.challenge) from exc
    except HTTPException as exc:
        raise MCPError(
            -32402,
            "Payment error",
            exc.detail if isinstance(exc.detail, dict) else str(exc.detail),
        ) from exc


def make_tool(mcp: MCPServer, entry: ServiceEntry) -> None:
    """Register a service as an MCP tool - enhanced if an enhancer exists, else headless."""
    enhancer_entry = get_enhancer(entry.name)
    if enhancer_entry is not None:
        _make_enhanced_tool(mcp, entry, enhancer_entry)
    else:
        _make_headless_tool(mcp, entry)


def _make_headless_tool(mcp: MCPServer, entry: ServiceEntry) -> None:
    """Sync wrapper. Returns the Pydantic output model so the SDK derives outputSchema."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    def tool_fn(ctx: Context, **kwargs):
        try:
            input_obj = _authorized_input(input_model, kwargs)
            _meter(entry)
            return func(input_obj)
        except ConnectRequiredError as exc:
            # MCP-only affordance: upgrade to the SEP-1036 URL-elicitation
            # error (-32042) so capable hosts open the consent flow natively.
            reraise_with_elicitation(ctx.session, exc)
        except _MODEL_VISIBLE as exc:
            _raise_for_model(exc)

    _apply_tool_signature(
        tool_fn, entry, return_annotation=output_model, include_context=True
    )
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)


def _make_enhanced_tool(
    mcp: MCPServer, entry: ServiceEntry, enhancer_entry: EnhancerEntry
) -> None:
    """Async wrapper that calls the enhancer with an `EnhancedTool`."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    async def tool_fn(ctx: Context, **kwargs) -> CallToolResult:
        try:
            input_obj = _authorized_input(input_model, kwargs)
            await _meter_async(entry)
        except _MODEL_VISIBLE as exc:
            _raise_for_model(exc)
        tool = EnhancedTool(ctx=ctx, input=input_obj, service_fn=func)
        result = await _run_enhancer(tool, entry, enhancer_entry, ctx)
        return _build_call_tool_result(result, tool)

    _apply_tool_signature(
        tool_fn,
        entry,
        # The metadata tells the SDK which model to publish as outputSchema and
        # to validate structuredContent against.
        return_annotation=Annotated[CallToolResult, output_model],
        include_context=True,
    )
    # Declare the app's ui:// resource on the tool definition so hosts see it
    # in tools/list (per MCP Apps spec) and not only on the call result.
    meta = (
        build_app_meta(enhancer_entry.app_uri)
        if enhancer_entry.app_uri is not None
        else None
    )
    mcp.tool(name=entry.name, description=entry.description, meta=meta)(tool_fn)


def _authorized_input(input_model: type, kwargs: dict) -> BaseModel:
    """Check scopes, bind ``user_id`` to the caller, and build the input model."""
    _check_scopes()
    if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
        user = current_user()
        if user is not None:
            kwargs["user_id"] = user.user_id
        elif not kwargs.get("user_id"):
            kwargs.setdefault("user_id", "")
    return input_model(**kwargs)


async def _run_enhancer(
    tool: EnhancedTool,
    entry: ServiceEntry,
    enhancer_entry: EnhancerEntry,
    ctx: Context,
) -> BaseModel:
    """Run the enhancer, applying its fallback policy if it crashes."""
    try:
        return await enhancer_entry.fn(tool)
    except ConnectRequiredError as exc:
        # Expected condition, not an enhancer crash: the headless fallback
        # would only raise it again. MCP-only affordance: upgrade to the
        # SEP-1036 URL-elicitation error (-32042) when possible.
        reraise_with_elicitation(ctx.session, exc)
    except (MCPError, ToolError):
        raise
    except Exception as enhancer_exc:  # noqa: BLE001
        # Enhancer failures of any kind must fall back to the pure service
        # so MCP clients still get a structured result on the headless path.
        if enhancer_entry.fallback == "error":
            if isinstance(enhancer_exc, _MODEL_VISIBLE):
                _raise_for_model(enhancer_exc)
            raise
        try:
            result = _recover_from_enhancer_crash(tool, entry, ctx, enhancer_exc)
        except _MODEL_VISIBLE as exc:
            _raise_for_model(exc)
        # Discard any partial output the enhancer accumulated before crashing.
        tool.extra_content = []
        tool.app_resource_uri = None
        return result


def _recover_from_enhancer_crash(
    tool: EnhancedTool,
    entry: ServiceEntry,
    ctx: Context,
    enhancer_exc: Exception,
) -> BaseModel:
    """Resolve the headless-fallback result after an enhancer crash.

    Never executes the service twice:

    - If the enhancer's last ``tool.call()`` completed, reuse that stashed
      result - re-invoking the service would duplicate side effects for
      mutating services (e.g. a second message or a double charge).
    - If there is no completed result and the service is ``mutating``,
      propagate the enhancer error instead of silently re-executing.
    - Otherwise (non-mutating, never completed) retry headless via
      ``tool.call()`` - the single code path that invokes the service.
    """
    if tool.call_result is not None:
        log.opt(exception=enhancer_exc).error(
            "enhancer for {!r} crashed after tool.call(); reusing the "
            "completed service result",
            entry.name,
        )
        return tool.call_result
    if entry.mutating:
        raise enhancer_exc
    log.opt(exception=enhancer_exc).error(
        "enhancer for {!r} crashed; falling back to headless", entry.name
    )
    try:
        return tool.call()
    except ConnectRequiredError as exc:
        reraise_with_elicitation(ctx.session, exc)


def _resolve_signature_default(param: inspect.Parameter, field_info: Any) -> Any:
    """Return a signature-safe default for a synthesized tool parameter.

    ``inspect.signature(model)`` renders a Pydantic ``default_factory`` field
    with the private ``<factory>`` sentinel instead of a real value. The SDK's
    ``func_metadata`` copies each parameter's ``default`` verbatim into the
    tool's argument model (see its ``func_arg_to_pydantic_field``), so that
    sentinel would be handed back as the argument value whenever the caller
    omits the field - failing validation with "Input should be a valid list
    ... input_value=<factory>" and making a nominally optional field
    effectively required. Resolve the factory to a concrete value so the field
    stays genuinely optional.

    ``FieldInfo.get_default`` runs the factory for us, dispatching between the
    zero-arg and validated-data factory forms by arity (``validated_data`` is
    ignored by a zero-arg factory) and returning a fresh value per call - so
    mutable defaults stay isolated across tool invocations.
    """
    if field_info is None or field_info.default_factory is None:
        return param.default
    return field_info.get_default(call_default_factory=True, validated_data={})


def _apply_tool_signature(
    tool_fn: Any,
    entry: ServiceEntry,
    return_annotation: Any,
    include_context: bool = False,
) -> None:
    """Synthesize __signature__ and __annotations__ so the SDK derives input/output schema."""
    tool_fn.__name__ = entry.name
    tool_fn.__doc__ = entry.description

    input_sig = inspect.signature(entry.input_model)
    annotations: dict = {k: v.annotation for k, v in input_sig.parameters.items()}
    if include_context:
        annotations["ctx"] = Context
    annotations["return"] = return_annotation
    tool_fn.__annotations__ = annotations

    # Map each signature parameter to its FieldInfo (keyed by alias when set,
    # matching how Pydantic names the synthesized __init__ parameters) so we can
    # resolve default_factory sentinels below.
    field_by_param = {
        (fi.alias or name): fi
        for name, fi in entry.input_model.model_fields.items()  # ty: ignore[unresolved-attribute]
    }
    params = [
        p.replace(default=_resolve_signature_default(p, field_by_param.get(name)))
        for name, p in input_sig.parameters.items()
    ]
    if include_context:
        ctx_param = inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context
        )
        params = [ctx_param, *params]
    tool_fn.__signature__ = input_sig.replace(
        parameters=params, return_annotation=return_annotation
    )


def _build_call_tool_result(result: BaseModel, tool: EnhancedTool) -> CallToolResult:
    """Assemble CallToolResult from a service result + accumulated extras.

    `result` must be a Pydantic BaseModel - the service's output_model
    instance. Other return types are rejected so outputSchema/structuredContent
    stay consistent and failures surface here rather than as opaque
    pydantic_core ValidationErrors deep inside CallToolResult.
    """
    if not isinstance(result, BaseModel):
        raise TypeError(
            f"Enhanced tool result must be a Pydantic BaseModel, got "
            f"{type(result).__name__}. Enhancers must return their service's "
            "output_model instance."
        )
    content: list = [
        TextContent(type="text", text=result.model_dump_json()),
        *tool.extra_content,
    ]
    kwargs: dict = {
        "content": content,
        "structured_content": result.model_dump(mode="json"),
    }
    app_meta = tool.app_meta()
    if app_meta is not None:
        kwargs["meta"] = app_meta
    return CallToolResult(**kwargs)
