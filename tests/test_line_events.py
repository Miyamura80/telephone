"""Unit tests for MCP Events delivery (retries, drops, expiry, signing)."""

import base64
import json
import socket
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from common import global_config
from models.telephone_events import SubscribeParams
from services.line_events import (
    EventsError,
    LineEvents,
    _check_callback_url,
    _http_send,
    decode_secret,
    sign,
    subscription_id,
)
from services.switchboard import Switchboard
from tests.test_template import TestTemplate

SECRET = "whsec_" + base64.b64encode(b"k" * 32).decode()


def _echo(url, body, headers):
    """A callback that passes verification: echo the challenge back."""
    challenge = json.loads(body)["challenge"]
    return 200, json.dumps({"challenge": challenge}).encode()


class InlineExecutor(Executor):
    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture(autouse=True)
def _insecure_callbacks_and_no_transcripts():
    with (
        patch.object(global_config.telephone.events, "allow_insecure_callbacks", True),
        patch.object(global_config.telephone, "transcript_dir", None),
    ):
        yield


def _setup(statuses: list[int]):
    board = Switchboard()
    sent: list[dict] = []
    sleeps: list[float] = []

    def sender(url, body, headers):
        sent.append(headers)
        return statuses.pop(0) if statuses else 200

    def schedule(delay, fn):
        sleeps.append(delay)
        fn()

    events = LineEvents(
        board,
        sender=sender,
        executor=InlineExecutor(),
        schedule=schedule,
        verifier=_echo,
    )
    _, caller = board.dial("ev", "help")
    params = SubscribeParams.model_validate(
        {
            "name": "line.message",
            "arguments": {"line": "ev", "seat": caller},
            "delivery": {"url": "http://localhost/hook", "secret": SECRET},
        }
    )
    sub = events.subscribe(params, owner="u1")
    claimed = board.answer("ev", wait_s=0)
    assert claimed is not None
    return board, events, sub, claimed[1], sent, sleeps


