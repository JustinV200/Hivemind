"""Build one Hive's Honey Store handles from its manifest, registry and Fanner.

The Honey Store (the Hive's cold tier of knowledge, roadmap phase 7) needs two model bindings: the
`EMBEDDER` slot for vectors and the `RIPENER` slot for summaries. Either can be unusable on a given
Hive -- Anthropic serves no embeddings, a local server may be down at startup, `[llm] offline`
refuses a hosted provider -- and ADR-0032 says the store then degrades rather than fails: no
embedder means full-text search only, no ripener means heuristic summaries. `build_honey_access`
resolves both once, logs why either is missing, and builds intake, the retriever and the Ripener
over one store and one `system` identity, every model call on a Fanner lane so it is metered like
any other (`llm.call` with slot `EMBEDDER` or `RIPENER`). `hive run`'s composition root and the
`hive honey` commands both call it, so a Hive and its maintenance commands never build the Honey
Store two different ways.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's composition root. Called by
    `hivemind.cli.compose.hive` and `hivemind.cli.honey`. Calls into `hivemind.honey_store`,
    `hivemind.llm` (the registry and Fanner), `hivemind.forage` (slots and tempo),
    `hivemind.cell` (the clearance enum) and `hivemind.manifest` only.

Key invariants:
    - Never raises for a missing or broken binding: an unusable `EMBEDDER` or `RIPENER` becomes
      None, logged once with its reason, and the store works without it (ADR-0032).
    - Ripening calls run on a LOW-accuracy lane so a background summary never queues ahead of a
      bee's own call for a seat; a query's own embedding runs on an ordinary lane.

See Also:
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for "degrade, never fail closed".
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
    HoneyIdentity,
    HoneyRetriever,
    HoneyStore,
    NectarIntake,
    RetrieverDeps,
    Ripener,
    RipenerDeps,
)
from hivemind.honey_store.access import HoneyAccess
from hivemind.llm import BoundEmbedder, BoundModel, Fanner, ProviderRegistry
from hivemind.manifest import HiveManifest
from waggle.clock import Clock

HONEY_ACTOR = "system"  # The Honey Store's own writes are the Hive's, never a bee's or the human's.

__all__ = ["HONEY_ACTOR", "build_honey_access", "resolve_embedder", "resolve_ripener"]

log = get_logger(__name__)


def build_honey_access(
    manifest: HiveManifest,
    store: HoneyStore,
    registry: ProviderRegistry,
    fanner: Fanner,
    clock: Clock,
) -> HoneyAccess:
    """Build intake, the retriever and the Ripener over `store`, bindings resolved or degraded.

    Args:
        manifest: The Hive's loaded manifest; its `[honey]` sections and `[hive]` identity.
        store: The Hive's own Honey Store (`hivemind.cli.stores.open_honey_store`).
        registry: Resolves the `EMBEDDER` and `RIPENER` slots.
        fanner: Meters every embedding and summary call on its own lanes.
        clock: Injected time source for every event and id the handles mint.

    Returns:
        A HoneyAccess whose embedder or ripener may be missing, never an error for either.
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
    )


def _build_ripener(base: RipenerDeps, registry: ProviderRegistry, fanner: Fanner) -> Ripener:
    """Give `base` its RIPENER binding and a background lane for every summary and embed call."""
    # Background work: a LOW-accuracy lane never queues ahead of a bee's own call for a seat.
    lane = fanner.lane(Tempo(accuracy=AccuracyBar.LOW))
    return Ripener(
        replace(base, ripener=resolve_ripener(registry), call_gate=lane, embed_gate=lane)
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


def _reason(exc: Exception) -> str:
    """Name a binding failure by its stable code when it has one, else by its class."""
    code = getattr(exc, "code", None)
    return f"{code}: {exc}" if isinstance(code, str) else f"{type(exc).__name__}: {exc}"
