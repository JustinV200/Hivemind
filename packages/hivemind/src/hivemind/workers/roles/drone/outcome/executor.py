"""Define _RecordingExecutor: the Drone's own name for the shared bounded-loop LoopExecutor.

Roadmap step 6.9 factored every behaviour out to `hivemind.workers.roles.bounded_loop.executor.
LoopExecutor` (which also gained an optional `on_result` hook the Drone itself never sets),
`HandoffRequestedError` and `collect_artifacts`, once the Forager and Scout needed the identical
pause/cancel/handoff cooperation a Drone already had. This module keeps the Drone's own private
name and import path alive, as a subclass with no added behaviour, so this package's own tests
(which import `_RecordingExecutor` from this exact path directly) see no change at all.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Used by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.workers.roles.bounded_loop.
    executor` only.

Key invariants:
    - None beyond `hivemind.workers.roles.bounded_loop.executor.LoopExecutor`'s own (see that
      module).

See Also:
    - hivemind.workers.roles.bounded_loop.executor for LoopExecutor, this class's one base and the
      canonical home of its logic, and for HandoffRequestedError and collect_artifacts.
    - hivemind.workers.roles.drone.outcome.build for how records become a Handoff.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.executor import (
    MAX_RECORDED_CALLS,
    HandoffRequestedError,
    LoopExecutor,
    collect_artifacts,
)

__all__ = ["MAX_RECORDED_CALLS", "HandoffRequestedError", "collect_artifacts"]


class _RecordingExecutor(LoopExecutor):
    """A ToolExecutor over a ToolRegistry that cooperates with pause/cancel/handoff and remembers.

    Every method is `LoopExecutor`'s own (module docstring): this subclass exists only so the
    Drone's own private name and import path keep working.
    """
