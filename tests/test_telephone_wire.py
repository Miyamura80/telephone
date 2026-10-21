"""Two real MCP clients holding a call through a live server (no LLM).

Boots the FastAPI app (with ``/mcp``) under uvicorn in a background thread and
drives it with the official MCP client over streamable HTTP: a scripted
"caller" and "implementer" talk concurrently, exactly as two Codex sessions
would, and every message crosses the wire as a tool result. The call runs in
both protocol eras the server speaks: the 2025 handshake (``mode="legacy"``,
what Codex CLI uses today) and 2026-07-28 (no session, no handshake). The MCP
Events extension is covered in ``test_events_wire.py``.
"""

import asyncio
import time

import httpx
from mcp import Client
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE

from services.line_events import line_events
from services.switchboard import switchboard
from tests.test_template import TestTemplate
from tests.wire_helpers import ERAS, _client, _json_rpc


async def _session_call(session: Client, tool: str, args: dict) -> dict:
    result = await session.call_tool(tool, args)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


class _Agent:
    """One MCP client; `run` hands it to a script and returns what it returns."""

    def __init__(self, base: str, key: str, mode: str = "auto") -> None:
        self.base, self.key, self.mode = base, key, mode

    async def run(self, script) -> list[dict]:
        async with _client(self.base, self.key, self.mode) as client:
            return await script(client)


async def _caller(session: Client) -> list[dict]:
    seen = []
    view = await _session_call(
        session,
        "dial",
        {
            "line": "acme-bug",
            "message": "acme-cli sync: KeyError 'region'",
            "wait_s": 10,
        },
    )
    seen.append(view)
    seat = view["seat"]
    assert view["outcome"] == "message", view
    view = await _session_call(
        session,
        "say",
        {
            "line": "acme-bug",
            "seat": seat,
            "text": "2.4.1, no region key",
            "wait_s": 10,
        },
    )
    seen.append(view)
    assert view["outcome"] == "message", view
    seen.append(
        await _session_call(
            session,
            "hang_up",
            {"line": "acme-bug", "seat": seat, "reason": "works", "resolved": True},
        )
    )
    return seen


async def _implementer(session: Client) -> list[dict]:
    seen = []
    view = await _session_call(session, "answer", {"line": "acme-bug", "wait_s": 10})
    while view["outcome"] == "no_call":
        view = await _session_call(
            session, "answer", {"line": "acme-bug", "wait_s": 10}
        )
    seen.append(view)
    seat = view["seat"]
    view = await _session_call(
        session,
        "say",
        {"line": "acme-bug", "seat": seat, "text": "Which version?", "wait_s": 10},
    )
    seen.append(view)
    view = await _session_call(
        session,
        "say",
        {"line": "acme-bug", "seat": seat, "text": "Fixed in 2.4.2", "wait_s": 10},
    )
    seen.append(view)
    while view["outcome"] != "ended":
        view = await _session_call(
            session, "listen", {"line": "acme-bug", "seat": seat, "wait_s": 10}
        )
    seen.append(view)
    return seen


class TestTwoAgentsOnTheWire(TestTemplate):
    def setup_method(self):
        switchboard.reset()
        line_events.reset()

    @ERAS
    def test_caller_and_implementer_hold_a_call(self, server, mode):
        base, key = server

        async def main():
            return await asyncio.gather(
                _Agent(base, key, mode).run(_caller),
                _Agent(base, key, mode).run(_implementer),
            )

        caller_views, impl_views = asyncio.run(asyncio.wait_for(main(), 60))
        assert impl_views[0]["messages"][0]["text"].startswith("acme-cli sync")
        assert caller_views[0]["messages"][0]["text"] == "Which version?"
        assert caller_views[1]["messages"][0]["text"] == "Fixed in 2.4.2"
        assert caller_views[2]["resolved"] is True
        assert impl_views[-1]["outcome"] == "ended"
        assert "caller hung up" in impl_views[-1]["ended_reason"]

    @ERAS
    def test_a_waiting_listen_does_not_block_other_requests(self, server, mode):
        base, key = server

        dialed: asyncio.Event | None = None

        async def waiter(session):
            assert dialed is not None
            view = await _session_call(
                session, "dial", {"line": "slow", "message": "hi", "wait_s": 0}
            )
            dialed.set()
            started = time.monotonic()
            await _session_call(
                session,
                "listen",
                {"line": "slow", "seat": view["seat"], "wait_s": 3},
            )
            return started, time.monotonic()

        async def prober(session):
            assert dialed is not None
            await dialed.wait()  # the line exists; the dial is not raced
            await asyncio.sleep(0.3)
            started = time.monotonic()
            view = await _session_call(session, "answer", {"line": "slow", "wait_s": 0})
            assert view["outcome"] == "message"
            return started, time.monotonic()

        async def main():
            nonlocal dialed
            dialed = asyncio.Event()
            return await asyncio.gather(
                _Agent(base, key, mode).run(waiter), _Agent(base, key, mode).run(prober)
            )

        (listen_start, listen_end), (probe_start, probe_end) = asyncio.run(
            asyncio.wait_for(main(), 30)
        )
        # The probe ran entirely inside the listen's 3s wait, and fast.
        assert listen_start < probe_start and probe_end < listen_end
        assert probe_end - probe_start < 2


class TestMalformedRequests(TestTemplate):
    """The SDK, not our code, now parses every /mcp body; pin that malformed
    input is answered cleanly instead of crashing the app."""

    def test_oversized_body_is_rejected(self, server):
        base, key = server
        resp = httpx.post(
            f"{base}/mcp",
            headers={"X-API-KEY": key, "content-type": "application/json"},
            content=b"x" * (DEFAULT_MAX_REQUEST_BODY_SIZE + 1),
            timeout=10,
        )
        assert resp.status_code == 413

    def test_non_string_method_passes_through(self, server):
        base, key = server
        resp = httpx.post(
            f"{base}/mcp",
            headers={"X-API-KEY": key, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": 5, "params": {}},
            timeout=10,
        )
        assert resp.status_code < 500

    def test_deeply_nested_body_passes_through(self, server):
        base, key = server
        resp = httpx.post(
            f"{base}/mcp",
            headers={"X-API-KEY": key, "content-type": "application/json"},
            content=b"[" * 100_000,
            timeout=10,
        )
        assert resp.status_code < 500

    def test_events_call_without_jsonrpc_version_is_rejected(self, server):
        base, key = server
        resp = httpx.post(
            f"{base}/mcp",
            headers={"X-API-KEY": key, "Accept": "application/json, text/event-stream"},
            json={"id": 3, "method": "events/list", "params": {}},
            timeout=10,
        )
        assert resp.status_code >= 400 or "error" in _json_rpc(resp)
