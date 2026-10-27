"""Ship a Warden's own trail segment to the Queen before it reports a task's outcome.

`report_refused` is the one such report made here: a task the Guard refused what it needed (a
binding, roadmap step 10.3) is reported FAILED with the Guard's reason, its trail shipped first.

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
    alarms` immediately before each `TaskResult` they send the Queen, by `hivemind.wardens.
    ticks.heartbeat.record_progress` when a bee reports it paused (roadmap step 10.6a: the Queen
    isolating this Cell waits a bounded time for that `worker.paused`), and by `hivemind.wardens.
    ticks.control.handle_release_lease` before the `LeaseReleased` the Queen pauses an
    Overwintered Cell on (phase 5 open item 8); `report_refused` by `.assign` and `.alarms`.
    Reads `WardenDeps.trail_sync` (a Warden without one, the Hive Stand's own, whose trail
    already is the Queen's, ships nothing) and sends on `WardenDeps.queen_link` only.

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

from hivemind.wardens.links import send_guarded
from waggle.envelope import wrap
from waggle.messages.base import MAX_REASON_CHARS as WIRE_REASON_CHARS
from waggle.messages.task import TaskAssign, TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

MAX_REASON_CHARS = 2_000  # Mirrors waggle.messages.task.reports.MAX_SUMMARY_CHARS's own bound.

__all__ = ["MAX_REASON_CHARS", "report_refused", "ship_trail_before_result"]


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


async def report_refused(warden: Warden, assignment: TaskAssign, reason: str) -> None:
    """Report `assignment`'s task FAILED to the Queen because the Guard refused what it needed.

    A refused binding (roadmap step 10.3) cannot run the task on this Warden at all; reporting it
    FAILED with the Guard's own reason lets the Queen retry, fail or escalate it, rather than
    leaving it RUNNING with no sub-bee.

    Args:
        warden: The owning Warden.
        assignment: The task's TaskAssign.
        reason: The Guard's reason sentence (already `guard.denied` on the trail).
    """
    result = TaskResult(
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        outcome=TaskOutcome.FAILED,
        summary=reason[:MAX_REASON_CHARS],
        clearance=assignment.clearance,
        artifacts=(),
        checked_by=warden._warden_id,
        handoff=assignment.resume_from,
        spend=0.0,
        reason=reason[:WIRE_REASON_CHARS],
    )
    # The same ordering every result keeps: this Cell's trail rows (the refusal) ship first.
    await ship_trail_before_result(warden)
    message = wrap(result, warden._deps.hop, clock=warden._deps.clock)
    await send_guarded(warden._deps.queen_link, message)  # A gone link is logged, not raised.
