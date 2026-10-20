"""MCP Events for the switchboard: subscriptions and signed webhook delivery.

Event-driven MCP clients (ChatGPT today) cannot hold a blocking ``listen``
open. Instead they subscribe to ``line.message`` / ``line.ended`` for their
seat, and the server POSTs each event to the client's callback URL, signed
per Standard Webhooks::

    webhook-signature: v1,<base64 HMAC-SHA256(secret, "{id}.{timestamp}.{body}")>

The client then reads and replies with ``listen`` / ``say`` (``wait_s=0``).

Subscriptions live in memory next to the switchboard (same single-process
constraint). The spec is a draft; see ``models/telephone_events.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from loguru import logger as log

from common import global_config
from models.telephone import Message
from models.telephone_events import (
    LINE_ENDED,
    LINE_MESSAGE,
    EventDefinition,
    EventsListResult,
    LineEventArguments,
    SubscribeParams,
    SubscribeResult,
)
from services.switchboard import CallEvent, Switchboard, switchboard

_NEXT_STEP = (
    "Call `listen` with this line, your seat and wait_s=0 to read new messages, "
    "then reply with `say` using wait_s=0."
)


class EventsError(ValueError):
    """Invalid subscribe/unsubscribe request; maps to JSON-RPC -32602."""


@dataclass
class Subscription:
    id: str
    name: str
    line: str
    seat: str
    url: str
    secret: bytes
    owner: str
    expires_at: float


Sender = Callable[[str, bytes, dict[str, str]], int]


def _http_send(url: str, body: bytes, headers: dict[str, str]) -> int:
    timeout = global_config.telephone.events.request_timeout_s
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        return client.post(url, content=body, headers=headers).status_code


def decode_secret(secret: str) -> bytes:
    """Decode a ``whsec_``-prefixed base64 secret (24-64 bytes)."""
    if not secret.startswith("whsec_"):
        raise EventsError("delivery.secret must be a whsec_ value")
    try:
        raw = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except ValueError as exc:
        raise EventsError("delivery.secret is not valid base64") from exc
    if not 24 <= len(raw) <= 64:
        raise EventsError("delivery.secret must decode to 24-64 bytes")
    return raw


def sign(secret: bytes, msg_id: str, timestamp: int, body: bytes) -> str:
    """Standard Webhooks ``webhook-signature`` value for one delivery."""
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(secret, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def _check_callback_url(url: str) -> None:
    """Reject non-https and private-network callbacks (SSRF guard)."""
    if global_config.telephone.events.allow_insecure_callbacks:
        return
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise EventsError("delivery.url must be an https URL")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except OSError as exc:
        raise EventsError("delivery.url host does not resolve") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise EventsError("delivery.url must resolve to a public address")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def _message_schema() -> dict:
    return Message.model_json_schema()


class LineEvents:
    def __init__(
        self,
        board: Switchboard,
        sender: Sender = _http_send,
        executor: Executor | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._board = board
        self._sender = sender
        self._executor = executor or ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="line-events"
        )
        self._sleep = sleep
        self._lock = threading.Lock()
        self._subs: dict[str, Subscription] = {}
        board.add_listener(self.on_event)

    # -- protocol surface ------------------------------------------------

    def list_events(self) -> EventsListResult:
        args = LineEventArguments.model_json_schema()
        return EventsListResult(
            events=[
                EventDefinition(
                    name=LINE_MESSAGE,
                    description="The other agent said something on your call.",
                    input_schema=args,
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "line": {"type": "string"},
                            "call_id": {"type": "string"},
                            "message": _message_schema(),
                            "next_step": {"type": "string"},
                        },
                    },
                ),
                EventDefinition(
                    name=LINE_ENDED,
                    description="Your call ended (hang-up, turn cap, or idle).",
                    input_schema=args,
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "line": {"type": "string"},
                            "call_id": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                ),
            ]
        )

    def subscribe(self, params: SubscribeParams, owner: str) -> SubscribeResult:
        cfg = global_config.telephone.events
        args = params.arguments
        # Raises a TelephoneError (a ValueError) if the seat is not on the call.
        self._board.transcript(args.line, args.seat, after_seq=10**9)
        _check_callback_url(params.delivery.url)
        secret = decode_secret(params.delivery.secret)
        ttl_s = cfg.default_ttl_s if params.ttl_ms is None else params.ttl_ms / 1000
        ttl_s = max(60.0, min(ttl_s, cfg.max_ttl_s))
        key = "|".join((owner, params.delivery.url, params.name, args.line, args.seat))
        sub_id = "sub_" + hashlib.sha256(key.encode()).hexdigest()[:24]
        now = time.time()
        sub = Subscription(
            id=sub_id,
            name=params.name,
            line=args.line,
            seat=args.seat,
            url=params.delivery.url,
            secret=secret,
            owner=owner,
            expires_at=now + ttl_s,
        )
        with self._lock:
            self._subs[sub_id] = sub
        return SubscribeResult(
            id=sub_id,
            expires_at=_iso(sub.expires_at),
            refresh_before=_iso(now + ttl_s * 0.8),
        )

    def unsubscribe(self, sub_id: str, owner: str) -> None:
        with self._lock:
            sub = self._subs.get(sub_id)
            if sub is None or sub.owner != owner:
                raise EventsError(f"unknown subscription {sub_id!r}")
            del self._subs[sub_id]

    def reset(self) -> None:
        """Drop every subscription. For tests and local development only."""
        with self._lock:
            self._subs.clear()

    # -- delivery -----------------------------------------------------------

    def on_event(self, event: CallEvent) -> None:
        name = LINE_MESSAGE if event.kind == "message" else LINE_ENDED
        now = time.time()
        with self._lock:
            for sub_id in [s for s, sub in self._subs.items() if sub.expires_at < now]:
                del self._subs[sub_id]
            targets = [
                sub
                for sub in self._subs.values()
                if sub.name == name
                and sub.line == event.line
                and sub.seat in event.recipient_seats
            ]
        for sub in targets:
            self._executor.submit(self._deliver, sub, self._payload(name, event))

    def _payload(self, name: str, event: CallEvent) -> dict:
        data: dict = {"line": event.line, "call_id": event.call_id}
        if event.message is not None:
            data["message"] = event.message.model_dump()
            data["next_step"] = _NEXT_STEP
            cursor = str(event.message.seq)
        else:
            data["reason"] = event.ended_reason
            cursor = "end"
        return {
            "eventId": f"evt_{secrets.token_hex(8)}",
            "name": name,
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "data": data,
            "cursor": cursor,
        }

    def _deliver(self, sub: Subscription, payload: dict) -> None:
        cfg = global_config.telephone.events
        try:
            # Re-check at send time: DNS may have changed since subscribe.
            _check_callback_url(sub.url)
        except EventsError as exc:
            log.warning("dropping subscription {}: {}", sub.id, exc)
            with self._lock:
                self._subs.pop(sub.id, None)
            return
        body = json.dumps(payload, separators=(",", ":")).encode()
        for attempt in range(cfg.max_attempts):
            timestamp = int(time.time())
            headers = {
                "content-type": "application/json",
                "webhook-id": payload["eventId"],
                "webhook-timestamp": str(timestamp),
                "webhook-signature": sign(
                    sub.secret, payload["eventId"], timestamp, body
                ),
                "X-MCP-Subscription-Id": sub.id,
            }
            try:
                status = self._sender(sub.url, body, headers)
            except httpx.HTTPError as exc:
                log.warning("event delivery to {} failed: {}", sub.url, exc)
                status = 0
            if 200 <= status < 300:
                return
            if status in (410, 413):
                log.info("dropping subscription {} after HTTP {}", sub.id, status)
                with self._lock:
                    self._subs.pop(sub.id, None)
                return
            if attempt + 1 < cfg.max_attempts:
                backoff = cfg.backoff_s[min(attempt, len(cfg.backoff_s) - 1)]
                self._sleep(backoff)
        log.warning("gave up delivering {} to {}", payload["eventId"], sub.url)


line_events = LineEvents(switchboard)
