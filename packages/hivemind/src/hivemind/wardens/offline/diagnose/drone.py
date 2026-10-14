"""Send a diagnostic Drone from a cut-off Warden's own pool, read how it ended, and retire it.

The three things a search does to a Drone (roadmap 11.9a, ADR-0056). `start_drone` finds the local
model to run it on (`decide.local_binding`, over what this Warden's servers are serving now), takes
one of the Warden's sub-bee slots, makes its task and grant (`assign`) and starts it through
`spawn_local`, which binds it through the Guard like any sub-bee; it says why when it did not:
the Warden has no model of its own, no lease on its Cell or no ceilings from the Queen, or the Cell
is already running as many sub-bees as its ceilings allow. `has_ended` and `collect` read the row:
a Drone ends by claiming a result, by raising an Alarm, or by being retired by the Warden's other
paths (a handoff, a quarantine), and `collect` turns whichever it was into a finding. `stop_drone`
retires one that has run out of time. The row is the Warden's own bookkeeping, so the retire is the
one every sub-bee takes (`retire_sub_bee`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Called by `.step`. Calls into `hivemind.wardens.spawn` (the
    local spawn, the binding check), `hivemind.wardens.ticks.alarms` (the Cell context and
    `retire_sub_bee`), `.assign`, `.decide`, `.state` and waggle only.

Key invariants:
    - A Drone takes no slot of the grant the Warden's tasks run under, so it never makes one of
      them wait; it is bounded by the Cell's ceilings instead (`Ceilings.max_sub_bees`, the
      Queen's cap on the machine), and is not started while the Cell is at that cap.
    - A Drone is retired exactly once, by `retire_sub_bee`, whichever way it ends.
    - A Drone is bound to a model that is served from this machine right now, and its whole
      fallback chain is local, so a cut-off Warden never reaches for a hosted one.
    - What a Drone wrote is never put on the trail; only its finding's cause is read from it.

See Also:
    - hivemind.wardens.spawn.local for the spawn this wraps.
    - hivemind.wardens.offline.diagnose.step for the tick that decides when to call these.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.offline.diagnose.assign import LocalPlan, local_assignment
from hivemind.wardens.offline.diagnose.decide import Finding, local_binding, read_finding
from hivemind.wardens.offline.diagnose.state import Investigation
from hivemind.wardens.offline.outage import RemoteDeps
from hivemind.wardens.spawn import spawn_local
from hivemind.wardens.spawn.binding import binding_is_local
from hivemind.wardens.ticks.alarms import cell_context, retire_sub_bee

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = ["Start", "collect", "has_ended", "start_drone", "stop_drone"]


class Start(Enum):
    """Whether a Drone could be sent, and if not, whether it ever could be."""

    STARTED = "STARTED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"  # No model of its own, no lease, or the Guard refused it.
    NO_SLOT = "NO_SLOT"  # The Cell is at its ceiling of sub-bees: ask again next tick.


async def start_drone(warden: Warden, investigation: Investigation, remote: RemoteDeps) -> Start:
    """Send one Drone on the local model, if the Warden has one and a slot for it.

    Args:
        warden: The cut-off Warden (read and written directly; see the ticks package docstring).
        investigation: The search the Drone belongs to; updated with the Drone sent.
        remote: The Warden's link facts: the Queen's host for the Drone's objective, and the budget
            whose wall-clock bounds the Drone's grant.

    Returns:
        STARTED, with `investigation.run` and `.worker_id` set and `.attempts` counted;
        otherwise why not.
    """
    ctx, budget, ceilings = cell_context(warden), remote.reconnect_budget, warden._ceilings
    deps = warden._deps
    loaded = tuple(s for server in warden.hosted_servers.values() for s in server.sources)
    binding = local_binding(deps.bindings, loaded, lambda key: binding_is_local(key, deps))
    if ctx is None or binding is None or budget is None or ceilings is None:
        return Start.NOT_ELIGIBLE
    if len(warden._sub_bees) >= ceilings.max_sub_bees:
        return Start.NO_SLOT
    left = budget.wall_clock_s - (deps.clock.now() - investigation.cut_since).total_seconds()
    plan = LocalPlan(
        warden_id=warden._warden_id,
        cell=ctx.cell,
        binding=binding,
        peer=remote.peer,
        earlier=tuple(investigation.findings),
        valid_for_s=max(left, 1.0),
        attempt=investigation.attempts + 1,
    )
    try:
        sub_bee = await spawn_local(ctx, local_assignment(plan, deps.clock))
    except BindingRefusedError:
        return Start.NOT_ELIGIBLE  # Nothing started; the Guard already recorded why.
    warden._sub_bees[sub_bee.worker_id] = sub_bee
    warden._sub_bee_iters[sub_bee.worker_id] = sub_bee.link.receive()
    investigation.run, investigation.worker_id = sub_bee.local, sub_bee.worker_id
    investigation.attempts += 1
    return Start.STARTED


def has_ended(warden: Warden, investigation: Investigation) -> bool:
    """Whether the Drone in flight has said how it ended, or is no longer a row of the Warden.

    Args:
        warden: The Warden whose sub-bee table is read.
        investigation: The search whose Drone is asked after.

    Returns:
        True when it claimed, raised an Alarm, or was retired by another path.
    """
    run = investigation.run
    if run is None:
        return False
    return run.has_ended or _row(warden, investigation) is None


async def collect(warden: Warden, investigation: Investigation) -> Finding:
    """Read how the ended Drone ended, retire its row if it still has one, and clear the search.

    Args:
        warden: The Warden whose sub-bee table is read and written.
        investigation: The search whose Drone has ended (`has_ended`).

    Returns:
        What it reported: the finding of its claim, or none when it raised an Alarm or was retired
        without saying anything.
    """
    run = investigation.run
    finding = Finding(None, "")
    if run is not None and run.claim is not None:
        finding = read_finding(run.claim.summary)
    elif run is not None and run.alarm is not None:
        # The kind only: an Alarm's detail is text the bee or a tool wrote (codingrules 12).
        finding = Finding(None, f"The Drone stopped with the Alarm {run.alarm.kind.value}.")
    await stop_drone(warden, investigation)
    return finding


async def stop_drone(warden: Warden, investigation: Investigation) -> None:
    """Retire the Drone in flight, if its row is still the Warden's, and forget it.

    Args:
        warden: The Warden whose sub-bee table is read and written.
        investigation: The search whose Drone is retired.
    """
    row = _row(warden, investigation)
    if row is not None:
        await retire_sub_bee(warden, row)  # The one retire every sub-bee takes.
    investigation.run, investigation.worker_id = None, None


def _row(warden: Warden, investigation: Investigation) -> SubBee | None:
    """The Drone in flight's row in the Warden's table, or None when it has left it."""
    if investigation.worker_id is None:
        return None
    return warden._sub_bees.get(investigation.worker_id)
