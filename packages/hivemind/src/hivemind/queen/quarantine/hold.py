"""Hold a task PAUSED in the Brood Chamber when its Warden reports it held.

A Warden (the supervisor of one Cell) never writes the Brood Chamber, the Queen's task store: a
Virtual Cell's Warden runs inside its container (ADR-0027). When the one quarantine code path
(`hivemind.wardens.quarantine`, roadmap step 10.6c) has stopped a bee and tainted its memory, the
Warden reports its task at stage `PAUSED` on `task.progress`, the wire's own "Warden -> Queen:
report a stage change", and `hold_task` is the chamber call that follows: `BroodChamber.pause`,
RUNNING -> PAUSED, the edge Clustering already uses (codingrules Appendix C, the Task row). A task
BLOCKED on a question its quarantined bee asked has that question withdrawn first (BLOCKED ->
RUNNING, the only edge the Task machine offers out of BLOCKED short of cancelling it): nobody is
left to receive its answer, and a question an injected bee raised should not reach the human as if
it were still wanted. A task already PAUSED, or finished, is left exactly as it is, so a repeated
report (a refused respawn's) changes nothing.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    quarantine sub-package. Called by `hivemind.queen.queen`'s tick for a `PAUSE_TASK` decision
    (`hivemind.queen.autopilot.table.decide` on a `TaskProgress` at stage PAUSED). Calls into
    `hivemind.brood_chamber` and `hivemind.queen.deps` only.

Key invariants:
    - Only RUNNING -> PAUSED (and, before it, BLOCKED -> RUNNING by a withdrawal) is ever asked
      of the chamber here; no other edge, and nothing for a task in any other status.
    - The chamber records `task.paused` (and `task.question_withdrawn`) itself.

See Also:
    - hivemind.brood_chamber.chamber.lifecycle for pause, and .questions for withdraw.
    - hivemind.wardens.quarantine.report for the report this answers.
"""

from __future__ import annotations

from hivemind.brood_chamber import TaskNotFoundError, TaskStatus
from hivemind.queen.deps import QueenDeps
from waggle.messages.task import TaskProgress

MAX_HOLD_REASON_CHARS = 1_000  # The chamber's reason lands on the trail, whose strings stop here.

__all__ = ["MAX_HOLD_REASON_CHARS", "hold_task"]


async def hold_task(deps: QueenDeps, progress: TaskProgress) -> None:
    """Move the task `progress` reports held to PAUSED in the Brood Chamber.

    Args:
        deps: The Queen's collaborators; only `chamber` is used.
        progress: A Warden's `task.progress` at stage PAUSED.
    """
    try:
        task = await deps.chamber.get(progress.task_id)
    except TaskNotFoundError:
        return  # A task the chamber never held: nothing to pause.
    reason = f"Held by its Warden at attempt {progress.attempt}: {progress.summary}"
    reason = reason[:MAX_HOLD_REASON_CHARS]
    if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
        task = await deps.chamber.withdraw(task.pending_question_id, reason)
    # Anything else (already PAUSED, finished, or never started) is left as it is.
    if task.status is TaskStatus.RUNNING:
        await deps.chamber.pause(progress.task_id, reason)
