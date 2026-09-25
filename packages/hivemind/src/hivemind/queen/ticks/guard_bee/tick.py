"""Define run_guard_bee: one Guard Bee round on the Queen's own tick, when she has a Guard Bee.

The Guard Bee (the Hive's security watcher, roadmap step 10.6, ADR-0043) runs in the Queen's
process on the Hive Stand, driven by her tick exactly as the House Bee's sweep is
(`hivemind.queen.ticks.housekeeping`, which calls this beside it), so no Cell action can take it
down and it always reads the central trail. `run_guard_bee` asks the Guard Bee on
`QueenDeps.guard_bee` for one round (the Guard Bee itself decides whether its interval is due) and
contains a failed round: a `GuardBeeError` is logged and the Queen's tick carries on, since a
watcher that trips over a malformed row must never stop the Queen it watches for. The Guard Bee's
awake episodes run in its own lane beside the tick, so a round never waits on a model.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called once per tick by `hivemind.queen.ticks.housekeeping.run_housekeeping`.
    Calls into `hivemind.common.logging`, `hivemind.queen.deps` (QueenDeps, under TYPE_CHECKING)
    and `hivemind.workers` (GuardBeeError) only.

Key invariants:
    - With no Guard Bee on `QueenDeps` (every composition root before one is wired) this does
      nothing at all.
    - Nothing but a `GuardBeeError` is caught here: cancellation and every other error keep their
      meaning for the Queen's own loop.

See Also:
    - hivemind.workers.roles.guard_bee for the Guard Bee and build_guard_bee.
    - hivemind.queen.ticks.housekeeping for the House Bee's sweep, run on the same tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.workers import GuardBeeError

if TYPE_CHECKING:
    # Only for the type hint below, as every hivemind.queen.ticks module keeps its QueenDeps import.
    from hivemind.queen.deps import QueenDeps

log = get_logger(__name__)

__all__ = ["run_guard_bee"]


async def run_guard_bee(deps: QueenDeps) -> None:
    """Run one Guard Bee round when the Queen has a Guard Bee; log a failed one and carry on.

    Args:
        deps: The Queen's collaborators; `guard_bee` is this call's one input.
    """
    guard_bee = deps.guard_bee
    if guard_bee is None:
        return  # No Guard Bee wired into this Queen: nothing watches the trail here.
    try:
        # Latency: local trail reads and writes only; its model calls run in its own lane.
        await guard_bee.tick()
    except GuardBeeError as exc:
        # The next due round tries again; the Queen's own tick must not end over it.
        log.error("queen.guard_bee_round_failed", error=str(exc))
