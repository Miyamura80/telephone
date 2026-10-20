"""Two real MCP clients holding a call through a live server (no LLM).

Boots the FastAPI app (with ``/mcp``) under uvicorn in a background thread and
drives it with the official MCP client over streamable HTTP: a scripted
"caller" and "implementer" talk concurrently, exactly as two Codex sessions
would, and every message crosses the wire as a tool result. Also covers the
MCP Events path (``events/*`` JSON-RPC + signed webhook delivery).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_server.auth.api_key_auth import create_api_key
from api_server.server import app
from common import global_config
from db import engine as db_engine
from db.base import Base
from services.line_events import line_events
from services.switchboard import switchboard
from tests.test_template import TestTemplate


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    # A file DB, not :memory: + StaticPool: the server's auth and quota checks
    # run on several threads at once and each needs its own connection.
    db_file = tmp_path_factory.mktemp("db") / "wire.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng)
    with ExitStack() as stack:
        stack.enter_context(patch.object(db_engine, "_engine", eng))
        stack.enter_context(patch.object(db_engine, "_SessionLocal", session_factory))
        mock_cfg = stack.enter_context(
            patch("api_server.middleware.mcp_auth.global_config")
        )
        mock_cfg.WORKOS_CLIENT_ID = None
        mock_cfg.WORKOS_AUTHKIT_DOMAIN = None
        stack.enter_context(
            patch.object(
                global_config.telephone,
                "transcript_dir",
                str(tmp_path_factory.mktemp("transcripts")),
            )
        )
        with session_factory() as session:
            key, _ = create_api_key(session, user_id="wire-test", scopes=["*"])
        port = _free_port()
        srv = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not srv.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", key
        finally:
            srv.should_exit = True
            thread.join(timeout=15)


async def _session_call(session: ClientSession, tool: str, args: dict) -> dict:
    result = await session.call_tool(tool, args)
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


class _Agent:
    """One MCP client session; `call` returns a tool's structured result."""

    def __init__(self, base: str, key: str) -> None:
        self.base, self.key = base, key

    async def run(self, script) -> list[dict]:
        headers = {"X-API-KEY": self.key}
        async with (
            streamablehttp_client(f"{self.base}/mcp", headers=headers) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            return await script(session)


async def _caller(session: ClientSession) -> list[dict]:
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


async def _implementer(session: ClientSession) -> list[dict]:
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

    def test_caller_and_implementer_hold_a_call(self, server):
        base, key = server

        async def main():
            return await asyncio.gather(
                _Agent(base, key).run(_caller), _Agent(base, key).run(_implementer)
            )

        caller_views, impl_views = asyncio.run(asyncio.wait_for(main(), 60))
        assert impl_views[0]["messages"][0]["text"].startswith("acme-cli sync")
        assert caller_views[0]["messages"][0]["text"] == "Which version?"
        assert caller_views[1]["messages"][0]["text"] == "Fixed in 2.4.2"
        assert caller_views[2]["resolved"] is True
        assert impl_views[-1]["outcome"] == "ended"
        assert "caller hung up" in impl_views[-1]["ended_reason"]

    def test_a_waiting_listen_does_not_block_other_requests(self, server):
        base, key = server

        async def waiter(session):
            view = await _session_call(
                session, "dial", {"line": "slow", "message": "hi", "wait_s": 0}
            )
            await _session_call(
                session,
                "listen",
                {"line": "slow", "seat": view["seat"], "wait_s": 3},
            )

        async def prober(session):
            await asyncio.sleep(0.5)
            started = time.monotonic()
            view = await _session_call(session, "answer", {"line": "slow", "wait_s": 0})
            assert view["outcome"] == "message"
            return time.monotonic() - started

        async def main():
            return await asyncio.gather(
                _Agent(base, key).run(waiter), _Agent(base, key).run(prober)
            )

        _, probe_s = asyncio.run(asyncio.wait_for(main(), 30))
        assert probe_s < 2


def _rpc(base: str, key: str, method: str, params: dict) -> dict:
    resp = httpx.post(
        f"{base}/mcp",
        headers={"X-API-KEY": key, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _verify(secret: bytes, headers: dict, body: bytes) -> bool:
    """Independent Standard Webhooks verification (as a subscriber would do)."""
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest())
    return headers["webhook-signature"] == "v1," + expected.decode()


class TestMcpEvents(TestTemplate):
    def setup_method(self):
        switchboard.reset()
        line_events.reset()

    def test_initialize_advertises_events(self, server):
        base, key = server

        async def check():
            headers = {"X-API-KEY": key}
            async with (
                streamablehttp_client(f"{base}/mcp", headers=headers) as (r, w, _),
                ClientSession(r, w) as session,
            ):
                init = await session.initialize()
                return init.capabilities.model_dump()

        caps = asyncio.run(check())
        assert caps["events"] == {"delivery": ["webhook"]}

    def test_events_list(self, server):
        base, key = server
        reply = _rpc(base, key, "events/list", {})
        names = {e["name"] for e in reply["result"]["events"]}
        assert names == {"line.message", "line.ended"}
        assert "inputSchema" in reply["result"]["events"][0]

    def test_subscribe_and_receive_signed_webhooks(self, server):
        base, key = server
        raw_secret = b"s" * 32
        secret = "whsec_" + base64.b64encode(raw_secret).decode()
        deliveries: list[tuple[str, bytes, dict]] = []
        delivered = threading.Event()

        def fake_send(url, body, headers):
            deliveries.append((url, body, headers))
            delivered.set()
            return 200

        dial = switchboard.dial("evt", "help")
        caller_seat = dial[1]
        with (
            patch.object(
                global_config.telephone.events, "allow_insecure_callbacks", True
            ),
            patch.object(line_events, "_sender", fake_send),
        ):
            reply = _rpc(
                base,
                key,
                "events/subscribe",
                {
                    "name": "line.message",
                    "arguments": {"line": "evt", "seat": caller_seat},
                    "delivery": {
                        "mode": "webhook",
                        "url": "http://localhost:9/hook",
                        "secret": secret,
                    },
                },
            )
            sub = reply["result"]
            assert sub["id"].startswith("sub_") and "expiresAt" in sub
            claimed = switchboard.answer("evt", wait_s=0)
            assert claimed is not None
            implementer_seat = claimed[1]
            switchboard.say("evt", implementer_seat, "Which version?")
            assert delivered.wait(5), "no webhook delivered"

        url, body, headers = deliveries[0]
        assert url == "http://localhost:9/hook"
        assert headers["X-MCP-Subscription-Id"] == sub["id"]
        assert _verify(raw_secret, headers, body)
        payload = json.loads(body)
        assert payload["name"] == "line.message"
        assert payload["data"]["message"]["text"] == "Which version?"
        assert "listen" in payload["data"]["next_step"]

        reply = _rpc(base, key, "events/unsubscribe", {"id": sub["id"]})
        assert reply["result"] == {}

    def test_subscribe_rejects_insecure_callback_and_bad_secret(self, server):
        base, key = server
        _, seat = switchboard.dial("evt2", "help")
        params = {
            "name": "line.message",
            "arguments": {"line": "evt2", "seat": seat},
            "delivery": {"url": "http://127.0.0.1/hook", "secret": "whsec_" + "A" * 44},
        }
        reply = _rpc(base, key, "events/subscribe", params)
        assert reply["error"]["code"] == -32602
        assert "https" in reply["error"]["message"]
        params["delivery"] = {"url": "https://example.com/hook", "secret": "nope"}
        reply = _rpc(base, key, "events/subscribe", params)
        assert reply["error"]["code"] == -32602

    def test_subscribe_rejects_foreign_seat(self, server):
        base, key = server
        switchboard.dial("evt3", "help")
        reply = _rpc(
            base,
            key,
            "events/subscribe",
            {
                "name": "line.message",
                "arguments": {"line": "evt3", "seat": "seat_forged"},
                "delivery": {"url": "https://example.com/h", "secret": "whsec_x"},
            },
        )
        assert reply["error"]["code"] == -32602
