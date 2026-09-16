"""Fake-provider run of the handoff-quality eval: roadmap step 4.5's own exit condition.

A bee is stopped mid-task after two of four steps; a fresh bee, built from nothing but the stored
Handoff, must finish the remaining two and never repeat what the Handoff's own `do_not_redo` names.
`test_a_fresh_bee_resumes_from_a_handoff_alone_and_completes_the_task` is the positive case; per
the roadmap step's own instruction to run "with the fake in CI", this module carries no marker
beyond what the rest of the fast unit run already excludes (`-m "not integration and not e2e and
not live_llm and not local_llm"`) -- everything here runs in-process over fakes with a FakeClock,
so it needs neither.
`test_the_no_redo_grade_catches_a_second_bee_that_repeats_a_do_not_redo_step` is the negative case
a grader with no teeth would let through unnoticed: it scripts the second bee to deliberately
rewrite a file the Handoff's own `do_not_redo` already names, and asserts the grade fails.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.5 for this eval's own exit condition.
    - tests.evals.handoff.scenario for the two-bee harness these tests drive.
    - tests.evals.handoff.grader for the pure grading functions these tests assert against.
"""

from __future__ import annotations

import json
from pathlib import Path

from builders.memory import make_handoff
from evals.handoff.grader import build_report, write_report_if_configured
from evals.handoff.scenario import run_fake_handoff_scenario


async def test_a_fresh_bee_resumes_from_a_handoff_alone_and_completes_the_task() -> None:
    """The positive case: completion, no-redo and Handoff shape all pass over the fake provider."""
    result = await run_fake_handoff_scenario()

    report = build_report(
        scenario="fake-positive",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )

    assert report.completion.passed, report.completion
    assert report.no_redo.passed, report.no_redo
    assert report.handoff_shape.passed, report.handoff_shape
    assert report.passed


async def test_the_no_redo_grade_catches_a_second_bee_that_repeats_a_do_not_redo_step() -> None:
    """Proves the grader has teeth (roadmap step 4.5): a deliberately repeating script must fail."""
    result = await run_fake_handoff_scenario(bad_second_bee=True)

    report = build_report(
        scenario="fake-negative",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )

    # The bad script still finishes all four files (it writes the redo *and* the two real
    # remaining steps), so completion still passes -- only the no-redo grade should catch it.
    assert report.completion.passed, report.completion
    assert not report.no_redo.passed
    assert result.expected_files[0] in "".join(report.no_redo.violated)
    assert not report.passed


def test_the_grader_also_catches_a_hand_built_handoff_with_no_teeth_of_its_own() -> None:
    """A second, scenario-free negative case: a bad Handoff/call list alone fails the grade.

    Deliberately bypasses `run_fake_handoff_scenario` entirely -- `tests.evals.handoff.grader`'s
    own functions are pure, so this asserts they have teeth without needing a Drone attempt at
    all, only a hand-built Handoff (`builders.memory.make_handoff`) and a plain call list.
    """
    handoff = make_handoff(do_not_redo=("Do not write scratch/output.txt again.",))

    report = build_report(
        scenario="hand-built-negative",
        expected_files=("scratch/output.txt",),
        present_files=("scratch/output.txt",),
        handoff=handoff,
        second_bee_calls=("scratch/output.txt",),
    )

    assert report.completion.passed
    assert not report.no_redo.passed
    assert not report.passed


def test_report_is_written_only_when_the_report_dir_env_var_is_set(tmp_path: Path) -> None:
    """CI must never write into the repository: only a caller-set env var writes a report."""
    handoff = make_handoff()
    report = build_report(
        scenario="report-write-check",
        expected_files=(),
        present_files=(),
        handoff=handoff,
        second_bee_calls=(),
    )
    report_dir = tmp_path / "reports"

    unset = write_report_if_configured(report, environ={})
    written = write_report_if_configured(
        report, environ={"HIVEMIND_EVAL_REPORT_DIR": str(report_dir)}
    )

    assert unset is None
    assert written is not None
    assert written == report_dir / "handoff-report-write-check.json"
    assert json.loads(written.read_text(encoding="utf-8"))["scenario"] == "report-write-check"
