"""Build one Hive's Honey Store handles from its manifest, registry and Fanner.

The Honey Store (the Hive's cold tier of knowledge, roadmap phase 7) needs three model bindings:
the `EMBEDDER` slot for vectors, the `RIPENER` slot for summaries and, for judge-reviewed label
lowering (ADR-0034), the `JUDGE` slot for the clearance judge that decides whether a Real Cell
deposit may carry a lower label. Any of them can be unusable on a given Hive -- Anthropic serves
no embeddings, a local server may be down at startup, `[llm] offline` refuses a hosted provider --
and ADR-0032 says the store then degrades rather than fails: no embedder means full-text search
only, no ripener means heuristic summaries, and no judge means every lowering proposal waits for
the human (`hive honey review`). `build_honey_access` resolves all three once, logs why any is
missing, and builds intake, the retriever, the Ripener and the clearance judge over one store and
one `system` identity, every model call on a Fanner lane so it is metered like any other
(`llm.call` with slot `EMBEDDER`, `RIPENER` or `JUDGE`). `hive run`'s composition root and the
`hive honey` commands both call it, so a Hive and its maintenance commands never build the Honey
Store two different ways.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's composition root. Called by
    `hivemind.cli.compose.hive` and `hivemind.cli.honey`. Calls into `hivemind.honey_store`,
    `hivemind.llm` (the registry and Fanner), `hivemind.forage` (slots and tempo),
    `hivemind.cell` (the clearance enum) and `hivemind.manifest` only.

Key invariants:
    - Never raises for a missing or broken binding: an unusable `EMBEDDER`, `RIPENER` or `JUDGE`
      becomes None, logged once with its reason, and the store works without it (ADR-0032).
    - `[honey.lowering] enabled = false` builds no judge at all (the JUDGE slot is not even
      resolved), so no clearance judge is ever called on that Hive (ADR-0034).
    - Ripening calls run on a LOW-accuracy lane so a background summary never queues ahead of a
      bee's own call for a seat; a query's own embedding and the clearance judge's verdicts run
      on ordinary lanes, because a judge's call is a decision, not batch work.

See Also:
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for "degrade, never fail closed".
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the clearance judge.
    - hivemind.honey_store.access for HoneyAccess, what this builds.
    - hivemind.cli.stores.open_honey_store for the store this is handed.
"""

from __future__ import annotations

from dataclasses import replace

from hivemind.cell import HoneyClearance
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.forage import AccuracyBar, ModelSlot, Tempo
from hivemind.honey_store import (
    ClearanceJudge,
    HoneyIdentity,
    HoneyRetriever,
    HoneyStore,
    ModelClearanceJudge,
    NectarIntake,
    RetrieverDeps,
    Ripener,
    RipenerDeps,
)
from hivemind.honey_store.access import HoneyAccess
from hivemind.llm import BoundEmbedder, BoundModel, Fanner, ProviderRegistry
from hivemind.manifest import HiveManifest, HoneyLoweringSection
from waggle.clock import Clock

HONEY_ACTOR = "system"  # The Honey Store's own writes are the Hive's, never a bee's or the human's.
# Why a Hive with lowering switched off has no judge: the reason logged, and printed by the CLI.
JUDGE_DISABLED = "[honey.lowering] enabled = false"

__all__ = [
    "HONEY_ACTOR",
    "JUDGE_DISABLED",
    "build_honey_access",
    "resolve_embedder",
    "resolve_judge",
    "resolve_ripener",
]

log = get_logger(__name__)


def build_honey_access(
    manifest: HiveManifest,
    store: HoneyStore,
    registry: ProviderRegistry,
    fanner: Fanner,
    clock: Clock,
) -> HoneyAccess:
    """Build intake, the retriever, the Ripener and the judge over `store`, bindings degraded.

    Args:
        manifest: The Hive's loaded manifest; its `[honey]` sections and `[hive]` identity.
        store: The Hive's own Honey Store (`hivemind.cli.stores.open_honey_store`).
        registry: Resolves the `EMBEDDER`, `RIPENER` and `JUDGE` slots.
        fanner: Meters every embedding, summary and judge call on its own lanes.
        clock: Injected time source for every event and id the handles mint.

    Returns:
        A HoneyAccess whose embedder, ripener or clearance judge may be missing, never an error
        for any of them.
    """
    honey = manifest.honey
    identity = HoneyIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=HONEY_ACTOR
    )
    embedder = resolve_embedder(registry)
    default_label = HoneyClearance.from_wire(honey.clearance.default_label)
    # A query's own embedding is on the asker's critical path: an ordinary lane.
    query_lane = fanner.lane(Tempo())
    retriever_deps = RetrieverDeps(store, identity, clock, honey.retrieval, embedder, query_lane)
    return HoneyAccess(
        store=store,
        intake=NectarIntake(store, identity, clock, honey.store, default_label),
        retriever=HoneyRetriever(retriever_deps),
        ripener=_build_ripener(
            RipenerDeps(store, identity, clock, honey.ripening, embedder=embedder),
            registry,
            fanner,
        ),
        identity=identity,
        retrieval=honey.retrieval,
        ripening=honey.ripening,
        clearance=honey.clearance,
        lowering=honey.lowering,
        judge=_build_judge(registry, fanner, honey.lowering),
    )


