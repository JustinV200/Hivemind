"""Turn a run's results into per-slot scores, then into measured grades for the Forage map.

Roadmap step 8.5: with `--update-map` the harness replaces the manifest's hand-set `grade` (1 to 5)
of each model source with one measured from the Hive's own goals, so routing's grade floors rest on
evidence (ADR-0045). A **slot** (a named place a model call goes, such as `QUEEN` or `WORKER`) is
scored from the columns that judge that slot's work: the QUEEN by whether her plans were valid and
declared sound `TaskNeeds`; the WORKER by whether the work landed, how often its output was
well-formed, and whether it answered and resumed when asked; the TRANSCRIBER by word error rate.
A slot is scored only when it actually made calls during the run. The score is in 0 to 1, and one
documented rule maps it to a grade.

**The mapping.** `grade = min(5, 1 + floor(score * 5))`: a score in [0.0, 0.2) is grade 1, [0.2,
0.4) is 2, [0.4, 0.6) is 3, [0.6, 0.8) is 4, and [0.8, 1.0] is 5. Even a score of zero is grade 1,
never "ungraded", because a source that was run and failed everything is the weakest that exists.
A slot's grade goes to the source its primary binding names (`ForageMap.for_slot`); a source that
serves several scored slots takes the lowest of their grades, since a floor promises what a source
can be relied on for.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Called by the
    evaluation pipeline after grading; its output is written through the Queen's ledger store
    (`LedgerStore.put_measured_grade`). Calls into `hivemind.cli.stores` (the manifest
    conversions), `hivemind.forage`, `hivemind.queen` (the ledger store) and this package's
    grades only.

Key invariants:
    - `grade_for_score` is total over any float: out-of-range scores clamp, NaN grades 1.
    - A slot with no calls in any result is never scored, so it is never graded on silence.
    - Nothing is written unless the caller asks (`--update-map`); scoring itself is pure.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md.
    - hivemind.cli.stores.build_forage_map for where the written grades are applied.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.llm.eval.grades import GoalResult
from hivemind.cli.stores import build_forage_map, slot_bindings
from hivemind.forage import ModelSlot
from hivemind.manifest import HiveManifest
from hivemind.queen.forage.ledger import LedgerStore
from waggle.clock import Clock

GRADE_BANDS = 5  # The grade scale is 1 to 5, so a score splits into five equal bands.
MIN_GRADE = 1  # Even a total failure is grade 1: it was measured, and it is the weakest there is.
MAX_GRADE = 5

# The mapping above, one line per grade, for the report's footer so a reader need not open this
# file.
BAND_LINES: tuple[str, ...] = (
    "grade 1: score below 0.2",
    "grade 2: 0.2 up to 0.4",
    "grade 3: 0.4 up to 0.6",
    "grade 4: 0.6 up to 0.8",
    "grade 5: 0.8 and above",
)

__all__ = [
    "BAND_LINES",
    "MeasuredEntry",
    "SlotScore",
    "assign_grades",
    "grade_for_score",
    "score_slots",
    "write_measured_grades",
]


class SlotScore(BaseModel):
    """One slot's score from a run: a number in 0 to 1 and how many goals it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str = Field(description="The ModelSlot name, e.g. 'QUEEN'.")
    score: float = Field(ge=0, le=1, description="The slot's mean score over its goals.")
    goals: int = Field(ge=0, description="How many goals (or clips) the score is the mean of.")


