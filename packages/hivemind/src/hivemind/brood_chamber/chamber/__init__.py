"""Compose BroodChamber, the public API the Queen uses to submit and advance tasks.

The Queen (the Hive's central orchestrator) never touches `TaskStore`
(`hivemind.brood_chamber.store.protocol`) or the task and question state machines
(`hivemind.brood_chamber.task.state`, `hivemind.brood_chamber.questions`) directly; it calls
`BroodChamber`, a thin facade that loads a `Task` or `Question`, asks the right state machine
whether a move is legal, builds the new value with `model_copy`, builds the matching `TaskEvent`
(`hivemind.pheromone`, the Pheromone Trail's audit record of who did what to what and when), and
hands both to the `TaskStore` in one call so the state change and its trail event commit together.
`BroodChamber` itself is nothing but the sum of five mixins, one per responsibility, because a
single class holding all of it broke codingrules 5.1's 200-line class limit: `submission.py`
(mint a task graph), `lifecycle.py` (placement, progress, Clustering), `outcomes.py` (complete,
fail, cancel), `questions.py` (ask, answer, withdraw) and `queries.py` (the read-only methods).
Every one of them shares `base.py`'s `_ChamberBase` -- the store, clock and identity, plus the
three private helpers that turn "apply these field updates and write a matching event" into one
call -- so this file's only job is to list them and re-export `ChamberIdentity`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by the Queen's planner and
    dispatcher (phase 3) and, until then, by `hivemind.cli.tasks` and `scripts/brood_demo.py`.
    Calls into every sibling module in this package, `hivemind.cell` (HoneyClearance) and
    `hivemind.pheromone` (TaskEvent) only.

Key invariants:
    - `BroodChamber` defines no method of its own: every one it has comes from exactly one mixin,
      so there is exactly one place to find any method's implementation.
    - Every mutating method's `TaskEvent` payload carries only ids, reasons, statuses and counts,
      never a task's objective or a question's text (codingrules section 12); enforced by each
      mixin, not by this file.

See Also:
    - .claude/roadmap.md phase 2 step 2.8 for this package's place in the build order.
    - hivemind.brood_chamber.chamber.base for _ChamberBase and ChamberIdentity.
    - hivemind.cli.tasks and hivemind.cli.trail for the CLI built on this facade (roadmap 2.9).

Public API:
    - BroodChamber: the facade itself (submit, assign, unassign, start, report_progress, ask,
      answer, withdraw, pause, resume, complete, fail, cancel, next_ready, get, list,
      pending_questions).
    - ChamberIdentity: the Hive, node and actor a BroodChamber stamps on every TaskEvent it writes.
"""

from __future__ import annotations

from hivemind.brood_chamber.chamber.base import ChamberIdentity
from hivemind.brood_chamber.chamber.lifecycle import _LifecycleMixin
from hivemind.brood_chamber.chamber.outcomes import _OutcomesMixin
from hivemind.brood_chamber.chamber.queries import _QueriesMixin
from hivemind.brood_chamber.chamber.questions import _QuestionsMixin
from hivemind.brood_chamber.chamber.submission import _SubmissionMixin

__all__ = ["BroodChamber", "ChamberIdentity"]


class BroodChamber(
    _SubmissionMixin, _LifecycleMixin, _OutcomesMixin, _QuestionsMixin, _QueriesMixin
):
    """The Queen's one door into the Brood Chamber: submit, advance, question, and read tasks.

    `__init__(store, clock, identity)` is inherited from `_ChamberBase`
    (`hivemind.brood_chamber.chamber.base`); every method is inherited from exactly one of the
    five mixins listed above. See each mixin's own module docstring for the group of methods
    it defines and the module docstring above for what every one of them guarantees.
    """
