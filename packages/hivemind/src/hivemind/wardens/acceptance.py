"""Run a task's acceptance criteria on the Warden's own session, never the bee that did the work.

Roadmap step 3.18: "A task reaches SUCCEEDED only when its Warden has run the acceptance checks and
they pass; the bee that did the work cannot mark itself done." Codingrules section 8.12 states the
same rule for Capping's postconditions: "The proposer never verifies its own work." `run_acceptance`
is the Warden-side half of that: it takes the same `waggle.messages.labels.Postcondition` shape a
task's `TaskAssign.acceptance` carries (the planner-side half, roadmap step 3.20, is what fills that
field in) and runs each one through `hivemind.supervision.capping.postconditions.
check_postcondition` on the Warden's own `CellSession` -- never the sub-bee's -- so a bee cannot
influence whether its own claim of completion is accepted. An assignment with no acceptance
criteria at all passes vacuously (`waggle.messages.task.assignment.TaskAssign.acceptance` is itself
never empty by construction, `MIN_ACCEPTANCE_ITEMS = 1`, but `run_acceptance` is also called
directly by tests with an empty sequence, and an empty ladder is trivially "everything held"); an
unsupported `PostconditionKind` (`HTTP_STATUS`, `ELEMENT_TEXT`, `JUDGE_RUBRIC` in v0) reports
`has_held=False`, so acceptance fails closed rather than silently passing something nobody actually
checked.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Called
    by `hivemind.wardens.ticks.results` once a sub-bee's `TaskResult(CLAIMED)` arrives. Calls into
    `hivemind.cell` (CellSession) and `hivemind.supervision.capping` (check_postcondition,
    PostconditionOutcome) and waggle only.

Key invariants:
    - `run_acceptance` never raises for a failing or unsupported criterion; every outcome (held or
      not) is a `PostconditionOutcome` in the returned report, never an exception.
    - `AcceptanceReport.passed` is `all(o.has_held for o in outcomes)`; `failing` is exactly the
      outcomes where that does not hold, in the same order they were checked.

See Also:
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work", the
      pattern this module mirrors for a task's own acceptance criteria.
    - .claude/roadmap.md step 3.18 for this module's own roadmap bullet.
    - hivemind.supervision.capping.postconditions for check_postcondition, the one check this
      module runs per criterion.
    - hivemind.wardens.ticks.results for the one caller, and where SUCCEEDED/ACCEPTANCE_FAILED are
      decided from this report.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CellSession
from hivemind.supervision.capping import PostconditionOutcome, check_postcondition
from waggle.messages.labels import Postcondition

__all__ = ["AcceptanceReport", "run_acceptance"]


class AcceptanceReport(BaseModel):
    """The outcome of running every one of a task's acceptance criteria."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: tuple[PostconditionOutcome, ...] = Field(
        description="Every criterion's outcome, in the order `postconditions` listed them."
    )
    passed: bool = Field(description="True when every outcome's `has_held` is True.")
    failing: tuple[PostconditionOutcome, ...] = Field(
        description="The subset of `outcomes` whose `has_held` is False; empty when `passed`."
    )


async def run_acceptance(
    session: CellSession, postconditions: Sequence[Postcondition]
) -> AcceptanceReport:
    """Check every acceptance criterion on the Warden's own session.

    Args:
        session: The Warden's own CellSession -- never the sub-bee's -- so the bee that did the
            work never verifies it (codingrules section 8.12).
        postconditions: The task's acceptance criteria, from `TaskAssign.acceptance`; an empty
            sequence passes vacuously.

    Returns:
        An AcceptanceReport: every outcome, whether all held, and which (if any) did not.
    """
    outcomes = tuple(
        [
            await check_postcondition(session, index, criterion)
            for index, criterion in enumerate(postconditions)
        ]
    )
    failing = tuple(outcome for outcome in outcomes if not outcome.has_held)
    return AcceptanceReport(outcomes=outcomes, passed=not failing, failing=failing)
