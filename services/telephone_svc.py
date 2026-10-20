"""Telephone services: dial, answer, say, listen, get_messages, hang_up.

Pure wrappers over :data:`services.switchboard.switchboard`. The blocking ones
(``dial``, ``answer``, ``say``, ``listen``) wait up to ``wait_s`` for the other
agent, which is how a client that cannot be woken by the server (Codex CLI)
stays on the line: the reply arrives as the tool result. Event-driven clients
(ChatGPT via MCP Events) pass ``wait_s=0`` and are woken by a webhook instead.
"""

from common import global_config
from models.telephone import (
    AnswerInput,
    CallView,
    DialInput,
    GetMessagesInput,
    HangUpInput,
    ListenInput,
    Message,
    Outcome,
    Role,
    SayInput,
    Transcript,
)
from services import service
from services.switchboard import Call, switchboard
from src.utils.cancellation import is_cancelled


def _wait(requested: float | None) -> float:
    cfg = global_config.telephone
    wait = cfg.default_wait_s if requested is None else requested
    return max(0.0, min(wait, cfg.max_wait_s))


def _next_step(outcome: Outcome, role: Role | None, call: Call | None) -> str:
    if outcome == "no_call":
        return "Nobody has dialed this line yet. Call `answer` again to keep waiting."
    if outcome == "ended":
        reason = call.ended_reason if call else "unknown"
        return f"The call is over ({reason}). Stop calling tools on this line."
    if outcome == "waiting":
        who = "Nobody has answered" if call and call.status == "ringing" else "No reply"
        return (
            f"{who} yet. Call `listen` again with the same line and seat; keep "
            "waiting rather than ending your turn."
        )
    if role == "caller":
        return (
            "Answer their questions with `say` (it waits for the reply). Once you "
            "have confirmed the problem is fixed, call `hang_up` with resolved=true; "
            "if it cannot be fixed, hang_up with resolved=false."
        )
    return (
        "Reply with `say` (it waits for the reply). Ask for whatever you need to "
        "reproduce the problem, fix it, then tell the caller what changed and ask "
        "them to verify."
    )


def _view(
    call: Call,
    role: Role,
    seat: str | None,
    messages: list[Message],
) -> CallView:
    # Ended wins: a final message that ends the call (turn cap, hang-up) is
    # still returned in `messages`, but replying to it would fail.
    if call.status == "ended":
        outcome: Outcome = "ended"
    elif messages:
        outcome = "message"
    else:
        outcome = "waiting"
    return CallView(
        outcome=outcome,
        line=call.line,
        call_id=call.call_id,
        seat=seat,
        role=role,
        status=call.status,
        messages=messages,
        turns=len(call.messages),
        max_turns=call.max_turns,
        ended_reason=call.ended_reason,
        resolved=call.resolved,
        next_step=_next_step(outcome, role, call),
    )


@service(
    name="dial",
    description=(
        "Place a call on a named line to an agent who can help, such as one "
        "with access to code you cannot change. Returns your seat token and "
        "waits for their first reply."
    ),
    input_model=DialInput,
    output_model=CallView,
    mutating=True,
)
def dial(input: DialInput) -> CallView:
    call, seat = switchboard.dial(input.line, input.message)
    call, role, fresh = switchboard.wait_for_peer(input.line, seat, _wait(input.wait_s))
    if is_cancelled():
        # Nobody will ever see this seat; free the line instead of holding it
        # until the idle timeout.
        call, role = switchboard.hang_up(
            input.line, seat, "the caller left before the call connected", None
        )
    return _view(call, role, seat, fresh)


@service(
    name="answer",
    description=(
        "Answer a call on a named line as the implementer. Waits for someone to "
        "dial, then returns your seat token and the caller's opening message."
    ),
    input_model=AnswerInput,
    output_model=CallView,
    mutating=True,
)
def answer(input: AnswerInput) -> CallView:
    claimed = switchboard.answer(input.line, _wait(input.wait_s))
    if claimed is None:
        return CallView(
            outcome="no_call",
            line=input.line,
            next_step=_next_step("no_call", None, None),
        )
    call, seat = claimed
    call, role, fresh = switchboard.wait_for_peer(input.line, seat, 0)
    return _view(call, role, seat, fresh)


@service(
    name="say",
    description=(
        "Say something to the other agent on your call, then wait for their "
        "reply (pass wait_s=0 to return immediately)."
    ),
    input_model=SayInput,
    output_model=CallView,
    mutating=True,
)
def say(input: SayInput) -> CallView:
    switchboard.say(input.line, input.seat, input.text)
    call, role, fresh = switchboard.wait_for_peer(
        input.line, input.seat, _wait(input.wait_s)
    )
    return _view(call, role, input.seat, fresh)


@service(
    name="listen",
    description=(
        "Wait for the other agent's next message on your call. Returns early "
        "when they speak or hang up; if nothing arrives, call listen again."
    ),
    input_model=ListenInput,
    output_model=CallView,
    # It consumes the messages it returns, so a REST retry must replay the
    # first response rather than wait again and skip what was handed over.
    mutating=True,
)
def listen(input: ListenInput) -> CallView:
    call, role, fresh = switchboard.wait_for_peer(
        input.line, input.seat, _wait(input.wait_s)
    )
    return _view(call, role, input.seat, fresh)


@service(
    name="get_messages",
    description=(
        "Read the full transcript of your call (both sides) without waiting. "
        "Use after_seq to fetch only newer messages."
    ),
    input_model=GetMessagesInput,
    output_model=Transcript,
)
def get_messages(input: GetMessagesInput) -> Transcript:
    return switchboard.transcript(input.line, input.seat, input.after_seq)


@service(
    name="hang_up",
    description=(
        "End your call. The caller should report whether their problem was "
        "resolved; the other agent is told the call ended."
    ),
    input_model=HangUpInput,
    output_model=CallView,
    mutating=True,
)
def hang_up(input: HangUpInput) -> CallView:
    call, role = switchboard.hang_up(
        input.line, input.seat, input.reason, input.resolved
    )
    return _view(call, role, input.seat, [])
