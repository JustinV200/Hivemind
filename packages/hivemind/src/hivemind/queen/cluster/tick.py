"""Define run_cluster_tick and awake_available: the two hooks a running Queen's tick wires in.

`run_cluster_tick` is "the single function the orchestrator wires into queen.py's tick" (this
dispatch's own deliverable): one call that checks cost caps
(`hivemind.queen.cluster.triggers.check_cost_caps`), drains every pending operator order
(`hivemind.queen.cluster.orders.OrderStore.pending`, `hive cluster`/`hive wake`'s own durable
rows, docs/adr/0024), probes every provider a live grant is bound to at the poller's slow steady
cadence and clusters one that has read DOWN `DOWN_PROBES_BEFORE_CLUSTER` times in a row while
some bee on it has no allowed binding on any other healthy provider (roadmap step 4.9's own first
trigger: "`ProviderHealth` failing with no fallback within Forage"), and probes every currently
clustered provider's health on its own backoff schedule
(`hivemind.queen.cluster.health.HealthPoller`), clustering or resuming through
`hivemind.queen.cluster.protocol` as each of those decides. `awake_available` is the small, pure
check roadmap step 4.9's own third deliverable asks for: "the Queen's own awake mode being
unavailable is handled by her autopilot running the same protocol" -- when `ModelSlot.QUEEN`'s own
bound provider is itself clustered, an awake episode has nowhere to go, so the orchestrator's own
`_run_awake` (not this dispatch's file to edit) is expected to call this first and skip straight to
`QueenAction.NEEDS_JUDGEMENT`'s autopilot fallback when it returns False.

`run_release_tick` is roadmap step 5.13's own addition: `hive cells release <lease-id>` writes a
durable `ClusterOrder(kind=RELEASE, lease_id=...)` row (`hivemind.queen.cluster.orders`'s own
module docstring explains why this reuses the CLUSTER/WAKE table rather than a sibling one), and
this is the running Queen's own drain for it, called separately from `run_cluster_tick` (so
`_drain_orders` below now filters to `{CLUSTER, WAKE}` explicitly, leaving a RELEASE row for this
function instead of falling into its own `else` branch, which used to mean "WAKE" whenever the
order was not CLUSTER, back when only two kinds existed). Two things happen for a lease whose Cell
this Queen still recognises: every live Forage grant on that Cell is revoked
(`hivemind.queen.forage.grants.revoke`, the same call `hivemind.workers.roles.undertaker.role.
Undertaker.release_real`'s own `GrantRevoker` makes), stopping further spend against it at once,
and an `Intervene(RELEASE_LEASE)` is sent to the Warden that owns it (found by `cell_id` among the
attached `wardens`, the same lookup `hivemind.queen.cluster.protocol`'s own `_send_task_pause`
uses) so that Warden actually stops its sub-bees and releases the lease itself -- the lever
`waggle.messages.supervision.InterventionAction` was missing until this dispatch (PROTOCOL_MINOR
5). The order is marked handled either way, so a lease this Queen's own trail never recorded (or
whose Warden is no longer attached, e.g. a crashed process) does not retry forever; `hive cells
abscond` (roadmap step 5.13, `hivemind.cli.readback.virtual`) stays the guaranteed path to fully
close a lease once its own Warden process is not running to cooperate at all.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. `run_cluster_tick`/`run_release_tick` are each called once per Queen tick by
    whichever orchestrator wires them in (see this dispatch's own report for the exact `queen.py`/
    `queen/ticks/housekeeping.py` lines); `awake_available` is called from `hivemind.queen.queen.
    _run_awake` (same caveat). Calls into `hivemind.llm` (HealthState), `hivemind.forage.slots`
    (ModelSlot), `hivemind.queen.cluster.health`, `.orders`, `.protocol`, `.triggers`,
    `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.forage.grants` (revoke, local
    import -- see `_revoke_grants_for_cell`'s own docstring), `hivemind.queen.state`
    (ClusterState), `hivemind.pheromone` (TrailQuery, MAX_QUERY_LIMIT) and waggle only.

Key invariants:
    - `run_cluster_tick` never awaits a model: `check_cost_caps`, order handling and
      `HealthPoller.probe` (which calls only `LLMProvider.health()`) are its whole body.
      `run_release_tick` never awaits a model either: it only reads the trail and revokes grants.
    - Every pending order is marked handled in the same call that acts on it, before the next
      order is read, so a WAKE row is consumed exactly once even if `run_cluster_tick` is called
      again before the store's own next write lands (`OrderStore`'s own contract); the same holds
      for a RELEASE row and `run_release_tick`.
    - `awake_available` returns True (never blocks awake) when `deps.bindings` names no binding
      for `ModelSlot.QUEEN`'s own manifest key: a missing binding is a configuration gap this
      function is not the place to raise on, and Clustering never invents a reason to block awake
      that health data does not actually support.

See Also:
    - .claude/roadmap.md step 4.9 for the deliverable `run_cluster_tick`/`awake_available` satisfy;
      step 5.13 for `run_release_tick`'s own deliverable.
    - hivemind.queen.cluster.protocol for cluster/resume, the effects this module's decisions run.
    - hivemind.queen.cluster.health for HealthPoller, the backoff schedule this module drives.
    - hivemind.queen.cluster.orders for OrderStore/ClusterOrder/OrderKind, the durable rows this
      module drains.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.forage.slots import ModelSlot
from hivemind.llm import HealthState
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen.cluster.orders import ClusterOrder, OrderKind
from hivemind.queen.cluster.protocol import cluster, resume
from hivemind.queen.cluster.triggers import (
    check_cost_caps,
    every_bee_has_a_fallback,
    providers_of,
)
from hivemind.queen.state import ClusterState
from waggle.envelope import wrap
from waggle.ids import CellId
from waggle.messages.forage.values import RevocationCause
from waggle.messages.supervision import Intervene, InterventionAction

if TYPE_CHECKING:
    # Only for the type hints below: see hivemind.queen.cluster.protocol's own TYPE_CHECKING
    # block for why a real import here would cycle back through queen/deps.py.
    from hivemind.queen.deps import QueenDeps, WardenLink

# Consecutive DOWN readings before a bound provider is clustered: one failed GET is a blip (a
# restart, a dropped packet); the second, one backoff later, is an outage worth pausing for.
DOWN_PROBES_BEFORE_CLUSTER = 2
# Orders run_cluster_tick's own _drain_orders acts on; a RELEASE row is left for run_release_tick.
_CLUSTER_TICK_KINDS = frozenset({OrderKind.CLUSTER, OrderKind.WAKE})

__all__ = ["DOWN_PROBES_BEFORE_CLUSTER", "awake_available", "run_cluster_tick", "run_release_tick"]


async def run_cluster_tick(
    deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> None:
    """Check cost caps, drain operator orders, probe bound providers, then probe clustered ones.

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
    await _probe_bound(deps, state, wardens)
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
    """Act on every pending CLUSTER/WAKE order once, then mark each handled.

    A RELEASE order (module docstring) is left pending for `run_release_tick`'s own drain: filtered
    out here explicitly rather than falling into an `else` branch that used to mean "WAKE" back
    when CLUSTER/WAKE were the only two kinds.
    """
    for order in await deps.orders.pending():
        if order.kind not in _CLUSTER_TICK_KINDS:
            continue
        providers = _providers_for_order(order.kind, order.provider, deps, state)
        for provider in providers:
            if order.kind is OrderKind.CLUSTER:
                await cluster(provider, "operator", deps, state, wardens)
            else:
                await resume(provider, deps, state, wardens)
                deps.health_poller.reset(provider)  # Probed again at once, then steadily.
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
        for provider in providers_of(grant, deps):
            if provider not in seen:
                seen.append(provider)
    return tuple(seen)


