"""Define the trail resource's read models: one Pheromone Trail event, and a page of them.

The Pheromone Trail is the Hive's audit log (codingrules 12), and it carries no content by
construction: every event is who did what to what and when, with a bounded payload of ids, counts
and reason codes that the event model itself refuses to let hold prompt or completion text. So
``TrailEventView`` shows an event as the trail holds it, under ``observe``: its kind, subject,
actor, node, time and payload, plus the slot, provider and normalised usage an ``llm.call`` event
carries (never a model id or a key). A page is read in trail order from a cursor, not an offset:
``TrailCursor`` is where the next page starts (an instant and how many events at exactly that
instant were already read), because events at one instant are common and must be neither skipped
nor read twice. ``TrailFilters`` is the read's query string: the filters, the cursor and the page
size, validated as one model (unknown parameters are refused).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models.views``.
    Answered by ``hivemind.entrance.routes.hive.trail`` (paged by
    ``hivemind.entrance.reads.trail``) and sent by the trail and Forage streams; published in
    the OpenAPI document. Calls into ``hivemind.pheromone`` and pydantic.

Key invariants:
    - A view carries what the trail carries and nothing more.

See Also:
    - hivemind.pheromone.events for what an event may hold.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.pheromone import MAX_QUERY_LIMIT, LlmEvent, PheromoneEvent
from waggle.messages.base import UtcDatetime

MAX_TRAIL_PAGE = 500  # Most events one page answers: a screenful, and bounded for a program.
MAX_TRAIL_SKIP = MAX_QUERY_LIMIT - MAX_TRAIL_PAGE  # So skip + limit fits one store query.
DEFAULT_TRAIL_PAGE = 100  # What a page holds when the caller does not say.
MAX_FILTER_CHARS = 128  # A family, a kind or a subject id: short identifiers, never text.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

__all__ = [
    "DEFAULT_TRAIL_PAGE",
    "MAX_FILTER_CHARS",
    "MAX_TRAIL_PAGE",
    "MAX_TRAIL_SKIP",
    "TrailCursor",
    "TrailEventView",
    "TrailFilters",
    "TrailPage",
    "TrailUsageView",
    "trail_event_view",
]


class TrailFilters(BaseModel):
    """A trail read's query string: filters, cursor and page size (unknown keys refused)."""

    model_config = _CONFIG

    family: str | None = Field(
        default=None, max_length=MAX_FILTER_CHARS, description="Only this family (task, cell...)."
    )
    kind: str | None = Field(
        default=None, max_length=MAX_FILTER_CHARS, description="Only this kind (task.assigned)."
    )
    subject_id: str | None = Field(
        default=None, max_length=MAX_FILTER_CHARS, description="Only events about this id."
    )
    since: UtcDatetime | None = Field(
        default=None, description="Read from this instant (inclusive); oldest first's cursor."
    )
    until: UtcDatetime | None = Field(
        default=None, description="Read up to this instant (inclusive); newest first's cursor."
    )
    skip: int = Field(
        default=0,
        ge=0,
        le=MAX_TRAIL_SKIP,
        description="Events at exactly the cursor's instant already read (a page's next.skip).",
    )
    limit: int = Field(
        default=DEFAULT_TRAIL_PAGE, ge=1, le=MAX_TRAIL_PAGE, description="Most events to return."
    )
    newest_first: bool = Field(default=False, description="Read newest first.")


class TrailUsageView(BaseModel):
    """The normalised usage an ``llm.call`` event records."""

    model_config = _CONFIG

    input_tokens: int = Field(description="Tokens the request consumed.")
    output_tokens: int = Field(description="Tokens the response generated.")
    cached_tokens: int = Field(description="Tokens served from a provider's cache.")
    cost_usd: float = Field(description="What the call cost, in US dollars.")


class TrailEventView(BaseModel):
    """One Pheromone Trail event, as the trail records it (observe)."""

    model_config = _CONFIG

    id: str = Field(description="The event's id.")
    kind: str = Field(description="<family>.<name>, e.g. task.assigned.")
    family: str = Field(description="The kind's family, e.g. task.")
    subject_id: str = Field(description="What it is about: a task, Cell, grant, device ... id.")
    actor: str = Field(description="Who did it: a bee's or device's id, human or system.")
    node_id: str = Field(description="The node (Queen or Warden) whose segment recorded it.")
    at: datetime = Field(description="When it happened.")
    payload: dict[str, JsonValue] = Field(description="Ids, counts and reason codes only.")
    slot: str | None = Field(default=None, description="llm.*: the model slot a call served.")
    provider: str | None = Field(default=None, description="llm.*: the provider's manifest name.")
    usage: TrailUsageView | None = Field(default=None, description="llm.call: its usage.")


class TrailCursor(BaseModel):
    """Where the next page starts: pass these back as query parameters, with the same filters."""

    model_config = _CONFIG

    since: datetime | None = Field(description="Oldest first: the instant to read from.")
    until: datetime | None = Field(description="Newest first: the instant to read back from.")
    skip: int = Field(description="Events at exactly that instant already read.")


class TrailPage(BaseModel):
    """One page of trail events, in trail order (or newest first when asked)."""

    model_config = _CONFIG

    events: list[TrailEventView] = Field(description="The page's events.")
    next: TrailCursor | None = Field(description="Where the next page starts; null at the end.")


def trail_event_view(event: PheromoneEvent) -> TrailEventView:
    """Shape a trail event, carrying an llm event's slot, provider and usage too.

    Args:
        event: The event as the trail holds it.

    Returns:
        Its view.
    """
    view = TrailEventView(
        id=event.id,
        kind=event.kind,
        family=event.family,
        subject_id=event.subject_id,
        actor=event.actor,
        node_id=event.node_id,
        at=event.at,
        payload=dict(event.payload),
    )
    # Only an llm event carries its routing and usage beside the base fields.
    if not isinstance(event, LlmEvent):
        return view
    usage = event.usage
    usage_view = (
        None
        if usage is None
        else TrailUsageView(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cost_usd=usage.cost_usd,
        )
    )
    return view.model_copy(
        update={"slot": event.slot, "provider": event.provider, "usage": usage_view}
    )
