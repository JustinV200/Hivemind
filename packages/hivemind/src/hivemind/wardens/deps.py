"""Define WardenDeps: everything one Warden is built with, wired by its composition root.

`hivemind.wardens.warden.Warden.__init__` takes a `WardenId` and this one bundle (codingrules
section 5.1: "Introduce a frozen dataclass for the argument group"), the same role
`hivemind.workers.runtime.deps.RuntimeDeps` plays for a `WorkerRuntime` and
`hivemind.supervision.capping.gate.GateDeps` plays for a `CappingGate`. It carries every
collaborator a Warden's tick touches: where its Cell comes from (`source`), how it talks to the
Queen (`queen_link`, `hop`), where it reads and writes memory and the trail (`memory`, `trail`,
`identity`, `clock`), its escalation playbook and Capping tier table (`policy`, `tiers`, `checks`),
its own model binding for awake episodes (`bound`) and the seat meter every model call passes
through (`call_gate`), how it builds a fresh role implementation per sub-bee (`worker_factory`) and
how it moves a stuck sub-bee to a stronger binding within its own grant (`rebind`), the cadence
and threshold constants its tick handlers read (`handoff_threshold`, `heartbeat_interval_s`,
`worker_heartbeat_interval_s`, `missed_heartbeats_before_stalled`), the Guard policy its own
capability set and every sub-bee's role default are built from (`guard`), and (roadmap step 10.3,
ADR-0039) the Guard's `Enforcer` its enforcement points and its sub-bees' tools call
(`enforcer`), the capability its own Cell's lease needs (`lease_capability`) and the `[llm.slots]`
rows a rebind's target is resolved to a slot against (`bindings`), and (roadmap step 10.6b) the
untrusted-content scanner its sub-bees' tool results pass through (`scanner`), and (roadmap step
10.6c) the stores beyond the memory tables a quarantine taints (`taint_ledgers`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Built once per Warden by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    wardens.make_warden_deps` in tests. Calls into `hivemind.cell`, `hivemind.guard.policy`,
    `hivemind.forage` (SlotBinding), `hivemind.guard` (Capability, Enforcer),
    `hivemind.llm.ladders.gate`, `hivemind.llm.slots`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.supervision`, `hivemind.supervision.capping`, `hivemind.workers` and waggle only.

Key invariants:
    - `WardenDeps` is frozen and slotted (codingrules section 8.5): it is not itself read from or
      written to JSON/TOML, so it is a dataclass, not a pydantic BaseModel, matching `RuntimeDeps`
      and `GateDeps`.
    - `source` is a `RealCellSource`: the Warden leases and releases through it, but never
      provisions (codingrules section 8.8, CLAUDE.md's "Wardens never provision Cells").
    - `bound` is scoped to `hivemind.forage.slots.ModelSlot.WARDEN`, for awake episodes only; a
      sub-bee's own `BoundModel` is resolved separately, per its `TaskAssign.slot`, by
      `hivemind.wardens.spawn.spawn`.
    - `lease_capability` is set by the composition root that knows which Cell it built
      (`cell:hive_stand`, `cell:virtual`), never derived from a Cell's kind (codingrules 8.7).

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit this bundle exists to keep.
    - .claude/codingrules.md section 8.8 for the Warden's own shape: its Cell, its grant, its
      Attendant, its autopilot and awake modes.
    - hivemind.workers.runtime.deps for RuntimeDeps, the pattern this bundle follows for a
      sub-bee's own runtime.
    - hivemind.wardens.warden for Warden, this bundle's one consumer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from hivemind.cell import NoopSnapshotter, RealCellSource, Snapshotter
from hivemind.forage.map import SlotBinding
from hivemind.forage.tempo import Tempo
from hivemind.guard import Capability, Enforcer
from hivemind.guard.policy import GuardPolicy, load_guard_policy
from hivemind.guard.scanner import ContentScanner, default_content_scanner
from hivemind.llm.ladders.gate import CallGate, DirectCallGate
from hivemind.llm.slots import BoundModel
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.memory.taint import TaintLedger
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import EscalationPolicy
from hivemind.supervision.capping import (
    AuditRates,
    AuditSampler,
    CarriedAuditRaises,
    FakeJudgeReviewer,
    FindingsSink,
    InMemoryFindingsSink,
    JudgeReviewer,
    JudgeRubric,
    RiskTier,
    load_judge_rubrics,
)
from hivemind.supervision.capping.checks import Check
from hivemind.supervision.capping.leave import LeavePolicyTable, load_leave_policy
from hivemind.supervision.capping.tiers import TierTable
from hivemind.wardens.trail_sync import TrailSync
from hivemind.workers import Worker
from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.messages.capping import CheckKind
from waggle.messages.task import WorkerRole
from waggle.transport.base import Transport

DEFAULT_DISK_RESERVE_MB = 1024  # Mirrors hivemind.manifest.schema.core.DEFAULT_DISK_RESERVE_MB.

__all__ = ["DEFAULT_DISK_RESERVE_MB", "WardenDeps"]


@dataclass(frozen=True, slots=True)
class WardenDeps:
    """Every collaborator one Warden is built with.

    Attributes:
        source: Where this Warden's Cell comes from; `HiveStandSource` in production,
            `hivemind.cell.fake.FakeCellSource` in tests. The Warden leases and releases through
            it and never provisions.
        queen_link: This Warden's own end of its Waggle link to the Queen.
        hop: This Warden's own address (`sender`), the Queen's (`recipient`) and the sending
            node's id, stamped on every envelope this Warden wraps and sends to the Queen.
        memory: Where this Warden reads and writes Pins, Notes, Handoffs and episodes.
        trail: The Pheromone Trail this Warden records every `warden.*` event to.
        identity: The Hive, node and actor this Warden stamps on every memory write it makes.
        clock: Injected time source for every id minted, every timestamp written and every sleep.
        policy: This Warden's escalation playbook, read for every Alarm it handles.
        tiers: The Capping tier table every sub-bee's `CappingGate` is built with.
        checks: The Capping check registry every sub-bee's `CappingGate` is built with; v0's
            `hivemind.supervision.capping.checks.deterministic_checks()`.
        bound: This Warden's own model binding (`ModelSlot.WARDEN`), for awake episodes only.
        call_gate: The seat meter every model call passes through; a `FannerLane` in production,
            `hivemind.llm.ladders.gate.DirectCallGate()` in tests. Handed to every sub-bee's
            `WorkerContext` too.
        worker_factory: Builds a fresh role implementation for a `TaskAssign.role`.
        rebind: Resolves a named `[llm.slots]` binding key to a fresh `BoundModel`, for a REBIND
            within the sub-bee's grant (`hivemind.llm.slots.resolve_key`, closed over this
            Warden's own provider lookup and Forage map).
        handoff_threshold: The manifest's `[memory] handoff_threshold` fraction, handed to every
            sub-bee's `WorkerContext`.
        heartbeat_interval_s: How often this Warden sends its own Heartbeat to the Queen.
        worker_heartbeat_interval_s: How often each sub-bee this Warden spawns sends its own
            Heartbeat back to this Warden.
        missed_heartbeats_before_stalled: How many of a sub-bee's own heartbeat intervals may
            elapse with nothing heard before this Warden raises `AlarmKind.WORKER_STALLED`.
        judge_reviewer: The independent `JudgeReviewer` `checks[CheckKind.JUDGE]` and
            `hivemind.wardens.spawn.audited_gate.AuditingCappingGate`'s own after-the-fact
            sampling both call (roadmap step 4.10). Additive: defaults to a `FakeJudgeReviewer`
            with nothing scripted, so a WardenDeps built before this field existed still
            constructs, and any tier that actually reaches for it without a real one wired in
            fails loudly (`JudgeUnavailableError`) rather than silently approving.
        judge_rubrics: Every configured tier's rubric (`hivemind.supervision.capping.checks.
            rubrics.load_judge_rubrics`), read by `checks[CheckKind.JUDGE]` and by audit
            sampling alike. Defaults to the shipped table.
        audit_sampler: Decides, per completed proposal, whether it is sampled for after-the-fact
            judge review (roadmap step 4.10). Defaults to a fresh, deterministic
            `AuditSampler()`.
        findings_sink: Where a sampled proposal's `AuditFinding` is deposited. Defaults to a
            fresh `InMemoryFindingsSink()` (the Honey Store's own Nectar intake is phase 7).
        audit_rates: Per-tier sampled/failed counts, the Guard Bee's own future read model
            (phase 10). Defaults to a fresh `AuditRates()`.
        carried_raises: The Guard Bee's audit-rate raises its grants carried (step 10.6).
        lane_for_grant: Builds a grant-attributed `CallGate` for one sub-bee
            (`hivemind.llm.fanner.Fanner.lane(tempo, grant_id=, goal_id=)`, closed over this
            Warden's own Fanner); `hivemind.wardens.spawn.spawn_sub_bee` calls it once per
            spawn with the assignment's own `Tempo`, so every `llm.call` a sub-bee makes
            carries its grant and goal id and its task's urgency reaches the Fanner's queue
            ordering and spill threshold (codingrules section 8.14). `call_gate` above stays
            the Warden's own unattributed lane, used for its own awake episodes and by any
            test that never names this field.
        leave_policy: The leave policy every sub-bee's `CappingGate` decides an outside-scratch
            write against (roadmap step 5.0c). Defaults to the shipped `leave-policy.toml`.
        keep_root: The manifest's `[hive_stand] keep_root` (roadmap step 5.0e), or None; threaded
            into every sub-bee's `GateDeps.keep_root` unchanged, and into
            `hivemind.wardens.spawn.spawn._widen_lease_reachability`.
        leave_home: This Warden's own Cell's home directory, for `~`-rooted `leaves` pattern
            expansion (roadmap step 5.0c); defaults to this process's own home, correct for the
            Hive Stand (v0's only Real Cell source).
        disk_reserve_mb: The manifest's own `[hive_stand] disk_reserve_mb` (roadmap step 5.0e), a
            `keep` COPY's destination is refused against; threaded into every sub-bee's
            `GateDeps.disk_reserve_mb` unchanged. Defaults to `DEFAULT_DISK_RESERVE_MB` (this
            module's own mirror of `hivemind.manifest.schema.core.DEFAULT_DISK_RESERVE_MB`, kept
            local rather than imported so this Layer 5 module does not reach into Layer 1 for one
            constant), so a WardenDeps built before this dispatch keeps checking against a sane
            figure rather than skipping the check silently.
        trail_sync: Ships this node's own local trail segment to the Queen on this Warden's own
            heartbeat cadence and once more from `stop()` (codingrules section 12: "a Warden that
            is offline writes to its local segment; on reconnection the segment merges into the
            Queen's trail"). `None` -- the default, and what the Hive Stand's own composition root
            leaves it as -- means this Warden already records into the Queen's own store, so there
            is nothing to ship; `hivemind.cli.in_cell.deps` wires a `hivemind.wardens.trail_sync.
            WaggleTrailSync` in, because a Virtual Cell's store dies with the container.
        snapshotter: Handed to every sub-bee's own `CappingGate` (`hivemind.wardens.spawn.spawn.
            _build_capping_gate`) as `GateDeps.snapshotter`. Defaults to `NoopSnapshotter()`
            (every Real Cell source's own answer, and the Hive Stand's own composition root's,
            since the Hive Stand can build a real `hivemind.hive.snapshot.snapshotter_for` one
            directly). `hivemind.cli.in_cell.deps` wires a `hivemind.wardens.snapshot_relay.
            RelaySnapshotter` in instead: a Virtual Cell's own Warden cannot reach the host
            backend itself (ADR-0027), so it asks the Queen over `queen_link` (roadmap step
            5.10's own follow-up gap).
        guard: The Guard policy (ADR-0039, roadmap step 10.2) this Warden's own set is built
            from at `start()` (`hivemind.guard.policy.warden_set`: the `warden` role default
            narrowed to its lease's access level, less the deny list) and every sub-bee's role
            default at spawn (`role_set`). Defaults to the shipped policy
            (`load_guard_policy()`); `hivemind.cli.compose.deps` builds it from the manifest's
            `[guard]` table.
        enforcer: The Guard's adapter (roadmap step 10.3) every enforcement point of this Warden
            and of its sub-bees' tools calls: lease creation, slot binding, question routing,
            tool invocation and session calls outside scratch; each refusal becomes a
            `guard.denied` on this Warden's own trail. Built by the composition root over the
            same policy as `guard`.
        lease_capability: What this Warden's own Cell's lease needs of its set (roadmap step
            10.3's `lease_creation` point): `cell:hive_stand` for the Hive Stand's Warden,
            `cell:virtual` for a Virtual Cell's, `cell:real:<node>` for a Swarm device's. Set by
            the composition root that built the Cell source, never derived from a Cell's kind.
        bindings: Every `[llm.slots]` row, forage-side, so a rebind to a named binding
            (`local_worker`) resolves to the slot whose fallback chain names it before its
            `llm:<slot>` is checked (`hivemind.forage.map.slot_for_binding`). Defaults to empty:
            only a slot's own key then resolves, and any named binding is refused.
        scanner: The untrusted-content scanner (roadmap step 10.6b) every sub-bee's tool results
            pass through (`WorkerContext.scanner`): the Hive Stand's composition root builds it
            from `[guard.untrusted_content]` and the Hive's secret store; a Virtual Cell's Warden
            gets the shipped patterns and thresholds with a key that lives and dies with the Cell.
        local_providers: The `[llm.providers]` names that serve from this Warden's own machine
            (in process, or on its loopback: `hivemind.llm.registry.runs_locally`), so a binding
            under Night Veil is local only when every provider its fallback chain can reach is
            one of these (roadmap step 10.3a). Defaults to none: nothing is shown local, so a
            Night Veil binding fails closed.
        taint_ledgers: The stores a quarantine taints beyond the memory tables (roadmap step
            10.6c): the Honey Store's Nectar ledger joins here in phase 7. Defaults to none.
    """

    source: RealCellSource
    queen_link: Transport
    hop: Hop
    memory: MemoryStore
    trail: PheromoneTrail
    identity: MemoryIdentity
    clock: Clock
    policy: EscalationPolicy
    tiers: TierTable
    checks: Mapping[CheckKind, Check]
    bound: BoundModel
    call_gate: CallGate
    worker_factory: Callable[[WorkerRole], Worker]
    rebind: Callable[[str], BoundModel]
    handoff_threshold: float
    heartbeat_interval_s: float
    worker_heartbeat_interval_s: float
    missed_heartbeats_before_stalled: int
    enforcer: Enforcer
    lease_capability: Capability
    # Roadmap step 4.10 (judge review and sampled audit): additive fields, every one defaulted so
    # a WardenDeps built before this dispatch (every existing test) keeps building unchanged.
    judge_reviewer: JudgeReviewer = field(default_factory=FakeJudgeReviewer)
    judge_rubrics: Mapping[RiskTier, JudgeRubric] = field(default_factory=load_judge_rubrics)
    audit_sampler: AuditSampler = field(default_factory=AuditSampler)
    findings_sink: FindingsSink = field(default_factory=InMemoryFindingsSink)
    audit_rates: AuditRates = field(default_factory=AuditRates)
    carried_raises: CarriedAuditRaises = field(default_factory=CarriedAuditRaises)
    # Roadmap step 4.8's own wiring step (per-grant lane attribution): additive, defaulted to an
    # unmetered DirectCallGate that ignores grant_id/goal_id (matching `call_gate`'s own default
    # in every builder that never names either field), so a WardenDeps built before this dispatch
    # keeps today's single-lane behaviour.
    lane_for_grant: Callable[[str, str, Tempo], CallGate] = field(
        default_factory=lambda: _default_lane_for_grant
    )
    # Roadmap step 5.0c (leave policy): additive fields, every one defaulted so a WardenDeps built
    # before this dispatch (every existing test) keeps building unchanged. keep_root stays None
    # until roadmap step 5.0e wires [hive_stand] keep_root; leave_home defaults to this process's
    # own home directory, correct for the Hive Stand (v0's only Real Cell source).
    leave_policy: LeavePolicyTable = field(default_factory=load_leave_policy)
    keep_root: Path | None = None
    leave_home: Path = field(default_factory=Path.home)
    # Roadmap step 5.0e: additive, defaulted like every field above it.
    disk_reserve_mb: int = DEFAULT_DISK_RESERVE_MB
    # Roadmap step 5.3 / ADR-0027: additive and defaulted to None, because only a Warden whose
    # trail store does not already live on the Queen's own machine has anything to ship
    # (`hivemind.wardens.trail_sync`'s own module docstring); every existing composition root and
    # every existing test keeps today's behaviour by never naming this field.
    trail_sync: TrailSync | None = None
    # Roadmap step 5.10's own follow-up gap (the snapshot relay): additive, defaulted to
    # NoopSnapshotter so a WardenDeps built before this dispatch (every existing test) keeps
    # building and behaving unchanged.
    snapshotter: Snapshotter = field(default_factory=NoopSnapshotter)
    # Roadmap step 10.2 (the Guard policy): additive, defaulted to the shipped policy so a
    # WardenDeps built before this dispatch keeps building; the shipped Warden and Drone defaults
    # reproduce every capability today's access-level ceilings granted.
    guard: GuardPolicy = field(default_factory=load_guard_policy)
    # Roadmap step 10.3: additive and defaulted to no rows, so a WardenDeps built without a slot
    # table still builds; every composition root passes its own `[llm.slots]` rows.
    bindings: tuple[SlotBinding, ...] = ()
    # Roadmap step 10.6b: additive and defaulted to the shipped patterns and thresholds with an
    # in-memory key, so a WardenDeps built without one (every test, a Virtual Cell) still scans.
    scanner: ContentScanner = field(default_factory=default_content_scanner)
    # Roadmap step 10.3a: additive and defaulted to none, so a Night Veil binding fails closed
    # unless the composition root states which providers run on this machine.
    local_providers: frozenset[str] = frozenset()
    # Roadmap step 10.6c: the phase 7 seam a quarantine's taint reaches Nectar through.
    taint_ledgers: tuple[TaintLedger, ...] = ()


def _default_lane_for_grant(grant_id: str, goal_id: str, tempo: Tempo) -> CallGate:
    """Return an unmetered DirectCallGate, ignoring every argument (WardenDeps's own default).

    A composition root that wants real per-grant Fanner attribution overrides `lane_for_grant`
    with a closure over its own `Fanner` (`hivemind.cli.compose.deps.build_warden_deps`); this
    default keeps every WardenDeps built without one behaving exactly like `call_gate`'s own
    default (`DirectCallGate()`, no metering).
    """
    del grant_id, goal_id, tempo  # Unused: this default carries no attribution or queue.
    return DirectCallGate()
