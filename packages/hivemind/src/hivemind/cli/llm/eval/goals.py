"""Define the fixed goal set `hive llm eval` runs: frozen data, one entry per behaviour measured.

A **goal** (what a human asks the Hive to do) is the unit the evaluation harness (roadmap step
8.5) runs end to end against whatever the manifest binds, then grades. The set is fixed and small
on purpose: the same four goals on every run make two reports comparable across models and across
dates, which is the point of `--update-map` writing grades from them. Each goal exercises one
behaviour a weak model tends to get wrong: writing several files (the three-haiku goal), running a
command and reporting what it printed, stopping to ask a question and carrying on once answered,
and being checkpointed mid-task and resumed from nothing but its Handoff (a resumable snapshot of
a task's memory). Each states the effects that prove it worked (files in the task's scratch
directory), the `TaskNeeds` (what a task requires from its Cell) a sound plan would declare, and
the human side of the conversation (the answer to give, whether to force a checkpoint).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Read by the
    runner (what to submit and how to attend the run) and the graders (what to expect). Calls
    into `hivemind.cell` (TaskNeeds, HoneyClearance, CombShieldLevel) only.

Key invariants:
    - Goal names are unique, lowercase and stable: they key the report and `--goals`.
    - No model id, provider or path outside scratch appears here; every effect is a relative
      path inside the task's scratch directory.
    - A goal that asks a question carries the answer to give; one that does not carries none.

See Also:
    - docs/evals/README.md for what each goal measures and how to read its row.
    - packages/hivemind/tests/evals/handoff for the phase 4 eval the checkpoint goal parallels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.cell import HoneyClearance, TaskNeeds

__all__ = [
    "DEFAULT_GOAL_TIMEOUT_S",
    "GOAL_SET",
    "NEEDS_FIELDS",
    "EvalGoal",
    "ExpectedEffect",
    "UnknownGoalError",
    "select_goals",
]

DEFAULT_GOAL_TIMEOUT_S = 600.0  # A local thinking model can spend minutes on one plan.
MAX_PLANNED_TASKS = 6  # A plan past this for goals this small is decomposition gone wrong.

# The TaskNeeds fields a plan is graded on. Isolation, tempo and disposability are left out: a
# sound planner legitimately varies them for these goals, while the fields below say what the
# Cell must be able to do (no display, no audio, no network, the plain tier).
NEEDS_FIELDS: tuple[str, ...] = (
    "exoskeleton",
    "browser_only",
    "audio",
    "network_scopes",
    "comb_shield",
)


class UnknownGoalError(KeyError):
    """Raise when `--goals` names a goal the fixed set does not hold."""


@dataclass(frozen=True, slots=True)
class ExpectedEffect:
    """One thing a finished goal must have left in its task's scratch directory."""

    path: str  # Relative to the scratch directory, with forward slashes.
    contains: str | None = None  # Text the file must hold (case-insensitive); None means any.


@dataclass(frozen=True, slots=True)
class EvalGoal:
    """One fixed goal: what to ask, what it must produce, and how the human side behaves."""

    name: str  # Stable key for the report and `--goals`.
    text: str  # The goal exactly as a human would state it.
    effects: tuple[ExpectedEffect, ...]  # Files that must exist when the goal ends.
    summary: str  # One line saying what the goal measures, for the report.
    # What a sound plan would declare for every task: the plain terminal-only case.
    needs: TaskNeeds = field(default_factory=TaskNeeds)
    clearance: HoneyClearance = HoneyClearance.C1  # The goal's data-sensitivity ceiling.
    answer: str | None = None  # The reply given to the one question the goal must provoke.
    forces_checkpoint: bool = False  # Whether the harness checkpoints the worker mid-task.
    max_tasks: int = MAX_PLANNED_TASKS  # The most tasks a sound plan holds.
    timeout_s: float = DEFAULT_GOAL_TIMEOUT_S  # Wall-clock budget, planning included.


GOAL_SET: tuple[EvalGoal, ...] = (
    EvalGoal(
        name="haiku",
        text="write three haiku about bees to separate files",
        effects=(
            ExpectedEffect("haiku_1.txt"),
            ExpectedEffect("haiku_2.txt"),
            ExpectedEffect("haiku_3.txt"),
        ),
        summary="Plans a small task and writes three files with the file tool.",
    ),
    EvalGoal(
        name="command",
        text=(
            "run one command that prints the result of 6 times 7 using Python, "
            "and write the single number it printed to answer.txt"
        ),
        effects=(ExpectedEffect("answer.txt", contains="42"),),
        summary="Runs one command and reports a fact it printed.",
    ),
    EvalGoal(
        name="ask",
        text=(
            "ask me which season to write about, then write one haiku about that season "
            "to season.txt"
        ),
        effects=(ExpectedEffect("season.txt"),),
        answer="autumn",
        summary="Stops to ask a question and carries on once it is answered.",
    ),
    EvalGoal(
        name="checkpoint",
        text="write four files part_1.txt to part_4.txt, each holding its own number",
        effects=tuple(ExpectedEffect(f"part_{n}.txt") for n in range(1, 5)),
        forces_checkpoint=True,
        summary="Is checkpointed mid-task and resumes from the Handoff alone.",
    ),
)


def select_goals(names: tuple[str, ...] | None = None) -> tuple[EvalGoal, ...]:
    """Return the goals named, in the fixed set's order, or all of them when none are named.

    Args:
        names: Goal names from `--goals`; None or empty selects the whole set.

    Returns:
        The selected goals in `GOAL_SET` order, so two runs with the same names compare.

    Raises:
        UnknownGoalError: A name is not in the set; the message lists the valid ones.
    """
    if not names:
        return GOAL_SET
    known = {goal.name for goal in GOAL_SET}
    unknown = sorted(set(names) - known)
    if unknown:
        raise UnknownGoalError(
            f"unknown goal(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}"
        )
    return tuple(goal for goal in GOAL_SET if goal.name in names)
