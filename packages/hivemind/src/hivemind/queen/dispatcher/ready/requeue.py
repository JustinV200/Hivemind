"""Define requeue_for_commands: re-place a task whose Cell refused its commands, or say why not.

ADR-0060 refuses every command on a Cell whose processes are not contained below FULL access, and
a Cell at READ_ONLY runs none at all. A task that reached such a Cell without `needs.commands` (its
plan never said it runs commands) comes back FAILED with `ResultCause.CELL_RUNS_NO_COMMANDS`, and a
`redispatch` to the same Cell would only be refused again. This is the other path: the same
placement decision `dispatch_ready` makes, asked first with the task's needs widened to
`commands`, so a task no Cell could ever take fails at once with placement's own reason; otherwise
the Brood Chamber moves it RUNNING -> PENDING with that need (`BroodChamber.requeue_for_commands`,
a new attempt), the grants it held on the refusing Cell go back to the pool, and an immediate
dispatch pass places it where commands run (placement rule 4d), or leaves it waiting like any
other PENDING task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher.ready`
    sub-package. Called by `hivemind.queen.ticks.results.requeue_task`. Calls into
    `hivemind.brood_chamber` (Task), `hivemind.queen.deps`, `hivemind.queen.dispatcher.snapshot`
    (build_inventory, build_forage_view), `hivemind.queen.forage.grants` (release_finished),
    `hivemind.queen.placement` (decide, PlacementError), this package's `.dispatch`
    (dispatch_ready) and waggle only.

Key invariants:
    - The feasibility check is `decide` itself over the same snapshot a dispatch pass builds, so
      "no Cell can ever run it" means exactly what a dispatch pass would conclude (a final
      `PlacementError`), never a rule restated here.
    - A task is re-placed for its commands at most once: the chamber refuses a task that already
      needs them, and the Queen's table never asks for one.
    - The chamber write runs under the dispatch lock, so no pass sees the task half-moved.

See Also:
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
    - hivemind.queen.placement.rules.command_shortfall for rule 4d, which the wider need meets.
    - hivemind.queen.dispatcher.ready.dispatch for dispatch_ready, the pass that places it next.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.ready.dispatch import dispatch_ready
from hivemind.queen.dispatcher.snapshot import build_forage_view, build_inventory
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.placement import PlacementError, decide
from waggle.ids import TaskId

# Why the chamber gives the placement up, and why its grants go back: plain words, no content.
REQUEUE_REASON = "Its Cell refused the commands it runs; re-placed where commands run."
REQUEUE_GRANT_REASON = "Its task was re-placed on a Cell that runs commands."

__all__ = ["REQUEUE_GRANT_REASON", "REQUEUE_REASON", "requeue_for_commands"]


async def requeue_for_commands(
    deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId, attempt: int
) -> str | None:
    """Re-place RUNNING `task_id` on a Cell that runs commands, or say why none ever could.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        task_id: The RUNNING task whose Cell refused its commands; it must not already need them.
        attempt: The attempt number its next placement carries (the Queen's own count).

    Returns:
        None once the task is PENDING again with `needs.commands` set (and placed, if a Cell
        fits now); otherwise the final placement reason, with the task left as it was for the
        caller to fail.

    Raises:
        ConflictError: The task already needs commands (the chamber's own bound).
    """
    task = await deps.chamber.get(task_id)
    refusal = await _final_refusal(deps, wardens, task)
    if refusal is not None:
        return refusal
    # Under the dispatch lock, like every other write that moves a task in or out of PENDING.
    async with deps.dispatch.lock:
        await deps.chamber.requeue_for_commands(task_id, REQUEUE_REASON, attempt)
        # Its grants on the refusing Cell would otherwise be renewed there for as long as it runs.
        await forage_grants.release_finished(
            deps.ledger, deps, {task_id}, reason=REQUEUE_GRANT_REASON
        )
    await dispatch_ready(deps, wardens)
    return None


async def _final_refusal(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> str | None:
    """Ask placement about `task` as it will be re-placed; its final reason, or None.

    The task is looked at as its next pass will see it: needing commands and placed nowhere. A
    placement error that is not final (no Cell has room now, none attached yet) is a wait, which
    the PENDING task meets like any other.
    """
    needs = task.spec.needs.model_copy(update={"commands": True})
    wider = task.model_copy(
        update={"spec": task.spec.model_copy(update={"needs": needs}), "cell_id": None}
    )
    inventory = await build_inventory(deps, wardens, task=wider)
    try:
        decide(needs, inventory, build_forage_view(deps, wider), deps.placement_policy)
    except PlacementError as error:
        return str(error) if error.final else None
    return None
