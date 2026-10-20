"""Cooperative cancellation for blocking work done on behalf of a request.

A transport that runs a blocking service call in a worker thread binds a
``threading.Event`` here and sets it if the request is abandoned (client
disconnect, cancellation). Long waits poll :func:`is_cancelled` so they stop
early and, crucially, do not consume data that nobody will receive.
"""

import threading
from contextvars import ContextVar, Token

_cancel_event: ContextVar[threading.Event | None] = ContextVar(
    "request_cancel_event", default=None
)


def bind_cancel_event(event: threading.Event) -> Token:
    return _cancel_event.set(event)


def unbind_cancel_event(token: Token) -> None:
    _cancel_event.reset(token)


def is_cancelled() -> bool:
    event = _cancel_event.get()
    return event is not None and event.is_set()
