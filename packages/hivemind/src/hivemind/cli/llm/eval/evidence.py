"""Define GoalEvidence: the raw data one goal run leaves behind, before anything is graded.

The runner (`hivemind.cli.llm.eval.runner`) runs a goal on a real Hive and keeps what happened:
the tasks the Queen planned, every Pheromone Trail event (the Hive's append-only audit log) the
run produced, the files the work left in scratch, and a few facts only the live Hive knows (how
the run ended, which ladder rung the planner's model started on). The graders
(`hivemind.cli.llm.eval.graders`) are pure functions over exactly this, so a grading bug and a run
bug can never be mixed up, and a grader is tested by handing it a hand-built `GoalEvidence`
without starting a Hive at all (the same split the phase 4 handoff eval makes between its
scenario and its grader).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Built by the
    runner, read by the graders. Calls into `hivemind.brood_chamber` (Task) and
    `hivemind.pheromone` (PheromoneEvent) for types only.

Key invariants:
    - Immutable: a run's evidence is never edited after the run, so every grader sees one truth.
    - `files` holds text only, each value cut to `MAX_FILE_CHARS`: the graders look for a short
      fact, and evidence must stay small enough to keep in a report.
    - `error` is set exactly when the run itself failed to start or to finish (a Hive that would
      not build, a crash): a goal that merely failed its acceptance has `error = None`.

See Also:
    - hivemind.cli.llm.eval.graders for the pure functions over this value.
    - hivemind.cli.llm.eval.runner for the only code that builds one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hivemind.brood_chamber import Task
from hivemind.pheromone import PheromoneEvent

MAX_FILE_CHARS = 4_096  # Enough for a haiku or a command's output; a report is not a file store.

__all__ = ["MAX_FILE_CHARS", "GoalEvidence"]


@dataclass(frozen=True, slots=True)
class GoalEvidence:
    """Everything one goal run produced that a grader may look at."""

    goal: str  # The goal's name, as in `GOAL_SET`.
    succeeded: bool  # Every task SUCCEEDED before the timeout.
    timed_out: bool  # The goal's wall-clock budget ran out first.
    elapsed_s: float  # Wall time from submission to the end of polling.
    tasks: tuple[Task, ...] = ()  # The tasks the Queen planned, as they ended.
    events: tuple[PheromoneEvent, ...] = ()  # Every trail event of the run, oldest first.
    files: Mapping[str, str] = field(default_factory=dict)  # Scratch path -> text, at the end.
    start_rung: str = (
        "PROMPTED"  # The ladder rung the planner's model starts on (its capabilities).
    )
    error: str | None = None  # Why the run could not complete at all, else None.
