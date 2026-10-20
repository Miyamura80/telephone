"""Unit tests for the in-memory switchboard and the telephone services."""

import json
import threading
import time
from unittest.mock import patch

import pytest

from common import global_config
from models.telephone import (
    AnswerInput,
    DialInput,
    GetMessagesInput,
    HangUpInput,
    ListenInput,
    SayInput,
)
from services import telephone_svc
from services.switchboard import (
    BadSeatError,
    CallEndedError,
    LineBusyError,
    Switchboard,
    UnknownCallError,
    switchboard,
)
from src.utils.cancellation import bind_cancel_event, unbind_cancel_event
from tests.test_template import TestTemplate


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    switchboard.reset()
    with patch.object(global_config.telephone, "transcript_dir", str(tmp_path)):
        yield
    switchboard.reset()


def _connect(board: Switchboard, line: str = "bug-1"):
    call, caller = board.dial(line, "acme-cli sync crashes")
    claimed = board.answer(line, wait_s=0)
    assert claimed is not None
    return call, caller, claimed[1]


class TestSwitchboard(TestTemplate):
    def test_dial_answer_and_exchange(self):
        board = Switchboard()
        call, caller, implementer = _connect(board)
        assert call.status == "connected"
        _, role, fresh = board.wait_for_peer("bug-1", implementer, 0)
        assert role == "implementer"
        assert [m.text for m in fresh] == ["acme-cli sync crashes"]
        board.say("bug-1", implementer, "what version?")
        _, _, fresh = board.wait_for_peer("bug-1", caller, 0)
        assert [m.sender for m in fresh] == ["implementer"]
        # Messages are handed over once; your own words never echo back.
        assert board.wait_for_peer("bug-1", caller, 0)[2] == []

    def test_wait_wakes_when_peer_speaks(self):
        board = Switchboard()
        _, caller, implementer = _connect(board)
        board.wait_for_peer("bug-1", implementer, 0)
        threading.Timer(0.2, board.say, ("bug-1", implementer, "hi")).start()
        started = time.monotonic()
        _, _, fresh = board.wait_for_peer("bug-1", caller, 5)
        assert [m.text for m in fresh] == ["hi"]
        assert time.monotonic() - started < 3

    def test_answer_waits_for_a_dial(self):
        board = Switchboard()
        threading.Timer(0.2, board.dial, ("late", "hello")).start()
        claimed = board.answer("late", wait_s=5)
        assert claimed is not None and claimed[0].status == "connected"

    def test_answer_times_out_without_a_dial(self):
        assert Switchboard().answer("nobody", wait_s=0) is None

    def test_busy_line_and_second_answer_rejected(self):
        board = Switchboard()
        _connect(board)
        with pytest.raises(LineBusyError):
            board.dial("bug-1", "again")
        with pytest.raises(LineBusyError):
            board.answer("bug-1", wait_s=0)

    def test_wrong_seat_and_unknown_line(self):
        board = Switchboard()
        _connect(board)
        with pytest.raises(BadSeatError):
            board.say("bug-1", "seat_forged", "hi")
        with pytest.raises(UnknownCallError):
            board.say("nope", "seat_x", "hi")

    def test_hang_up_ends_call_and_wakes_peer(self):
        board = Switchboard()
        _, caller, implementer = _connect(board)
        board.wait_for_peer("bug-1", implementer, 0)
        threading.Timer(0.2, board.hang_up, ("bug-1", caller, "fixed", True)).start()
        call, _, fresh = board.wait_for_peer("bug-1", implementer, 5)
        assert fresh == [] and call.status == "ended"
        assert call.resolved is True
        assert "caller hung up: fixed" in (call.ended_reason or "")
        with pytest.raises(CallEndedError):
            board.say("bug-1", implementer, "wait")

    def test_only_caller_sets_resolved(self):
        board = Switchboard()
        _, _, implementer = _connect(board)
        call, _ = board.hang_up("bug-1", implementer, "giving up", True)
        assert call.resolved is None

    def test_max_turns_ends_call(self):
        board = Switchboard()
        with patch.object(global_config.telephone, "max_turns", 3):
            _, caller, implementer = _connect(board)
            board.say("bug-1", implementer, "2")
            call, _ = board.say("bug-1", caller, "3")
        assert call.status == "ended"
        assert "max_turns" in (call.ended_reason or "")

    def test_idle_timeout_ends_call_and_line_can_be_redialed(self):
        clock = FakeClock()
        board = Switchboard(clock=clock)
        _, caller, _ = _connect(board)
        clock.now += global_config.telephone.idle_timeout_s + 1
        call, _, _ = board.wait_for_peer("bug-1", caller, 0)
        assert call.status == "ended" and "idle" in (call.ended_reason or "")
        new_call, _ = board.dial("bug-1", "second try")
        assert new_call.call_id != call.call_id

    def test_listeners_get_message_and_end_events(self):
        board = Switchboard()
        events = []
        board.add_listener(events.append)
        _, caller, implementer = _connect(board)
        board.say("bug-1", implementer, "q")
        board.hang_up("bug-1", caller, "done", True)
        kinds = [(e.kind, e.recipient_seats) for e in events]
        assert kinds[0] == ("message", ())  # nobody had answered yet
        assert kinds[1] == ("message", (caller,))
        assert kinds[2][0] == "ended" and set(kinds[2][1]) == {caller, implementer}

    def test_a_failing_listener_does_not_break_the_call(self):
        board = Switchboard()

        def boom(_event):
            raise RuntimeError("listener down")

        board.add_listener(boom)
        _, caller, implementer = _connect(board)
        board.wait_for_peer("bug-1", implementer, 0)  # the opening message
        board.say("bug-1", caller, "still works")
        _, _, fresh = board.wait_for_peer("bug-1", implementer, 0)
        assert [m.text for m in fresh] == ["still works"]

    def test_transcript_file_records_the_call(self, tmp_path):
        board = Switchboard()
        call, caller, _ = _connect(board)
        board.hang_up("bug-1", caller, "fixed", True)
        path = tmp_path / f"bug-1__{call.call_id}.jsonl"
        events = [json.loads(line)["event"] for line in path.read_text().splitlines()]
        assert events == ["dialed", "message", "answered", "ended"]


