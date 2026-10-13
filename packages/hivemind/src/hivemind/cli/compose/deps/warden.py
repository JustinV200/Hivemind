"""Build the Hive Stand's own WardenDeps from a Hive Manifest and the shared HiveParts.

`build_warden_deps` is the one conversion from `[supervision]`/`[memory]`/`[hive_stand]` and the
shared parts into the bag `hivemind.wardens.Warden` takes for the Hive Stand's Warden; its helpers
each wire one later phase's seam: the judge reviewer and per-grant Fanner lanes (roadmap steps
4.8 and 4.10), `keep_root` (5.0e), the Exoskeleton (6.4 to 6.6, `hivemind.cli.compose.
exoskeleton`), the Guard policy and Enforcer (10.2 and 10.3), the live Forage map, routing rules
and the model servers it may host (8.1 and 8.3), and the Royal Jelly Lab's handles (phase 9).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.cli.compose.deps.parts`,
    `hivemind.cli.compose.exoskeleton`, `hivemind.cli.compose.links`,
    `hivemind.cli.compose.night_veil`, `hivemind.cli.stores`, `hivemind.basket`, `hivemind.cell.
    local`, `hivemind.forage`, `hivemind.guard`, `hivemind.llm`, `hivemind.manifest`,
    `hivemind.supervision`, `hivemind.wardens` and `hivemind.workers` only.

Key invariants:
    - The Warden's lease needs `cell:hive_stand` (`HIVE_STAND_LEASE`), because this package is the
      one place that knows it built the Hive Stand's own source; never read off a Cell's kind.
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
from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps.parts import HiveParts, supervision_file, system_identity
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
from hivemind.workers.roles import worker_for

# Roadmap step 10.3: what the Hive Stand's own Warden's lease needs of its set (the lease_creation
# point); set here because this package built the Hive Stand's source, never read off a Cell's kind.
HIVE_STAND_LEASE = Capability(family=CapabilityFamily.CELL_HIVE_STAND)

__all__ = ["HIVE_STAND_LEASE", "build_warden_deps"]


def build_warden_deps(parts: HiveParts, source: HiveStandSource, links: HiveLinks) -> WardenDeps:
    """Build the Hive Stand's own WardenDeps from `[supervision]`/`[memory]` and the shared parts.

    Args:
        parts: This Hive's shared collaborators.
        source: The Hive Stand's own RealCellSource: leased and released, never provisioned.
        links: Both ends of the Queen<->Warden link; `.warden_transport`/`.warden_hop` are its end.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(links.warden_id, deps)`.
    """
    manifest = parts.manifest
    supervision = manifest.supervision
    # Roadmap step 4.10: a model JudgeReviewer, so CheckKind.JUDGE runs where a tier turns it on.
    judge_rubrics = load_judge_rubrics()
    judge_reviewer = _build_judge_reviewer(parts)
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
        keep_root=_keep_root(manifest),  # Roadmap step 5.0e: like the Queen's scratch_root.
        disk_reserve_mb=manifest.hive_stand.disk_reserve_mb,
        royal_jelly=parts.royal_jelly,  # Roadmap phase 9: the Lab's and the Basket's handles.
        resident_basket=_resident_basket(parts),  # The Hive Stand's own resident view.
        guard=parts.enforcer.policy,  # Roadmap step 10.2: every set this Warden builds.
        enforcer=parts.enforcer,  # Roadmap step 10.3: the Guard's adapter, lease and slots.
        lease_capability=HIVE_STAND_LEASE,
        bindings=slot_bindings(manifest),
        local_providers=local_providers(manifest),  # Roadmap step 10.3a: a binding's locality.
    )
    # Roadmap steps 6.4-6.6: screen, browser launcher, flight recorder and ears (.exoskeleton).
    return hive_stand_exoskeleton(parts).apply(_routed(deps, parts))


def _routed(deps: WardenDeps, parts: HiveParts) -> WardenDeps:
    """Give `deps` the live map, the routing rules (8.1) and the model servers it may host (8.3).

    Split from `build_warden_deps` only to keep that function within codingrules 5.1's limit:
    the map is the Fanner's own, so routing and metering read the same live figures, and a hosted
    server's sources are added to that very map. The provider door is closed over this Hive's one
    registry, so a started server's provider is the instance every other call resolves too.
    """
    register, unregister = provider_door(parts.registry, provider_configs(parts.manifest))
    return dataclasses.replace(
        deps,
        map=parts.fanner.deps.map,
        routing_rules=routing_rules(parts.manifest),
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
