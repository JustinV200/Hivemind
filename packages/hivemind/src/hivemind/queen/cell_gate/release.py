"""Define make_on_task_finished: release a Virtual Cell once its task ends, then overwinter or not.

Roadmap step 5.6/5.9's own missing edge: `hivemind.hive.lifecycle.CellLifecycle.release` must be
told about every task that finishes on a Virtual Cell, so a GRANTED Cell does not sit stuck forever.
The Queen never reads `cell.kind` outside placement (codingrules section 8.7): this module's own
callable does not need to either, because `hivemind.hive.lifecycle.CellLifecycle.status_of` already
answers "does the lifecycle know this Cell at all" -- None for a Real Cell (the lifecycle never
tracked one), a real `VirtualCellStatus` for a Virtual one. `hivemind.queen.ticks.results.
complete_task` calls the callable this factory builds for every finished task's own Cell,
unconditionally, via the additive `QueenDeps.on_task_finished` seam (documented there and on
`QueenDeps` itself): this module is what the composition root actually builds that callable from,
once it also holds a `CellLifecycle` and a `Scrubber`.

`make_on_cell_granted` is the other half of the same seam: `hivemind.queen.dispatcher.ready`
awaits `QueenDeps.on_cell_granted` the moment a task is granted onto a Cell, and the callable built
here walks the lifecycle's own READY -> GRANTED edge for a Cell it tracks, so `cell.granted` is
recorded by the dispatch that caused it and precedes the assignment on the trail.

A FAILED task's own Cell is torn down outright, skipping `hivemind.hive.overwinter.policy.
decide_release` entirely: the roadmap step 5.10 Capping gate that would tell this module whether
the Cell's own state is actually suspect (`ReleaseOutcome.rolled_back_whole_cell`) is not yet
built, so a failed task is the conservative signal available today -- never reuse a Cell a task
just failed on.
A SUCCEEDED task builds a best-effort `ReleaseOutcome` (module docstring: `rolled_back_whole_cell`
and `has_block_wax` both default False, since neither Capping nor a second Cell Wax query is wired
at this call site yet; `single_use` defaults False, since `TaskNeeds.disposable` is not threaded
this far either) and lets the real policy decide; a backend that turns out not to support pause
after all (`BackendCapabilityError`, from `lifecycle.overwinter`'s own `backend.pause` call) falls
back to teardown rather than raising out of a Queen tick.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built by
    the composition root alongside `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider`,
    over the same `CellLifecycle` and `Scrubber`, and set on `QueenDeps.on_task_finished`. Called by
    `hivemind.queen.ticks.results.complete_task` (and, in a later pass, `fail_task`/`retry_task`,
    left out of this dispatch's own minimal edit -- see that module's own docstring). Calls into
    `hivemind.brood_chamber` (TaskOutcome, TaskStatus), `hivemind.hive` (BackendCapabilityError),
    `hivemind.hive.lifecycle` (CellLifecycle), `hivemind.hive.overwinter` (OverwinterDecision,
    ReleaseOutcome, Scrubber) and waggle only.

Key invariants:
    - The built callable is a true no-op (returns immediately) for any `cell_id`
      `lifecycle.status_of` does not recognise: this is what keys the whole seam off "the lifecycle
      knows this Cell", never `cell.kind` (module docstring, codingrules section 8.7).
    - A FAILED task's own Cell is always torn down, never offered to `decide_release`.
    - `lifecycle.overwinter`'s own `BackendCapabilityError` never propagates out of the built
      callable: it falls back to `lifecycle.teardown` instead.

See Also:
    - .claude/roadmap.md step 5.9 for the release-then-decide edge this module wires up.
    - docs/adr/0029-overwintering-policy.md for the ReleaseOutcome shape this module builds.
    - hivemind.queen.ticks.results for complete_task, this module's one caller today.
    - hivemind.queen.cell_gate.provider for LifecycleVirtualCellProvider, built alongside this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.hive import BackendCapabilityError
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.overwinter import OverwinterDecision, ReleaseOutcome, Scrubber
from waggle.ids import CellId, GrantId

__all__ = ["make_on_cell_granted", "make_on_task_finished"]

OnTaskFinished = Callable[[CellId, TaskOutcome], Awaitable[None]]
OnCellGranted = Callable[[CellId, GrantId], Awaitable[None]]


def make_on_cell_granted(lifecycle: CellLifecycle) -> OnCellGranted:
    """Build the callable `QueenDeps.on_cell_granted` holds, closed over `lifecycle`.

    Args:
        lifecycle: Walked READY -> GRANTED for a Cell it tracks; a Cell it does not track (a Real
            Cell, most tasks) is a no-op, which is how the Queen avoids reading `cell.kind`.

    Returns:
        An async callable `hivemind.queen.dispatcher.ready` awaits once per dispatched task.
    """

    async def _on_cell_granted(cell_id: CellId, grant_id: GrantId) -> None:
        """Grant `cell_id` in the lifecycle if it tracks the Cell."""
        if lifecycle.status_of(cell_id) is not None:
            await lifecycle.grant(cell_id, grant_id)

    return _on_cell_granted


def make_on_task_finished(lifecycle: CellLifecycle, scrub: Scrubber) -> OnTaskFinished:
    """Build the callable `QueenDeps.on_task_finished` holds, closed over `lifecycle` and `scrub`.

    Args:
        lifecycle: Told about every finished task's own Cell, via `release`/`overwinter`/
            `teardown`.
        scrub: Passed straight through to `lifecycle.overwinter`, for a Cell that is kept dormant.

    Returns:
        An async callable `hivemind.queen.ticks.results.complete_task` awaits once per finished
        task, for that task's own Warden's Cell.
    """

    async def _on_task_finished(cell_id: CellId, outcome: TaskOutcome) -> None:
        """Release `cell_id` if the lifecycle tracks it, then overwinter or tear it down."""
        if lifecycle.status_of(cell_id) is None:
            return  # Not a Virtual Cell this lifecycle tracks (a Real Cell, most tasks): no-op.
        if outcome.status is not TaskStatus.SUCCEEDED:
            # Conservative default until Capping (roadmap 5.10) can say more precisely (module
            # docstring): a task that did not succeed never leaves its own Cell eligible for reuse.
            await lifecycle.teardown(cell_id)
            return
        release_outcome = ReleaseOutcome(
            rolled_back_whole_cell=False,
            has_block_wax=False,
            single_use=False,
            backend_can_pause=True,
        )
        decision = await lifecycle.release(cell_id, release_outcome)
        if decision is OverwinterDecision.TEARDOWN:
            await lifecycle.teardown(cell_id)
            return
        try:
            await lifecycle.overwinter(cell_id, scrub=scrub)
        except BackendCapabilityError:
            await lifecycle.teardown(cell_id)

    return _on_task_finished
