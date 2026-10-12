"""Build the Thoughts page: a bee's episode records, newest first, and the Queen's autopilot rules.

Thoughts are memory, not audit (codingrules 12): every awake episode is an ``EpisodeRecord``, and
the Observation Hive's thoughts view reads them for any bee (codingrules 8.11). The existing
``/v1/episodes`` read answers the newest few; this page is the paginated one: records come newest
first and the page carries an opaque cursor naming the last record it showed, so the next request
continues exactly after it even while newer episodes arrive. For the Queen the page also lists her
recent deterministic decisions with the rule that fired: the ``queen.decided`` and ``alarm.handled``
trail events, each with the action taken and the reason code recorded. Autopilot leaves no episode
record today, so these trail rows are where the Hive shows it; they carry codes and ids only.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from
    memory's episodes and the trail by ``hivemind.entrance.reads.screens.thoughts``; answered only
    behind ``observe:thoughts`` and ``honey:clearance:c2``. Pure: calls into the episode view and
    pydantic.

Key invariants:
    - Pure: records and events are handed in.
    - A cursor names a (time, id) pair in URL-safe form; a malformed one is refused when the
      query is validated.
    - The decisions list is the Queen's: it is empty for any other principal.

See Also:
    - hivemind.observation.views.episodes for the record view this page carries.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hivemind.memory import EpisodeRecord
from hivemind.memory.episodes import MAX_PRINCIPAL_CHARS
from hivemind.observation.views.episodes import EpisodeView, episode_view
from hivemind.pheromone import PheromoneEvent

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

QUEEN_PRINCIPAL = "queen"  # The principal whose autopilot decisions the page adds.
DEFAULT_THOUGHTS_PAGE = 50  # Records answered when the caller does not say.
MAX_THOUGHTS_PAGE = 200  # Most records one page answers.
MAX_DECISIONS = 50  # Most autopilot decisions one page lists.
_SEPARATOR = "."  # Between the instant and the id in a cursor; neither can contain it.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # Cursors count microseconds from here, in UTC.
_MICROSECOND = timedelta(microseconds=1)  # The unit a cursor counts in: exact, no float rounding.
_DECISION_KINDS = frozenset({"queen.decided", "alarm.handled"})  # The trail's autopilot rows.

__all__ = [
    "DEFAULT_THOUGHTS_PAGE",
    "MAX_DECISIONS",
    "MAX_THOUGHTS_PAGE",
    "QUEEN_PRINCIPAL",
    "AutopilotDecisionView",
    "ThoughtsPage",
    "ThoughtsQuery",
    "thoughts_page",
]


class ThoughtsQuery(BaseModel):
    """A thoughts read's query string: whose, where to continue from, and how many."""

    model_config = _CONFIG

    principal: str | None = Field(
        default=None,
        max_length=MAX_PRINCIPAL_CHARS,
        description="Only this bee's records (its id or role); every bee's when omitted.",
    )
    cursor: str | None = Field(
        default=None,
        max_length=128,
        description="A previous page's next_cursor: continue after the record it names.",
    )
    limit: int = Field(
        default=DEFAULT_THOUGHTS_PAGE, ge=1, le=MAX_THOUGHTS_PAGE, description="Most records."
    )

    @field_validator("cursor")
    @classmethod
    def _cursor_decodes(cls, value: str | None) -> str | None:
        """Refuse a cursor that does not decode, so the route answers 422 instead of guessing."""
        if value is not None:
            decode_cursor(value)
        return value


class AutopilotDecisionView(BaseModel):
    """One deterministic decision of the Queen's and the rule outcome recorded for it."""

    model_config = _CONFIG

    id: str = Field(description="The trail event's id.")
    at: datetime = Field(description="When it was decided.")
    kind: str = Field(description="The trail kind: queen.decided or alarm.handled.")
    subject_id: str = Field(description="What it concerned: a task, Cell, Alarm or the Hive.")
    action: str | None = Field(description="The action taken (REBIND, CLUSTER, RECORD ...).")
    reason: str | None = Field(description="The reason code recorded with it, if any.")
    alarm_id: str | None = Field(description="The Alarm that prompted it, if any.")


