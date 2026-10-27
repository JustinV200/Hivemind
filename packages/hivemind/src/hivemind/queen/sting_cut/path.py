"""Sting Cut one Cell: the one code path every cut takes, the Queen's and the human's alike.

Roadmap step 13.4a: the emergency disconnect of one Cell. The human's lever at the Hive Entrance
(after a step-up and the typed phrase) and the Queen's decision on a Guard request both end here,
in this order:

1. The Cell must be reachable: its Warden attached, or a Virtual Cell the Hive still tracks. A
   Cell already cut is answered as such, changing nothing (a cut is idempotent).
2. The `sting_cut` enforcement point (`authority`): the Queen never cuts the Hive Stand's own
   lease, and never past her hourly cap; a refusal is `guard.denied` and changes nothing else.
3. An isolation first (`steps.isolation_first`): the Queen cuts only an isolated Cell; the
   human's cut isolates one that is not.
4. Evidence before scrubbing: a snapshot of a Virtual Cell. The Queen never scrubs what she could
   not keep (a Real Cell, whose scratch would go to tainted Nectar, not built yet; a backend that
   cannot snapshot): she leaves the Cell isolated and the human decides. A Night Veil Cell keeps
   nothing by design (codingrules 12).
5. Every grant on the Cell revoked; its Warden releases the lease (its processes killed, its
   scratch removed, its session closed); a Virtual Cell's key is forgotten and its link closed.
6. `cell.sting_cut` recorded with the reason, who cut, the report, the evidence and each step's
   result; central audit state is left intact. A Night Veil Cell is then torn down, which purges
   its segment and every record its tier retained.
7. The human is told by a CRITICAL SECURITY Alarm, pushed to every device.

A Virtual Cell that is not Night Veil is left standing, cut off, with its snapshot: the evidence
lives as long as it does, and the human destroys it (`hive cells destroy`) once done with it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Called by the Queen's Guard request decision and her Sting Cut door.
    Calls into `hivemind.common.logging`, `hivemind.hive.night_veil`, `hivemind.queen.errors`,
    `hivemind.queen.guard_requests` (SecurityAlert), `hivemind.queen.isolation`,
    `hivemind.supervision` and this sub-package's own modules only.

Key invariants:
    - Every cut of every caller goes through `sting_cut_cell`.
    - Nothing is scrubbed before the point allowed it and, for the Queen, before evidence is kept.
    - `cell.sting_cut` is recorded before a Night Veil Cell's teardown purges its segment.

See Also:
    - .claude/roadmap.md step 13.4a; docs/guard/sting-cut.md for the operator's view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.hive.night_veil import is_night_veil_cell
from hivemind.queen.errors import UnknownCellError
from hivemind.queen.guard_requests import SecurityAlert
from hivemind.queen.isolation import IsolationSite, alert_human, is_hive_stand
from hivemind.queen.sting_cut.authority import authorize_sting_cut
from hivemind.queen.sting_cut.order import (
    Cutter,
    StingCutOrder,
    StingCutOutcome,
    StingCutRefusal,
)
from hivemind.queen.sting_cut.record import read_cut, record_cut
from hivemind.queen.sting_cut.steps import forget_key, isolation_first, revoke_grants, scrub
from hivemind.supervision import AlarmSeverity
from waggle.ids import EventId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import WardenLink

log = get_logger(__name__)

__all__ = ["sting_cut_cell"]


async def sting_cut_cell(site: IsolationSite, order: StingCutOrder) -> StingCutOutcome:
    """Carry out `order`: the one Sting Cut path (module docstring, steps 1-7).

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        order: The Cell, who ordered it, why, and on what evidence.

    Returns:
        The outcome: cut (with every step's result), already cut, or refused.

    Raises:
        UnknownCellError: No attached Warden runs the Cell and the Virtual side does not track it.
    """
    deps, cut = site.deps, site.deps.guard.cut
    link = site.link_for(order.cell_id)
    if link is None and (cut is None or not cut.tracks(order.cell_id)):
        raise UnknownCellError(order.cell_id)
    if await read_cut(deps, order.cell_id) is not None:
        return StingCutOutcome(cell_id=order.cell_id, already_cut=True)
    ready, night_veil = await _ready(site, order, link)
    if ready.refusal is not None:
        return ready
    return await _cut_off(site, order, link, ready, night_veil)


async def _ready(
    site: IsolationSite, order: StingCutOrder, link: WardenLink | None
) -> tuple[StingCutOutcome, bool]:
    """Steps 2-4: the point, the isolation, the evidence; a refusal, or what the cut follows."""
    deps, cut = site.deps, site.deps.guard.cut
    cell = link.cell if link is not None else None
    hive_stand = cell is not None and is_hive_stand(cell)
    refusal = await authorize_sting_cut(deps, order, cell, hive_stand)
    if refusal is not None:
        return StingCutOutcome(cell_id=order.cell_id, refusal=refusal), False
    may_go_on, isolated = await isolation_first(site, order)
    if not may_go_on:
        return StingCutOutcome(cell_id=order.cell_id, refusal=StingCutRefusal.NOT_ISOLATED), False
    night_veil = await is_night_veil_cell(deps.trail, order.cell_id)
    snapshot = None if night_veil or cut is None else await cut.keep_evidence(order.cell_id)
    # Roadmap 13.4a: her cut preserves evidence before scrubbing; without it she stops here.
    kept = snapshot is not None or night_veil or order.cut_by is Cutter.HUMAN
    ready = StingCutOutcome(
        cell_id=order.cell_id,
        refusal=None if kept else StingCutRefusal.NO_EVIDENCE,
        isolated_event_id=isolated,
        snapshot_id=snapshot,
    )
    return ready, night_veil


async def _cut_off(
    site: IsolationSite,
    order: StingCutOrder,
    link: WardenLink | None,
    ready: StingCutOutcome,
    night_veil: bool,
) -> StingCutOutcome:
    """Steps 5-7: revoke, scrub, forget, record, tear a Night Veil Cell down, tell the human."""
    deps, cut = site.deps, site.deps.guard.cut
    outcome = ready.model_copy(
        update={
            "revoked_grant_ids": await revoke_grants(deps, link, order.cell_id, order.reason),
            "scrub": await scrub(deps, cut, link, order.reason),
            "key_forgotten": await forget_key(cut, link, order.cell_id),
        }
    )
    event_id = await record_cut(deps, order, outcome)
    # After the record: the teardown purges the segment the record went to, leaving the skeleton.
    torn_down = night_veil and cut is not None and await cut.teardown(order.cell_id)
    outcome = outcome.model_copy(update={"event_id": event_id, "torn_down": torn_down})
    await _tell_human(site, order, outcome, event_id)
    log.info(
        "queen.cell_sting_cut",
        cell_id=order.cell_id,
        event_id=event_id,
        cut_by=order.cut_by.value,
        scrub=outcome.scrub.value,
        key_forgotten=outcome.key_forgotten,
        torn_down=torn_down,
    )
    return outcome


async def _tell_human(
    site: IsolationSite, order: StingCutOrder, outcome: StingCutOutcome, event_id: EventId
) -> None:
    """Raise the CRITICAL SECURITY Alarm for a cut: who cut, what was kept, what to do next."""
    answering = f" on report {order.report_id}" if order.report_id else ""
    kept = (
        f"Snapshot {outcome.snapshot_id} kept; hive cells destroy it when done."
        if outcome.snapshot_id
        else "No snapshot kept."
    )
    torn = " Torn down." if outcome.torn_down else ""
    # Ids and enum words only, inside the Alarm's 300-character detail.
    detail = (
        f"Cell {order.cell_id} Sting Cut by the {order.cut_by.value}{answering}: "
        f"{len(outcome.revoked_grant_ids)} grant(s) revoked, lease release "
        f"{outcome.scrub.value}, key {'forgotten' if outcome.key_forgotten else 'kept'}.{torn} "
        f"{kept}"
    )
    # No report id: the isolation before it already showed that report's one Alarm.
    alert = SecurityAlert(
        severity=AlarmSeverity.CRITICAL, detail=detail, cell_id=order.cell_id, event_id=event_id
    )
    await alert_human(site, alert)