def resolve_embedder(registry: ProviderRegistry) -> BoundEmbedder | None:
    """Resolve the `EMBEDDER` slot, or None (logged once with why) when it cannot embed.

    Args:
        registry: The Hive's provider registry.

    Returns:
        The bound embedder; None when the slot's provider has no embedding endpoint, is
        unknown, is refused by `[llm] offline`, or cannot be built from its configuration.
    """
    try:
        return registry.embedder(ModelSlot.EMBEDDER)
    except (HiveMindError, ValueError) as exc:
        # ADR-0032: degrade, never fail closed. ValueError covers a provider config the adapter
        # itself refuses at construction (pydantic's ValidationError is one).
        log.warning("honey.embedder_unavailable", reason=_reason(exc))
        return None


def resolve_ripener(registry: ProviderRegistry) -> BoundModel | None:
    """Resolve the `RIPENER` slot, or None (logged once with why): summaries turn heuristic.

    Args:
        registry: The Hive's provider registry.

    Returns:
        The bound ripener model; None when its binding cannot be resolved or built.
    """
    try:
        return registry.bound(ModelSlot.RIPENER)
    except (HiveMindError, ValueError) as exc:
        # The Ripener falls back to heuristic summaries; ripening itself never waits on a model.
        log.warning("honey.ripener_unavailable", reason=_reason(exc))
        return None


def resolve_judge(registry: ProviderRegistry, lowering: HoneyLoweringSection) -> BoundModel | None:
    """Resolve the `JUDGE` slot for label lowering, or None (logged once with why).

    Args:
        registry: The Hive's provider registry.
        lowering: `[honey.lowering]`; `enabled = false` means no judge is ever called.

    Returns:
        The bound judge model; None when review is switched off, or when the binding cannot be
        resolved or built. Either way every lowering proposal then waits for the human.
    """
    # The operator's own word: the slot is not even resolved, so no judge can ever be called.
    if not lowering.enabled:
        log.info("honey.judge_unavailable", reason=JUDGE_DISABLED)
        return None
    try:
        return registry.bound(ModelSlot.JUDGE)
    except (HiveMindError, ValueError) as exc:
        # ADR-0034: with no judge every proposal waits in `hive honey review`; nothing fails.
        log.warning("honey.judge_unavailable", reason=_reason(exc))
        return None


def _build_ripener(base: RipenerDeps, registry: ProviderRegistry, fanner: Fanner) -> Ripener:
    """Give `base` its RIPENER binding and a background lane for every summary and embed call."""
    # Background work: a LOW-accuracy lane never queues ahead of a bee's own call for a seat.
    lane = fanner.lane(Tempo(accuracy=AccuracyBar.LOW))
    return Ripener(
        replace(base, ripener=resolve_ripener(registry), call_gate=lane, embed_gate=lane)
    )


def _build_judge(
    registry: ProviderRegistry, fanner: Fanner, lowering: HoneyLoweringSection
) -> ClearanceJudge | None:
    """Build the model-backed clearance judge on an ordinary lane, or None when there is none."""
    bound = resolve_judge(registry, lowering)
    if bound is None:
        return None
    # A verdict is a safety decision, not batch work: an ordinary (NORMAL-accuracy) lane, since a
    # LOW one would also lower routing's grade floor for the very model that clears a label.
    return ModelClearanceJudge(bound, fanner.lane(Tempo()))


def _reason(exc: Exception) -> str:
    """Name a binding failure by its stable code when it has one, else by its class."""
    code = getattr(exc, "code", None)
    return f"{code}: {exc}" if isinstance(code, str) else f"{type(exc).__name__}: {exc}"