class MeasuredEntry(BaseModel):
    """A grade the harness measured for one Forage map source, ready to write to the ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(description="The `[forage.map.<source_id>]` key being graded.")
    slots: tuple[str, ...] = Field(description="The scored slots whose primary binding is it.")
    score: float = Field(ge=0, le=1, description="The lowest of those slots' scores.")
    grade: int = Field(ge=1, le=5, description="The measured grade, from `grade_for_score`.")
    evidence: str = Field(description="A one-line summary of the scores behind the grade.")


def grade_for_score(score: float) -> int:
    """Map a 0 to 1 score to a grade 1 to 5: `min(5, 1 + floor(score * 5))`.

    Args:
        score: The score; clamped to 0 to 1 first, NaN counts as 0.

    Returns:
        1 for [0.0, 0.2), 2 for [0.2, 0.4), 3 for [0.4, 0.6), 4 for [0.6, 0.8), 5 for [0.8, 1.0].
    """
    if math.isnan(score):
        return MIN_GRADE
    clamped = min(1.0, max(0.0, score))
    return min(MAX_GRADE, MIN_GRADE + math.floor(clamped * GRADE_BANDS))


def score_slots(
    results: Sequence[GoalResult], transcriber: SlotScore | None = None
) -> tuple[SlotScore, ...]:
    """Score every slot the run exercised.

    Args:
        results: Every graded goal of the run.
        transcriber: The TRANSCRIBER slot's score from the clip set, when `--fixtures` was given.

    Returns:
        One score per exercised, scorable slot, QUEEN first; empty when nothing qualified.
    """
    scores: list[SlotScore] = []
    for slot, per_goal in (
        (ModelSlot.QUEEN, _queen_score),
        (ModelSlot.WORKER, _worker_score),
    ):
        # Only goals in which the slot actually made a call say anything about it.
        used = [result for result in results if result.slot_calls.get(slot.value, 0) > 0]
        if used:
            mean = sum(per_goal(result) for result in used) / len(used)
            scores.append(SlotScore(slot=slot.value, score=mean, goals=len(used)))
    if transcriber is not None:
        scores.append(transcriber)
    return tuple(scores)


def assign_grades(
    scores: Sequence[SlotScore], manifest: HiveManifest, clock: Clock
) -> tuple[MeasuredEntry, ...]:
    """Attribute slot scores to the Forage map sources their bindings name, and grade them.

    Args:
        scores: The scored slots.
        manifest: The manifest whose `[llm.slots]` and `[forage.map]` decide who is graded.
        clock: Passed to the throwaway map built to resolve slots.

    Returns:
        One entry per source, ordered by source id. A slot whose binding names no source on the
        map is left out: there is nothing to write a grade onto.
    """
    forage_map = build_forage_map(manifest, clock)
    bindings = slot_bindings(manifest)
    by_source: dict[str, list[SlotScore]] = {}
    for score in scores:
        source = forage_map.for_slot(ModelSlot(score.slot), bindings)
        if source is not None:
            by_source.setdefault(source.source_id, []).append(score)
    return tuple(_entry(source_id, grouped) for source_id, grouped in sorted(by_source.items()))


async def write_measured_grades(
    entries: Sequence[MeasuredEntry], store: LedgerStore, measured_at: datetime
) -> int:
    """Write each entry through the Queen's ledger store, replacing earlier measurements.

    Args:
        entries: The grades to persist.
        store: The ledger store of the Hive whose map should be graded.
        measured_at: When the run happened (timezone-aware).

    Returns:
        How many grades were written.
    """
    for entry in entries:
        await store.put_measured_grade(entry.source_id, entry.grade, measured_at, entry.evidence)
    return len(entries)


def _entry(source_id: str, scores: Sequence[SlotScore]) -> MeasuredEntry:
    """Build one source's entry: the lowest slot score decides, every score is the evidence."""
    lowest = min(score.score for score in scores)
    evidence = ", ".join(f"{s.slot} {s.score:.2f} over {s.goals}" for s in scores)
    return MeasuredEntry(
        source_id=source_id,
        slots=tuple(score.slot for score in scores),
        score=lowest,
        grade=grade_for_score(lowest),
        evidence=f"hive llm eval: {evidence}",
    )


def _queen_score(result: GoalResult) -> float:
    """Score the QUEEN on one goal: a valid plan, and tasks that declare sound needs."""
    return (float(result.decomposition.passed) + result.needs.score) / 2


def _worker_score(result: GoalResult) -> float:
    """Score the WORKER on one goal: the work landed, output was well-formed, asks and resumes held.

    Each component that applies to the goal counts equally, so a goal with no question or
    checkpoint is not padded with free points.
    """
    components = [float(result.succeeded and result.effects.passed)]
    if result.tool_calls.validity_rate is not None:
        components.append(result.tool_calls.validity_rate)
    for verdict in (result.question.passed, result.handoff.passed):
        if verdict is not None:
            components.append(float(verdict))
    return sum(components) / len(components)
