"""Build the Queen's own QueenDeps from a Hive Manifest and the shared HiveParts.

`build_queen_deps` is the one conversion from `[forage]`/`[supervision]`/`[memory]`/`[placement]`
and the shared parts into the bag `hivemind.queen.Queen` takes; its helpers each read one manifest
slice (the goal budgets, the awake memory budget, the placement policy with its Night Veil tier
profile, roadmap step 10.3a) or fold one later phase's seam in additively (the Virtual Cell parts,
roadmap step 5.6, which phase 9's sandbox Cells and uplink keys ride on; the Honey Store, phase 7;
the Royal Jelly Lab, phase 9).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.cli.compose.deps.parts`,
    `hivemind.cli.compose.night_veil`, `hivemind.cli.compose.virtual_cells`, `hivemind.cli.stores`,
    `hivemind.common.logging`, `hivemind.common.sqlite`, `hivemind.forage`,
    `hivemind.honey_store`, `hivemind.manifest`, `hivemind.queen` (the Requeening store over
    `[hive] db`, ADR-0062, among the rest) and `hivemind.supervision` only.

Key invariants:
    - Every seam a later phase added is additive: `virtual_cells=None` keeps placement Real-only
      and `honey=None` keeps the Queen answering queries empty, exactly as before those phases.
    - The ledger is the one `build_hive` already built and restored (roadmap step 4.8); this module
      never opens a second.

See Also:
    - hivemind.queen.deps for QueenDeps, the bag this module returns.
    - hivemind.cli.compose.deps.parts for HiveParts and build_ledger.
"""

from __future__ import annotations

import asyncio
import dataclasses

from hivemind.cli.compose.deps.parts import HiveParts, footprints, supervision_file, system_identity
from hivemind.cli.compose.night_veil import in_process_providers, night_veil_constraints
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.cli.stores import open_cluster_orders, routing_rules, slot_bindings
from hivemind.common.logging import get_logger
from hivemind.common.sqlite import connect
from hivemind.forage import ForageMap, GoalBudgets, Tempo
from hivemind.honey_store import HoneyAccess
from hivemind.manifest import ForageSection, HiveManifest, budget_over_compact_line
from hivemind.queen import ForageLedger, MemoryBudget, QueenDeps
from hivemind.queen.placement import PlacementPolicy
from hivemind.queen.requeening import RequeeningBook, SqliteRequeeningStore
from hivemind.supervision import load_policy
from waggle.clock import SystemClock

log = get_logger(__name__)

__all__ = ["build_queen_deps"]


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
        A QueenDeps ready for `hivemind.queen.Queen(deps)`.
    """
    book = _requeening_book(parts.manifest)
    if virtual_cells is not None:
        # ADR-0062: the Cell listener keeps the port the OS chose in her store, so her next start
        # binds the one every Cell that outlives her will redial.
        virtual_cells.listener.bind_ports(book.store)
    base = dataclasses.replace(
        _base_queen_deps(parts, forage_map, ledger),
        honey=honey,
        royal_jelly=parts.royal_jelly,
        requeening=book,
    )
    return _with_virtual_cells(base, virtual_cells)


def _requeening_book(manifest: HiveManifest) -> RequeeningBook:
    """Open her Requeening store over `[hive] db` (ADR-0062), with the `[queen]` cadences.

    SAFETY: a fresh event loop for this one setup call, the seam `open_cluster_orders` uses: the
    store's migration is async, `build_hive` is a sync composition root, and the call is local
    SQLite only.
    """

    async def _open() -> SqliteRequeeningStore:
        """Connect to the Hive's database file and apply the store's migration."""
        connection = connect(manifest.resolve_path(manifest.hive.db))
        return await SqliteRequeeningStore.create(connection, SystemClock())

    return RequeeningBook(
        store=asyncio.run(_open()),
        grace_s=manifest.queen.requeening_grace_s,
        snapshot_interval_s=manifest.queen.snapshot_interval_s,
    )


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
        nurse_max_rounds=manifest.nurse.max_rounds,  # ADR-0061: her cap on fix-forward rounds.
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
    # Valid but self-defeating: say so once, at the Hive's start (hivemind.manifest.advice).
    advice = budget_over_compact_line(manifest.memory)
    if advice is not None:
        log.warning("manifest.budget_over_compact_line", advice=advice)
    return MemoryBudget(
        budget_fraction=manifest.memory.budget_fraction,
        output_reserve_tokens=manifest.memory.output_reserve_tokens,
    )
