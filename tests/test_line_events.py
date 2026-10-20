"""Unit tests for MCP Events delivery (retries, drops, expiry, signing)."""

import base64
from concurrent.futures import Executor, Future
from unittest.mock import patch

import pytest

from common import global_config
from models.telephone_events import SubscribeParams
from services.line_events import EventsError, LineEvents, decode_secret, sign
from services.switchboard import Switchboard
from tests.test_template import TestTemplate

SECRET = "whsec_" + base64.b64encode(b"k" * 32).decode()


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

    events = LineEvents(
        board, sender=sender, executor=InlineExecutor(), sleep=sleeps.append
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
        with patch.object(
            global_config.telephone.events, "allow_insecure_callbacks", False
        ):
            board.say("ev", implementer, "hello")
        assert sent == [] and events._subs == {}

    def test_expired_subscriptions_are_skipped(self):
        board, events, sub, implementer, sent, _ = _setup([])
        events._subs[sub.id].expires_at = 0
        board.say("ev", implementer, "hello")
        assert sent == []

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
