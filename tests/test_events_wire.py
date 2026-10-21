"""MCP Events over a live server: discovery, subscribe, signed webhooks.

Uses the same uvicorn-backed server as ``test_telephone_wire.py`` (see
``wire_helpers.py``) and covers both protocol eras' capability keys.
"""

import asyncio
import base64
import json
import threading
from unittest.mock import patch

from common import global_config
from mcp_server.events import EVENTS_EXTENSION
from services.line_events import line_events
from services.switchboard import switchboard
from tests.test_template import TestTemplate
from tests.wire_helpers import _client, _rpc, _rpc_2026, _verify


class TestMcpEvents(TestTemplate):
    def setup_method(self):
        switchboard.reset()
        line_events.reset()

    def test_the_events_extension_is_advertised(self, server):
        # `capabilities.extensions` exists from 2026-07-28; the SDK strips it
        # for 2025-era clients, which get the top-level key tested below.
        base, key = server

        async def check():
            async with _client(base, key) as client:
                assert client.protocol_version >= "2026-07-28"
                return client.server_capabilities

        caps = asyncio.run(check())
        assert caps is not None and caps.extensions is not None
        assert caps.extensions[EVENTS_EXTENSION] == {"delivery": ["webhook"]}

    def test_discover_carries_both_capability_keys(self, server):
        # ChatGPT is on 2026-07-28 and reads `capabilities.events`; the SEP
        # names the extension. Both must survive the SDK's result sieve.
        base, key = server
        caps = _rpc_2026(base, key, "server/discover", {})["result"]["capabilities"]
        assert caps["extensions"][EVENTS_EXTENSION] == {"delivery": ["webhook"]}
        assert caps["events"] == {"delivery": ["webhook"]}

    def test_events_list_in_the_2026_shape(self, server):
        base, key = server
        reply = _rpc_2026(base, key, "events/list", {})
        assert {e["name"] for e in reply["result"]["events"]} == {
            "line.message",
            "line.ended",
        }

    def test_initialize_also_carries_top_level_events(self, server):
        # ChatGPT reads the pre-SEP `capabilities.events` key.
        base, key = server
        reply = _rpc(
            base,
            key,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0"},
            },
        )
        assert reply["result"]["capabilities"]["events"] == {"delivery": ["webhook"]}

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

        challenges: list[dict] = []

        def echo_challenge(url, body, headers):
            challenges.append({"body": json.loads(body), "headers": headers})
            reply = {"challenge": json.loads(body)["challenge"]}
            return 200, json.dumps(reply).encode()

        dial = switchboard.dial("evt", "help")
        caller_seat = dial[1]
        with (
            patch.object(
                global_config.telephone.events, "allow_insecure_callbacks", True
            ),
            patch.object(line_events, "_sender", fake_send),
            patch.object(line_events, "_verifier", echo_challenge),
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
            assert sub["id"].startswith("sub_") and "refreshBefore" in sub
            assert sub["cursor"] is None and sub["truncated"] is False
            # The callback proved it wanted these events before any delivery.
            assert challenges[0]["body"]["type"] == "verification"
            # Owned by the authenticated caller, so nobody else can unsubscribe it.
            assert line_events._subs[sub["id"]].owner == "wire-test"
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
        assert "say" in payload["data"]["next_step"]

        # Unsubscribe by the key it was subscribed with, as ChatGPT does.
        reply = _rpc(
            base,
            key,
            "events/unsubscribe",
            {
                "name": "line.message",
                "arguments": {"line": "evt", "seat": caller_seat},
                "delivery": {"url": "http://localhost:9/hook"},
            },
        )
        assert reply["result"] == {}
        assert line_events._subs == {}

    def test_a_callback_that_fails_the_challenge_gets_32015(self, server):
        base, key = server
        _, seat = switchboard.dial("evt4", "help")
        secret = "whsec_" + base64.b64encode(b"s" * 32).decode()
        with (
            patch.object(
                global_config.telephone.events, "allow_insecure_callbacks", True
            ),
            patch.object(line_events, "_verifier", lambda u, b, h: (404, b"")),
        ):
            reply = _rpc(
                base,
                key,
                "events/subscribe",
                {
                    "name": "line.message",
                    "arguments": {"line": "evt4", "seat": seat},
                    "delivery": {"url": "http://localhost:9/hook", "secret": secret},
                },
            )
        assert reply["error"]["code"] == -32015
        assert reply["error"]["data"] == {"reason": "challenge_failed"}

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
        # Isolate the secret check: allow the callback so only the secret fails.
        params["delivery"] = {"url": "http://localhost/hook", "secret": "nope"}
        with patch.object(
            global_config.telephone.events, "allow_insecure_callbacks", True
        ):
            reply = _rpc(base, key, "events/subscribe", params)
        assert "whsec_" in reply["error"]["message"]
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
