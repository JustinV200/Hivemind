"""Read what the Nuc scenario asserts off the Hive's trail: what ran when, how many, how often.

The Hive's trail is the one record of a scenario that spans three machines' worth of events (the
Queen's, the Hive Stand Wardens' and, merged after a reconnection, the device Warden's), and the
claims the exit criterion makes about it are claims about order and count: the ceilings come before
the order that uses them, the new source comes after the order and with no request beside it, a
task runs as the bees it needs and ends once, nothing the device recorded while it was cut off
comes twice when its segment is merged. These helpers answer exactly those questions over a list of
events and nothing else, so each is checked in milliseconds in `tests/unit/swarm/test_colony_rig.py`
before a container is started, and a scenario's assertion reads as the sentence it proves.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 Nuc scenario;
    imports `hivemind.pheromone` for the event type and nothing else of the Hive.

Key invariants:
    - Every function is pure: events in, a value out, no clock and no I/O.
    - An event "is about" a task when it names the task as its subject or in its payload, which is
      how the Queen's `task.*` events and a Warden's `worker.*` events each name it.
    - Two events are the same event only if they share a kind and a subject; a bee that ran twice
      is two bees, and a task that ended twice is the duplicate these helpers exist to find.

See Also:
    - tests/e2e/test_phase11_exit_nuc.py for the claims these answer.
    - hivemind.pheromone for the events themselves.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime

from hivemind.pheromone import LlmEvent, PheromoneEvent

__all__ = [
    "about",
    "after",
    "bees_of",
    "between",
    "ends_of",
    "grant_of",
    "llm_calls",
    "named",
    "peak_bees",
    "position",
    "recorded_twice",
    "repeated",
]

_ENDS_A_BEE = ("worker.done", "worker.failed", "worker.killed")  # What takes a bee off its hands.
_RUNS_A_BEE = (
    "worker.started",
    "worker.resumed",
)  # What has a bee working: first, or after a pause.
_STOPS_A_BEE = (*_ENDS_A_BEE, "worker.paused")  # A paused bee is not working either.


def named(events: Iterable[PheromoneEvent], *kinds: str) -> list[PheromoneEvent]:
    """The events of any of `kinds`, in the order given."""
    return [event for event in events if event.kind in kinds]


def about(events: Iterable[PheromoneEvent], task_id: str) -> list[PheromoneEvent]:
    """The events that name `task_id`, as their subject or as the task in their payload."""
    return [
        event
        for event in events
        if event.subject_id == task_id or event.payload.get("task_id") == task_id
    ]


def after(events: Iterable[PheromoneEvent], at: datetime) -> list[PheromoneEvent]:
    """The events recorded at or after `at`."""
    return [event for event in events if event.at >= at]


def between(
    events: Iterable[PheromoneEvent], start: datetime, end: datetime
) -> list[PheromoneEvent]:
    """The events recorded from `start` up to, not including, `end`."""
    return [event for event in events if start <= event.at < end]


def bees_of(events: Iterable[PheromoneEvent], task_id: str) -> list[PheromoneEvent]:
    """The `worker.started` events of the bees that ran `task_id`, in the order they started."""
    return [e for e in named(events, "worker.started") if e.payload.get("task_id") == task_id]


def ends_of(events: Sequence[PheromoneEvent], task_id: str) -> list[PheromoneEvent]:
    """The `worker.done` and `worker.failed` events that ended the bees of `task_id`."""
    bees = {bee.subject_id for bee in bees_of(events, task_id)}
    return [e for e in named(events, *_ENDS_A_BEE) if e.subject_id in bees]


def llm_calls(events: Iterable[PheromoneEvent], **where: str) -> list[LlmEvent]:
    """The model calls that were made, narrowed to those that match every `where`.

    Args:
        events: The trail.
        **where: Fields a call must have: `grant_id` or `goal_id` (the grant and goal it was made
            under, in its payload), `provider` or `slot` (which model answered it, on the event).

    Returns:
        The `llm.call` events that match all of them, in the order given; a call that failed is
        not an `llm.call` and is not here.
    """
    return [
        event
        for event in events
        if isinstance(event, LlmEvent)
        and event.kind == "llm.call"
        and all(event.payload.get(k, getattr(event, k, None)) == v for k, v in where.items())
    ]


def grant_of(events: Iterable[PheromoneEvent], task_id: str) -> str | None:
    """The first grant the Queen issued for `task_id`: the subject of its `forage.granted`.

    Args:
        events: The trail.
        task_id: A task the Queen placed.

    Returns:
        The grant's id, or None when the trail holds no grant for the task.
    """
    for event in named(events, "forage.granted"):
        if event.payload.get("task_id") == task_id:
            return event.subject_id
    return None


def position(events: Sequence[PheromoneEvent], kind: str, **payload: object) -> int | None:
    """The index of the first event of `kind` whose payload holds `payload`, else None.

    Args:
        events: The trail, oldest first.
        kind: The kind to find.
        **payload: Payload keys and the values they must have.

    Returns:
        Where it is in `events`; two positions compare as the order the events were recorded in.
    """
    for index, event in enumerate(events):
        if event.kind == kind and all(event.payload.get(k) == v for k, v in payload.items()):
            return index
    return None


def repeated(events: Iterable[PheromoneEvent], kinds: Iterable[str]) -> list[tuple[str, str]]:
    """Every (kind, subject) of `kinds` that the trail holds more than once.

    Args:
        events: The trail.
        kinds: The kinds that must be recorded once per subject: a bee that starts, a task that
            succeeds.

    Returns:
        The pairs seen twice or more; empty when nothing was recorded twice.
    """
    wanted = frozenset(kinds)
    counts = Counter((event.kind, event.subject_id) for event in events if event.kind in wanted)
    return sorted(pair for pair, count in counts.items() if count > 1)


def recorded_twice(events: Iterable[PheromoneEvent]) -> list[tuple[str, str, str]]:
    """Every (node, kind, subject) the trail holds more than once for the same instant.

    The trail refuses a second event with the same id, so a segment merged twice adds nothing. This
    finds the other way an event comes twice: two events with different ids that say the same
    thing, at the same time, about the same subject, from the same node.

    Args:
        events: The trail.

    Returns:
        The triples seen twice or more; empty when no event was recorded twice.
    """
    counts = Counter((e.node_id, e.kind, e.subject_id, e.at) for e in events)
    return sorted(
        {(node, kind, subject) for (node, kind, subject, _), n in counts.items() if n > 1}
    )


def peak_bees(events: Iterable[PheromoneEvent], task_id: str | None = None) -> int:
    """The most bees that were working at once, from `worker.started` to a pause or an end.

    Args:
        events: The trail; sorted by time here, because a merged segment arrives after the events
            the Queen recorded meanwhile.
        task_id: Count only the bees of this task: how many ran it at once. All tasks' bees when
            omitted: how many the Warden had running, which its ceiling bounds.

    Returns:
        The largest number of bees that were working at any moment.
    """
    # Only a bee's start names its task; the events that resume or stop it name the bee alone.
    task_of = {e.subject_id: e.payload.get("task_id") for e in named(events, "worker.started")}
    ordered = sorted(named(events, *_RUNS_A_BEE, *_STOPS_A_BEE), key=lambda e: e.at)
    working: set[str] = set()
    peak = 0
    for event in ordered:
        if event.kind in _RUNS_A_BEE:
            if task_id is None or task_of.get(event.subject_id) == task_id:
                working.add(event.subject_id)
                peak = max(peak, len(working))
        else:
            working.discard(event.subject_id)
    return peak
