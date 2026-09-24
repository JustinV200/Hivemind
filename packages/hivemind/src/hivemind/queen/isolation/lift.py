"""Lift a Cell's isolation, or the Queen's placement holds on it: the human's lever alone.

ADR-0035: "lifting an isolation is the human's, with step-up; the Queen never lifts on her own."
The human's lift at the Hive Entrance (`POST /v1/cells/{cell_id}/lift`, an interactive device
inside its step-up window) reaches the Queen's isolation door and ends here. It releases every
placement hold the Queen left on the Cell (the Hive Stand's fallback, where she may not isolate),
clears the BLOCK Cell Wax the isolation wrote, restores a Virtual Cell's egress, and records
`cell.isolation_lifted`, naming the `cell.isolated` event it ends (none, when only holds stood).
What a lift deliberately leaves alone: the Cell's tainted memory stays tainted until a judge
clears it (`hivemind.memory.taint.clear_taint`), and every task the isolation paused stays PAUSED
until that clearing resumes it from its checkpoint (`hivemind.queen.quarantine.release`) or the
human cancels it. A lift restores placement and egress, never trust in what the Cell wrote.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.door` only. Calls into
    `hivemind.common.logging`, `hivemind.hive` (EgressOutcome), `hivemind.queen.errors` and this
    sub-package's own modules only.

Key invariants:
    - Nothing but the human's door calls `lift_isolation` (the Queen's tick never does).
    - A lift with no isolation and no hold standing changes nothing and raises.

See Also:
    - hivemind.queen.isolation.path for what a lift undoes.
    - hivemind.entrance.routes.isolation for the human's route, and its step-up.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.hive import EgressOutcome
from hivemind.queen.errors import CellNotIsolatedError
from hivemind.queen.isolation.access import unblock_cell
from hivemind.queen.isolation.order import LiftOutcome
from hivemind.queen.isolation.record import IsolationState, read_isolation, record_lifted
from hivemind.queen.isolation.site import IsolationSite
from waggle.ids import CellId, DeviceId

log = get_logger(__name__)

__all__ = ["lift_isolation"]


async def lift_isolation(
    site: IsolationSite, cell_id: CellId, device_id: DeviceId | None
) -> LiftOutcome:
    """Lift `cell_id`'s isolation and the Queen's holds on it, for the human.

    Tainted memory stays tainted: only a judge's clearing (`clear_taint`) removes the label, and a
    task the isolation paused resumes only from a checkpoint that clearing let through.

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        cell_id: The Cell the human lifts.
        device_id: The enrolled device the human lifted it from, for the trail.

    Returns:
        What the lift did: the isolation it ended, the wax it cleared, the holds it released.

    Raises:
        CellNotIsolatedError: The Cell is neither isolated nor held; nothing changed.
    """
    deps = site.deps
    standing = await read_isolation(deps, cell_id)
    isolated = standing.isolated if standing.state is IsolationState.ISOLATED else None
    released = await deps.guard.requests.release_holds(cell_id, deps.clock.now())
    if isolated is None and not released:
        raise CellNotIsolatedError(cell_id)
    cleared: str | None = None
    egress = EgressOutcome.UNTRACKED
    if isolated is not None:
        # Undo exactly what the isolation did: the wax `cell.isolated` names, the egress it cut.
        reason = f"Lifted by the human from device {device_id}."
        wax_id = isolated.payload.get("wax_id")
        cleared = await unblock_cell(deps, cell_id, str(wax_id) if wax_id else None, reason)
        egress = await _restore_egress(site, cell_id)
    payload: dict[str, JsonValue] = {
        "isolated_event_id": isolated.id if isolated else None,
        "device_id": device_id,
        "wax_cleared": cleared,
        "egress": egress.value,
        "released_holds": len(released),
    }
    event_id = await record_lifted(deps, cell_id, payload)
    log.info("queen.cell_lifted", cell_id=cell_id, event_id=event_id, holds=len(released))
    return LiftOutcome(
        cell_id=cell_id,
        event_id=event_id,
        isolated_event_id=isolated.id if isolated else None,
        wax_cleared=cleared,
        egress=egress,
        released_holds=len(released),
    )


async def _restore_egress(site: IsolationSite, cell_id: CellId) -> EgressOutcome:
    """Give the Cell its own network policy back, when the Hive has a backend tracking it."""
    egress = site.deps.guard.egress
    if egress is None:
        return EgressOutcome.UNTRACKED  # Nothing was cut: no Virtual side in this Hive.
    return await egress.restore(cell_id)
