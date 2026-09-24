"""Define check_cost_caps: cluster the provider a goal is spending on once its cap hits zero.

Roadmap step 4.9's own trigger list: "triggered per provider by ProviderHealth failing with no
fallback within Forage, by a cost cap, or by `hive cluster`". `hivemind.queen.cluster.health`
covers the first; `hivemind.queen.cluster.orders` covers the third; this module is the second, and
deliberately small (this dispatch's own instruction): for every live grant whose goal has spent
its cap (`deps.ledger.spend.headroom`, roadmap step 4.8), cluster every provider that grant is
allowed to spend on, once each, through the same `hivemind.queen.cluster.protocol.cluster` every
other trigger calls.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Called by `hivemind.queen.cluster.tick.run_cluster_tick`, once per tick, ahead of
    the order and health checks. Calls into `hivemind.forage` (UnknownSourceError),
    `hivemind.queen.cluster.protocol` (cluster), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.intake` (goal_spend_cap: a requested goal's own budget, roadmap step 10.5),
    `hivemind.queen.state` (ClusterState) and waggle only.

Key invariants:
    - A provider already in `state.clustered_providers` is skipped without a second `cluster`
      call for it this pass (`cluster` is itself idempotent per provider, but skipping first
      avoids the wasted ledger/chamber reads).
    - Never awaits a model: only `deps.chamber`, `deps.ledger` and `deps.map` are read, matching
      `hivemind.queen.cluster.protocol.cluster`'s own invariant.

See Also:
    - .claude/roadmap.md step 4.9 for "triggered... by a cost cap".
    - .claude/roadmap.md step 4.8 for SpendBook.headroom, the figure this module checks.
    - hivemind.queen.cluster.protocol for cluster, this module's one effectful call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.forage import ForageGrant, UnknownSourceError
from hivemind.llm import HealthState
from hivemind.queen.cluster.protocol import ClusterOutcome, cluster
from hivemind.queen.intake import goal_spend_cap
from hivemind.queen.state import ClusterState

if TYPE_CHECKING:
    # Only for the type hints below: see hivemind.queen.cluster.protocol's own TYPE_CHECKING
    # block for why a real import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["check_cost_caps", "cluster_if_down", "every_bee_has_a_fallback", "providers_of"]


async def check_cost_caps(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> tuple[ClusterOutcome, ...]:
    """Cluster every provider whose current spend, on a goal that has hit its cap, is live.

    Args:
        deps: The Queen's collaborators.
        state: The Queen's own mode and clustered-provider set; mutated by any `cluster` call
            this makes.
        wardens: Every Warden currently attached.

    Returns:
        One ClusterOutcome per provider newly clustered this call, in the order found; empty when
        every goal is still within its cap, or every provider it would name is already clustered.
    """
    outcomes: list[ClusterOutcome] = []
    already_seen: set[str] = set()
    for grant in deps.ledger.live_grants():
        if grant.task_id is None:
            continue
        task = await deps.chamber.get(grant.task_id)
        # Roadmap step 10.5: a requested goal's own budget is its cap, when lower than the
        # manifest's own per-goal cap.
        cap = goal_spend_cap(deps.budgets, task.spec)
        headroom = deps.ledger.spend.headroom(task.goal_id, cap)
        if headroom > 0:
            continue  # This goal is still within its cap; its grant's providers stay untouched.
        for provider in providers_of(grant, deps):
            if provider in already_seen or provider in state.clustered_providers:
                continue
            already_seen.add(provider)
            outcomes.append(await cluster(provider, "cost_cap", deps, state, wardens))
    return tuple(outcomes)


def providers_of(grant: ForageGrant, deps: QueenDeps) -> tuple[str, ...]:
    """Return every provider name `grant`'s own allowed bindings resolve to on `deps.map`.

    Shared with `hivemind.queen.cluster.tick`, whose bound-provider probe needs the same
    grant-to-provider reading to decide whether a DOWN provider leaves a bee any fallback.

    Args:
        grant: A live grant from `deps.ledger.live_grants()`.
        deps: The Queen's collaborators; `map` resolves each binding's source to its provider.
    """
    providers: list[str] = []
    for binding in grant.allowed:
        try:
            source = deps.map.get(binding.source_id)
        except UnknownSourceError:
            continue
        if source.spec.provider not in providers:
            providers.append(source.spec.provider)
    return tuple(providers)


def every_bee_has_a_fallback(provider: str, deps: QueenDeps, state: ClusterState) -> bool:
    """Return whether every live grant naming `provider` also names a provider still up.

    "No fallback within Forage" (roadmap step 4.9), read off the ledger: a grant's `allowed`
    bindings are exactly what its sub-bees may call, so a grant whose every binding sits on
    `provider` or on an already clustered one has nowhere to spill, and its bees must pause.

    Args:
        provider: The provider that has just read DOWN.
        deps: The Queen's collaborators; `ledger` and `map` are read.
        state: The Queen's own clustered-provider set, already unavailable too.
    """
    unavailable = set(state.clustered_providers) | {provider}
    for grant in deps.ledger.live_grants():
        providers = set(providers_of(grant, deps))
        if provider in providers and not providers - unavailable:
            return False
    return True


async def cluster_if_down(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink], task_id: str
) -> bool:
    """Probe the providers `task_id`'s grant is bound to, now, and cluster any that read DOWN.

    The Alarm-driven trigger: a bee's `PROVIDER_UNAVAILABLE` Alarm is one signal and this probe is
    the second, so a single DOWN reading is enough here (the tick's own steady probe waits for two
    because it has no failed call to corroborate it). A provider that answers is left alone; the
    caller retries the task instead of pausing it.

    Args:
        deps: The Queen's collaborators; `provider_lookup` None means no probe is possible.
        state: The Queen's own clustered-provider set; mutated by any `cluster` call made here.
        wardens: Every Warden currently attached.
        task_id: The task the Alarm concerns; its live grant names the providers to probe.

    Returns:
        True when at least one of the task's providers is clustered once this returns (whether by
        this call or already), so the task is paused and will resume from its Handoff; False when
        every one of them answered, or nothing could be probed.
    """
    if deps.provider_lookup is None:
        return False
    grant = next((g for g in deps.ledger.live_grants() if g.task_id == task_id), None)
    if grant is None:
        return False
    clustered = False
    now = deps.clock.now()
    for provider in providers_of(grant, deps):
        if provider in state.clustered_providers:
            clustered = True
            continue
        # External await: a health probe, never a completion call (Clustering never awaits a model).
        reading = await deps.health_poller.probe(provider, deps.provider_lookup, now)
        if reading.state is HealthState.DOWN and not every_bee_has_a_fallback(
            provider, deps, state
        ):
            await cluster(provider, "provider_down", deps, state, wardens)
            clustered = True
    return clustered
