"""In-memory switchboard: named lines, calls, seats, and blocking waits.

Transport-agnostic and thread-safe. Every transport reaches the same
module-level :data:`switchboard`, so both participants must talk to the same
server process (one replica, one worker). Blocking waits use a
``threading.Condition``; the MCP layer runs them in a worker thread so the
event loop stays free (see ``mcp_server/enhancers/telephone.py``).

Lifecycle of a call on a line::

    dial ──> ringing ──answer──> connected ──hang_up / max_turns / idle──> ended

A line can be dialed again once its call has ended; the new call replaces it.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger as log

from common import global_config
from models.telephone import CallStatus, Message, Role, Transcript
from src.utils.cancellation import is_cancelled

# Ended calls older than this are dropped when a new call is dialed.
_ENDED_RETENTION_S = 3600.0


class TelephoneError(ValueError):
    """Base class for switchboard errors; the message is shown to the agent."""


class LineBusyError(TelephoneError):
    pass


class UnknownCallError(TelephoneError):
    pass


class BadSeatError(TelephoneError):
    pass


class CallEndedError(TelephoneError):
    pass


@dataclass
class Call:
    line: str
    call_id: str
    max_turns: int
    status: CallStatus = "ringing"
    seats: dict[Role, str] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    # Highest seq each role has been handed by wait_for_peer.
    cursors: dict[Role, int] = field(
        default_factory=lambda: {"caller": 0, "implementer": 0}
    )
    last_activity: float = 0.0
    ended_at: float | None = None
    ended_reason: str | None = None
    resolved: bool | None = None

    def role_of(self, seat: str) -> Role:
        for role, held in self.seats.items():
            if secrets.compare_digest(held.encode(), seat.encode()):
                return role
        raise BadSeatError(
            f"That seat does not belong to the call on line {self.line!r}."
        )

    def transcript(self, after_seq: int = 0) -> Transcript:
        return Transcript(
            line=self.line,
            call_id=self.call_id,
            status=self.status,
            messages=[m for m in self.messages if m.seq > after_seq],
            turns=len(self.messages),
            max_turns=self.max_turns,
            ended_reason=self.ended_reason,
            resolved=self.resolved,
        )


@dataclass(frozen=True)
class CallEvent:
    """Emitted to listeners after a change, outside the switchboard lock."""

    kind: str  # "message" | "ended"
    line: str
    call_id: str
    recipient_seats: tuple[str, ...]
    message: Message | None = None
    ended_reason: str | None = None


Listener = Callable[[CallEvent], None]


def peer_of(role: Role) -> Role:
    return "implementer" if role == "caller" else "caller"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Switchboard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._calls: dict[str, Call] = {}
        self._listeners: list[Listener] = []
        # Events and transcript lines produced under the lock, emitted by
        # _flush() after release so listeners and disk I/O never hold it.
        self._pending: list[CallEvent] = []
        self._pending_writes: list[tuple[Path, str]] = []
        # Held from draining through delivery, so concurrent flushes cannot
        # reorder events or transcript lines. Re-entrant: a listener may call
        # back into the board.
        self._deliver = threading.RLock()
        self._reaper: threading.Thread | None = None

    # -- listeners ---------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _flush(self) -> None:
        with self._deliver:
            with self._cond:
                events, self._pending = self._pending, []
                writes, self._pending_writes = self._pending_writes, []
            _write_transcripts(writes)
            self._emit(events)

    def _emit(self, events: list[CallEvent]) -> None:
        for event in events:
            for listener in list(self._listeners):
                try:
                    listener(event)
                except Exception:  # noqa: BLE001
                    # Listener boundary: a failing subscriber (e.g. a webhook
                    # sender) must never break the call for the two agents.
                    log.exception("switchboard listener failed on {}", event.kind)

    # -- call lifecycle ------------------------------------------------------

    def dial(self, line: str, opening: str) -> tuple[Call, str]:
        """Start a call on ``line`` and post the caller's opening message."""
        self._ensure_reaper()
        with self._cond:
            self._prune()
            existing = self._live_call(line)
            if existing is not None:
                raise LineBusyError(
                    f"Line {line!r} already has a call in progress. Pick another "
                    "line name or wait for that call to end."
                )
            call = Call(
                line=line,
                call_id=f"call_{secrets.token_hex(6)}",
                max_turns=global_config.telephone.max_turns,
                last_activity=self._clock(),
            )
            seat = f"seat_{secrets.token_urlsafe(12)}"
            call.seats["caller"] = seat
            self._calls[line] = call
            self._record(call, {"event": "dialed"})
            self._post_locked(call, "caller", opening)
            self._cond.notify_all()
        self._flush()
        return call, seat

    def answer(self, line: str, wait_s: float) -> tuple[Call, str] | None:
        """Take the implementer seat on ``line``, waiting up to ``wait_s`` for a dial."""
        try:
            return self._answer(line, wait_s)
        finally:
            self._flush()

    def _answer(self, line: str, wait_s: float) -> tuple[Call, str] | None:
        deadline = self._clock() + wait_s
        with self._cond:
            while True:
                if is_cancelled():  # abandoned: never claim a seat for no one
                    return None
                call = self._live_call(line)
                if call is not None:
                    if "implementer" in call.seats:
                        raise LineBusyError(f"Someone already answered line {line!r}.")
                    seat = f"seat_{secrets.token_urlsafe(12)}"
                    call.seats["implementer"] = seat
                    call.status = "connected"
                    call.last_activity = self._clock()
                    self._record(call, {"event": "answered"})
                    self._cond.notify_all()
                    return call, seat
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=min(remaining, 1.0))

    def say(self, line: str, seat: str, text: str) -> tuple[Call, Role]:
        try:
            return self._say(line, seat, text)
        finally:
            self._flush()

    def _say(self, line: str, seat: str, text: str) -> tuple[Call, Role]:
        with self._cond:
            call = self._call_for(line)
            role = call.role_of(seat)
            self._expire_if_idle(call)
            if call.status == "ended":
                raise CallEndedError(
                    f"The call on line {line!r} has ended ({call.ended_reason})."
                )
            self._post_locked(call, role, text)
            self._cond.notify_all()
        return call, role

    def wait_for_peer(
        self, line: str, seat: str, wait_s: float
    ) -> tuple[Call, Role, list[Message]]:
        """Return unseen peer messages, waiting up to ``wait_s`` for at least one.

        Returns early (possibly with no messages) when the call ends, or when
        the request this wait serves is abandoned: then nothing is consumed, so
        the agent's next ``listen`` still sees the message.
        """
        deadline = self._clock() + wait_s
        with self._cond:
            call = self._call_for(line)
            role = call.role_of(seat)
            peer = peer_of(role)
            while True:
                if is_cancelled():
                    fresh = []
                    break
                fresh = [
                    m
                    for m in call.messages
                    if m.sender == peer and m.seq > call.cursors[role]
                ]
                if fresh:
                    call.cursors[role] = fresh[-1].seq
                    break
                self._expire_if_idle(call)
                if call.status == "ended":
                    break
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 1.0))
                # A redial replaces the call object; keep waiting on ours.
        self._flush()
        return call, role, fresh

    def hang_up(
        self, line: str, seat: str, reason: str, resolved: bool | None
    ) -> tuple[Call, Role]:
        with self._cond:
            call = self._call_for(line)
            role = call.role_of(seat)
            if call.status == "ended":
                return call, role
            if role == "caller" and resolved is not None:
                call.resolved = resolved
            self._end_locked(call, f"{role} hung up: {reason}")
            self._cond.notify_all()
        self._flush()
        return call, role

    def transcript(self, line: str, seat: str, after_seq: int) -> Transcript:
        with self._cond:
            call = self._call_for(line)
            call.role_of(seat)
            self._expire_if_idle(call)
            result = call.transcript(after_seq)
        self._flush()
        return result

    def reset(self) -> None:
        """Drop every call. For tests and local development only."""
        with self._cond:
            self._calls.clear()
            self._cond.notify_all()

    # -- internals (caller holds the lock) ----------------------------------

    def _call_for(self, line: str) -> Call:
        call = self._calls.get(line)
        if call is None:
            raise UnknownCallError(f"No call has been placed on line {line!r}.")
        return call

    def _live_call(self, line: str) -> Call | None:
        call = self._calls.get(line)
        if call is None:
            return None
        self._expire_if_idle(call)
        return None if call.status == "ended" else call

    def _post_locked(self, call: Call, role: Role, text: str) -> None:
        message = Message(
            seq=len(call.messages) + 1, sender=role, text=text, sent_at=_now_iso()
        )
        call.messages.append(message)
        call.last_activity = self._clock()
        self._record(call, {"event": "message", **message.model_dump()})
        peer_seat = call.seats.get(peer_of(role))
        self._pending.append(
            CallEvent(
                kind="message",
                line=call.line,
                call_id=call.call_id,
                recipient_seats=(peer_seat,) if peer_seat else (),
                message=message,
            )
        )
        if len(call.messages) >= call.max_turns:
            self._end_locked(call, f"max_turns ({call.max_turns}) reached")

    def _end_locked(self, call: Call, reason: str) -> None:
        call.status = "ended"
        call.ended_reason = reason
        call.ended_at = self._clock()
        self._record(
            call, {"event": "ended", "reason": reason, "resolved": call.resolved}
        )
        self._pending.append(
            CallEvent(
                kind="ended",
                line=call.line,
                call_id=call.call_id,
                recipient_seats=tuple(call.seats.values()),
                ended_reason=reason,
            )
        )
        self._cond.notify_all()

    def _expire_if_idle(self, call: Call) -> None:
        if call.status == "ended":
            return
        idle_for = self._clock() - call.last_activity
        if idle_for >= global_config.telephone.idle_timeout_s:
            self._end_locked(call, f"idle for {int(idle_for)}s")

    def _ensure_reaper(self) -> None:
        """Start the idle reaper once, so idle calls end (and emit
        ``line.ended``) even when no request touches them."""
        with self._cond:
            if self._reaper is not None:
                return
            self._reaper = threading.Thread(
                target=self._reap_forever, name="switchboard-reaper", daemon=True
            )
            self._reaper.start()

    def _reap_forever(self) -> None:
        while True:
            interval = min(30.0, global_config.telephone.idle_timeout_s / 4)
            time.sleep(max(1.0, interval))
            with self._cond:
                for call in list(self._calls.values()):
                    self._expire_if_idle(call)
            self._flush()

    def _prune(self) -> None:
        cutoff = self._clock() - _ENDED_RETENTION_S
        stale = [
            line
            for line, call in self._calls.items()
            if call.ended_at is not None and call.ended_at < cutoff
        ]
        for line in stale:
            del self._calls[line]

    def _record(self, call: Call, entry: dict) -> None:
        directory = global_config.telephone.transcript_dir
        if not directory:
            return
        # Relative to the server's working directory (absolute paths also work).
        path = Path(directory) / f"{call.line}__{call.call_id}.jsonl"
        line = json.dumps({"at": _now_iso(), **entry}) + "\n"
        self._pending_writes.append((path, line))


def _write_transcripts(writes: list[tuple[Path, str]]) -> None:
    """Append transcript lines; owner-only, since they hold both agents' words."""
    for path, line in writes:
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line)
        except OSError:
            log.exception("could not write transcript {}", path)


switchboard = Switchboard()
