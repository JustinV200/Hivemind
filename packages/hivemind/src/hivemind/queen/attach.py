"""Define detach_warden: remove one attached Warden's own bookkeeping from a Queen.

`hivemind.queen.queen.Queen.attach_warden` records an already-built `WardenLink`; this is its own
mirror, roadmap step 5.6's own new edge -- `hivemind.queen.cell_gate.CellListener` calls it once a
Virtual Cell's accepted connection ends, so a dead link is never drained again on the next tick. A
free function taking `queen: Queen` rather than a new method on `Queen` itself (unlike
`attach_warden`): `hivemind.queen.queen`'s own file is already at codingrules 5.1's 300-line file
cap, and this function's real body -- reaping the Warden's in-flight receive task, not just
popping four dicts -- is too much to inline there without pushing it over; `queen.py`'s own
`_stop_queen`/`_send_intervene` already live as module-level delegates for the same class-size
reason, this is one file further out for the same file-size reason. `CellListener` (and any test)
calls `hivemind.queen.attach.detach_warden(queen, warden_id)` directly.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.cell_gate.CellListener` on every accepted connection's own close. Calls
    into `hivemind.common.tasks` (reap) only; reaches into `Queen`'s own private attributes
    directly, the same cross-file access `hivemind.wardens.ticks.control` already takes on
    `Warden`'s private state for the identical reason (module docstring there).

Key invariants:
    - Never raises for a `warden_id` that is not currently attached: a caller may detach
      defensively (e.g. a connection that never finished the readiness handshake).
    - A receive task still in flight for the detached Warden is always reaped, never left
      cancelled-but-unawaited (codingrules section 11).

See Also:
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
    - hivemind.queen.cell_gate for CellListener, this function's one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.tasks import reap
from waggle.ids import WardenId

if TYPE_CHECKING:
    from hivemind.queen.queen import Queen

__all__ = ["detach_warden"]


async def detach_warden(queen: Queen, warden_id: WardenId) -> None:
    """Remove `warden_id`'s own bookkeeping from `queen` and reap its in-flight receive task.

    Does not close the transport itself (the caller already did, or is about to) and does not
    touch the Brood Chamber or any Cell record; a later phase's Undertaker sweep reconciles those.

    Args:
        queen: The Queen to detach `warden_id` from.
        warden_id: The Warden to detach.
    """
    for table in (queen._wardens, queen._link_iters, queen._liveness, queen._last_heartbeat):
        table.pop(warden_id, None)
    receive_task = queen._receive_tasks.pop(warden_id, None)
    if receive_task is not None:
        await reap(receive_task)
