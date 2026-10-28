"""Read and write a Supersedure's trail: its start, the candidate's readiness and its outcome.

ADR-0065 keeps "never two Queens" as data in each node's own trail, because that trail is what a
restarted process can always read. `queen.supersedure_started` is written by the old Queen before
the first byte of her stores is copied, so the copy carries it to the candidate too: it names the
old node (the event's own `node_id`), the candidate, the notice sequence and the grace deadline.
`queen.supersedure_ready` is the candidate's word that she is up and held. Each Supersedure ends in
exactly one outcome per trail: `queen.superseded` (the move happened) or
`queen.supersedure_aborted` (rolled back, or settled so by the human). `latest` folds the newest
start and its outcome into a `SupersedureRecord`; `next_sequence` gives the next `QueenMoved`
sequence, strictly above every notice this Hive has sent. `Recorder` writes the events.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Read by `.gate` (start rules, the
    candidate's hold), `.run`, `.handover` and `.rollback`. Calls into `hivemind.pheromone` and
    waggle only.

Key invariants:
    - Payloads carry ids, a sequence, booleans, enum strings and instants only (codingrules 12).
    - An outcome counts only for the start it names (`started_id`); a later start is a new
      Supersedure.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import JsonValue

from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    PheromoneEvent,
    PheromoneTrail,
    QueenEvent,
    TrailQuery,
)
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

STARTED_KIND = "queen.supersedure_started"
READY_KIND = "queen.supersedure_ready"
SUPERSEDED_KIND = "queen.superseded"
ABORTED_KIND = "queen.supersedure_aborted"
_OUTCOME_KINDS = (SUPERSEDED_KIND, ABORTED_KIND)

__all__ = [
    "ABORTED_KIND",
    "READY_KIND",
    "STARTED_KIND",
    "SUPERSEDED_KIND",
    "AbortCause",
    "Recorder",
    "SupersedureRecord",
    "latest",
    "next_sequence",
    "was_ready",
]


class AbortCause(Enum):
    """Why a Supersedure was rolled back."""

    COPY_FAILED = "COPY_FAILED"  # The backup, its transfer, the restore or the start failed.
    NOT_READY = "NOT_READY"  # The candidate was not ready before the grace deadline.
    SETTLED = "SETTLED"  # The human settled an unfinished one as aborted after a restart.
    DISCARDED = "DISCARDED"  # The candidate was told by the old Queen to stand down.


@dataclass(frozen=True, slots=True)
class SupersedureRecord:
    """The newest Supersedure on a trail, and how it ended (None while it has not).

    Attributes:
        started_id: The `queen.supersedure_started` event.
        old_node_id: The node whose Queen started it.
        candidate_node_id: The node it moves the Hive Stand to.
        sequence: The `QueenMoved` sequence reserved for the move.
        grace_until: The deadline for the candidate's readiness, and the notice's grace.
        old_address: The old Queen's Waggle address, which the candidate probes after the window.
        outcome: `SUPERSEDED_KIND`, `ABORTED_KIND`, or None while unfinished.
    """

    started_id: str
    old_node_id: str
    candidate_node_id: str
    sequence: int
    grace_until: datetime
    old_address: str
    outcome: str | None


@dataclass(frozen=True, slots=True)
class Recorder:
    """Writes Supersedure events as one node of one Hive."""

    trail: PheromoneTrail
    hive_id: HiveId
    node_id: NodeId
    clock: Clock

    async def record(self, kind: str, actor: str, **payload: JsonValue) -> str:
        """Record one `queen.*` Supersedure event subject to the Hive.

        Args:
            kind: One of the four kinds above.
            actor: Who acts: the Queen's hive id, or the human's device.
            **payload: Ids, a sequence, booleans, enum strings and instants only.

        Returns:
            The new event's id.
        """
        event = QueenEvent(
            id=new_event_id(self.clock),
            hive_id=self.hive_id,
            node_id=self.node_id,
            at=self.clock.now(),
            actor=actor,
            kind=kind,
            subject_id=self.hive_id,
            payload=payload,
        )
        await self.trail.record(event)
        return event.id


async def latest(trail: PheromoneTrail) -> SupersedureRecord | None:
    """Return the newest Supersedure this trail started or copied in, with its outcome.

    Args:
        trail: A node's own trail.

    Returns:
        The record, or None when no Supersedure was ever started on this Hive's copy.
    """
    # Latency: two indexed local reads; a Hive starts a handful of Supersedures in its life.
    started = await _newest(trail, STARTED_KIND)
    if started is None:
        return None
    outcome = None
    for kind in _OUTCOME_KINDS:
        events = await _all(trail, kind)
        if any(str(e.payload.get("started_id")) == started.id for e in events):
            outcome = kind
    payload = started.payload
    return SupersedureRecord(
        started_id=started.id,
        old_node_id=started.node_id,
        candidate_node_id=str(payload["candidate_node_id"]),
        sequence=_int(payload["sequence"]),
        grace_until=datetime.fromisoformat(str(payload["grace_until"])),
        old_address=str(payload.get("old_address", "")),
        outcome=outcome,
    )


async def was_ready(trail: PheromoneTrail, started_id: str) -> bool:
    """Return whether this trail holds the candidate's `queen.supersedure_ready` for a start.

    Args:
        trail: The candidate's trail.
        started_id: The Supersedure asked about.

    Returns:
        True once the candidate recorded she is up and held.
    """
    events = await _all(trail, READY_KIND)
    return any(str(e.payload.get("started_id")) == started_id for e in events)


async def next_sequence(trail: PheromoneTrail) -> int:
    """Return the next `QueenMoved` sequence: above every one this Hive reserved or sent.

    Args:
        trail: The Queen's own trail.

    Returns:
        One more than the highest sequence any start or rollback recorded; 1 for the first.
    """
    highest = 0
    for kind in (STARTED_KIND, SUPERSEDED_KIND, ABORTED_KIND):
        for event in await _all(trail, kind):
            for key in ("sequence", "notice_sequence"):
                value = event.payload.get(key)
                if isinstance(value, int):
                    highest = max(highest, value)
    return highest + 1


async def _newest(trail: PheromoneTrail, kind: str) -> PheromoneEvent | None:
    """The newest event of `kind`, or None."""
    found = await trail.query(TrailQuery(kind=kind, limit=1, newest_first=True))
    return found[0] if found else None


async def _all(trail: PheromoneTrail, kind: str) -> tuple[PheromoneEvent, ...]:
    """Every event of `kind`, newest first, in one bounded page."""
    return await trail.query(TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT, newest_first=True))


def _int(value: JsonValue) -> int:
    """A payload integer, refusing anything else (the trail is the source of truth here)."""
    if not isinstance(value, int):
        raise ValueError(f"A Supersedure sequence must be an integer, not {value!r}.")
    return value
