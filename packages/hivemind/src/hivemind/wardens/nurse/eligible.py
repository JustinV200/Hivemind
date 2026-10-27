"""Choose the JUDGE models a Nurse Bee may run on for one attempt, strongest-provider-apart first.

ADR-0061: the Nurse runs on `ModelSlot.JUDGE`, but only on a binding at least as strong as the
Worker (the identical model counts, so a Worker on the top model can still be reviewed), whose
call fits the task's remaining spend and the model's context window, preferring a provider the
Worker never used. This module filters the JUDGE binding's fallback chain down to those links and
re-links them into a chain of their own, so `complete_structured` can still fall back, but only
ever onto a model that is itself eligible. An empty result is the "not nursed" case.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by
    `hivemind.wardens.nurse.reviewer.ModelNurseReviewer.review` once per review. Calls into
    `hivemind.forage` (ForageMap, the grades) and `hivemind.llm` (BoundModel) only.

Key invariants:
    - Every link of a returned chain has a known effective grade at or above the Worker's; a
      model the Forage map cannot grade is never eligible, since its strength cannot be proved.
    - The returned grade is the lowest in the chain: the ladder does not say which link answered,
      so the review records the weakest model that might have, never one stronger than it was.
    - Within each group (another provider first, then the Worker's own), the manifest's chain
      order is kept: that order is the operator's preference.
    - An unpriced link (no Forage price) passes the budget check: its cost cannot be estimated,
      and the Fanner still meters whatever it really costs.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the rule.
    - hivemind.wardens.nurse.strength for the Worker grade this compares against.
    - hivemind.llm.slots.BoundModel for the chain shape this rebuilds.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from hivemind.forage import ForageMap
from hivemind.llm import BoundModel
from hivemind.supervision.capping import NurseRequest

TOKENS_PER_MILLION = 1_000_000  # Forage prices are per million tokens.

__all__ = ["CallSize", "NurseChain", "nurse_chain"]


@dataclass(frozen=True, slots=True)
class CallSize:
    """How big the Nurse's one call is, estimated before it is made."""

    input_tokens: int  # The rendered system prompt and user turn, by estimate.
    output_tokens: int  # The call site's reply budget; a binding's own cap replaces it.


@dataclass(frozen=True, slots=True)
class NurseChain:
    """The eligible JUDGE links re-linked as one chain, and the grade a review on it records."""

    bound: BoundModel  # The first eligible link; `.fallback` reaches only eligible links.
    grade: int  # The lowest effective grade in the chain.


def nurse_chain(
    judge: BoundModel, request: NurseRequest, forage: ForageMap, size: CallSize
) -> NurseChain | None:
    """Return the JUDGE links eligible to review `request`, re-linked in preference order.

    Args:
        judge: The resolved `ModelSlot.JUDGE` binding and its fallback chain.
        request: The review request: its Worker grade, providers and spend left.
        forage: The Forage map that grades each link.
        size: The call's estimated token counts, for the context and budget checks.

    Returns:
        The eligible chain and its lowest grade, or None when no link is eligible.
    """
    graded: list[tuple[BoundModel, int]] = []

    # Keep each link that is strong enough, fits its own window and fits the spend left.
    for link in judge.chain():
        source = forage.find(link.provider.name, link.model)
        if source is None or source.effective_grade < request.worker_grade:
            continue
        output = link.max_output_tokens or size.output_tokens
        if size.input_tokens + output > link.context_window:
            continue
        if request.spend_left is not None and _cost(link, size.input_tokens, output) > (
            request.spend_left
        ):
            continue
        graded.append((link, source.effective_grade))
    if not graded:
        return None

    # A provider the Worker never used goes first; the stable sort keeps chain order otherwise.
    graded.sort(key=lambda pair: pair[0].provider.name in request.worker_providers)
    return NurseChain(bound=_relink([link for link, _ in graded]), grade=min(g for _, g in graded))


def _cost(link: BoundModel, input_tokens: int, output_tokens: int) -> float:
    """Return the most `link` could charge for this call; an unpriced side counts as zero."""
    input_price = link.cost_per_million_input_usd or 0.0
    output_price = link.cost_per_million_output_usd or 0.0
    return (input_tokens * input_price + output_tokens * output_price) / TOKENS_PER_MILLION


def _relink(links: list[BoundModel]) -> BoundModel:
    """Chain `links` (never empty) in order through `.fallback`, dropping their old fallbacks."""
    # Built from the tail so each link's fallback is the already-relinked rest of the list.
    chain = dataclasses.replace(links[-1], fallback=None)
    for link in reversed(links[:-1]):
        chain = dataclasses.replace(link, fallback=chain)
    return chain