class TestSwitchboardRegressions(TestTemplate):
    def test_speaking_does_not_skip_unread_peer_messages(self):
        board = Switchboard()
        _, caller, implementer = _connect(board)
        board.say("bug-1", implementer, "question one")
        # The caller speaks before reading; the question must still arrive.
        board.say("bug-1", caller, "meanwhile, more context")
        _, _, fresh = board.wait_for_peer("bug-1", caller, 0)
        assert [m.text for m in fresh] == ["question one"]

    def test_abandoned_wait_consumes_nothing(self):
        board = Switchboard()
        _, caller, implementer = _connect(board)
        board.say("bug-1", implementer, "reply")
        event = threading.Event()
        event.set()
        token = bind_cancel_event(event)
        try:
            _, _, fresh = board.wait_for_peer("bug-1", caller, 5)
        finally:
            unbind_cancel_event(token)
        assert fresh == []
        assert [m.text for m in board.wait_for_peer("bug-1", caller, 0)[2]] == ["reply"]

    def test_abandoned_answer_claims_no_seat(self):
        board = Switchboard()
        board.dial("bug-1", "help")
        event = threading.Event()
        event.set()
        token = bind_cancel_event(event)
        try:
            assert board.answer("bug-1", wait_s=5) is None
        finally:
            unbind_cancel_event(token)
        # The seat is still free for the implementer who is really there.
        assert board.answer("bug-1", wait_s=0) is not None

    def test_transcripts_are_owner_only(self, tmp_path):
        board = Switchboard()
        call, _, _ = _connect(board)
        path = tmp_path / f"bug-1__{call.call_id}.jsonl"
        assert path.stat().st_mode & 0o777 == 0o600

    def test_non_ascii_seat_is_a_bad_seat(self):
        board = Switchboard()
        _connect(board)
        with pytest.raises(BadSeatError):
            board.say("bug-1", "seat_\u00e9", "hi")

    def test_reaper_ends_idle_calls_without_requests(self):
        clock = FakeClock()
        board = Switchboard(clock=clock)
        ended = threading.Event()

        def on_event(event):
            if event.kind == "ended":
                ended.set()

        board.add_listener(on_event)
        with patch.object(global_config.telephone, "idle_timeout_s", 4.0):
            _connect(board)
            clock.now += 10
            assert ended.wait(5), "reaper never expired the idle call"


