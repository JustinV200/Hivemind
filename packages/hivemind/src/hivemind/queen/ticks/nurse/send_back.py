"""Define send_back: a Nurse Bee rejection sends its task back to fix forward on the same Cell.

ADR-0061: a Virtual Cell keeps the rejected attempt's changes and nothing is rolled back; a fresh
Worker starts on the Cell as it stands, its brief carrying what the Nurse asked to change. The
round is counted in the Brood Chamber first (`BroodChamber.note_nurse_round`), so the count the
Queen's cap reads survives her restart, and so the assignment that goes out already carries the
new count as `TaskAssign.review_round`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.ticks.
    nurse`. Called by `hivemind.queen.queen._act_on_task_result` for a RETRY_TASK on a Nurse
    rejection. Calls into `hivemind.brood_chamber` (TaskStatus), `hivemind.queen.deps`,
    `hivemind.queen.ticks.results` (AttemptBook, retry_task) and waggle only.

Key invariants:
    - A task no longer RUNNING (cancelled, paused, failed meanwhile) is left alone: no round is
      counted and nothing is sent.
    - The round is counted before the assignment is built, so the fresh Worker's
      `review_round` is never below the count of rejections it is fixing.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.queen.dispatcher.ready.assign for where the findings reach the TaskAssign.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import TaskStatus
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.results import AttemptBook, retry_task
from waggle.messages.task import TaskResult

__all__ = ["send_back"]


async def send_back(
    deps: QueenDeps, wardens: Sequence[WardenLink], payload: TaskResult, attempts: AttemptBook
) -> None:
    """Count one more Nurse round on `payload`'s task and retry it with the review's findings.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; the task's own Warden must be among these.
        payload: The FAILED result carrying the Nurse's CHANGES_REQUESTED review.
        attempts: The Queen's own AttemptBook; the retry counts in it and takes its next number.
    """
    task = await deps.chamber.get(payload.task_id)
    if task.status is not TaskStatus.RUNNING:
        return  # It moved on meanwhile; a retry now would revive work the Queen already closed.
    verdict = payload.review.verdict if payload.review is not None else None
    findings = verdict.findings if verdict is not None else ()
    await deps.chamber.note_nurse_round(task.id)
    await retry_task(deps, wardens, task.id, attempts, review_findings=findings)
