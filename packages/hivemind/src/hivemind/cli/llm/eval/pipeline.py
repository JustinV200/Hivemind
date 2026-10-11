"""Run the whole evaluation: goals, grading, optional clip set, scoring, and the report.

`run_evaluation` is what `hive llm eval` does, minus the terminal: run each selected goal on its
own fresh Hive (`runner`), grade each run (`graders`), grade the TRANSCRIBER slot on a fixture clip
set when one is given (`transcriber`), score the slots and map the scores to grades (`measured`),
write those grades through the Queen's ledger store when asked, and return the `EvalReport`. It is
a function over an `EvalRequest` so a test drives the same path the command does, with scripted
fake providers and an in-memory ledger store, and no terminal in between.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Called by the
    `hive llm eval` command and by the harness tests. Calls into every other module of this
    package, `hivemind.cli.stores` (the registry, the slot bindings), `hivemind.llm` and
    `hivemind.queen` (the ledger store).

Key invariants:
    - Synchronous: it runs goals one after another, each in its own event loop, because building
      a Hive must happen outside a running loop (`runner`'s module docstring).
    - The ledger is written only when `request.ledger` is given, and only with grades for slots
      that made calls; nothing else the Hive owns is touched.
    - A goal that fails to run is a failed row in the report, never an exception out of here.

See Also:
    - hivemind.cli.llm.eval.commands for the typer command over this.
    - docs/evals/README.md for what a run produces.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hivemind.cli.llm.eval.goals import EvalGoal
from hivemind.cli.llm.eval.graders import grade_goal
from hivemind.cli.llm.eval.grades import GoalResult
from hivemind.cli.llm.eval.measured import (
    SlotScore,
    assign_grades,
    score_slots,
    write_measured_grades,
)
from hivemind.cli.llm.eval.report import BindingRow, EvalReport
from hivemind.cli.llm.eval.runner import RunnerOptions, run_goal_evidence
from hivemind.cli.llm.eval.transcriber import TranscriberGrade, grade_clips
from hivemind.cli.stores import build_registry, closing_registry, slot_bindings
from hivemind.forage import ModelSlot
from hivemind.llm import BoundTranscriber
from hivemind.manifest import HiveManifest
from hivemind.queen.forage.ledger import LedgerStore

__all__ = ["EvalRequest", "run_evaluation"]


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """What one evaluation run is asked to do (codingrules 5.1's argument group)."""

    manifest: HiveManifest  # The bindings under evaluation.
    manifest_name: str  # How the report names it (the file the operator passed).
    goals: tuple[EvalGoal, ...]  # The goals to run, in order.
    runner: RunnerOptions  # The world the goals run in.
    fixtures: Path | None = None  # A clip directory for the TRANSCRIBER grade; None skips it.
    ledger: LedgerStore | None = None  # Write measured grades here (`--update-map`); None never.
    transcriber: BoundTranscriber | None = None  # Replaces the registry's own; tests only.
    on_goal: Callable[[GoalResult], None] | None = None  # Told of each goal as it finishes.


def run_evaluation(request: EvalRequest) -> EvalReport:
    """Run the goals (and clips), grade them, optionally write the grades, and report.

    Args:
        request: What to run and where to write.

    Returns:
        The report, whether or not any goal passed.
    """
    results: list[GoalResult] = []
    for goal in request.goals:
        result = grade_goal(goal, run_goal_evidence(request.manifest, goal, request.runner))
        results.append(result)
        if request.on_goal is not None:
            request.on_goal(result)
    clock = request.runner.clock
    fixtures = request.fixtures
    transcriber = _grade_transcriber(request, fixtures) if fixtures is not None else None
    scores = score_slots(results, _transcriber_score(transcriber))
    entries = assign_grades(scores, request.manifest, clock)
    now = clock.now()
    updated = request.ledger is not None and bool(entries)
    if request.ledger is not None and entries:
        asyncio.run(write_measured_grades(entries, request.ledger, now))
    return EvalReport(
        day=now.date(),
        generated_at=now,
        manifest=request.manifest_name,
        bindings=tuple(
            BindingRow(key=row.key, provider=row.provider, model=row.model)
            for row in slot_bindings(request.manifest)
        ),
        goals=tuple(results),
        slot_scores=scores,
        transcriber=transcriber,
        measured=entries,
        map_updated=updated,
    )


def _grade_transcriber(request: EvalRequest, fixtures: Path) -> TranscriberGrade:
    """Grade the clip set on the TRANSCRIBER slot's resolved chain (or the test's own)."""
    clock = request.runner.clock
    if request.transcriber is not None:
        return asyncio.run(grade_clips(request.transcriber, fixtures, clock))
    registry = build_registry(request.manifest, request.runner.environ, clock)
    work = grade_clips(registry.bound_transcriber(), fixtures, clock)
    return asyncio.run(closing_registry(registry, work))


def _transcriber_score(grade: TranscriberGrade | None) -> SlotScore | None:
    """Wrap a clip-set grade as the TRANSCRIBER slot's score, or None when no clips were run."""
    if grade is None:
        return None
    return SlotScore(slot=ModelSlot.TRANSCRIBER.value, score=grade.score, goals=len(grade.clips))
