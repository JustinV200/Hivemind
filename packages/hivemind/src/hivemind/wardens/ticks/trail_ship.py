"""Ship a Warden's own trail segment to the Queen before it reports a task's outcome.

The Queen acts on a `TaskResult` at once: `hivemind.queen.cell_gate.release.make_on_task_finished`
releases the Cell and pauses (overwinters) or tears it down in the same tick. A Virtual Cell's own
trail otherwise ships on its Warden's heartbeat cadence (`hivemind.wardens.warden`'s own
`_sync_trail`) and once more at `Warden.stop`, and a pause freezes the Warden between the two: the
second real Docker run (2026-09-23) overwintered a Cell whose `worker.done` and every `capping.*`
row were still inside it, and they were lost for good when that Cell was later destroyed.
Shipping right before the `TaskResult`, over the same ordered link, means the Queen holds the
task's own events before it can pause or destroy the Cell that recorded them.

Best-effort, like every other sync (codingrules section 12: the merge is idempotent by event id).
`TrailSync.sync` never raises (phase-7 handoff open item 8: it reports a closed or dropped link
through its own return value); a closed link fails the `TaskResult` send that follows the same
way, and the heartbeat sync is the one that records `warden.offline` for it, so this ignores the
return value on purpose rather than adding a second record.

Fits into the Hive:
    Layer 5 (Wardens). Called by `hivemind.wardens.ticks.results` and `hivemind.wardens.ticks.
    alarms` immediately before each `TaskResult` they send the Queen, and by `hivemind.wardens.
    ticks.heartbeat.record_progress` when a bee reports it paused (roadmap step 10.6a: the Queen
    isolating this Cell waits a bounded time for that `worker.paused`). Reads
    `WardenDeps.trail_sync` only; a Warden without one (the Hive Stand's own, whose trail already
    is the Queen's) is a no-op.

Key invariants:
    - Never raises for a closed or lost link: `TrailSync.sync` reports that through its own
      return value, which this ignores on purpose (module docstring).
    - Never records an event of its own.

See Also:
    - hivemind.wardens.trail_sync for what one sync exports and sends.
    - hivemind.queen.cell_gate.release for what the Queen does the moment the result lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["ship_trail_before_result"]


async def ship_trail_before_result(warden: Warden) -> None:
    """Ship `warden`'s own trail segment now, so it lands before the TaskResult sent next.

    Args:
        warden: The owning Warden; only its `_deps.trail_sync` is read.
    """
    if warden._deps.trail_sync is None:
        return  # The Hive Stand's own Warden: its trail is already the Queen's own store.
    # A closed or dropped link fails the TaskResult send that follows the same way, and that is
    # the one that records warden.offline for it (module docstring); the bool is not this
    # function's to react to.
    await warden._deps.trail_sync.sync()