async def _probe_bound(deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]) -> None:
    """Probe every bound, unclustered provider that is due; cluster one down with no fallback."""
    if deps.provider_lookup is None:
        return  # No way to reach a live LLMProvider by name; nothing this tick can probe.
    now = deps.clock.now()
    for provider in _bound_providers(deps):
        if provider in state.clustered_providers or not deps.health_poller.is_due(provider, now):
            continue
        # External await: a health probe, never a completion call (module docstring).
        reading = await deps.health_poller.probe(provider, deps.provider_lookup, now)
        if reading.state is not HealthState.DOWN:
            continue  # HEALTHY keeps running; DEGRADED (a 429, a 5xx) is the Fanner's to spill.
        if deps.health_poller.failed_probes(provider) < DOWN_PROBES_BEFORE_CLUSTER:
            continue  # One blip; the backoff schedule brings the confirming probe soon.
        if every_bee_has_a_fallback(provider, deps, state):
            continue  # The ladder and the Fanner move each call to the next binding instead.
        await cluster(provider, "provider_down", deps, state, wardens)


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
        # A HEALTHY reading schedules its own next probe at the steady cadence (HealthPoller.
        # probe), so a provider resumed here keeps being watched like any other bound one.
        # External await: a health probe, never a completion call (module docstring); the
        # provider's own health() contract carries no fixed timeout of its own to name here.
        reading = await deps.health_poller.probe(provider, deps.provider_lookup, now)
        if reading.state is HealthState.HEALTHY:
            await resume(provider, deps, state, wardens)


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.13: run_release_tick, the RELEASE-only counterpart to _drain_orders above.
# ──────────────────────────────────────────────────────────────────────────────


