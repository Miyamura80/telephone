"""Wire models for MCP Events (draft spec) as served by the switchboard.

The MCP Triggers and Events extension is still a draft; these shapes follow
the design sketch and what ChatGPT implements (webhook delivery only, signed
per Standard Webhooks). See ``services/line_events.py``.
"""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from models.telephone import LINE_PATTERN

EventName = Literal["line.message", "line.ended"]
LINE_MESSAGE, LINE_ENDED = get_args(EventName)


class _Wire(BaseModel):
    """camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LineEventArguments(BaseModel):
    line: str = Field(pattern=LINE_PATTERN)
    seat: str = Field(min_length=1)


class WebhookDelivery(BaseModel):
    mode: Literal["webhook"] = "webhook"
    url: str = Field(min_length=1)
    secret: str = Field(
        min_length=1,
        repr=False,
        description="Client-generated Standard Webhooks secret (whsec_...).",
    )


class SubscribeParams(_Wire):
    name: EventName
    arguments: LineEventArguments
    delivery: WebhookDelivery
    # The server enforces 60s..max_ttl_s; reject what it would not honor
    # rather than silently stretching it (and keep huge values from overflowing).
    ttl_ms: int | None = Field(default=None, ge=60_000, le=10**12)


class SubscribeResult(_Wire):
    id: str
    expires_at: str
    refresh_before: str
    # Webhook-only server: no replay cursor, never truncated.
    cursor: str | None = None
    truncated: bool = False


class _DeliveryTarget(BaseModel):
    url: str = Field(min_length=1)


class UnsubscribeParams(BaseModel):
    """By subscription ``id``, or by the key it was subscribed with."""

    id: str | None = Field(default=None, min_length=1)
    name: EventName | None = None
    arguments: LineEventArguments | None = None
    delivery: _DeliveryTarget | None = None

    @model_validator(mode="after")
    def _id_or_key(self) -> "UnsubscribeParams":
        key = (self.name, self.arguments, self.delivery)
        by_key = all(part is not None for part in key)
        some_key = any(part is not None for part in key)
        if (self.id is None and not by_key) or (self.id is not None and some_key):
            raise ValueError(
                "pass either id, or all of name, arguments and delivery.url"
            )
        return self


class EventDefinition(_Wire):
    name: str
    description: str
    input_schema: dict[str, Any]
    payload_schema: dict[str, Any]


class EventsListResult(BaseModel):
    events: list[EventDefinition]
