"""Reduce a finished Night Veil task and its questions to their skeleton: ids, states and times.

Codingrules section 12: nothing of a Night Veil Cell's work outlives the Cell but its skeleton,
and the Brood Chamber holds the work's own words: a task's title, objective and acceptance
criteria (the plan the Queen made from the human's request), its last progress summary, its
outcome's summary and artifacts, and every question it asked and the answer it got. At a Night
Veil Cell's teardown the purge's Brood Chamber side channel has each store rewrite those rows to
what this module returns: every id, status, count and timestamp kept, every word replaced by one
placeholder (the models require their text fields non-empty). `due_for_scrub` says which rows:
a task that has ended, is one of the Cell's own (by the ids its segment filed under it) or asked
for Night Veil, and was not reduced already. A task still live keeps its words (it may yet be
retried on another Cell): the teardown after it ends reduces it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.store`.
    Called by both `TaskStore` implementations' `scrub_night_veil` (`.memory`, `.sqlite`). Calls
    into `hivemind.brood_chamber.questions`, `hivemind.brood_chamber.task`, `hivemind.cell`
    (CombShieldLevel) and waggle only.

Key invariants:
    - Pure (codingrules 8.3): no I/O; the same row in gives the same reduced row out.
    - A reduced row keeps its id, status, timestamps, ids and counts, and holds no word the
      original held; reducing it again changes nothing (`due_for_scrub` is then False).

See Also:
    - .claude/codingrules.md section 12 for the boundary.
    - hivemind.pheromone.retention.purge for the teardown purge whose side channel calls this.
"""

from __future__ import annotations

from hivemind.brood_chamber.questions import Answer, Question
from hivemind.brood_chamber.task.model import Task, TaskOutcome
from hivemind.brood_chamber.task.state import is_terminal
from hivemind.cell import CombShieldLevel
from waggle.messages import Postcondition

SCRUBBED_TEXT = "[night veil]"  # What every word of a reduced row reads as, from now on.

__all__ = ["SCRUBBED_TEXT", "due_for_scrub", "scrub_question", "scrub_task"]


def due_for_scrub(task: Task, task_ids: frozenset[str]) -> bool:
    """Return whether `task` is a finished Night Veil task whose words are still stored.

    Args:
        task: One stored task.
        task_ids: The ids the Night Veil Cell's segment filed under it (its tasks among them).

    Returns:
        True for an ended task that is one of `task_ids` or asked for NIGHT_VEIL, not yet reduced.
    """
    night_veil = task.id in task_ids or task.spec.needs.comb_shield is CombShieldLevel.NIGHT_VEIL
    return night_veil and is_terminal(task.status) and task.spec.title != SCRUBBED_TEXT


def scrub_task(task: Task) -> Task:
    """Return `task` reduced to its skeleton: every id, status and time kept, every word gone.

    Args:
        task: A task `due_for_scrub` chose.

    Returns:
        The same task with its title, objective, acceptance criteria, planned leavings, last
        summary and outcome words replaced or dropped.
    """
    spec = task.spec.model_copy(
        update={
            "title": SCRUBBED_TEXT,
            "objective": SCRUBBED_TEXT,
            "acceptance": tuple(_scrub_postcondition(item) for item in task.spec.acceptance),
            "leaves": (),  # Planned paths on the Cell: the plan's own words.
        }
    )
    return task.model_copy(
        update={"spec": spec, "last_summary": None, "outcome": _scrub_outcome(task.outcome)}
    )


def scrub_question(question: Question) -> Question:
    """Return `question` reduced to its skeleton: its words and its answer's replaced.

    Args:
        question: A question a task `due_for_scrub` chose had asked.

    Returns:
        The same question with its text, every option and its answer's text replaced; the option
        count stays, so a chosen option's index still points at one.
    """
    answer = question.answer
    scrubbed: Answer | None = (
        answer.model_copy(update={"text": SCRUBBED_TEXT}) if answer is not None else None
    )
    return question.model_copy(
        update={
            "text": SCRUBBED_TEXT,
            "options": tuple(SCRUBBED_TEXT for _ in question.options),
            "answer": scrubbed,
        }
    )


def _scrub_postcondition(item: Postcondition) -> Postcondition:
    """Keep a criterion's kind; replace what it names, runs or expects."""
    expected = SCRUBBED_TEXT if item.expected is not None else None
    return item.model_copy(update={"subject": SCRUBBED_TEXT, "argv": (), "expected": expected})


def _scrub_outcome(outcome: TaskOutcome | None) -> TaskOutcome | None:
    """Keep an outcome's status, verifier and spend; replace its summary, drop its artifacts."""
    if outcome is None:
        return None
    return outcome.model_copy(update={"summary": SCRUBBED_TEXT, "artifacts": ()})
