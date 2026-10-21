"""Callback verification for MCP Events: the signed challenge, its failure
reasons, the per-caller cache, and the bounded challenge reply."""

import json
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from common import global_config
from models.telephone_events import SubscribeParams, UnsubscribeParams
from services.line_events import (
    CallbackError,
    LineEvents,
    _http_post,
    decode_secret,
    sign,
)
from services.switchboard import Switchboard
from tests.test_line_events import SECRET, InlineExecutor, _echo
from tests.test_template import TestTemplate


@pytest.fixture(autouse=True)
def _insecure_callbacks_and_no_transcripts():
    with (
        patch.object(global_config.telephone.events, "allow_insecure_callbacks", True),
        patch.object(global_config.telephone, "transcript_dir", None),
    ):
        yield


def _params(url="http://localhost/cb"):
    return SubscribeParams.model_validate(
        {
            "name": "line.message",
            "arguments": {"line": "vf", "seat": "PLACEHOLDER"},
            "delivery": {"url": url, "secret": SECRET},
        }
    )


class TestCallbackVerification(TestTemplate):
    def _events(self, verifier):
        board = Switchboard()
        _, seat = board.dial("vf", "help")
        events = LineEvents(
            board,
            sender=lambda *a: 200,
            executor=InlineExecutor(),
            verifier=verifier,
        )
        params = _params()
        params.arguments.seat = seat
        return events, params

    def test_challenge_is_signed_and_must_be_echoed(self):
        seen = []

        def verifier(url, body, headers):
            seen.append((body, headers))
            return _echo(url, body, headers)

        events, params = self._events(verifier)
        events.subscribe(params, owner="u1")
        body, headers = seen[0]
        assert json.loads(body)["type"] == "verification"
        expected = sign(
            decode_secret(SECRET),
            headers["webhook-id"],
            int(headers["webhook-timestamp"]),
            body,
        )
        assert headers["webhook-signature"] == expected

    def test_wrong_echo_fails_with_challenge_failed(self):
        events, params = self._events(lambda u, b, h: (200, b'{"challenge": "nope"}'))
        with pytest.raises(CallbackError) as info:
            events.subscribe(params, owner="u1")
        assert info.value.reason == "challenge_failed"
        assert events._subs == {}

    def test_non_2xx_fails_and_timeouts_are_categorized(self):
        events, params = self._events(lambda u, b, h: (500, b""))
        with pytest.raises(CallbackError, match="challenge_failed"):
            events.subscribe(params, owner="u1")

        def slow(url, body, headers):
            raise httpx.ReadTimeout("slow")

        events, params = self._events(slow)
        with pytest.raises(CallbackError) as info:
            events.subscribe(params, owner="u1")
        assert info.value.reason == "timeout"

    def test_a_verified_callback_is_not_challenged_again(self):
        calls = []

        def verifier(url, body, headers):
            calls.append(url)
            return _echo(url, body, headers)

        events, params = self._events(verifier)
        events.subscribe(params, owner="u1")
        events.subscribe(params, owner="u1")  # a refresh
        assert len(calls) == 1
        events.subscribe(params, owner="someone-else")  # cached per caller
        assert len(calls) == 2

    def test_challenge_reply_is_read_only_up_to_the_cap(self):
        served = []

        class Endless:
            status_code = 200

            def iter_raw(self):
                while True:
                    served.append(1)
                    yield b"x" * 8192

        class StreamClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            @contextmanager
            def stream(self, method, url, content, headers, extensions):
                sent_headers.update(headers)
                yield Endless()

        sent_headers: dict = {}
        with patch("services.line_events.httpx.Client", StreamClient):
            status, reply = _http_post("http://localhost/cb", b"{}", {})
        assert status == 200 and len(reply) == 64 * 1024
        assert len(served) == 8  # stopped reading at the cap
        # Raw bytes, no compression: the cap counts what crosses the wire.
        assert sent_headers["accept-encoding"] == "identity"

    def test_unsubscribe_rejects_mixed_or_partial_selectors(self):
        with pytest.raises(ValueError):
            UnsubscribeParams.model_validate({"id": "sub_x", "name": "line.message"})
        with pytest.raises(ValueError):
            UnsubscribeParams.model_validate({"name": "line.message"})
        assert UnsubscribeParams.model_validate({"id": "sub_x"}).id == "sub_x"

    def test_stale_verifications_are_pruned_and_ttl_starts_after_verify(self):
        clock = [1000.0]

        def slow_echo(url, body, headers):
            clock[0] += 30  # the challenge takes 30s
            return _echo(url, body, headers)

        board = Switchboard()
        _, seat = board.dial("vf2", "help")
        events = LineEvents(
            board,
            sender=lambda *a: 200,
            executor=InlineExecutor(),
            verifier=slow_echo,
            clock=lambda: clock[0],
        )
        params = _params()
        params.arguments.seat = seat
        params.arguments.line = "vf2"
        params.ttl_ms = 60_000
        sub = events.subscribe(params, owner="u1")
        assert events._subs[sub.id].expires_at == 1030.0 + 60
        challenges = len(events._verified)
        assert challenges == 1
        clock[0] += global_config.telephone.events.verification_cache_s + 1
        with events._lock:
            events._prune_expired_locked(clock[0])
        assert events._verified == {}

        # A refresh after the window goes through subscribe() and challenges again.
        calls = []
        events._verifier = lambda u, b, h: calls.append(u) or _echo(u, b, h)
        events.subscribe(params, owner="u1")
        assert calls == [params.delivery.url]
        assert events._verified[("u1", params.delivery.url)] == clock[0]