class ThoughtsPage(BaseModel):
    """One page of a bee's thinking (observe:thoughts, C2)."""

    model_config = _CONFIG

    episodes: list[EpisodeView] = Field(description="Episode records, newest first.")
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at end.")
    decisions: list[AutopilotDecisionView] = Field(
        description="The Queen's recent autopilot decisions, newest first; empty for other bees."
    )


def thoughts_page(
    records: Sequence[EpisodeRecord], events: Sequence[PheromoneEvent], query: ThoughtsQuery
) -> ThoughtsPage:
    """Cut one page of episode records after the query's cursor.

    Args:
        records: Episode records in any order, already limited to the query's principal.
        events: Trail events from which the Queen's autopilot decisions are read.
        query: The validated query.

    Returns:
        The page: up to ``limit`` records newest first, the cursor for the next, the decisions.
    """
    ordered = sorted(records, key=lambda record: (record.at, record.id), reverse=True)
    # Continue strictly after the record the cursor names, whatever has arrived since.
    if query.cursor is not None:
        after = decode_cursor(query.cursor)
        ordered = [record for record in ordered if (record.at, record.id) < after]
    page = ordered[: query.limit]
    more = len(ordered) > query.limit
    wants_decisions = query.principal in (None, QUEEN_PRINCIPAL)
    return ThoughtsPage(
        episodes=[episode_view(record) for record in page],
        next_cursor=encode_cursor(page[-1]) if more else None,
        decisions=_decisions(events) if wants_decisions else [],
    )


def encode_cursor(record: EpisodeRecord) -> str:
    """Name a record as a cursor.

    Args:
        record: The last record of a page.

    Returns:
        ``<microseconds since the epoch>.<id>``: URL-safe (no ``+`` or ``:``) so a client can
        paste it into a query string unescaped. ``decode_cursor`` reads it back.
    """
    micros = (record.at - _EPOCH) // _MICROSECOND
    return f"{micros}{_SEPARATOR}{record.id}"


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Read a cursor back into the instant and id it names.

    Args:
        cursor: A string from ``encode_cursor``.

    Returns:
        The (instant, id) pair.

    Raises:
        ValueError: The cursor is not '<microseconds>.<id>'.
    """
    micros, separator, record_id = cursor.partition(_SEPARATOR)
    if not separator or not record_id or not micros.isdecimal():
        raise ValueError("A cursor is '<microseconds>.<id>'.")
    try:
        return _EPOCH + int(micros) * _MICROSECOND, record_id
    except OverflowError:
        # A number too large to be an instant is as malformed as a word.
        raise ValueError("A cursor's instant is out of range.") from None


def _decisions(events: Sequence[PheromoneEvent]) -> list[AutopilotDecisionView]:
    """Shape the Queen's deterministic decisions, newest first."""
    chosen = sorted(
        (event for event in events if event.kind in _DECISION_KINDS),
        key=lambda event: event.at,
        reverse=True,
    )
    return [
        AutopilotDecisionView(
            id=event.id,
            at=event.at,
            kind=event.kind,
            subject_id=event.subject_id,
            action=_text(event.payload.get("action")),
            reason=_text(event.payload.get("reason")) or _text(event.payload.get("cause")),
            alarm_id=_text(event.payload.get("alarm_id")) or _alarm_subject(event),
        )
        for event in chosen[:MAX_DECISIONS]
    ]


def _alarm_subject(event: PheromoneEvent) -> str | None:
    """Name the Alarm an ``alarm.handled`` event is about: its subject; other kinds name none."""
    return event.subject_id if event.kind == "alarm.handled" else None


def _text(value: object) -> str | None:
    """Return a payload value when it is a string, else None."""
    return value if isinstance(value, str) else None
