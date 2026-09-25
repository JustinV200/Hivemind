"""Isolate one Cell: the one code path every isolation takes, the Queen's and the human's alike.

ADR-0043, "Only the Queen isolates a Cell" (roadmap step 10.6a). A Guard Bee only requests; a
Warden's policy never loads an ISOLATE row; the Queen's decision on a Guard request, her own
escalation policy and the human's lever at the Hive Entrance all end here, in this order:

1. The Cell's own Warden must be attached (the Queen isolates through it), and an isolation that
   already stands is answered as such, changing nothing.
2. The `isolation` enforcement point (`authority`): the Queen may never isolate the Hive Stand's
   own lease, only the human may; a refusal is `guard.denied` and changes nothing else.
3. A BLOCK Cell Wax note, first, so placement sends nothing more there while the rest runs (none
   for a Night Veil Cell, which placement never offers to another task: codingrules 12).
4. The Warden's grants are revoked, so it spawns nothing more.
5. Every bee on the Cell is checkpointed and paused, with a bounded wait for its answer.
6. A Virtual Cell's egress is cut to its Waggle link alone (`hivemind.hive.CellEgress`); a
   backend that cannot says so, and a Real Cell is left exactly as found (never reconfigured).
7. `cell.isolated` is recorded with the reason, the report, the evidence and every step's result.
8. The Cell's memory is tainted from the first evidence on, caused by that event: the Hive's own
   tables here, and the store a Virtual Cell's Warden keeps inside the Cell by its order
   (`CellTaintOrder`, which the Warden carries out with the same setter).
9. The human is told by a CRITICAL SECURITY Alarm, pushed to every device.

The lease and its scratch are kept intact for forensics: nothing here releases, tears down or
overwinters the Cell. Only the human lifts an isolation (`hivemind.queen.isolation.lift`). For a
Night Veil Cell every record of all this lives in the Cell's own segment, which the isolation's
reads reach (`hivemind.pheromone.query_cell`), and goes with it at teardown.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by the Queen's Guard request decision, her Alarm handling
    (`hivemind.queen.ticks.alarms`, an ISOLATE_CELL row) and her isolation door (the human's).
    Calls into `hivemind.common.logging`, `hivemind.hive` (EgressOutcome), `hivemind.queen.errors`,
    `hivemind.supervision` (AlarmSeverity) and this sub-package's own modules only.

Key invariants:
    - Every isolation of every caller goes through `isolate_cell`; nothing composes the steps by
      hand elsewhere.
    - Nothing is written, revoked, paused, cut or tainted unless the point allowed it.
    - `cell.isolated` is recorded before any memory is tainted, and names every step's result.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/isolation.md for the operator's view of an isolated Cell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.hive import EgressOutcome
from hivemind.queen.errors import UnknownCellError
from hivemind.queen.guard_requests import SecurityAlert
from hivemind.queen.isolation.access import block_cell, revoke_cell_grants
from hivemind.queen.isolation.authority import authorize_isolation
from hivemind.queen.isolation.order import IsolationOrder, IsolationOutcome
from hivemind.queen.isolation.pause import pause_cell_bees
from hivemind.queen.isolation.record import IsolationState, read_isolation, record_isolated
from hivemind.queen.isolation.site import IsolationSite, alert_human
from hivemind.queen.isolation.taint import taint_cell, taint_reason
from hivemind.supervision import AlarmSeverity
from waggle.ids import CellId, timestamp_of

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import WardenLink

log = get_logger(__name__)

__all__ = ["isolate_cell"]


async def isolate_cell(site: IsolationSite, order: IsolationOrder) -> IsolationOutcome:
    """Carry out `order`: the one isolation path (module docstring, steps 1-9).

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        order: The Cell, who ordered it, why, and on what evidence.

    Returns:
        The outcome: isolated (with every step's result), already isolated, or refused.

    Raises:
        UnknownCellError: No attached Warden runs the Cell.
    """
    deps = site.deps
    link = site.link_for(order.cell_id)
    if link is None:
        raise UnknownCellError(order.cell_id)
    standing = await read_isolation(deps, order.cell_id)
    if standing.state is IsolationState.ISOLATED:
        return IsolationOutcome(cell_id=order.cell_id, already_isolated=True)
    refusal = await authorize_isolation(deps, link, order)
    if refusal is not None:
        return IsolationOutcome(cell_id=order.cell_id, refusal=refusal)
    # Suspect from the first evidence on, or, citing none, from now: the pause's checkpoints too.
    suspect_at = timestamp_of(order.evidence[0]) if order.evidence else deps.clock.now()
    outcome = await _cut_off(site, link, order)
    outcome = outcome.model_copy(update={"suspect_at": suspect_at})
    # The state change is the event (Appendix C); the taint names it as its cause, so it is first.
    event_id = await record_isolated(deps, order, outcome)
    reason = taint_reason(order.cell_id, order.ordered_by.value, order.report_id)
    tainted = await taint_cell(deps, link, reason, event_id, suspect_at)
    alert = SecurityAlert(
        severity=AlarmSeverity.CRITICAL,
        detail=_detail(order),
        cell_id=order.cell_id,
        event_id=event_id,
        report_id=order.report_id,  # One Alarm per report: its first showing is this one.
    )
    await alert_human(site, alert)
    log.info(
        "queen.cell_isolated",
        cell_id=order.cell_id,
        event_id=event_id,
        ordered_by=order.ordered_by.value,
        report_id=order.report_id,
        egress=outcome.egress.value,
        paused=len(outcome.paused_task_ids),
        tainted_count=tainted,
    )
    return outcome.model_copy(update={"event_id": event_id, "tainted_count": tainted})


async def _cut_off(
    site: IsolationSite, link: WardenLink, order: IsolationOrder
) -> IsolationOutcome:
    """Steps 3-6: block placement, revoke the grants, pause the bees, cut the egress."""
    deps = site.deps
    # Placement first: the wait below is the one span where a new task could otherwise land.
    wax_id = await block_cell(deps, order)
    revoked = await revoke_cell_grants(deps, link, order.reason)
    paused = await pause_cell_bees(deps, link, order.reason, deps.guard.pause_timeout_s)
    egress = await _cut_egress(site, order.cell_id)
    return IsolationOutcome(
        cell_id=order.cell_id,
        wax_id=wax_id,
        revoked_grant_ids=revoked,
        paused_task_ids=paused.paused,
        unacknowledged_task_ids=paused.unacknowledged,
        egress=egress,
    )


async def _cut_egress(site: IsolationSite, cell_id: CellId) -> EgressOutcome:
    """Cut the Cell's egress to its Waggle link, when the Hive has a backend that tracks it."""
    egress = site.deps.guard.egress
    if egress is None:
        return EgressOutcome.UNTRACKED  # No Virtual side in this Hive: no Cell of its own to cut.
    return await egress.cut(cell_id)


def _detail(order: IsolationOrder) -> str:
    """The human's Alarm sentence: who isolated which Cell, and on which report (ids only)."""
    answering = f" on Guard report {order.report_id}" if order.report_id else ""
    return (
        f"Cell {order.cell_id} isolated by the {order.ordered_by.value}{answering}: its bees are "
        "paused, its grant revoked, nothing is placed there and its memory is tainted from the "
        "first cited event. Only you can lift it."
    )
