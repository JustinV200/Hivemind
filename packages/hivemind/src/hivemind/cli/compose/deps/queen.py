"""Build the Queen's own QueenDeps from a Hive Manifest and the shared HiveParts.

`build_queen_deps` is the one conversion from `[forage]`/`[supervision]`/`[memory]`/`[placement]`
and the shared parts into the bag `hivemind.queen.Queen` takes; its helpers each read one manifest
slice (the goal budgets, the awake memory budget, the placement policy with its Night Veil tier
profile, roadmap step 10.3a) or fold one later phase's seam in additively (the Virtual Cell parts,
roadmap step 5.6, which phase 9's sandbox Cells and uplink keys ride on; the Honey Store, phase 7;
the Royal Jelly Lab, phase 9; the Swarm's registry, phase 11, whose enrolled devices sign the
Basket blobs their Wardens ship up with their own node keys).

The uplink keys are the one field two seams feed: a Virtual Cell's Warden signs with the key the
Cell gate registered at provisioning, and a colonized device's Warden signs with the node key the
swarm registry pinned at enrolment. `chain_uplink_keys` asks each holder in turn and answers with
the first key found, so the Queen's Basket receiver never branches on what kind of Cell a link is:
it asks who holds a key for this link, and verifies against it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.cli.compose.deps.parts`,
    `hivemind.cli.compose.night_veil`, `hivemind.cli.compose.virtual_cells`, `hivemind.cli.stores`,
    `hivemind.forage`, `hivemind.honey_store`, `hivemind.manifest`, `hivemind.queen`,
    `hivemind.queen.swarm_gate.keys`, `hivemind.supervision` and `hivemind.swarm` only.

Key invariants:
    - Every seam a later phase added is additive: `virtual_cells=None` keeps placement Real-only,
      `honey=None` keeps the Queen answering queries empty and a Hive with no Swarm never calls
      `with_swarm_uplink_keys`, leaving the uplink keys to the Cell gate alone, as before.
    - The ledger is the one `build_hive` already built and restored (roadmap step 4.8); this module
      never opens a second.
    - Each uplink-key lookup stays in its own layer (the Cell gate's in `hivemind.queen.cell_gate`,
      the registry's in `hivemind.queen.swarm_gate.keys`); this module only chains them.

See Also:
    - hivemind.queen.deps for QueenDeps, the bag this module returns.
    - hivemind.cli.compose.deps.parts for HiveParts and build_ledger.
"""

from __future__ import annotations

import dataclasses

from hivemind.cli.compose.deps.parts import HiveParts, footprints, supervision_file, system_identity
from hivemind.cli.compose.night_veil import in_process_providers, night_veil_constraints
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.cli.stores import open_cluster_orders, routing_rules, slot_bindings
from hivemind.forage import ForageMap, GoalBudgets, Tempo
from hivemind.honey_store import HoneyAccess
from hivemind.manifest import ForageSection, HiveManifest
from hivemind.queen import ForageLedger, MemoryBudget, QueenDeps
from hivemind.queen.deps import UplinkKeys, WardenLink
from hivemind.queen.placement import PlacementPolicy
from hivemind.queen.swarm_gate.keys import RegistryUplinkKeys
from hivemind.supervision import load_policy
from hivemind.swarm import SwarmRegistry

__all__ = ["build_queen_deps", "chain_uplink_keys", "with_swarm_uplink_keys"]