async def run_release_tick(deps: QueenDeps, wardens: Sequence[WardenLink]) -> tuple[str, ...]:
    """Drain every pending RELEASE order: revoke grants, tell the Warden to release, mark handled.

    See the module docstring for exactly what this does for a lease whose Cell is still known.

    Args:
        deps: The Queen's collaborators; `orders`, `trail`, `ledger`, `clock` and `identity` are
            what this reads and acts through.
        wardens: Every Warden currently attached, so the one holding the lease's Cell can be sent
            `Intervene(RELEASE_LEASE)`.

    Returns:
        The id of every RELEASE order this call handled, in the order `deps.orders.pending()`
        returned them.
    """
    handled: list[str] = []
    for order in await deps.orders.pending():
        if order.kind is not OrderKind.RELEASE:
            continue
        await _handle_release_order(deps, order, wardens)
        handled.append(order.id)
    return tuple(handled)


async def _handle_release_order(
    deps: QueenDeps, order: ClusterOrder, wardens: Sequence[WardenLink]
) -> None:
    """Revoke the named lease's Cell's live grants, tell its Warden to release, then mark handled.

    A `lease_id` this Hive's trail never recorded a `cell.leased` for (a typo, or a lease from a
    different Hive's own database) has no Cell to act on; the order is still marked handled,
    matching `OrderStore`'s own "consumed exactly once" contract for every other kind. Likewise, a
    Cell whose Warden is not currently attached (a crashed process, or one that has not reconnected
    yet) has its grants revoked but no Intervene to send -- there is nothing this tick can do about
    that beyond what it already did; `hive cells abscond` is the operator's own fallback.
    """
    if order.lease_id is not None:
        cell_id = await _cell_id_for_lease(deps, order.lease_id)
        if cell_id is not None:
            await _revoke_grants_for_cell(deps, cell_id)
            await _send_release_lease(deps, wardens, cell_id, order.lease_id)
    await deps.orders.mark_handled(order.id, deps.clock.now())


async def _send_release_lease(
    deps: QueenDeps, wardens: Sequence[WardenLink], cell_id: CellId, lease_id: str
) -> None:
    """Send Intervene(RELEASE_LEASE) to the attached Warden that owns `cell_id`, if any."""
    link = next((link for link in wardens if link.cell.id == cell_id), None)
    if link is None:
        return  # Not currently attached; grants are already revoked, and this tick can do no more.
    message = Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,  # Always the recipient itself (module docstring, waggle spec section 8.3).
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=f"hive cells release: the operator asked for lease {lease_id} to be released.",
    )
    # Mirrors this function's own docstring for the "not currently attached" case just above:
    # grants are already revoked, and this tick can do no more for a Warden it cannot reach.
    await link.send(wrap(message, link.hop, clock=deps.clock))


async def _cell_id_for_lease(deps: QueenDeps, lease_id: str) -> CellId | None:
    """Return the Cell a `cell.leased` event on the trail recorded `lease_id` for, or None."""
    events = await deps.trail.query(TrailQuery(kind="cell.leased", limit=MAX_QUERY_LIMIT))
    for event in events:
        if str(event.payload.get("lease_id")) == lease_id:
            return CellId(event.subject_id)
    return None


async def _revoke_grants_for_cell(deps: QueenDeps, cell_id: CellId) -> int:
    """Revoke every live grant on `cell_id`, skipping one still ISSUED (never drawn on).

    Local import (module docstring): `hivemind.queen.forage.grants` sits inside this same
    `hivemind.queen` package tree, and every sibling module here keeps a cross-sub-package call
    like this local rather than module-level, the same way `hivemind.queen.cluster.protocol.
    resume`'s own docstring explains for `hivemind.queen.dispatcher.resume_paused`.
    """
    from hivemind.forage import InvalidGrantTransitionError
    from hivemind.queen.forage.grants import revoke

    revoked = 0
    for grant in tuple(deps.ledger.live_grants()):
        if grant.cell_id != cell_id:
            continue
        try:
            await revoke(
                deps.ledger,
                deps,
                grant,
                RevocationCause.RELEASED,
                "hive cells release: the operator asked for this lease's Cell to be released.",
            )
        except InvalidGrantTransitionError:
            continue  # Still ISSUED (never drawn on): nothing REVOKED can be moved from yet.
        revoked += 1
    return revoked
