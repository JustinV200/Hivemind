"""Wait, bounded and by name, for what a scenario that spans minutes is waiting on.

The Nuc scenario moves one device up two rungs of the ladder and cuts it off, and each step is
something the Hive does on its own beat: the Queen acts on the operator's wish when her swarm gate
next looks, a Warden notices a cut when its websocket's keepalive gives up on the link, a held task
starts again a few seconds after the link is back. Nothing here is a sleep that hopes: each wait
polls a condition on the real clock and ends with an error that says what it was waiting for and for
how long, so a scenario that stops half way reads as "the Warden never noticed the cut", not as
"wait_until: condition never held". The conditions are questions about the Hive's own trail and
task chamber, which are what the criteria are claims about.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `e2e.swarm.colony.run`;
    builds on `e2e.kernel_helpers.wait_until` and the Hive's trail and task chamber.

Key invariants:
    - Every wait has a bound, and every bound is named at the call, never defaulted.
    - A condition only reads: nothing here changes the Hive or the container.
    - The trail is read whole (up to the store's page limit) or not at all: a scenario that
      outgrew a page fails loudly instead of asserting over a part of its trail.

See Also:
    - tests/e2e/kernel_helpers.py for `wait_until`, which this names.
    - tests/e2e/swarm/hive.py for the single-event reads the simpler scenarios use.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from e2e.kernel_helpers import wait_until

from hivemind.brood_chamber import TaskFilter, is_terminal
from hivemind.cli.compose import Hive
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.ids import TaskId

POLL_S = 1.0  # How often a condition is asked: the Hive moves in seconds, not milliseconds.

__all__ = [
    "POLL_S",
    "goal_has_ended",
    "has_event",
    "promoted_to",
    "tasks_of",
    "until",
    "whole_trail",
]


async def until(
    what: str, condition: Callable[[], bool | Awaitable[bool]], timeout_s: float
) -> None:
    """Wait up to `timeout_s` real seconds for `condition`, and say what was awaited if it fails.

    Args:
        what: What is being waited for, as a phrase that completes "timed out waiting for ...".
        condition: Asked every `POLL_S`; a plain bool or an awaitable one.
        timeout_s: The longest to wait.

    Raises:
        AssertionError: The condition did not hold in time, naming `what` and the bound.
    """
    try:
        await wait_until(condition, timeout_s=timeout_s, poll_s=POLL_S)
    except AssertionError as error:
        raise AssertionError(f"Timed out after {timeout_s:.0f} s waiting for {what}.") from error


async def whole_trail(hive: Hive) -> list[PheromoneEvent]:
    """Every event on the Hive's trail, oldest first.

    Args:
        hive: The running Hive.

    Returns:
        The events; the call fails rather than return a page that may not be the whole trail.
    """
    events = list(await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT)))
    assert len(events) < MAX_QUERY_LIMIT, "the trail outgrew one page: read it in pages"
    return events


async def has_event(
    hive: Hive, kind: str, since: datetime | None = None, **payload: object
) -> bool:
    """Whether the trail holds an event of `kind` (from `since` on) whose payload matches.

    Args:
        hive: The running Hive.
        kind: The event kind.
        since: Only events recorded at or after this moment count; any when omitted.
        **payload: Payload keys and the values they must have.
    """
    events = await hive.stores.trail.query(TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT))
    return any(
        (since is None or e.at >= since) and all(e.payload.get(k) == v for k, v in payload.items())
        for e in events
    )


async def promoted_to(hive: Hive, level: str) -> bool:
    """Whether the Queen has recorded a device reaching `level` (`swarm.promoted`)."""
    return await has_event(hive, "swarm.promoted", to_level=level)


async def goal_has_ended(hive: Hive, goal: TaskId) -> bool:
    """Whether every task of `goal` is in a terminal state."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal))
    return bool(tasks) and all(is_terminal(task.status) for task in tasks)


async def tasks_of(hive: Hive, goal: TaskId) -> dict[str, str]:
    """The tasks of `goal` by their titles, each an id; empty before the Queen has planned it."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal))
    return {task.spec.title: str(task.id) for task in tasks}