def build_queen_deps(
    parts: HiveParts,
    forage_map: ForageMap,
    ledger: ForageLedger,
    virtual_cells: VirtualCellsParts | None = None,
    honey: HoneyAccess | None = None,
) -> QueenDeps:
    """Build the Queen's own QueenDeps from `[forage]`/`[supervision]`/`[memory]` and shared parts.

    Args:
        parts: This Hive's shared collaborators.
        forage_map: The same live map the registry (`hivemind.cli.stores.
            build_provider_registry`) and `build_fanner` share.
        ledger: The Queen's live book of Forage, built by `build_ledger` ahead of `build_fanner`
            (roadmap step 4.8's own wiring step: `build_fanner` needs it too, before `QueenDeps`
            itself can exist to carry it).
        virtual_cells: `hivemind.cli.compose.virtual_cells.build_virtual_cells`'s own return
            value, when `[virtual_cells] backend` is set; folded into `QueenDeps.virtual_provider`/
            `.virtual_backend_source`/`.dormant_cell_source`/`.on_task_finished`. `None` (the
            default, and every pre-5.6 caller) leaves those four fields at their own defaults,
            keeping placement Real-only.
        honey: The Hive's Honey Store handles (`hivemind.cli.compose.honey.build_honey_access`),
            becoming `QueenDeps.honey`; None (no Honey Store, every pre-phase-7 caller) keeps
            the Queen answering queries empty and skipping every pre-check and deposit.

    Returns:
        A QueenDeps ready for `hivemind.queen.Queen(deps)`; `with_swarm_uplink_keys` adds the
        Swarm's registry to its uplink keys once the Swarm is built.
    """
    base = dataclasses.replace(
        _base_queen_deps(parts, forage_map, ledger), honey=honey, royal_jelly=parts.royal_jelly
    )
    return _with_virtual_cells(base, virtual_cells)


def with_swarm_uplink_keys(deps: QueenDeps, registry: SwarmRegistry) -> QueenDeps:
    """Add the Swarm registry's pinned node keys to the keys `deps` verifies Basket uploads with.

    Roadmap phase 11: a colonized device's Warden signs the manifest of every blob it ships up
    with its own node key, which only the registry holds. The lookup is chained after whatever
    `deps.uplink_keys` already holds (the Cell gate's, for Virtual Cells), so the receiver asks
    each holder in turn and never branches on what kind of Cell a link is.

    Args:
        deps: The bag `build_queen_deps` returned.
        registry: The Swarm's registry of enrolled devices (`hivemind.cli.compose.swarm.
            build_swarm`'s `SwarmAccess.registry`).

    Returns:
        `deps` with the registry's lookup chained into `uplink_keys`.
    """
    holders: list[UplinkKeys] = []
    if deps.uplink_keys is not None:
        holders.append(deps.uplink_keys)  # Phase 9: the Cell gate's keys come first, as wired.
    holders.append(RegistryUplinkKeys(registry))  # Phase 11: then the devices the Swarm enrolled.
    return dataclasses.replace(deps, uplink_keys=chain_uplink_keys(*holders))


def chain_uplink_keys(*lookups: UplinkKeys) -> UplinkKeys:
    """Chain uplink-key lookups: the first holder that has a key for a link answers for it.

    Args:
        *lookups: The holders, asked in order; each answers None for a link it holds no key for.

    Returns:
        An `UplinkKeys` that asks each in turn and answers None once every holder has.
    """

    async def lookup(link: WardenLink) -> bytes | None:
        for holder in lookups:
            key = await holder(link)
            if key is not None:
                return key
        return None

    return lookup


