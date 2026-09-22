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

**Graceful teardown (this dispatch's own fix, a real Docker run's own defect):** every path that
ends in `lifecycle.teardown` now calls the injected `quiesce` first -- a FAILED task's own Cell, a
SUCCEEDED task whose `decide_release` came back TEARDOWN, and the `BackendCapabilityError` fallback
from a pause that turned out unsupported. `hivemind.queen.cell_gate.quiesce.make_quiesce` builds the
real one: it asks the Cell's own Warden to stop gracefully and waits (bounded) for it to actually
detach, so `hivemind.wardens.warden.Warden.stop`'s own final trail sync has a real chance to land on
the Queen's trail before `backend.destroy` kills the container out from under it (that module's own
docstring has the full defect and fix). The overwinter branch never calls `quiesce`: a Cell being
paused, not destroyed, must keep its Warden running so the pause can later resume it (`quiesce`'s
own module docstring names this as a deliberate, documented gap, not an oversight).

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
    - `quiesce` runs before every `lifecycle.teardown` call and never before `lifecycle.overwinter`
      (module docstring's own "Graceful teardown").

See Also:
    - .claude/roadmap.md step 5.9 for the release-then-decide edge this module wires up.
    - docs/adr/0029-overwintering-policy.md for the ReleaseOutcome shape this module builds.
    - hivemind.queen.cell_gate.quiesce for make_quiesce, the real `quiesce` implementation.
    - hivemind.queen.ticks.results for complete_task, this module's one caller today.
    - hivemind.queen.cell_gate.provider for LifecycleVirtualCellProvider, built alongside this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.hive import BackendCapabilityError
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.overwinter import OverwinterDecision, ReleaseOutcome, Scrubber
from waggle.ids import CellId, GrantId

__all__ = ["make_on_cell_granted", "make_on_task_finished"]

OnTaskFinished = Callable[[CellId, TaskOutcome], Awaitable[None]]
OnCellGranted = Callable[[CellId, GrantId], Awaitable[None]]
# hivemind.queen.cell_gate.quiesce.Quiesce, restated here rather than imported: a quiesce that
# does nothing is a valid, complete default (module docstring's own "before every teardown"), so
# this module needs only the shape, never quiesce.py's own make_quiesce.
Quiesce = Callable[[CellId], Awaitable[None]]


def make_on_cell_granted(lifecycle: CellLifecycle) -> OnCellGranted:
    """Build the callable `QueenDeps.on_cell_granted` holds, closed over `lifecycle`.

    Args:
        lifecycle: Walked READY -> GRANTED for a Cell it tracks; a Cell it does not track (a Real
            Cell, most tasks) is a no-op, which is how the Queen avoids reading `cell.kind`.

    Returns:
        An async callable `hivemind.queen.dispatcher.ready` awaits once per dispatched task.
    """

    async def _on_cell_granted(cell_id: CellId, grant_id: GrantId) -> None:
        """Walk READY -> GRANTED for a Cell the lifecycle tracks; a re-grant is a no-op.

        The dispatcher grants the same Cell again on a retry, a redispatch or a resume from
        pause (hivemind.queen.dispatcher.ready), all of which reuse a Cell already GRANTED: the
        state machine has no GRANTED -> GRANTED edge and must not be asked for one (the first
        real Docker run crashed the whole Queen on exactly that).
        """
        if lifecycle.status_of(cell_id) is VirtualCellStatus.READY:
            await lifecycle.grant(cell_id, grant_id)

    return _on_cell_granted


def make_on_task_finished(
    lifecycle: CellLifecycle, scrub: Scrubber, quiesce: Quiesce | None = None
) -> OnTaskFinished:
    """Build the callable `QueenDeps.on_task_finished` holds, closed over `lifecycle` and `scrub`.

    Args:
        lifecycle: Told about every finished task's own Cell, via `release`/`overwinter`/
            `teardown`.
        scrub: Passed straight through to `lifecycle.overwinter`, for a Cell that is kept dormant.
        quiesce: Awaited before every `lifecycle.teardown` call this builds, never before
            `lifecycle.overwinter` (module docstring's own "Graceful teardown"). `None` (the
            default, every pre-this-dispatch test) is a true no-op;
            `hivemind.queen.cell_gate.quiesce.make_quiesce` builds the real one, over a live Queen.

    Returns:
        An async callable `hivemind.queen.ticks.results.complete_task` awaits once per finished
        task, for that task's own Warden's Cell.
    """
    quiesce_fn = quiesce if quiesce is not None else _no_op_quiesce

    async def _on_task_finished(cell_id: CellId, outcome: TaskOutcome) -> None:
        """Release `cell_id` if the lifecycle tracks it, then overwinter or tear it down."""
        if lifecycle.status_of(cell_id) is None:
            return  # Not a Virtual Cell this lifecycle tracks (a Real Cell, most tasks): no-op.
        if outcome.status is not TaskStatus.SUCCEEDED:
            # Conservative default until Capping (roadmap 5.10) can say more precisely (module
            # docstring): a task that did not succeed never leaves its own Cell eligible for reuse.
            await quiesce_fn(cell_id)
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
            await quiesce_fn(cell_id)
            await lifecycle.teardown(cell_id)
            return
        try:
            # No quiesce_fn here: this Cell is being paused, not destroyed, so its Warden must
            # stay running for the pause to later resume it (module docstring's own key invariant).
            await lifecycle.overwinter(cell_id, scrub=scrub)
        except BackendCapabilityError:
            await quiesce_fn(cell_id)
            await lifecycle.teardown(cell_id)

    return _on_task_finished


async def _no_op_quiesce(cell_id: CellId) -> None:
    """The default `quiesce`: nothing extra happens before teardown, unchanged from before."""
