"""Read what the diagnosis scenarios assert off the Hive: the Nectar that arrived, and from where.

The last exit criterion of phase 11 is a claim about order and provenance over three machines'
worth of record: the Warden on the device records `warden.diagnosed` and sends its findings up as
Nectar, which the Queen's intake stores and trails as `honey.nectar_received`; the Hive Stand's own
Drone writes a file, the Queen completes its task and deposits the outcome as Nectar the same way,
and the claim is that this precedes her decision about the device. A trail event names a Nectar only
by its id, so `deposits` joins each arrival with its stored row (who sent it, from which Cell, for
which task) and the helpers below pick out the one a claim is about. `for_device` narrows a trail to
what the device's own Warden recorded, which reaches the Hive's trail as a signed segment.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 diagnosis
    scenarios; reads the Hive's Honey Store and trail and changes neither.

Key invariants:
    - Every function only reads: nothing here deposits, relabels or ripens.
    - A deposit is the Nectar row as the store holds it, never what the sender claimed; the label
      and the Cell are the ones intake decided.
    - An arrival with no stored row (it was purged) is skipped, not an error: the trail outlives
      the rows the House Bee may prune.

See Also:
    - hivemind.honey_store.nectar for the intake that stores and trails each deposit.
    - e2e/swarm/colony/trail.py for the reads of the trail alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from e2e.swarm.colony.trail import named
from e2e.swarm.colony.waits import whole_trail

from hivemind.cli.compose import Hive
from hivemind.honey_store import Nectar, NectarNotFoundError, NectarOrigin
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode
from waggle.ids import NectarId
from waggle.messages.honey import NectarKind

__all__ = [
    "Deposit",
    "content_of",
    "deposits",
    "findings_of",
    "for_device",
    "outcome_of",
]


@dataclass(frozen=True, slots=True)
class Deposit:
    """One arrival of Nectar: the trail event that recorded it and the row intake stored.

    Attributes:
        event: The `honey.nectar_received` event, whose subject is the Nectar's id.
        nectar: The stored row: kind, origin, Cell, task and title as intake settled them.
    """

    event: PheromoneEvent
    nectar: Nectar


async def deposits(hive: Hive) -> list[Deposit]:
    """Every Nectar the Hive's intake has stored, in the order it arrived.

    Args:
        hive: The running Hive, with a Honey Store.

    Returns:
        One entry per `honey.nectar_received` event whose row still exists; a repeat deposit that
        was merged onto an earlier row (`honey.nectar_deduplicated`) is not an arrival.
    """
    assert hive.honey is not None, "the Hive was built without a Honey Store"
    found: list[Deposit] = []
    for event in named(await whole_trail(hive), "honey.nectar_received"):
        try:
            row = await hive.honey.store.get_nectar(NectarId(event.subject_id))
        except NectarNotFoundError:
            continue  # Pruned since: the trail still says it came.
        found.append(Deposit(event, row))
    return found


def findings_of(arrivals: Iterable[Deposit], node: SwarmNode) -> list[Deposit]:
    """The Nectar a device's Warden deposited about its lost link (ADR-0056).

    Args:
        arrivals: What `deposits` returned.
        node: The device whose Warden sent it.

    Returns:
        The deposits from the device's Cell that no task of the Queen's owns and that the Warden,
        not a Worker, sent: a finding of origin BEE with no task.
    """
    return [
        d
        for d in arrivals
        if d.nectar.cell_id == node.cell_id
        and d.nectar.origin is NectarOrigin.BEE
        and d.nectar.kind is NectarKind.FINDING
        and d.nectar.task_id is None
    ]


def outcome_of(arrivals: Iterable[Deposit], task_id: str) -> list[Deposit]:
    """The Nectar the Queen deposited when `task_id` completed: its verified outcome.

    Args:
        arrivals: What `deposits` returned.
        task_id: The task, as its id string.

    Returns:
        The deposits of origin TASK_OUTCOME that name the task.
    """
    return [
        d
        for d in arrivals
        if d.nectar.origin is NectarOrigin.TASK_OUTCOME and d.nectar.task_id == task_id
    ]


async def content_of(hive: Hive, deposit: Deposit) -> str:
    """Return the text of a stored Nectar."""
    assert hive.honey is not None, "the Hive was built without a Honey Store"
    return (await hive.honey.store.nectar_content(deposit.nectar.id)).decode("utf-8")


def for_device(trail: Iterable[PheromoneEvent], node: SwarmNode) -> list[PheromoneEvent]:
    """The events the device's own Warden recorded, which reached the Hive's trail as a segment."""
    return [event for event in trail if event.node_id == node.node_id]
