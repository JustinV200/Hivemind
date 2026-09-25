"""Define the frames the Entrance's live streams send: chat lines and security events.

A live view sends JSON text frames, one model per stream (ADR-0040), so a client parses a stream
from the published contract alone. ``/v1/chat/stream`` sends a ``ChatFrame`` per new chat line
(with its position, so a client that reconnects resumes from where it was); ``/v1/entrance/stream``
sends a ``SecurityFrame`` per Entrance security event, the ``guard.*`` trail event itself (its kind,
subject and bounded, identifier-only payload); ``/v1/push/stream`` sends ``PushNotice`` frames, the
push channel's own model.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by the
    stream views; published in the OpenAPI document's ``x-hive-streams``. Calls into the chat
    model and pydantic.

Key invariants:
    - A security frame carries what the trail carries and nothing more.

See Also:
    - hivemind.entrance.streams for the views that send them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.entrance.models.chat import ChatLine
from hivemind.pheromone import PheromoneEvent

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every frame here: immutable, no strays.

__all__ = ["ChatFrame", "SecurityEventView", "SecurityFrame", "security_frame"]


class ChatFrame(BaseModel):
    """One new chat line, as ``/v1/chat/stream`` sends it (C2)."""

    model_config = _CONFIG

    type: Literal["chat"] = Field(default="chat", description="Always chat.")
    entry: ChatLine = Field(description="The line; its seq is the resume cursor.")


class SecurityEventView(BaseModel):
    """An Entrance security event, as the trail records it."""

    model_config = _CONFIG

    id: str = Field(description="The trail event's id: a security_event push's ref.")
    kind: str = Field(description="guard.entrance_*, guard.reduced, guard.reopened, ...")
    subject_id: str = Field(description="The device (or the Hive) it is about.")
    actor: str = Field(description="Who did it: a device id, human or system.")
    at: datetime = Field(description="When it happened.")
    payload: dict[str, JsonValue] = Field(description="Identifiers, counts and reasons only.")


class SecurityFrame(BaseModel):
    """One Entrance security event, as ``/v1/entrance/stream`` sends it."""

    model_config = _CONFIG

    type: Literal["security_event"] = Field(
        default="security_event", description="Always security_event."
    )
    event: SecurityEventView = Field(description="The event.")


def security_frame(event: PheromoneEvent) -> SecurityFrame:
    """Shape a security trail event as a frame.

    Args:
        event: A ``guard.*`` trail event.

    Returns:
        The frame.
    """
    view = SecurityEventView(
        id=event.id,
        kind=event.kind,
        subject_id=event.subject_id,
        actor=event.actor,
        at=event.at,
        payload=dict(event.payload),
    )
    return SecurityFrame(event=view)
