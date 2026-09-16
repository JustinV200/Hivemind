"""Grade a Handoff-quality eval run: pure functions over completion, no-redo and Handoff shape.

Roadmap step 4.5: "graded on completion and on not repeating do-not-redo steps." Three pure
functions do the actual scoring -- `grade_completion` (every expected effect exists at the end),
`grade_no_redo` (no tool call the fake session recorded after the checkpoint repeats a step the
Handoff's own `do_not_redo` names) and `grade_handoff_shape` (the Handoff's mandatory fields hold
real content, not just an empty tuple the schema happens to allow) -- each returning a small,
frozen pydantic grade model; `build_report` combines all three into one `HandoffEvalReport`.
Nothing here runs a scenario or touches a provider: `tests.evals.handoff.scenario` produces the
raw data these functions grade, so a grading bug and a scenario bug can never be conflated in one
stack trace, and `test_handoff_eval.py`'s own negative test can call these functions directly
against a hand-built, deliberately bad Handoff/call list without running anything at all.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests.evals.handoff.test_handoff_eval` and `tests.evals.handoff.test_handoff_eval_live`.

Key invariants:
    - Every grading function is pure: no I/O, no clock, no randomness. `write_report_if_configured`
      is the one function here with a side effect, and only when `HIVEMIND_EVAL_REPORT_DIR` (or a
      caller-supplied environ override) is set -- CI must never write into the repository
      (roadmap step 4.5's own "run with the fake in CI" implies no report file lands there).
    - `grade_handoff_shape`'s "mandatory fields non-empty" checks only the fields a real Drone
      attempt always populates today (`goal`, `progress`, `written_by`, `decisions`,
      `next_steps` -- see `hivemind.workers.roles.drone.outcome.build_handoff_outcome`); the
      optional list fields a Drone attempt never populates (`tried_and_failed`, `constraints`,
      `open_threads`, `pinned_facts`) are legitimately empty on a short task and are not graded
      as a failure for being so (the Handoff pydantic model itself has no `min_length` on them).

See Also:
    - .claude/roadmap.md step 4.5 for this eval's own exit condition.
    - docs/evals/README.md and packages/hivemind/tests/evals/README.md for where a written report
      lands and what it contains.
    - hivemind.memory.handoff for Handoff, the model every grading function here reads.
    - tests.evals.handoff.scenario for HandoffScenarioResult, the raw data build_report expects.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.memory import Handoff

# The env var CI leaves unset (module docstring): only a human or a local run that exports it
# gets a written report, so a fake-provider CI run never touches the working tree.
REPORT_DIR_ENV_VAR = "HIVEMIND_EVAL_REPORT_DIR"

# Handoff fields hivemind.workers.roles.drone.outcome.build_handoff_outcome always populates for
# real (module docstring's own "Key invariants" note); a Drone-authored Handoff missing one of
# these would mean the checkpoint path itself broke, not merely that a short task had nothing to
# say for an optional field.
_MANDATORY_TEXT_FIELDS: tuple[str, ...] = ("goal", "progress", "written_by")
_MANDATORY_LIST_FIELDS: tuple[str, ...] = ("decisions", "next_steps")

__all__ = [
    "REPORT_DIR_ENV_VAR",
    "CompletionGrade",
    "HandoffEvalReport",
    "HandoffShapeGrade",
    "NoRedoGrade",
    "build_report",
    "grade_completion",
    "grade_handoff_shape",
    "grade_no_redo",
    "write_report_if_configured",
]


class CompletionGrade(BaseModel):
    """Whether every expected effect (a scratch file, in this eval) exists at the end of a run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected: tuple[str, ...] = Field(
        description="Every effect the whole task was meant to produce."
    )
    missing: tuple[str, ...] = Field(description="Expected effects absent once the run finished.")
    passed: bool = Field(description="True when nothing in `expected` is missing.")


class NoRedoGrade(BaseModel):
    """Whether any post-checkpoint tool call repeated a step the Handoff's `do_not_redo` names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    do_not_redo: tuple[str, ...] = Field(description="The Handoff's own do_not_redo entries.")
    violated: tuple[str, ...] = Field(
        description="do_not_redo entries a later tool call's recorded effect repeated."
    )
    passed: bool = Field(description="True when nothing in `do_not_redo` was violated.")


class HandoffShapeGrade(BaseModel):
    """Whether a Handoff's mandatory fields (module docstring's own list) hold real content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing_fields: tuple[str, ...] = Field(description="Mandatory Handoff fields found empty.")
    passed: bool = Field(description="True when every mandatory field held content.")


