"""Record `cell.sting_cut`, and read back which Cells were cut and how many the Queen cut lately.

A Sting Cut (roadmap step 13.4a) is one trail event, `cell.sting_cut`, recorded with the reason,
who cut, the report and evidence, and every step's result; the trail is where "is this Cell cut"
and "how many has the Queen cut this hour" are answered, so both survive a restart. A Night Veil
Cell's event goes to its own segment and survives only as the skeleton (`cell.sting_cut` with no
payload, codingrules 12), so a skeleton row counts against the Queen's hourly cap whoever cut: a
cap that cannot tell must not undercount. `cut_cell_ids` names every Cell ever cut, which a
restarted Queen reads so a cut Cell's key is never re-armed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Called by `.path` and `.authority`, and by
    `hivemind.cli.compose.hive.run` (`cut_cell_ids`). Calls into `hivemind.pheromone` and the
    sub-package's own order only; `QueenDeps` only for its type.

Key invariants:
    - Ids, enum values and counts only on the event; never content the Cell held.

See Also:
    - hivemind.guard.policy.catalogue, where `cell.sting_cut` is authorised at `sting_cut`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    CellEvent,
    PheromoneEvent,
    PheromoneTrail,
    TrailQuery,
    query_cell,
)
from hivemind.queen.sting_cut.order import Cutter, StingCutOrder, StingCutOutcome
from waggle.ids import CellId, EventId, new_event_id

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps

STING_CUT_KIND = "cell.sting_cut"  # The cut itself: the event a Cell is never un-cut from.
CAP_WINDOW = timedelta(hours=1)  # max_autonomous_sting_cuts_per_hour's own window.

__all__ = [
    "CAP_WINDOW",
    "STING_CUT_KIND",
    "cut_cell_ids",
    "queen_cuts_in_window",
    "read_cut",
    "record_cut",
]


async def read_cut(deps: QueenDeps, cell_id: CellId) -> PheromoneEvent | None:
    """Return `cell_id`'s `cell.sting_cut` event, or None when it was never cut.

    Args:
        deps: The Queen's collaborators; `trail` is read, with a Night Veil Cell's segment.
        cell_id: The Cell.

    Returns:
        The newest cut event, or None.
    """
    query = TrailQuery(kind=STING_CUT_KIND, subject_id=cell_id, newest_first=True, limit=1)
    events = await query_cell(deps.trail, cell_id, query)
    return events[0] if events else None


async def queen_cuts_in_window(deps: QueenDeps) -> int:
    """Count the Queen's cuts in the last hour; a skeleton row, whoever cut, counts as hers.

    Args:
        deps: The Queen's collaborators; `trail` and `clock` are read.

    Returns:
        How many `cell.sting_cut` events in the window were the Queen's or cannot say.
    """
    since = deps.clock.now() - CAP_WINDOW
    query = TrailQuery(kind=STING_CUT_KIND, since=since, limit=MAX_QUERY_LIMIT)
    events = await deps.trail.query(query)
    # A skeleton row keeps no payload (codingrules 12): counted, so the cap never undercounts.
    queen = Cutter.QUEEN.value
    return sum(1 for event in events if event.payload.get("cut_by", queen) == queen)


async def cut_cell_ids(trail: PheromoneTrail) -> frozenset[CellId]:
    """Return every Cell the durable trail shows cut, for a restarted Queen's gate to skip.

    Args:
        trail: The Hive's trail.

    Returns:
        The Cells with a `cell.sting_cut` (bounded by the trail's own query limit).
    """
    events = await trail.query(TrailQuery(kind=STING_CUT_KIND, limit=MAX_QUERY_LIMIT))
    return frozenset(CellId(event.subject_id) for event in events)


async def record_cut(deps: QueenDeps, order: StingCutOrder, outcome: StingCutOutcome) -> EventId:
    """Record `cell.sting_cut` for `order`, with what each step of `outcome` did.

    Args:
        deps: The Queen's collaborators.
        order: The cut carried out.
        outcome: Its steps' results (the event id itself is minted here).

    Returns:
        The event's id.
    """
    payload: dict[str, JsonValue] = {
        "reason": order.reason,
        "cut_by": order.cut_by.value,
        "report_id": order.report_id,
        "evidence": list(order.evidence),
        "decision_event_id": order.decision_event_id,
        "device_id": order.device_id,
        "isolated_event_id": outcome.isolated_event_id,
        "snapshot_id": outcome.snapshot_id,
        "revoked_grant_ids": list(outcome.revoked_grant_ids),
        "scrub": outcome.scrub.value,
        "key_forgotten": outcome.key_forgotten,
    }
    event = CellEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=STING_CUT_KIND,
        subject_id=order.cell_id,
        payload=payload,
    )
    await deps.trail.record(event)
    return event.id
