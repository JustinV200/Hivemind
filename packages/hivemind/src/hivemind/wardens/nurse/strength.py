"""Measure how strong a Worker was: the grade and providers a Nurse Bee is held against.

ADR-0061 asks for a Nurse at least as strong as the Worker it reviews. "The Worker" means every
model the attempt could have reached, not just the one it was bound to first: a Fanner spill
(`hivemind.llm.fanner`) or a rebind moves a call to another model mid-attempt, so the grade is
the highest over the Worker's bindings (each one's whole fallback chain) and over the providers
its `llm.call` trail events actually reached. The providers feed the Nurse's preference for a
model the Worker never used, so blind spots do not correlate.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by the Warden's
    accept path when it builds a `NurseRequest` (a later step). Calls into `hivemind.forage`
    (ForageMap, the grades), `hivemind.llm` (BoundModel), `hivemind.pheromone` (LlmEvent) and
    `hivemind.supervision.capping.checks.nurse` (the provider bound) only.

Key invariants:
    - The grade never understates the Worker: a model the Forage map cannot grade makes it
      MAX_MODEL_GRADE, so only a top-grade Nurse can review work no grade can be proved for.
    - An `llm.call` event names a provider, never a model id (the trail keeps model ids off it),
      so an event counts as the strongest model the map lists on that provider.
    - Providers come out de-duplicated, in first-seen order, at most MAX_NURSE_PROVIDERS.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.wardens.nurse.eligible for the other side of the comparison.
    - hivemind.llm.fanner.lane for the llm.call payload this reads.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from hivemind.forage import ForageMap
from hivemind.llm import BoundModel
from hivemind.pheromone import LlmEvent
from hivemind.supervision.capping.checks import MAX_NURSE_PROVIDERS
from waggle.messages.forage.values import MAX_MODEL_GRADE

LLM_CALL_KIND = "llm.call"  # The one LlmEvent kind that means a model really answered a call.

__all__ = ["WorkerStrength", "worker_strength"]


@dataclass(frozen=True, slots=True)
class WorkerStrength:
    """The two NurseRequest facts about a Worker's models: its grade and the providers it used."""

    grade: int  # The highest effective grade it could have reached; MAX_MODEL_GRADE when unknown.
    providers: tuple[str, ...]  # Manifest provider keys, first-seen order, de-duplicated.


def worker_strength(
    bindings: Iterable[BoundModel], calls: Iterable[LlmEvent], forage: ForageMap
) -> WorkerStrength:
    """Return the Worker's strength from its bindings and the llm.call events it made.

    Args:
        bindings: The Worker's bound models; each one's whole fallback chain counts.
        calls: Trail events from the Worker's calls; kinds other than llm.call are ignored.
        forage: The Forage map that grades each model.

    Returns:
        The highest grade any of them reached, or MAX_MODEL_GRADE when one has no grade, plus
        the providers they ran on.
    """
    grades: list[int | None] = []
    providers: list[str] = []

    # Every link of every chain: a spill along the chain is a model the Worker may have used.
    for bound in bindings:
        for link in bound.chain():
            source = forage.find(link.provider.name, link.model)
            grades.append(source.effective_grade if source is not None else None)
            providers.append(link.provider.name)

    # Every provider a real call reached, which catches a rebind outside the chains above.
    for event in calls:
        if event.kind != LLM_CALL_KIND or event.provider is None:
            continue
        grades.append(_provider_grade(forage, event.provider))
        providers.append(event.provider)

    # No grades at all, or any unknown one, means strength cannot be proved: demand the top.
    known = [grade for grade in grades if grade is not None]
    grade = MAX_MODEL_GRADE if not known or len(known) < len(grades) else max(known)
    return WorkerStrength(grade=grade, providers=_dedupe(providers)[:MAX_NURSE_PROVIDERS])


def _provider_grade(forage: ForageMap, provider: str) -> int | None:
    """Return the strongest grade the map lists on `provider`, or None when it lists none."""
    grades = [s.effective_grade for s in forage.sources() if s.spec.provider == provider]
    return max(grades, default=None)


def _dedupe(items: list[str]) -> tuple[str, ...]:
    """Return `items` without repeats, keeping each one's first position."""
    return tuple(dict.fromkeys(items))
