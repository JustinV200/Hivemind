"""Build the Hive Stand's own WardenDeps from a Hive Manifest and the shared HiveParts.

`build_warden_deps` is the one conversion from `[supervision]`/`[memory]`/`[hive_stand]` and the
shared parts into the bag `hivemind.wardens.Warden` takes for the Hive Stand's Warden; its helpers
each wire one later phase's seam: the judge reviewer and per-grant Fanner lanes (roadmap steps
4.8 and 4.10), the Nurse Bee from `[nurse]` (ADR-0061), `keep_root` (5.0e), the Exoskeleton (6.4
to 6.6, `hivemind.cli.compose.exoskeleton`), the Guard policy and Enforcer (10.2 and 10.3), the
live Forage map, routing rules and the model servers it may host (8.1 and 8.3), and the Royal
Jelly Lab's handles (phase 9).

The bag is built in two layers (roadmap step 11.6). `build_cell_warden_deps` is what any Real
Cell's Warden needs, given the source it leases through and the capability its lease needs, and
`build_warden_deps` adds what only the Hive Stand's Warden has, because only its Cell is this
process's own disk, display and Basket: the keep root, the Royal Jelly handles, the model servers
and the Exoskeleton. A Swarm device's Warden (`hivemind.cli.compose.swarm`) is built from the
first alone.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.hive.build_hive` and `hivemind.cli.compose.swarm`. Calls into
    `hivemind.cli.compose.deps.parts`, `.watch`, `hivemind.cli.compose.exoskeleton`,
    `hivemind.cli.compose.links`, `hivemind.cli.compose.night_veil`, `hivemind.cli.stores`,
    `hivemind.basket`, `hivemind.cell.local`, `hivemind.forage`, `hivemind.guard`, `hivemind.llm`,
    `hivemind.manifest`, `hivemind.supervision`, `hivemind.wardens` and `hivemind.workers` only.

Key invariants:
    - The Warden's lease needs `cell:hive_stand` (`HIVE_STAND_LEASE`), because this package is the
      one place that knows it built the Hive Stand's own source; never read off a Cell's kind. A
      caller building another Real Cell's Warden names its own lease capability.
    - A Warden built by `build_cell_warden_deps` has none of the Hive Stand's own handles: no keep
      root, Royal Jelly access, resident Basket, hosted model server or Exoskeleton, so it can
      never reach this machine's disk, display or models through a Cell that is somebody else's.
    - `worker_factory` is `hivemind.workers.roles.worker_for` (roadmap step 6.9): a fresh Drone,
      Forager or Scout per `TaskAssign.role`, the same mapping `hivemind.cli.in_cell.deps` wires.

See Also:
    - hivemind.wardens.deps for WardenDeps, the bag this module returns.
    - hivemind.cli.compose.deps.parts for HiveParts, what this module builds over.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from hivemind.basket import ResidentBasket
from hivemind.cell import RealCellSource
from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps.parts import HiveParts, supervision_file, system_identity
from hivemind.cli.compose.deps.watch import hive_stand_watch
from hivemind.cli.compose.exoskeleton import hive_stand_exoskeleton
from hivemind.cli.compose.links import HiveLinks
from hivemind.cli.compose.night_veil import local_providers
from hivemind.cli.stores import provider_configs, routing_rules, server_configs, slot_bindings
from hivemind.forage import ModelSlot, Tempo
from hivemind.guard import Capability, CapabilityFamily
from hivemind.llm import CallGate
from hivemind.manifest import HiveManifest
from hivemind.supervision import load_warden_policy
from hivemind.supervision.capping import deterministic_checks, judge_checks, load_tiers
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.wardens import ModelJudgeReviewer, WardenDeps
from hivemind.wardens.local_pool.hosting import provider_door
from hivemind.wardens.nurse import ModelNurseReviewer, NurseWiring
from hivemind.workers.roles import worker_for

# Roadmap step 10.3: what the Hive Stand's own Warden's lease needs of its set (the lease_creation
# point); set here because this package built the Hive Stand's source, never read off a Cell's kind.
HIVE_STAND_LEASE = Capability(family=CapabilityFamily.CELL_HIVE_STAND)

__all__ = ["HIVE_STAND_LEASE", "build_cell_warden_deps", "build_warden_deps"]


def build_warden_deps(parts: HiveParts, source: HiveStandSource, links: HiveLinks) -> WardenDeps:
    """Build the Hive Stand's own WardenDeps from `[supervision]`/`[memory]` and the shared parts.

    Args:
        parts: This Hive's shared collaborators.
        source: The Hive Stand's own RealCellSource: leased and released, never provisioned.
        links: Both ends of the Queen<->Warden link; `.warden_transport`/`.warden_hop` are its end.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(links.warden_id, deps)`.
    """
    deps = dataclasses.replace(
        build_cell_warden_deps(parts, source, links, HIVE_STAND_LEASE),
        keep_root=_keep_root(parts.manifest),  # Roadmap step 5.0e: like the Queen's scratch_root.
        disk_reserve_mb=parts.manifest.hive_stand.disk_reserve_mb,  # `[hive_stand]`'s own reserve.
        royal_jelly=parts.royal_jelly,  # Roadmap phase 9: the Lab's and the Basket's handles.
        resident_basket=_resident_basket(parts),  # The Hive Stand's own resident view.
        watch=hive_stand_watch(parts),  # Roadmap step 11.10: it watches through its own session.
    )
    # Roadmap steps 6.4-6.6: screen, browser launcher, flight recorder and ears (.exoskeleton).
    return hive_stand_exoskeleton(parts).apply(_hosting(deps, parts))


def build_cell_warden_deps(
    parts: HiveParts, source: RealCellSource, links: HiveLinks, lease_capability: Capability
) -> WardenDeps:
    """Build the WardenDeps any Real Cell's Warden shares: the Hive Stand's, and a device's.

    What only the Hive Stand's own Warden has is left to `build_warden_deps` (module docstring).

    Args:
        parts: This Hive's shared collaborators.
        source: The RealCellSource the Warden leases through: leased and released, never made.
        links: Both ends of the Queen<->Warden link; `.warden_transport`/`.warden_hop` are its end.
        lease_capability: What the Warden's set must hold to lease this Cell (`lease_creation`),
            named by whoever built `source`: `cell:hive_stand`, or `cell:real:<cell id>`.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(links.warden_id, deps)`.
    """
    manifest = parts.manifest
    supervision = manifest.supervision
    judge_rubrics = load_judge_rubrics()
    judge_reviewer = _build_judge_reviewer(parts)  # Roadmap step 4.10: CheckKind.JUDGE's reviewer.
    deps = WardenDeps(
        source=source,
        queen_link=links.warden_transport,
        hop=links.warden_hop,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=system_identity(manifest),
        clock=parts.clock,
        policy=load_warden_policy(supervision_file(manifest, supervision.policy_file)),
        tiers=load_tiers(supervision_file(manifest, supervision.capping_tiers_file)),
        checks={**deterministic_checks(), **judge_checks(judge_reviewer, judge_rubrics)},
        bound=parts.registry.bound(ModelSlot.WARDEN),
        call_gate=parts.fanner.lane(Tempo()),
        worker_factory=worker_for,
        rebind=lambda key: parts.registry.bound_for_key(key, ModelSlot.WORKER),
        handoff_threshold=manifest.memory.handoff_threshold,
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        worker_heartbeat_interval_s=supervision.heartbeat_interval_s,
        missed_heartbeats_before_stalled=supervision.heartbeat_miss_limit,
        judge_reviewer=judge_reviewer,
        judge_rubrics=judge_rubrics,
        lane_for_grant=_lane_for_grant(parts),  # Roadmap step 4.8: one Fanner lane per grant.
        guard=parts.enforcer.policy,  # Roadmap step 10.2: every set this Warden builds.
        enforcer=parts.enforcer,  # Roadmap step 10.3: the Guard's adapter, lease and slots.
        lease_capability=lease_capability,
        bindings=slot_bindings(manifest),
        local_providers=local_providers(manifest),  # Roadmap step 10.3a: a binding's locality.
    )
    return _with_nurse(_routed(deps, parts), parts)


def _with_nurse(deps: WardenDeps, parts: HiveParts) -> WardenDeps:
    """Give `deps` the Nurse Bee `[nurse]` asks for (ADR-0061), or none when it is disabled.

    The Nurse runs on `ModelSlot.JUDGE`, one Fanner lane per review on the task's tempo, and grades
    models on the Fanner's own live map, the same figures routing and metering read.
    """
    section = parts.manifest.nurse
    if not section.enabled:
        return deps
    forage = parts.fanner.deps.map
    reviewer = ModelNurseReviewer(
        bound=parts.registry.bound(ModelSlot.JUDGE), forage=forage, lane_for=parts.fanner.lane
    )
    wiring = NurseWiring(
        reviewer=reviewer,
        forage=forage,
        skip_roles=section.skipped(),
        max_evidence_chars=section.max_evidence_chars,
    )
    return dataclasses.replace(deps, nurse=wiring)


def _routed(deps: WardenDeps, parts: HiveParts) -> WardenDeps:
    """Give `deps` the live map and the routing rules (roadmap step 8.1).

    The map is the Fanner's own, so routing and metering read the same live figures.
    """
    return dataclasses.replace(
        deps, map=parts.fanner.deps.map, routing_rules=routing_rules(parts.manifest)
    )


def _hosting(deps: WardenDeps, parts: HiveParts) -> WardenDeps:
    """Give the Hive Stand's `deps` the model servers it may host on its own Cell (roadmap 8.3).

    A hosted server's sources are added to the Fanner's map `_routed` gave it. The provider door is
    closed over this Hive's one registry, so a started server's provider is the instance every
    other call resolves too. Only the Hive Stand's Warden hosts: a device is borrowed, and a model
    server on it is a promotion the operator asks for (ADR-0053), never a default.
    """
    register, unregister = provider_door(parts.registry, provider_configs(parts.manifest))
    return dataclasses.replace(
        deps,
        servers=server_configs(parts.manifest),
        register_provider=register,
        unregister_provider=unregister,
    )


def _resident_basket(parts: HiveParts) -> ResidentBasket | None:
    """Return the Hive Stand Warden's resident Basket view, None without a Royal Jelly access."""
    return parts.royal_jelly.resident if parts.royal_jelly is not None else None


def _keep_root(manifest: HiveManifest) -> Path | None:
    """Resolve `[hive_stand] keep_root` against the manifest's own directory, or None."""
    keep_root = manifest.hive_stand.keep_root
    return manifest.resolve_path(keep_root) if keep_root is not None else None


def _build_judge_reviewer(parts: HiveParts) -> ModelJudgeReviewer:
    """Build the Warden-layer JudgeReviewer on `ModelSlot.JUDGE`, one Fanner lane per review."""
    # `Fanner.lane` itself is the lane factory: each review gets a lane on the proposal's own
    # tempo, so an urgent task's judge call queues ahead of a thorough one's.
    return ModelJudgeReviewer(
        bound=parts.registry.bound(ModelSlot.JUDGE), lane_for=parts.fanner.lane
    )


def _lane_for_grant(parts: HiveParts) -> Callable[[str, str, Tempo], CallGate]:
    """Return `WardenDeps.lane_for_grant`: one Fanner lane per (grant, goal) on the task's tempo."""
    return lambda grant_id, goal_id, tempo: parts.fanner.lane(
        tempo, grant_id=grant_id, goal_id=goal_id
    )
