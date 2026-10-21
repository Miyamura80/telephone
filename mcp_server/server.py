"""MCP server (SDK v2 ``MCPServer``) that registers services as MCP tools and exposes app HTML resources.

Two registration paths (see `_tool_factory.py` for details):

- **Headless** (default): sync wrapper, returns the Pydantic output model directly.
- **Enhanced** (opt-in via `@enhance`): async wrapper with `Context`, may elicit
  user input, attach images/audio, or render an MCP App (iframe dashboard).

Primary transport is streamable HTTP, mounted on the FastAPI app at ``/mcp``
(see ``api_server/server.py``). Stdio is supported via the ``telephone-mcp``
console script for local dev / Claude Desktop only.

See ``mcp_server/MCP_UI_ARCHITECTURE.md`` and ``mcp_server/MCP_UI_EDGE_CASES.md``.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger as log
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from common import global_config
from mcp_server._tool_factory import make_tool
from mcp_server.events import LineEventsExtension, advertise_top_level_events
from services import ServiceEntry, discover_services, get_registry

_APPS_DIR = Path(__file__).parent / "apps"
_APP_MIME_TYPE = "text/html;profile=mcp-app"

# Module-level singleton: app_tools / enhancers may import this at module-load
# time (e.g. ``@mcp.tool``), so it must exist before discovery runs.
_MCP_INSTRUCTIONS = (
    "Telephone is a switchboard for agent-to-agent calls on named lines. "
    "Stuck on something another agent can fix (e.g. a bug in a tool whose code "
    "you cannot change)? `dial` a line with a precise opening message, then keep "
    "talking with `say` and `listen` until the problem is solved, and `hang_up` "
    "with resolved=true or false. Asked to take calls? `answer` the line, ask "
    "diagnosis questions with `say`, fix the problem, and tell the caller what "
    "changed. Pass your seat token on every call after dial/answer. When a tool "
    "returns outcome='waiting', call `listen` again instead of ending your turn; "
    "stop only when outcome='ended'. Event-driven clients subscribe to "
    "`line.message` via events/subscribe (extension "
    "io.modelcontextprotocol/events) and pass wait_s=0."
)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding allowlist for the streamable-HTTP transport.

    The SDK enables DNS-rebinding protection and, by default, only
    whitelists localhost - so any real deployment
    behind a custom domain gets ``421 Misdirected Request`` on ``/mcp``. We keep
    the protection on but additionally allow the public host derived from
    ``MCP_PUBLIC_URL`` (so it auto-tracks whatever domain the server is deployed
    at) plus loopback for stdio/local dev and the test client.
    """
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "localhost", "127.0.0.1"]
    # Loopback origins (any port) mirror the loopback hosts above: stdio/local
    # dev, the test client, and the conformance harness (which drives /mcp from
    # http://127.0.0.1:<port>) send an Origin header that must be allowed, or
    # the DNS-rebinding middleware answers 403. A remote attacker cannot forge a
    # loopback Origin, so this does not weaken cross-origin protection.
    loopback_origins = [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "http://localhost:*",
        "http://127.0.0.1:*",
        "http://[::1]:*",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost:*",
        "https://127.0.0.1:*",
        "https://[::1]:*",
    ]
    origins: list[str] = [*loopback_origins, *global_config.server.allowed_origins]
    public = global_config.MCP_PUBLIC_URL
    if public:
        parts = urlsplit(public)
        if parts.netloc:
            # Exact entry covers the default-port (443/80) case where the Host
            # header carries no port; the ":*" entry covers explicit ports.
            hosts.extend([parts.netloc, f"{parts.netloc}:*"])
            origins.append(f"{parts.scheme or 'https'}://{parts.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# ``stateless_http=True`` (the config default) only changes how 2025-era
# clients are served: no per-session state is kept for ``Mcp-Session-Id``, so a
# restart, a redeploy or a request routed to another replica does not end their
# session ("MCP session has been terminated", mcp_session_terminated). Clients on
# the 2026-07-28 revision have no session at all and never read this flag.
#
# The trade-off for those legacy clients: server-initiated requests (sampling,
# push elicitation) cannot complete without a session, so every elicitation call
# site gates on the client having DECLARED the capability, which a stateless
# request never shows, and falls back. Deployments that want host-native push
# elicitation for legacy clients must run one replica with
# ``server.mcp_stateless_http: false``.
mcp: MCPServer = MCPServer(
    "telephone",
    instructions=_MCP_INSTRUCTIONS,
    extensions=[LineEventsExtension()],
    middleware=[advertise_top_level_events],
)

_populated: bool = False
_EXCLUDED_DEFAULT_MCP_SERVICES = frozenset(
    {
        "config_get",
        "config_set",
        "config_show",
        "doctor",
        "doctor_fix",
        "greet",
    }
)


def build_mcp_server() -> MCPServer:
    """Populate the MCPServer singleton and return it. Idempotent."""
    global _populated
    if _populated:
        return mcp

    # Deferred by design (see CLAUDE.md): discovery imports app_tools /
    # enhancer modules at registration time, and those modules import this
    # module's `mcp` singleton back, so the packages must only load here.
    from mcp_server.app_tools import discover_app_tools  # noqa: PLC0415
    from mcp_server.enhancers import discover_enhancers  # noqa: PLC0415

    discover_services()
    discover_enhancers()
    discover_app_tools()

    for entry in get_registry():
        if entry.name in _EXCLUDED_DEFAULT_MCP_SERVICES:
            log.debug("Skipping default MCP registration for service {!r}", entry.name)
            continue
        make_tool(mcp, entry)
    _register_app_resources(mcp)

    _populated = True
    return mcp


def llm_tool_surface() -> list[ServiceEntry]:
    """Service entries this server exposes to the LLM as MCP tools.

    The registry minus the CLI-only defaults that ``build_mcp_server`` skips.
    App-only tools (registered directly via ``@mcp.tool`` and hidden from the
    LLM) are not in the service registry, so they are excluded automatically.

    This is the LLM-facing tool surface advertised pre-connection in the
    SEP-2127 server card (``/.well-known/mcp/server-card.json``). Sorted by name
    so the committed landing-page snapshot is stable across environments.
    """
    build_mcp_server()
    surface = (
        e for e in get_registry() if e.name not in _EXCLUDED_DEFAULT_MCP_SERVICES
    )
    return sorted(surface, key=lambda e: e.name)


def _register_app_resources(mcp: MCPServer) -> None:
    """Register ui:// resources for each MCP App with a built dist/mcp-app.html."""
    if not _APPS_DIR.is_dir():
        return
    for app_dir in sorted(_APPS_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        html_path = app_dir / "dist" / "mcp-app.html"
        uri = f"ui://telephone/{app_dir.name}"
        _register_app_resource(mcp, uri, html_path, app_dir.name)


def _register_app_resource(
    mcp: MCPServer, uri: str, html_path: Path, app_name: str
) -> None:
    @mcp.resource(uri, mime_type=_APP_MIME_TYPE, name=f"{app_name} app")
    def _read_app() -> str:
        if not html_path.exists():
            log.warning("MCP App {!r} missing build at {}", app_name, html_path)
            return f"<!-- {app_name} not built. Run `make build_apps`. -->"
        return html_path.read_text()


def mount_on(app, path: str = "/mcp") -> Starlette:
    """Mount the streamable-HTTP MCP server onto a Starlette/FastAPI app.

    ``streamable_http_app()`` already serves at ``/mcp`` internally,
    so we mount it at root to avoid a doubled prefix. Caller must also include
    :func:`lifespan` in the parent app's lifespan to start the session manager.

    Returns the mounted sub-app. Because the mount sits at ``/``, it - not the
    parent - receives every path the parent's own routes don't claim, so a
    caller that wants to customize the 404 for unknown paths must register the
    handler on the returned app.
    """
    if path != "/mcp":
        raise ValueError(
            "Custom mount paths are not supported; the MCP app serves at /mcp internally."
        )
    mcp = build_mcp_server()
    mcp_app = mcp.streamable_http_app(
        stateless_http=global_config.server.mcp_stateless_http,
        transport_security=_transport_security(),
    )
    app.mount("/", mcp_app)
    return mcp_app


@asynccontextmanager
async def lifespan(_app):
    """Async context manager that runs the streamable-HTTP session manager.

    The parent FastAPI app must include this in its ``lifespan=`` argument or
    incoming /mcp requests will fail with "Task group is not initialized".
    """
    mcp = build_mcp_server()
    sm = mcp.session_manager
    # StreamableHTTPSessionManager.run() refuses re-entry once _has_started is set.
    # Reset it so the same instance can be restarted (tests with --count, hot-reload).
    sm._has_started = False
    async with sm.run():
        yield


def main() -> None:
    """Run the MCP server on stdio transport (legacy / local-dev only)."""
    print(
        "[telephone-mcp] stdio transport is legacy; "
        "prefer `telephone-serve` and connect via streamable HTTP at /mcp.",
        file=sys.stderr,
    )
    server = build_mcp_server()
    server.run(transport="stdio")


# Populate the singleton at import time so tests / direct importers that reach
# into ``mcp._tool_manager`` see registered tools without an explicit build call.
build_mcp_server()
