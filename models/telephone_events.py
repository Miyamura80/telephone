"""Wire models for MCP Events (draft spec) as served by the switchboard.

The MCP Triggers and Events extension is still a draft; these shapes follow
the design sketch and what ChatGPT implements (webhook delivery only, signed
per Standard Webhooks). See ``services/line_events.py``.
"""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
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
        description="Client-generated Standard Webhooks secret (whsec_...).",
    )


class SubscribeParams(_Wire):
    name: EventName
    arguments: LineEventArguments
    delivery: WebhookDelivery
    ttl_ms: int | None = Field(default=None, ge=1)


class SubscribeResult(_Wire):
    id: str
    expires_at: str
    refresh_before: str


class UnsubscribeParams(BaseModel):
    id: str = Field(min_length=1)


class EventDefinition(_Wire):
    name: str
    description: str
    input_schema: dict[str, Any]
    payload_schema: dict[str, Any]


class EventsListResult(BaseModel):
    events: list[EventDefinition]