class TestLineEvents(TestTemplate):
    def test_retries_with_backoff_then_succeeds(self):
        board, _, _, implementer, sent, sleeps = _setup([500, 502, 200])
        board.say("ev", implementer, "hello")
        assert len(sent) == 3
        assert sleeps == [1.0, 5.0]
        # The same event id is reused across retries.
        assert len({h["webhook-id"] for h in sent}) == 1

    def test_gone_drops_the_subscription(self):
        board, events, _, implementer, sent, _ = _setup([410])
        board.say("ev", implementer, "one")
        board.say("ev", implementer, "two")
        assert len(sent) == 1
        assert events._subs == {}

    def test_callback_that_turns_private_is_dropped_at_send_time(self):
        board, events, _, implementer, sent, _ = _setup([])

        def private_sender(url, body, headers):
            # The real sender re-runs the guard at send time; simulate DNS
            # now pointing somewhere private.
            raise EventsError("delivery.url must resolve to a public address")

        events._sender = private_sender
        board.say("ev", implementer, "hello")
        assert events._subs == {}

    def test_payload_too_large_skips_only_that_event(self):
        board, events, sub, implementer, sent, sleeps = _setup([413])
        board.say("ev", implementer, "huge")
        board.say("ev", implementer, "small")
        assert len(sent) == 2 and sleeps == []
        assert sub.id in events._subs

    def test_gives_up_after_max_attempts(self):
        board, events, sub, implementer, sent, sleeps = _setup([500] * 10)
        board.say("ev", implementer, "hello")
        cfg = global_config.telephone.events
        assert len(sent) == cfg.max_attempts
        assert len(sleeps) == cfg.max_attempts - 1
        assert sub.id in events._subs  # a failed event does not drop the sub

    def test_line_ended_is_delivered(self):
        board, events, sub, implementer, _, _ = _setup([])
        params = SubscribeParams.model_validate(
            {
                "name": "line.ended",
                "arguments": {"line": "ev", "seat": events._subs[sub.id].seat},
                "delivery": {"url": "http://localhost/hook", "secret": SECRET},
            }
        )
        events.subscribe(params, owner="u1")
        bodies: list[dict] = []

        def sender(url, body, headers):
            bodies.append(json.loads(body))
            return 200

        events._sender = sender
        board.hang_up("ev", implementer, "fixed it", None)
        ended = [b for b in bodies if b["name"] == "line.ended"]
        assert len(ended) == 1 and "fixed it" in ended[0]["data"]["reason"]

    def test_no_delivery_after_unsubscribe(self):
        board, events, sub, implementer, sent, _ = _setup([500])
        statuses = [500, 200]

        def sender(url, body, headers):
            sent.append(headers)
            events.unsubscribe(sub.id, owner="u1")  # gone before the retry
            return statuses.pop(0)

        events._sender = sender
        board.say("ev", implementer, "hello")
        assert len(sent) == 1

    def test_subscribe_to_an_ended_call_is_rejected(self):
        board, events, sub, implementer, _, _ = _setup([])
        board.hang_up("ev", implementer, "done", None)
        caller = events._subs[sub.id].seat
        params = SubscribeParams.model_validate(
            {
                "name": "line.ended",
                "arguments": {"line": "ev", "seat": caller},
                "delivery": {"url": "http://localhost/other", "secret": SECRET},
            }
        )
        with pytest.raises(EventsError, match="already ended"):
            events.subscribe(params, owner="u1")

    def test_expired_subscriptions_are_skipped(self):
        board, events, sub, implementer, sent, _ = _setup([])
        events._subs[sub.id].expires_at = 0
        board.say("ev", implementer, "hello")
        assert sent == []

    def test_retry_after_expiry_is_not_sent(self):
        board, events, sub, implementer, sent, _ = _setup([500, 200])
        sender = events._sender

        def expiring_sender(url, body, headers):
            events._subs[sub.id].expires_at = 0  # the TTL lapses mid-retry
            return sender(url, body, headers)

        events._sender = expiring_sender
        board.say("ev", implementer, "hello")
        assert len(sent) == 1
        assert events._subs == {}

    def test_subscribe_racing_hang_up_is_rejected(self):
        board, events, sub, implementer, _, _ = _setup([])
        caller = events._subs[sub.id].seat
        real = board.transcript
        calls = 0

        def transcript(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:  # the call ends between the check and registration
                board.hang_up("ev", implementer, "done", None)
            return real(*args, **kwargs)

        params = SubscribeParams.model_validate(
            {
                "name": "line.ended",
                "arguments": {"line": "ev", "seat": caller},
                "delivery": {"url": "http://localhost/other", "secret": SECRET},
            }
        )
        with (
            patch.object(board, "transcript", transcript),
            pytest.raises(EventsError, match="already ended"),
        ):
            events.subscribe(params, owner="u1")
        assert list(events._subs) == [sub.id]

    def test_line_ended_never_overtakes_the_last_message(self):
        board = Switchboard()
        order: list[str] = []

        def sender(url, body, headers):
            name = json.loads(body)["name"]
            if name == "line.message":
                time.sleep(0.2)  # a slow first delivery
            order.append(name)
            return 200

        pool = ThreadPoolExecutor(max_workers=4)
        events = LineEvents(board, sender=sender, executor=pool, verifier=_echo)
        _, caller = board.dial("ord", "help")
        for name in ("line.message", "line.ended"):
            events.subscribe(
                SubscribeParams.model_validate(
                    {
                        "name": name,
                        "arguments": {"line": "ord", "seat": caller},
                        "delivery": {"url": "http://localhost/h", "secret": SECRET},
                    }
                ),
                owner="u1",
            )
        claimed = board.answer("ord", wait_s=0)
        assert claimed is not None
        board.say("ord", claimed[1], "last words")
        board.hang_up("ord", claimed[1], "done", None)
        pool.shutdown(wait=True)
        assert order == ["line.message", "line.ended"]

    def test_a_crashed_delivery_does_not_wedge_the_receiver(self):
        board, events, _, implementer, sent, _ = _setup([])
        calls = 0

        def flaky(url, body, headers):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected")  # logged by the board
            sent.append(headers)
            return 200

        events._sender = flaky
        board.say("ev", implementer, "one")
        board.say("ev", implementer, "two")
        assert len(sent) == 1 and events._draining == set()

    def test_short_ttl_is_rejected_and_secret_is_hidden(self):
        params = {
            "name": "line.message",
            "arguments": {"line": "ev", "seat": "s"},
            "delivery": {"url": "http://localhost/h", "secret": SECRET},
        }
        with pytest.raises(ValueError):
            SubscribeParams.model_validate({**params, "ttlMs": 1000})
        with pytest.raises(ValueError):
            SubscribeParams.model_validate({**params, "ttlMs": 10**400})
        assert SECRET not in repr(SubscribeParams.model_validate(params))

    def test_unsubscribe_by_key(self):
        _, events, sub, _, _, _ = _setup([])
        seat = events._subs[sub.id].seat
        events.unsubscribe(
            subscription_id("u1", "http://localhost/hook", "line.message", "ev", seat),
            owner="u1",
        )
        assert events._subs == {}

    def test_unsubscribe_requires_the_owner(self):
        _, events, sub, _, _, _ = _setup([])
        with pytest.raises(EventsError):
            events.unsubscribe(sub.id, owner="someone-else")
        events.unsubscribe(sub.id, owner="u1")

    def test_resubscribe_is_idempotent(self):
        board, events, sub, _, _, _ = _setup([])
        params = SubscribeParams.model_validate(
            {
                "name": "line.message",
                "arguments": {"line": "ev", "seat": events._subs[sub.id].seat},
                "delivery": {"url": "http://localhost/hook", "secret": SECRET},
            }
        )
        assert events.subscribe(params, owner="u1").id == sub.id
        assert len(events._subs) == 1

    def test_secret_validation_and_signature_shape(self):
        with pytest.raises(EventsError):
            decode_secret("whsec_" + base64.b64encode(b"short").decode())
        signature = sign(decode_secret(SECRET), "evt_1", 1700000000, b"{}")
        assert signature.startswith("v1,")


class TestCallbackGuard(TestTemplate):
    def test_private_ip_literal_fails_only_the_address_check(self):
        with (
            patch.object(
                global_config.telephone.events, "allow_insecure_callbacks", False
            ),
            pytest.raises(EventsError, match="public address"),
        ):
            _check_callback_url("https://127.0.0.1/hook")

    def test_malformed_urls_become_events_errors(self):
        with patch.object(
            global_config.telephone.events, "allow_insecure_callbacks", False
        ):
            for url in ("https://example.com:99999/x", "ftp://example.com/x"):
                with pytest.raises(EventsError):
                    _check_callback_url(url)

    def test_send_connects_to_the_validated_address(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        seen = {}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            @contextmanager
            def stream(self, method, url, content, headers, extensions):
                seen.update(url=url, headers=headers, extensions=extensions)
                yield type("R", (), {"status_code": 204, "iter_raw": lambda s: []})()

        with (
            patch.object(
                global_config.telephone.events, "allow_insecure_callbacks", False
            ),
            patch("services.line_events.socket.getaddrinfo", return_value=public),
            patch("services.line_events.httpx.Client", FakeClient),
        ):
            status = _http_send("https://hooks.example.com/cb?x=1", b"{}", {})
        assert status == 204
        assert seen["url"] == "https://93.184.216.34/cb?x=1"
        assert seen["headers"]["host"] == "hooks.example.com"
        assert seen["extensions"] == {"sni_hostname": "hooks.example.com"}
