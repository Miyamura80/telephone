"""Models for the telephone switchboard: lines, calls, seats, and messages.

A *line* is a name both agents agree on out of band (e.g. ``acme-cli-bug``).
Dialing a line starts a *call*; each participant holds a *seat* (an opaque
token returned by ``dial`` / ``answer``) that authorizes speaking on it.
"""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["caller", "implementer"]
CallStatus = Literal["ringing", "connected", "ended"]
# What a tool result means for the agent's next step:
#   message - the peer said something (see `messages`)
#   waiting - nothing new before wait_s elapsed; call `listen` again
#   ended   - the call is over (see `ended_reason`)
#   no_call - `answer` found nobody dialing the line yet; call `answer` again
Outcome = Literal["message", "waiting", "ended", "no_call"]

LINE_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"

_LINE = Field(
    pattern=LINE_PATTERN,
    description="Line name both agents agreed on, e.g. 'acme-cli-bug'.",
)
_SEAT = Field(
    min_length=1,
    description="Your seat token from dial/answer. Pass it on every call.",
)
_WAIT = Field(
    default=None,
    ge=0,
    description=(
        "Seconds to wait for the other agent before returning. Omit for the "
        "server default; pass 0 to return immediately (event-driven clients)."
    ),
)


class Message(BaseModel):
    seq: int = Field(description="Position in the call, starting at 1.")
    sender: Role
    text: str
    sent_at: str = Field(description="ISO-8601 UTC timestamp.")


class CallView(BaseModel):
    """What one participant sees of the call after a tool returns."""

    outcome: Outcome
    line: str
    call_id: str | None = None
    seat: str | None = Field(
        default=None, description="Your seat token. Keep it for later calls."
    )
    role: Role | None = None
    status: CallStatus | None = None
    messages: list[Message] = Field(
        default_factory=list,
        description="Messages from the other agent you have not seen yet.",
    )
    turns: int = 0
    max_turns: int = 0
    ended_reason: str | None = None
    resolved: bool | None = None
    next_step: str = Field(description="What to do next, in plain words.")


class DialInput(BaseModel):
    line: str = _LINE
    message: str = Field(
        min_length=1,
        description=(
            "Opening message for the agent who answers: the problem, what you "
            "ran, the exact error, and what you already tried."
        ),
    )
    wait_s: float | None = _WAIT


class AnswerInput(BaseModel):
    line: str = _LINE
    wait_s: float | None = _WAIT


class SayInput(BaseModel):
    line: str = _LINE
    seat: str = _SEAT
    text: str = Field(min_length=1, description="What to tell the other agent.")
    wait_s: float | None = _WAIT


class ListenInput(BaseModel):
    line: str = _LINE
    seat: str = _SEAT
    wait_s: float | None = _WAIT


class HangUpInput(BaseModel):
    line: str = _LINE
    seat: str = _SEAT
    reason: str = Field(
        default="done", description="Why you are ending the call, in a few words."
    )
    resolved: bool | None = Field(
        default=None,
        description=(
            "Caller only: true if your problem is fixed, false if it is not. "
            "Leave unset otherwise."
        ),
    )


class GetMessagesInput(BaseModel):
    line: str = _LINE
    seat: str = _SEAT
    after_seq: int = Field(
        default=0, ge=0, description="Only return messages with seq above this."
    )


class Transcript(BaseModel):
    line: str
    call_id: str
    status: CallStatus
    messages: list[Message]
    turns: int
    max_turns: int
    ended_reason: str | None = None
    resolved: bool | None = None
