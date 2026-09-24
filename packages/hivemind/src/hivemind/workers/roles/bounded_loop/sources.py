"""Define RoleSources: what a bounded-loop attempt knows, shaped for hot-state packing.

`hivemind.memory.hot_state.summaries.HotStateSources` is the Protocol `hivemind.memory.assemble`
reads from; a role built on the bounded tool loop has no task graph, no Alarm list and no question
inbox of its own -- it is a single-task sub-bee -- so `RoleSources` answers each of that Protocol's
seven methods from exactly what one `Worker.run` call already holds: the current `TaskAssign` as
its one active task, no open Alarms, no pending questions of its own (its Warden owns those), the
`resume_from` Handoff itself (both its own decisions, for `recent_decisions`, and the whole thing,
for `handoff`), and pins/notes read straight through `ctx.memory`. Moved out of
`hivemind.workers.roles.drone.sources.DroneSources` unchanged (roadmap step 6.9): every field of
this class was already role-agnostic, so the Drone's own module now subclasses this one with no
added behaviour, and the Forager and Scout use it directly.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.bounded_loop`. Built fresh by
    `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop` for every attempt and handed to
    `hivemind.memory.assemble`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory`,
    `hivemind.workers.context` and waggle only.

Key invariants:
    - Every summary this class builds carries the assignment's own `HoneyClearance`
      (`hivemind.cell.HoneyClearance.from_wire`), matching the rest of the attempt: a role never
      manufactures a different clearance for its own hot-state view.
    - `open_alarms` and `pending_questions` always return `()`: a role's own Alarms and questions
      are its Warden's concern, never packed into its own prompt.

See Also:
    - .claude/codingrules.md section 8.9 for the hot-state tiers this class packs from.
    - hivemind.memory.hot_state.summaries for HotStateSources, the Protocol this class implements,
      and the summary models it builds.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this class's one builder.
    - hivemind.workers.roles.drone.sources for DroneSources, the subclass that keeps this class's
      old name and import path alive for the Drone's own, unchanged tests.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.memory import (
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    Handoff,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
)
from hivemind.workers.context import WorkerContext
from waggle.ids import CellId
from waggle.messages.task import TaskAssign

# Mirror hivemind.memory.hot_state.summaries' own per-field caps (a non-init submodule this
# package may not import from directly, codingrules section 5.4); truncating to these same numbers
# keeps every summary this class builds within that Protocol's own validated bounds.
_TASK_TITLE_CAP_CHARS = 200  # Mirrors SUMMARY_TITLE_CAP_CHARS.
_SUMMARY_TEXT_CAP_CHARS = 500  # Mirrors SUMMARY_TEXT_CAP_CHARS.

ROLE_NOTES_LIMIT = 20  # Enough recent notes to matter; hot-state packing still drops by budget.

__all__ = ["ROLE_NOTES_LIMIT", "RoleSources"]


class RoleSources:
    """What one bounded-loop attempt knows, answering `hivemind.memory.HotStateSources`."""

    def __init__(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> None:
        """Build a RoleSources over one attempt's own context, assignment and optional Handoff.

        Args:
            ctx: This attempt's WorkerContext; supplies `memory` (pins, notes) and `clock`.
            assignment: The task this attempt is working; becomes its own one active task.
            resume_from: The Handoff this attempt resumes from, if any; its `decisions` become
                `recent_decisions`.
        """
        self._ctx = ctx
        self._assignment = assignment
        self._resume_from = resume_from
        self._clearance = HoneyClearance.from_wire(assignment.clearance)

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Return the assignment itself, as this role's one active task."""
        return (
            TaskSummary(
                id=self._assignment.task_id,
                title=self._assignment.objective[:_TASK_TITLE_CAP_CHARS],
                status="RUNNING",
                objective=self._assignment.objective[:_SUMMARY_TEXT_CAP_CHARS],
                updated_at=self._ctx.clock.now(),
                clearance=self._clearance,
            ),
        )

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Return no Alarms: this role's own Alarms belong to its Warden's inbox, not its prompt."""
        return ()

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Return no pending questions: a role blocks on `ctx.asker.ask`, never packs its own."""
        return ()

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return the resumed Handoff's own decisions, newest-first index, capped to `limit`."""
        if self._resume_from is None:
            return ()
        decisions = self._resume_from.decisions[:limit]
        return tuple(
            DecisionSummary(
                episode_id=f"handoff-decision-{index}",
                at=self._ctx.clock.now(),
                decision=decision.what[:_SUMMARY_TEXT_CAP_CHARS],
                action=decision.why[:_SUMMARY_TEXT_CAP_CHARS],
                clearance=self._clearance,
            )
            for index, decision in enumerate(decisions)
        )

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin within this attempt's clearance, from `ctx.memory`."""
        return await self._ctx.memory.list_pins(self._clearance)

    async def notes(self) -> tuple[Note, ...]:
        """Return up to `ROLE_NOTES_LIMIT` recent notes within this attempt's clearance."""
        return await self._ctx.memory.list_notes(None, self._clearance, ROLE_NOTES_LIMIT)

    async def handoff(self) -> Handoff | None:
        """Return the Handoff this attempt resumes from, verbatim, or None for a fresh attempt."""
        return self._resume_from

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """Return no Cell Wax: placement already chose this attempt's Cell before it ever ran."""
        del cells  # Unused: always empty, for the reason in this method's own docstring.
        return ()
