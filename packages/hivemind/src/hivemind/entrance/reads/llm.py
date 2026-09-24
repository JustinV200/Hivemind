"""Build the LLM view: every provider and how the Queen judges it, and every slot binding.

The Queen already keeps what the LLM view shows (ADR-0032: read it, never ask her): her health
poller counts each provider's consecutive DOWN readings, and her Clustering state names the
providers whose bees she paused because one went down with no fallback (roadmap 4.9). A provider
is judged DOWN while it is clustered, DEGRADED while the poller has a failure on record, and
HEALTHY otherwise; no provider is probed to answer the read, so a busy dashboard can never become
load on a model server. Each ``[llm.slots]`` row is shown with the slot it serves (its own, or the
one whose fallback chain reaches it), its provider, model, fallback and effort.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads``. Called by
    ``hivemind.entrance.routes.hive.llm``. Calls into the forage slot helpers, the Queen's
    Clustering state and health poller through ``LlmReads``, and the view models.

Key invariants:
    - Answering never calls a provider: only the Queen's own bookkeeping is read.
    - No API key, key variable or base URL reaches the view.

See Also:
    - hivemind.queen.cluster.health for the poller.
"""

from __future__ import annotations

from hivemind.entrance.gate.reads import LlmReads
from hivemind.entrance.models.views import LlmView, ProviderHealthView, ProviderView, SlotView
from hivemind.forage import slot_for_binding

__all__ = ["llm_view"]


def llm_view(llm: LlmReads) -> LlmView:
    """Build the LLM view from the Queen's own bookkeeping.

    Args:
        llm: The providers, bindings, Clustering state and health poller.

    Returns:
        The view: the Queen's mode, every provider, every slot binding.
    """
    clustered = llm.cluster.clustered_providers
    providers = [
        ProviderView(
            name=name,
            kind=kind,
            health=_health(name in clustered, llm.health.failed_probes(name)),
            clustered=name in clustered,
            failed_probes=llm.health.failed_probes(name),
        )
        for name, kind in llm.providers.items()
    ]
    slots = [
        SlotView(
            key=binding.key,
            serves=slot_for_binding(binding.key, llm.bindings),
            provider=binding.provider,
            model=binding.model,
            fallback=binding.fallback,
            effort=binding.effort,
        )
        for binding in llm.bindings
    ]
    return LlmView(queen_mode=llm.cluster.mode, providers=providers, slots=slots)


def _health(clustered: bool, failed_probes: int) -> ProviderHealthView:
    """Judge a provider from the Queen's bookkeeping alone (module docstring)."""
    if clustered:
        return ProviderHealthView.DOWN
    return ProviderHealthView.DEGRADED if failed_probes > 0 else ProviderHealthView.HEALTHY