class TestTelephoneServices(TestTemplate):
    def test_full_call_through_services(self):
        dialed = telephone_svc.dial(DialInput(line="svc", message="help", wait_s=0))
        assert dialed.outcome == "waiting" and dialed.status == "ringing"
        assert "listen" in dialed.next_step
        answered = telephone_svc.answer(AnswerInput(line="svc", wait_s=0))
        assert answered.outcome == "message" and answered.role == "implementer"
        assert answered.messages[0].text == "help"
        seat_i, seat_c = answered.seat, dialed.seat
        assert seat_i and seat_c
        reply = telephone_svc.say(
            SayInput(line="svc", seat=seat_i, text="version?", wait_s=0)
        )
        assert reply.outcome == "waiting"
        heard = telephone_svc.listen(ListenInput(line="svc", seat=seat_c, wait_s=0))
        assert heard.outcome == "message" and heard.messages[0].text == "version?"
        assert "hang_up" in heard.next_step
        transcript = telephone_svc.get_messages(
            GetMessagesInput(line="svc", seat=seat_c)
        )
        assert [m.sender for m in transcript.messages] == ["caller", "implementer"]
        ended = telephone_svc.hang_up(
            HangUpInput(line="svc", seat=seat_c, reason="fixed", resolved=True)
        )
        assert ended.outcome == "ended" and ended.resolved is True
        after = telephone_svc.listen(ListenInput(line="svc", seat=seat_i, wait_s=0))
        assert after.outcome == "ended"

    def test_final_message_that_ends_the_call_reports_ended(self):
        with patch.object(global_config.telephone, "max_turns", 2):
            dialed = telephone_svc.dial(DialInput(line="cap", message="q", wait_s=0))
            answered = telephone_svc.answer(AnswerInput(line="cap", wait_s=0))
            assert answered.seat
            last = telephone_svc.say(
                SayInput(line="cap", seat=answered.seat, text="bye", wait_s=0)
            )
        assert last.outcome == "ended"
        assert dialed.seat
        heard = telephone_svc.listen(
            ListenInput(line="cap", seat=dialed.seat, wait_s=0)
        )
        assert heard.outcome == "ended" and heard.messages[0].text == "bye"

    def test_abandoned_dial_frees_the_line(self):
        event = threading.Event()
        event.set()
        token = bind_cancel_event(event)
        try:
            view = telephone_svc.dial(DialInput(line="gone", message="hi", wait_s=5))
        finally:
            unbind_cancel_event(token)
        assert view.outcome == "ended"
        # The line is free right away, not after the idle timeout.
        again = telephone_svc.dial(DialInput(line="gone", message="hi", wait_s=0))
        assert again.outcome == "waiting"

    def test_answer_with_nobody_dialing(self):
        view = telephone_svc.answer(AnswerInput(line="empty", wait_s=0))
        assert view.outcome == "no_call" and view.seat is None

    def test_wait_is_clamped_to_max(self):
        with patch.object(global_config.telephone, "max_wait_s", 0.0):
            view = telephone_svc.answer(AnswerInput(line="empty", wait_s=999))
        assert view.outcome == "no_call"

    def test_line_names_are_validated(self):
        with pytest.raises(ValueError):
            DialInput(line="Bad Line!", message="x")
