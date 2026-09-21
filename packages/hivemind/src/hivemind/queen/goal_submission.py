"""Define submit_goal: plan a goal into a task graph, persist it, and place what's ready.

`Queen.submit_goal` used to do this work in its own class body; it is pulled out to a module-level
function, mirroring `hivemind.queen.dispatcher.dispatch_ready` and `hivemind.queen.questions`'s own
free functions, so `queen.py` (pinned exactly at codingrules 5.1's 300-line file cap) never has to
grow to carry a new `PlanBrief` field (roadmap step 5.0b: `scratch_root`, so a plan that declares a
leaving inside the Hive Stand's own scratch is refused while planning, not discovered at release;
roadmap step 5.0e: `keep_root`, so the planner prompt can be told the manifest's own keep root and
declare a leaving under it). `Queen.submit_goal` becomes a one-line delegator, exactly like
`Queen._tick`/`_on_tick_failed` are for `_run_tick`/`_record_recovered_tick_error` in `queen.py`
itself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen.submit_goal`, the only caller. Calls into `hivemind.cell`
    (HoneyClearance), `hivemind.forage.slots` (ModelSlot), `hivemind.queen.deps` (QueenDeps,
    WardenLink), `hivemind.queen.dispatcher` (dispatch_ready), `hivemind.queen.planner` (PlanBrief,
    plan_goal), `hivemind.queen.trail` (record_event) and waggle (TaskId) only.

Key invariants:
    - Takes `deps` and `wardens` explicitly, never a `Queen` instance: the Queen delegates by
      handing her own collaborators to a free function (codingrules section 8.8), never by a
      module reaching into her private attributes from outside `queen.py`.
    - The returned TaskId is always the first task minted from the plan (the goal's own id),
      matching `Queen.submit_goal`'s own documented contract.

See Also:
    - .claude/codingrules.md section 5.1 for the file-size limit this module exists to keep.
    - .claude/roadmap.md step 5.0b for "the plan declares what stays".
    - hivemind.queen.planner.plan for plan_goal and PlanBrief, this module's one model call.
    - hivemind.queen.queen for Queen.submit_goal, this module's one caller.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.planner import PlanBrief, plan_goal
from hivemind.queen.trail import record_event
from waggle.ids import TaskId

__all__ = ["submit_goal"]


async def submit_goal(
    deps: QueenDeps, wardens: Sequence[WardenLink], goal: str, *, clearance: HoneyClearance
) -> TaskId:
    """Plan `goal` into a task graph, persist it, and place whatever is ready at once.

    Args:
        deps: The Queen's own collaborators (`Queen._deps`).
        wardens: Every Warden currently attached (`Queen.wardens`); placement matches the plan's
            needs against their Cells, and `dispatch_ready` assigns to them.
        goal: The goal text, as the human (or a bee on the human's behalf) stated it.
        clearance: The goal's own data-sensitivity ceiling.

    Returns:
        The goal's own id (the first task minted from the plan).
    """
    bound = deps.bound_for(ModelSlot.QUEEN)
    brief = PlanBrief(
        goal,
        clearance,
        [link.cell for link in wardens],
        scratch_root=deps.scratch_root,
        keep_root=deps.keep_root,
    )
    draft = await plan_goal(brief, bound, gate=deps.call_gate)
    minted = await deps.chamber.submit(draft)
    await record_event(deps, "queen.planned", minted[0].id, task_count=len(minted))
    await dispatch_ready(deps, wardens)
    return minted[0].id  # The goal's own id: the first task minted from the plan.
