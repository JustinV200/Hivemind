"""Define run_cluster_tick and awake_available: the two hooks a running Queen's tick wires in.

`run_cluster_tick` is "the single function the orchestrator wires into queen.py's tick" (this
dispatch's own deliverable): one call that checks cost caps
(`hivemind.queen.cluster.triggers.check_cost_caps`), drains every pending operator order
(`hivemind.queen.cluster.orders.OrderStore.pending`, `hive cluster`/`hive wake`'s own durable
rows, docs/adr/0024), and probes every currently clustered provider's health on its own backoff
schedule (`hivemind.queen.cluster.health.HealthPoller`), clustering or resuming through
`hivemind.queen.cluster.protocol` as each of those decides. `awake_available` is the small, pure
check roadmap step 4.9's own third deliverable asks for: "the Queen's own awake mode being
unavailable is handled by her autopilot running the same protocol" -- when `ModelSlot.QUEEN`'s own
bound provider is itself clustered, an awake episode has nowhere to go, so the orchestrator's own
`_run_awake` (not this dispatch's file to edit) is expected to call this first and skip straight to
`QueenAction.NEEDS_JUDGEMENT`'s autopilot fallback when it returns False.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. `run_cluster_tick` is called once per Queen tick by whichever orchestrator wires
    it in (see this dispatch's own report for the exact `queen.py` lines, since this dispatch may
    not edit that file); `awake_available` is called from `hivemind.queen.queen._run_awake`
    (same caveat). Calls into `hivemind.llm` (HealthState), `hivemind.forage.slots` (ModelSlot),
    `hivemind.queen.cluster.health`, `.orders`, `.protocol`, `.triggers`, `hivemind.queen.deps`
    (QueenDeps, WardenLink), `hivemind.queen.state` (ClusterState) and waggle only.

Key invariants:
    - `run_cluster_tick` never awaits a model: `check_cost_caps`, order handling and
      `HealthPoller.probe` (which calls only `LLMProvider.health()`) are its whole body.
    - Every pending order is marked handled in the same call that acts on it, before the next
      order is read, so a WAKE row is consumed exactly once even if `run_cluster_tick` is called
      again before the store's own next write lands (`OrderStore`'s own contract).
    - `awake_available` returns True (never blocks awake) when `deps.bindings` names no binding
      for `ModelSlot.QUEEN`'s own manifest key: a missing binding is a configuration gap this
      function is not the place to raise on, and Clustering never invents a reason to block awake
      that health data does not actually support.

See Also:
    - .claude/roadmap.md step 4.9 for the deliverable this module's two functions satisfy.
    - hivemind.queen.cluster.protocol for cluster/resume, the effects this module's decisions run.
    - hivemind.queen.cluster.health for HealthPoller, the backoff schedule this module drives.
    - hivemind.queen.cluster.orders for OrderStore/ClusterOrder/OrderKind, the durable rows this
      module drains.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.forage import UnknownSourceError
from hivemind.forage.slots import ModelSlot
from hivemind.llm import HealthState
from hivemind.queen.cluster.orders import OrderKind
from hivemind.queen.cluster.protocol import cluster, resume
from hivemind.queen.cluster.triggers import check_cost_caps
from hivemind.queen.state import ClusterState

if TYPE_CHECKING:
    # Only for the type hints below: see hivemind.queen.cluster.protocol's own TYPE_CHECKING
    # block for why a real import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["awake_available", "run_cluster_tick"]


async def run_cluster_tick(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> None:
    """Check cost caps, drain pending operator orders, and probe clustered providers' health.

    Args:
        deps: The Queen's collaborators; `orders`, `health_poller`, `provider_lookup`, `ledger`
            and `map` are what this reads and acts through.
        state: The Queen's own mode and clustered-provider set; mutated by every `cluster`/
            `resume` this call makes.
        wardens: Every Warden currently attached (see `hivemind.queen.cluster.protocol.cluster`'s
            own docstring for why this is a parameter here too, not a `deps` field).
    """
    await check_cost_caps(deps, state, wardens)
    await _drain_orders(deps, state, wardens)
    await _probe_clustered(deps, state, wardens)


def awake_available(state: ClusterState, deps: QueenDeps) -> bool:
    """Return whether the Queen's own awake mode can run right now.

    Pure: reads `state`/`deps.bindings` only, never awaits anything (module docstring).

    Args:
        state: The Queen's own mode and clustered-provider set.
        deps: The Queen's collaborators; `bindings` names `ModelSlot.QUEEN`'s own provider.

    Returns:
        False exactly when `ModelSlot.QUEEN`'s bound `[llm.slots]` provider is itself in
        `state.clustered_providers`; True otherwise (including when no such binding is known at
        all, module docstring's own invariant).
    """
    queen_key = ModelSlot.QUEEN.manifest_key
    binding = next((b for b in deps.bindings if b.key == queen_key), None)
    if binding is None:
        return True  # No known binding for the Queen's own slot; never block on that alone.
    return binding.provider not in state.clustered_providers


async def _drain_orders(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> None:
    """Act on every pending ClusterOrder once, then mark each handled."""
    for order in await deps.orders.pending():
        providers = _providers_for_order(order.kind, order.provider, deps, state)
        for provider in providers:
            if order.kind is OrderKind.CLUSTER:
                await cluster(provider, "operator", deps, state, wardens)
            else:
                await resume(provider, deps, state, wardens)
                deps.health_poller.reset(provider)
        await deps.orders.mark_handled(order.id, deps.clock.now())


def _providers_for_order(
    kind: OrderKind, named: str | None, deps: QueenDeps, state: ClusterState
) -> tuple[str, ...]:
    """Resolve one order's own `provider` field to the providers it actually names.

    A named provider is used as-is; an unnamed CLUSTER order names every provider currently in
    play (every live grant's own allowed bindings' providers); an unnamed WAKE order names every
    currently clustered provider.
    """
    if named is not None:
        return (named,)
    if kind is OrderKind.CLUSTER:
        return _bound_providers(deps)
    return tuple(state.clustered_providers)


def _bound_providers(deps: QueenDeps) -> tuple[str, ...]:
    """Return every provider name at least one live grant's allowed bindings currently name."""
    seen: list[str] = []
    for grant in deps.ledger.live_grants():
        for binding in grant.allowed:
            try:
                source = deps.map.get(binding.source_id)
            except UnknownSourceError:
                continue
            if source.spec.provider not in seen:
                seen.append(source.spec.provider)
    return tuple(seen)


async def _probe_clustered(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> None:
    """Probe every clustered provider due for a probe; resume any that reads HEALTHY."""
    if deps.provider_lookup is None:
        return  # No way to reach a live LLMProvider by name; nothing this tick can probe.
    now = deps.clock.now()
    for provider in tuple(state.clustered_providers):
        if not deps.health_poller.is_due(provider, now):
            continue
        # External await: a health probe, never a completion call (module docstring); the
        # provider's own health() contract carries no fixed timeout of its own to name here.
        reading = await deps.health_poller.probe(provider, deps.provider_lookup, now)
        if reading.state is HealthState.HEALTHY:
            await resume(provider, deps, state, wardens)
