"""Define an isolated Cell's two states, and read and write the trail events that move it.

A Cell (a unit of compute) is OPEN or ISOLATED (roadmap step 10.6a, ADR-0035; codingrules
Appendix C, "Cell isolation"). OPEN -> ISOLATED is `cell.isolated`, recorded by the one isolation
path once the Cell's grant is revoked, its bees paused, its `BLOCK` Cell Wax written and its egress
cut: the reason, who ordered it, the Guard report it answers, the trail ids that justified it and
what each step did. ISOLATED -> OPEN is `cell.isolation_lifted`, which only the human's lift
records. The trail itself is where the state lives: the Cell's state is whichever of its two
events is newer (`read_isolation`), so a Queen that restarts finds every Cell as it left it with
no table of her own to reconcile, and the event is the state change, trivially in one transaction
(Appendix C rule 3). What placement reads is the `BLOCK` wax the isolation wrote, not this state.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by the isolation path and the lift. Calls into
    `hivemind.pheromone` (CellEvent, TrailQuery), the sub-package's own order and waggle only;
    `QueenDeps` only for its type.

Key invariants:
    - TRANSITIONS has exactly one entry per IsolationState, each with the one edge out of it.
    - Every payload carries ids, enum values and counts only (codingrules section 12).

See Also:
    - .claude/codingrules.md Appendix C, "Cell isolation" row.
    - hivemind.guard.policy.catalogue: cell.isolated is authorised at `isolation`, the lift at
      `entrance_route`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.pheromone import CellEvent, PheromoneEvent, TrailQuery
from hivemind.queen.isolation.order import IsolationOrder, IsolationOutcome
from waggle.ids import CellId, EventId, new_event_id

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps

ISOLATED_KIND = "cell.isolated"  # OPEN -> ISOLATED.
LIFTED_KIND = "cell.isolation_lifted"  # ISOLATED -> OPEN, the human's alone.

__all__ = [
    "ISOLATED_KIND",
    "LIFTED_KIND",
    "TRANSITIONS",
    "IsolationRecord",
    "IsolationState",
    "can_transition",
    "read_isolation",
    "record_isolated",
    "record_lifted",
]


class IsolationState(Enum):
    """Where one Cell is: open to placements and its own egress, or isolated."""

    OPEN = "open"  # No isolation stands: placements land, its egress is its own policy's.
    ISOLATED = "isolated"  # Cut off: grant revoked, bees paused, BLOCK wax written, egress cut.


# The single transition table (codingrules section 9), each edge commented with who takes it.
TRANSITIONS: Mapping[IsolationState, frozenset[IsolationState]] = MappingProxyType(
    {
        IsolationState.OPEN: frozenset({IsolationState.ISOLATED}),  # the Queen, or the human
        IsolationState.ISOLATED: frozenset({IsolationState.OPEN}),  # the human, with step-up
    }
)


@dataclass(frozen=True, slots=True)
class IsolationRecord:
    """A Cell's isolation state as the trail has it, and the `cell.isolated` event behind it.

    Attributes:
        state: OPEN or ISOLATED.
        isolated: The newest `cell.isolated` event for the Cell (the one standing when ISOLATED,
            the one lifted last when OPEN); None for a Cell never isolated.
    """

    state: IsolationState
    isolated: PheromoneEvent | None


def can_transition(from_state: IsolationState, to_state: IsolationState) -> bool:
    """Return whether the table allows moving a Cell from `from_state` to `to_state`.

    Args:
        from_state: The Cell's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True for one of the table's edges.
    """
    return to_state in TRANSITIONS[from_state]


async def read_isolation(deps: QueenDeps, cell_id: CellId) -> IsolationRecord:
    """Return `cell_id`'s isolation state: whichever of its two events is newer.

    Args:
        deps: The Queen's collaborators; `trail` is read.
        cell_id: The Cell.

    Returns:
        ISOLATED with its standing `cell.isolated`, or OPEN.
    """
    isolated = await _newest(deps, ISOLATED_KIND, cell_id)
    lifted = await _newest(deps, LIFTED_KIND, cell_id)
    # Event ids are ULIDs minted on the Queen's own clock, so (at, id) orders her two edges.
    if isolated is None or (
        lifted is not None and (lifted.at, lifted.id) > (isolated.at, isolated.id)
    ):
        return IsolationRecord(state=IsolationState.OPEN, isolated=isolated)
    return IsolationRecord(state=IsolationState.ISOLATED, isolated=isolated)


async def record_isolated(
    deps: QueenDeps, order: IsolationOrder, outcome: IsolationOutcome
) -> EventId:
    """Record `cell.isolated` for `order`, with what each step of `outcome` did.

    Args:
        deps: The Queen's collaborators.
        order: The isolation carried out.
        outcome: Its steps' results so far (the event id itself is minted here).

    Returns:
        The event's id: every `memory.tainted` row of this isolation names it as its cause.
    """
    payload: dict[str, JsonValue] = {
        "reason": order.reason,
        "ordered_by": order.ordered_by.value,
        "report_id": order.report_id,
        "evidence": list(order.evidence),
        "decision_event_id": order.decision_event_id,
        "device_id": order.device_id,
        "wax_id": outcome.wax_id,
        "revoked_grant_ids": list(outcome.revoked_grant_ids),
        "paused_task_ids": list(outcome.paused_task_ids),
        "unacknowledged_task_ids": list(outcome.unacknowledged_task_ids),
        "egress": outcome.egress.value,
    }
    return await _record(deps, ISOLATED_KIND, order.cell_id, payload)


async def record_lifted(deps: QueenDeps, cell_id: CellId, payload: dict[str, JsonValue]) -> EventId:
    """Record `cell.isolation_lifted` for `cell_id` with the lift's facts (ids and counts).

    Args:
        deps: The Queen's collaborators.
        cell_id: The Cell the human lifted.
        payload: The lift's facts: the isolation it ended, the wax cleared, the egress restored.

    Returns:
        The event's id.
    """
    return await _record(deps, LIFTED_KIND, cell_id, payload)


async def _newest(deps: QueenDeps, kind: str, cell_id: CellId) -> PheromoneEvent | None:
    """Return the newest `kind` event about `cell_id`, or None."""
    query = TrailQuery(kind=kind, subject_id=cell_id, newest_first=True, limit=1)
    events = await deps.trail.query(query)
    return events[0] if events else None


async def _record(
    deps: QueenDeps, kind: str, cell_id: CellId, payload: dict[str, JsonValue]
) -> EventId:
    """Build and record one `cell.*` event about `cell_id`, stamped with the Queen's identity."""
    event = CellEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=cell_id,
        payload=payload,
    )
    await deps.trail.record(event)
    return event.id