def _base_queen_deps(parts: HiveParts, forage_map: ForageMap, ledger: ForageLedger) -> QueenDeps:
    """Build every `QueenDeps` field `build_queen_deps` set before roadmap step 5.6's own field."""
    manifest = parts.manifest
    supervision = manifest.supervision
    forage = manifest.forage
    return QueenDeps(
        chamber=parts.stores.chamber,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=system_identity(manifest),
        clock=parts.clock,
        policy=load_policy(supervision_file(manifest, supervision.policy_file)),
        enforcer=parts.enforcer,  # Roadmap step 10.3: every enforcement point the Queen passes.
        goal_requests=parts.stores.goal_requests,  # Roadmap step 10.5: her durable goal requests.
        chat=parts.stores.chat,  # Roadmap step 10.5: the human end of her inbox.
        bound_for=parts.registry.bound,
        rebind=parts.registry.bound_for_key,
        bindings=slot_bindings(manifest),
        call_gate=parts.fanner.lane(Tempo()),
        map=forage_map,
        routing_rules=routing_rules(manifest),  # Roadmap step 8.1: the plan's per-slot floors.
        budgets=_goal_budgets(forage),
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        heartbeat_miss_limit=supervision.heartbeat_miss_limit,
        alarm_attempt_limit=supervision.alarm_attempt_limit,
        memory_budget=_awake_memory_budget(manifest),
        scratch_root=manifest.resolve_path(manifest.hive_stand.scratch_root),  # roadmap 5.0b
        footprints=footprints(forage.roles),
        reserve=forage.reserve,
        grant_ttl_s=forage.grant_ttl_s,
        # roadmap step 4.8: the same ForageLedger build_hive already built (see build_queen_deps's
        # own docstring), restored from whatever its SqliteLedgerStore already held.
        ledger=ledger,
        # Roadmap step 4.9: a SqliteOrderStore over the same [hive] db file (hive cluster/wake
        # share it); provider_lookup satisfies HealthPoller.probe's one collaborator.
        orders=open_cluster_orders(manifest.resolve_path(manifest.hive.db)),
        provider_lookup=parts.registry.provider,
        # Roadmap step 4.3: the manifest's own sweep cadence for the Queen's House Bee sweep.
        sweep_interval_s=manifest.memory.sweep_interval_s,
        hot_window_s=manifest.memory.hot_window_s,
        # Roadmap step 5.7: the [placement] section, as the slice decide() reads; roadmap step
        # 10.3a adds the Night Veil tier profile, which no composition root used to build.
        placement_policy=_placement_policy(manifest),
        in_process_providers=in_process_providers(manifest),  # Roadmap step 10.3a.
    )


def _placement_policy(manifest: HiveManifest) -> PlacementPolicy:
    """Convert `[placement]` and the Night Veil tier profile into the policy decide() reads."""
    section = manifest.placement
    return PlacementPolicy(
        prefer=section.prefer,
        allow_hive_stand=section.allow_hive_stand,
        role_overrides={
            key: override.prefer
            for key, override in section.roles.items()
            if override.prefer is not None
        },
        night_veil=night_veil_constraints(manifest),
    )


def _with_virtual_cells(base: QueenDeps, virtual_cells: VirtualCellsParts | None) -> QueenDeps:
    """Fold the live Virtual Cell seam into `base`, additive; `base` unchanged when `None`."""
    if virtual_cells is None:
        return base
    return dataclasses.replace(
        base,
        virtual_provider=virtual_cells.provider,
        virtual_backend_source=virtual_cells.virtual_backend_source,
        dormant_cell_source=virtual_cells.dormant_cell_source,
        on_task_finished=virtual_cells.on_task_finished,
        on_cell_granted=virtual_cells.on_cell_granted,
        sandboxes=virtual_cells.provider,  # Phase 9: the Comb's sandbox Cells, and the keys for
        uplink_keys=virtual_cells.provider.uplink_key,  # the manifests their Wardens sign.
    )


def _goal_budgets(forage: ForageSection) -> GoalBudgets:
    """Build the `[forage]` goal-level caps `build_queen_deps`'s own `budgets` field carries."""
    return GoalBudgets(
        spend_cap_usd=forage.spend_cap_per_goal_usd,
        token_budget=forage.token_budget_per_goal,
        max_sub_bees=forage.max_sub_bees_per_goal,
    )


def _awake_memory_budget(manifest: HiveManifest) -> MemoryBudget:
    """Build the `[memory]` slice `build_queen_deps`'s own `memory_budget` field carries."""
    return MemoryBudget(
        budget_fraction=manifest.memory.budget_fraction,
        output_reserve_tokens=manifest.memory.output_reserve_tokens,
    )