class HandoffEvalReport(BaseModel):
    """The whole graded result of one Handoff-quality eval scenario run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str = Field(description="Which scenario produced this report, e.g. 'fake-positive'.")
    completion: CompletionGrade = Field(description="Grade (a): every expected effect landed.")
    no_redo: NoRedoGrade = Field(description="Grade (b): nothing on do_not_redo was repeated.")
    handoff_shape: HandoffShapeGrade = Field(description="Grade (c): the Handoff itself is real.")
    passed: bool = Field(
        description="True only when completion, no_redo and handoff_shape all are."
    )


def grade_completion(expected: Sequence[str], present: Sequence[str]) -> CompletionGrade:
    """Grade (a): every effect `expected` names must exist among `present` at the end of a run.

    Args:
        expected: Every effect (scratch path) the whole two-bee task was meant to produce.
        present: The effects that actually existed once the second bee finished.

    Returns:
        A CompletionGrade; `passed` is True exactly when `missing` is empty.
    """
    present_set = set(present)
    missing = tuple(path for path in expected if path not in present_set)
    return CompletionGrade(expected=tuple(expected), missing=missing, passed=not missing)


def grade_no_redo(handoff: Handoff, later_calls: Sequence[str]) -> NoRedoGrade:
    """Grade (b): no call in `later_calls` may repeat a step `handoff.do_not_redo` names.

    A do_not_redo entry is "violated" when its own text contains one of `later_calls`' own
    recorded effects as a substring (`tests.evals.handoff.scenario._augment_do_not_redo` builds
    every entry from exactly such a path, so this is an exact match in practice, not a fuzzy
    heuristic).

    Args:
        handoff: The Handoff the second bee resumed from; `do_not_redo` is what it must respect.
        later_calls: Every effect (e.g. a written path) recorded strictly after the checkpoint --
            "the tool calls the fake session recorded" roadmap step 4.5 asks this to be measured
            from, not the model's own claimed summary.

    Returns:
        A NoRedoGrade; `passed` is True exactly when `violated` is empty. Vacuously True when
        `do_not_redo` is empty, matching "nothing was repeated because nothing was forbidden."
    """
    violated = tuple(
        entry for entry in handoff.do_not_redo if any(call in entry for call in later_calls)
    )
    return NoRedoGrade(do_not_redo=handoff.do_not_redo, violated=violated, passed=not violated)


def grade_handoff_shape(handoff: Handoff) -> HandoffShapeGrade:
    """Grade (c): the mandatory fields a real checkpoint always fills hold actual content.

    Args:
        handoff: The Handoff to check.

    Returns:
        A HandoffShapeGrade; `passed` is True exactly when `missing_fields` is empty.
    """
    empty_text = tuple(
        name for name in _MANDATORY_TEXT_FIELDS if not getattr(handoff, name).strip()
    )
    empty_list = tuple(name for name in _MANDATORY_LIST_FIELDS if not getattr(handoff, name))
    missing = empty_text + empty_list
    return HandoffShapeGrade(missing_fields=missing, passed=not missing)


def build_report(
    *,
    scenario: str,
    expected_files: Sequence[str],
    present_files: Sequence[str],
    handoff: Handoff,
    second_bee_calls: Sequence[str],
) -> HandoffEvalReport:
    """Run all three grades and combine them into one HandoffEvalReport.

    Args:
        scenario: A short label for which scenario produced this report (e.g. "fake-positive").
        expected_files: See `grade_completion`.
        present_files: See `grade_completion`.
        handoff: See `grade_no_redo` and `grade_handoff_shape`.
        second_bee_calls: See `grade_no_redo`'s own `later_calls`.

    Returns:
        A HandoffEvalReport whose `passed` is the conjunction of all three grades.
    """
    completion = grade_completion(expected_files, present_files)
    no_redo = grade_no_redo(handoff, second_bee_calls)
    shape = grade_handoff_shape(handoff)
    return HandoffEvalReport(
        scenario=scenario,
        completion=completion,
        no_redo=no_redo,
        handoff_shape=shape,
        passed=completion.passed and no_redo.passed and shape.passed,
    )


def write_report_if_configured(
    report: HandoffEvalReport, *, environ: Mapping[str, str] | None = None
) -> Path | None:
    """Write `report` as JSON under `$HIVEMIND_EVAL_REPORT_DIR` only when that var is set.

    Args:
        report: The report to (maybe) write.
        environ: Where to read `HIVEMIND_EVAL_REPORT_DIR` from; the real `os.environ` when None
            (a test passes its own mapping so it never depends on the process environment).

    Returns:
        The path written, or None when the env var is unset or empty -- the module docstring's
        "CI must never write into the repository."
    """
    active_environ = environ if environ is not None else os.environ
    report_dir = active_environ.get(REPORT_DIR_ENV_VAR)
    if not report_dir:
        return None
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"handoff-{report.scenario}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path
