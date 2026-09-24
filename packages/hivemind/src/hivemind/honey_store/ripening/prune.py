"""Drop a superseded embedding model's vectors, only on the operator's own word (ADR-0033).

ADR-0032 keeps every embedding model's vectors beside the others' so switching back needs no
second re-embed, and nothing drops them automatically. `prune_vectors` is the one place that
changes: `hive honey reembed --prune` (a later dispatch) calls it after draining the re-embedding
backlog for the bound `EMBEDDER`, and it deletes every other model's vectors, but only when every
live Honey row already has one for the kept model -- otherwise it refuses and changes nothing.
This module holds no pipeline stage of its own: it is a thin wrapper around `HoneyStore.
prune_vectors`, which does the atomic check-and-delete, kept beside `embed.py` because both are
about the same table, `honey_vectors`, from the two opposite directions (filling it in, versus
clearing it out).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hive honey reembed --prune` (a later dispatch), never by the House Bee's own timer:
    pruning is on request only, never automatic (ADR-0033). Calls into `hivemind.honey_store`
    (identity, store) and `hivemind.pheromone` (HoneyEvent) only.

Key invariants:
    - `prune_vectors` never asks the caller to check coverage itself: the store's own
      `prune_vectors` does the check, the deletion and the event in one transaction, so nothing
      can slip a new gap in between this function's call and the store's own read.
    - A refusal (`PruneOutcome.refused`) writes nothing to the trail: mirrors `add_nectar`'s own
      rule that an outcome with nothing to record leaves no event at all.

See Also:
    - docs/adr/0033-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for the
      prune-on-request rule this module implements.
    - hivemind.honey_store.ripening.embed for embed_pending_rows, the opposite direction over the
      same table.
    - hivemind.honey_store.store.protocol for HoneyStore.prune_vectors, PruneResult, PruneEvents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.store import PruneResult
from hivemind.pheromone import HoneyEvent

VECTORS_PRUNED_KIND = "honey.vectors_pruned"  # The one event this module ever records.

__all__ = ["VECTORS_PRUNED_KIND", "PruneOutcome", "prune_vectors"]


@dataclass(frozen=True, slots=True)
class PruneOutcome:
    """What one `prune_vectors` call did: pruned counts per model, or the refusal's own count."""

    kept_model: str  # The embedding model every live row was checked against.
    dropped: dict[str, int] = field(default_factory=dict)  # Model -> vector rows removed.
    missing: int = 0  # Live rows still lacking a vector for kept_model; >0 means refused.

    @property
    def refused(self) -> bool:
        """Whether the store refused to prune because some live row still lacks a vector."""
        return self.missing > 0


async def prune_vectors(deps: RipenerDeps, kept_model: str) -> PruneOutcome:
    """Drop every embedding model's vectors but `kept_model`'s, if every live row has one.

    The caller (`hive honey reembed --prune`) is expected to have already drained the
    re-embedding backlog for the bound `EMBEDDER` first, so a refusal here means backlog remains;
    this function makes no attempt to embed anything itself.

    Args:
        deps: The store, identity and clock; no ripener or embedder binding is needed to prune.
        kept_model: The embedding model to keep every vector of.

    Returns:
        The dropped counts per model on success, or the refusal's own `missing` count
        (`PruneOutcome.refused`); either way `kept_model`'s own vectors are never touched.
    """

    def events(result: PruneResult) -> tuple[HoneyEvent, ...]:
        # Refused: nothing changed, so nothing is recorded (mirrors add_nectar's Night Veil case).
        if result.missing > 0:
            return ()
        return (_pruned_event(deps, result),)

    # Local SQLite, bounded by the connection's busy timeout: one transaction for the coverage
    # check, the deletion of every other model's vectors and the event below.
    result = await deps.store.prune_vectors(kept_model, events)
    return PruneOutcome(
        kept_model=result.kept_model, dropped=result.dropped, missing=result.missing
    )


def _pruned_event(deps: RipenerDeps, result: PruneResult) -> HoneyEvent:
    """Build the one `honey.vectors_pruned` event a successful prune records.

    The payload stays flat and scalar like every other honey.* event (codingrules section 12):
    the kept model, how many other models were dropped, and their combined row count. The exact
    per-model breakdown a caller wants to show the operator is `PruneOutcome.dropped` itself,
    returned above; it is not repeated here so the trail vocabulary never grows a dynamic shape.
    """
    return honey_event(
        deps.identity,
        deps.clock,
        VECTORS_PRUNED_KIND,
        deps.identity.hive_id,
        kept_model=result.kept_model,
        dropped_models=len(result.dropped),
        dropped_rows=sum(result.dropped.values()),
    )
