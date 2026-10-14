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
10.6c) the stores beyond the memory tables a quarantine taints (`taint_ledgers`), and (roadmap
step 8.3) the model servers it may host on its own Cell (`servers`, `register_provider`,
`unregister_provider`), and (roadmap step 11.6) what only a Swarm device's Warden is built with
(`device`), and (roadmap step 11.10) what a Warden that watches its idle Cell is built with
(`watch`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Built once per Warden by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    wardens.make_warden_deps` in tests. Calls into `hivemind.cell`, `hivemind.guard.policy`,
    `hivemind.forage` (SlotBinding), `hivemind.guard` (Capability, Enforcer),
    `hivemind.wardens.local_pool.hosting.config` (ServerConfig), `hivemind.wardens.watch`
    (WatchDeps),
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
    - hivemind.wardens.links for send_guarded, the one way code holding these links sends on
      them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from hivemind.basket import BasketStore, ResidentBasket
from hivemind.cell import NoopSnapshotter, RealCellSource, Snapshotter
from hivemind.exoskeleton import ExoskeletonConfig
from hivemind.exoskeleton.browser import BrowserLauncher
from hivemind.exoskeleton.recorder import RecordingStore
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.tempo import Tempo
from hivemind.guard import Capability, Enforcer
from hivemind.guard.policy import GuardPolicy, load_guard_policy
from hivemind.guard.scanner import ContentScanner, default_content_scanner
from hivemind.llm.ladders.gate import CallGate, DirectCallGate
from hivemind.llm.routing import RoutingRules
from hivemind.llm.slots import BoundModel
from hivemind.llm.transcription import Ears
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.memory.taint import TaintLedger
from hivemind.pheromone import PheromoneTrail
from hivemind.royal_jelly import RoyalJellyAccess
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
from hivemind.wardens.local_pool.hosting.config import (
    RegisterProvider,
    ServerConfig,
    UnregisterProvider,
)
from hivemind.wardens.trail_sync import TrailSync
from hivemind.wardens.watch import WatchDeps
from hivemind.workers import Worker
from waggle.clock import Clock
from waggle.codec import Signer, Verifier
from waggle.envelope import Hop
from waggle.messages.capping import CheckKind
from waggle.messages.task import WorkerRole
from waggle.transport.base import Transport

if TYPE_CHECKING:
    # Type-only: the offline package imports the tick handlers, which import this module.
    from hivemind.wardens.offline.outage import Outage, RemoteDeps

DEFAULT_DISK_RESERVE_MB = 1024  # Mirrors hivemind.manifest.schema.core.DEFAULT_DISK_RESERVE_MB.

__all__ = ["DEFAULT_DISK_RESERVE_MB", "DeviceDeps", "WardenDeps", "device_outage"]


@dataclass(frozen=True, slots=True)
class DeviceDeps:
    """What only the Warden of a Swarm device (a Real Cell the Hive borrows) is built with.

    A device's Warden runs on the Hive Stand at Level 0 of the runtime ladder while its hands are
    on the device, so a few things differ from the Hive Stand's own Warden. They are grouped in
    this one value so `WardenDeps` carries a single optional field for all of them, and so the
    steps that follow (colonization, watch mode, offline operation) extend this value instead of
    growing that class (codingrules 5.1).

    Attributes:
        release_lease_when_idle: Whether the Warden gives its lease back the moment it settles to
            WATCH with no sub-bee and no assignment waiting, and leases again when the next
            `TaskAssign` arrives (ADR-0054: a borrowed device keeps no Hive scratch between tasks).
        outage: What the Warden remembers while it is cut off (roadmap 11.9): the tasks it paused
            at a Handoff, when the cut began, and the wake that makes its tick look at it. None
            keeps today's behaviour: a Level 0 Warden then lets a lost Cell's commands fail.
        remote: Set for a Warden that dials the Queen from the device itself (Level 1): its
            reconnecting link, whose state decides OFFLINE, and the limit past which it clusters
            fully. None for a Warden on the Hive Stand, whose Queen link is in process and never
            goes down.
    """

    release_lease_when_idle: bool = True
    outage: Outage | None = None
    remote: RemoteDeps | None = None


@dataclass(frozen=True, slots=True)
class WardenDeps:
    """Every collaborator one Warden is built with.

    Attributes:
        source: Where this Warden's Cell comes from (`HiveStandSource`, a device's `SwarmSource`,
            `FakeCellSource` in tests); it leases and releases through it, never provisions.
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
        call_gate: The seat meter every model call passes through (a `FannerLane` in production,
            `DirectCallGate()` in tests); handed to every sub-bee's `WorkerContext` too.
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
            `AuditingCappingGate`'s after-the-fact sampling both call (roadmap step 4.10). Defaults
            to an unscripted `FakeJudgeReviewer`: reached unwired, it fails loudly.
        judge_rubrics: Every configured tier's rubric (`load_judge_rubrics`), read by the judge
            check and by audit sampling alike. Defaults to the shipped table.
        audit_sampler: Decides, per completed proposal, whether it is sampled for after-the-fact
            judge review (roadmap step 4.10). Defaults to a fresh, deterministic `AuditSampler()`.
        findings_sink: Where a sampled proposal's `AuditFinding` is deposited. Defaults to a
            fresh `InMemoryFindingsSink()` (the Honey Store's own Nectar intake is phase 7).
        audit_rates: Per-tier sampled/failed counts, the Guard Bee's own future read model
            (phase 10). Defaults to a fresh `AuditRates()`.
        carried_raises: The Guard Bee's audit-rate raises its grants carried (step 10.6).
        lane_for_grant: Builds a grant-attributed `CallGate` for one sub-bee (`Fanner.lane(tempo,
            grant_id=, goal_id=)` over this Warden's Fanner); `spawn_sub_bee` calls it once per
            spawn with the assignment's `Tempo`, so every `llm.call` carries its grant and goal
            id and the task's urgency reaches the Fanner's queue (codingrules section 8.14).
            `call_gate` stays the Warden's own unattributed lane, for its awake episodes.
        leave_policy: The leave policy every sub-bee's `CappingGate` decides an outside-scratch
            write against (roadmap step 5.0c). Defaults to the shipped `leave-policy.toml`.
        keep_root: The manifest's `[hive_stand] keep_root` (roadmap step 5.0e), or None; threaded
            into every sub-bee's `GateDeps.keep_root` and into `widen_lease_reachability`.
        leave_home: This Warden's own Cell's home directory, for `~`-rooted `leaves` pattern
            expansion (roadmap step 5.0c); defaults to this process's own home (the Hive Stand's).
        disk_reserve_mb: The manifest's `[hive_stand] disk_reserve_mb` (roadmap step 5.0e), a
            `keep` COPY's destination is refused against; threaded into every sub-bee's
            `GateDeps.disk_reserve_mb` unchanged. Defaults to `DEFAULT_DISK_RESERVE_MB` (a local
            mirror of the manifest's, so Layer 5 imports nothing from Layer 1 for one constant).
        trail_sync: Ships this node's local trail segment to the Queen on the heartbeat cadence and
            once more from `stop()` (codingrules section 12). `None` (the Hive Stand's default)
            means this Warden already records into the Queen's own store; `hivemind.cli.in_cell.
            deps` wires a `WaggleTrailSync` in, because a Virtual Cell's store dies with the Cell.
        watch: What a Warden that watches its idle Cell is built with (`WatchDeps`: probe,
            `[supervision.watch]` numbers, schedule); None watches nothing (roadmap step 11.10).
        snapshotter: Handed to every sub-bee's `CappingGate` as `GateDeps.snapshotter`. Defaults to
            `NoopSnapshotter()`; `hivemind.cli.in_cell.deps` wires a `RelaySnapshotter` in
            instead: a Virtual Cell's Warden cannot reach the host backend (ADR-0027), so it asks
            the Queen over `queen_link`.
        exoskeleton_config: How `hivemind.wardens.spawn.equip` equips a sub-bee whose task needs
            an Exoskeleton: screen size, readiness budget, whether the browser keeps its sandbox.
        browser_launcher: Starts a lease's browser (the Playwright launcher in production), or
            None where this Hive has no browser extra installed.
        recording_store: Where every sub-bee's GUI proposals are recorded (the flight recorder,
            roadmap step 6.6); None keeps no recordings.
        ears: The transcriber slot's resolved chain behind the Fanner's gate, handed to every
            sub-bee for its `listen` tool (roadmap step 6.5); None where no transcriber is bound.
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
            `llm:<slot>` is checked (`hivemind.forage.map.slot_for_binding`); empty by default.
        scanner: The untrusted-content scanner (roadmap step 10.6b) every sub-bee's tool results
            pass through (`WorkerContext.scanner`): built from `[guard.untrusted_content]` and the
            Hive's secret store on the Hive Stand; a Virtual Cell's gets the shipped defaults.
        local_providers: The `[llm.providers]` names that serve from this Warden's own machine
            (in process, or on its loopback: `hivemind.llm.registry.runs_locally`), so a binding
            under Night Veil is local only when every provider its fallback chain can reach is
            one of these (roadmap step 10.3a). Defaults to none, so Night Veil fails closed.
        taint_ledgers: The stores a quarantine taints beyond the memory tables (roadmap step
            10.6c): the Honey Store's Nectar ledger joins here in phase 7. Defaults to none.
        map: The live Forage map routing reads before a sub-bee is bound (roadmap step 8.1)
            and a hosted model server adds its models to (step 8.3); None binds the chain as is.
        routing_rules: `[llm.floors]`, `offline` and the spend cap as routing reads them (8.1);
            None binds the slot's own key with no routing decision and no `llm.fallback`.
        servers: Every `[llm.servers]` row this Warden may host on its Cell, by server id (roadmap
            step 8.3, ADR-0044); empty hosts none (a Virtual Cell's runtime config has none).
        register_provider: Registers a started server's provider and returns the live instance
            (`hosting.provider_door`, closed over the model registry); None cannot host a server.
        unregister_provider: Gives that provider back on stop; True when the caller must close it.
        royal_jelly: The Royal Jelly Lab's and the Basket's handles (roadmap phase 9); only the Hive
            Stand's Warden holds them, a Cell's Warden reaches the Basket through the Queen.
        resident_basket: This Warden's own resident view of the Basket (ADR-0048): the Hive Stand's
            (`RoyalJellyAccess.resident`) or, in a Cell, one over a store in its scratch.
        basket_store, node_signer, node_signer_id, hive_verifier: A Cell's Warden's own blob store
            and node keys: the signer of the manifests it deposits, the id its key is registered
            under at the Queen, and the verifier of the manifests it receives. None on the Hive
            Stand, whose store and Hive key are in `royal_jelly`.
        device: What only a Swarm device's Warden is built with (`DeviceDeps`); None for the Hive
            Stand's and a Virtual Cell's, which keep their lease for as long as they live.
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
    # Roadmap step 4.10 (judge review, sampled audit): additive and defaulted, so old callers build.
    judge_reviewer: JudgeReviewer = field(default_factory=FakeJudgeReviewer)
    judge_rubrics: Mapping[RiskTier, JudgeRubric] = field(default_factory=load_judge_rubrics)
    audit_sampler: AuditSampler = field(default_factory=AuditSampler)
    findings_sink: FindingsSink = field(default_factory=InMemoryFindingsSink)
    audit_rates: AuditRates = field(default_factory=AuditRates)
    carried_raises: CarriedAuditRaises = field(default_factory=CarriedAuditRaises)
    # Roadmap step 4.8 (per-grant lane attribution): defaulted to an unmetered gate, one lane.
    lane_for_grant: Callable[[str, str, Tempo], CallGate] = field(
        default_factory=lambda: _default_lane_for_grant
    )
    # Roadmap step 5.0c (leave policy): additive and defaulted. keep_root stays None until step 5.0e
    # wires [hive_stand] keep_root; leave_home defaults to this process's own home directory,
    # correct for the Hive Stand (v0's only Real Cell source).
    leave_policy: LeavePolicyTable = field(default_factory=load_leave_policy)
    keep_root: Path | None = None
    leave_home: Path = field(default_factory=Path.home)
    disk_reserve_mb: int = DEFAULT_DISK_RESERVE_MB  # Roadmap step 5.0e: defaulted like the rest.
    # Roadmap step 5.3 / ADR-0027: None by default, because only a Warden whose trail store does not
    # live on the Queen's own machine has anything to ship (`hivemind.wardens.trail_sync`).
    trail_sync: TrailSync | None = None
    watch: WatchDeps | None = None  # Roadmap step 11.10: set by a root that watches its idle Cell.
    # Roadmap step 5.10's snapshot relay: a no-op by default, so every earlier WardenDeps behaves.
    snapshotter: Snapshotter = field(default_factory=NoopSnapshotter)
    # Roadmap step 6.4 (ADR-0031): how a sub-bee that needs an Exoskeleton is equipped. Defaulted:
    # no browser launcher means a desktop still attaches, and a browser-only task is refused.
    exoskeleton_config: ExoskeletonConfig = field(default_factory=ExoskeletonConfig)
    browser_launcher: BrowserLauncher | None = None
    recording_store: RecordingStore | None = None  # Roadmap step 6.6; None records nothing.
    ears: Ears | None = None  # Roadmap step 6.5: handed to every sub-bee's WorkerContext.
    # Roadmap step 10.2: additive, defaulted to the shipped policy (every ceiling it grants today).
    guard: GuardPolicy = field(default_factory=load_guard_policy)
    # Roadmap step 10.3: additive, no rows by default; every composition root passes its own.
    bindings: tuple[SlotBinding, ...] = ()
    # Roadmap step 10.6b: additive; the shipped patterns with an in-memory key by default.
    scanner: ContentScanner = field(default_factory=default_content_scanner)
    # Roadmap step 10.3a: additive, none by default, so a Night Veil binding fails closed.
    local_providers: frozenset[str] = frozenset()
    taint_ledgers: tuple[TaintLedger, ...] = ()  # Roadmap step 10.6c: the quarantine taint seam.
    # Roadmap step 8.1: additive, None by default, so a pre-phase-8 caller binds the chain as is.
    map: ForageMap | None = None
    routing_rules: RoutingRules | None = None
    # Roadmap step 8.3: additive, nothing hosted by default, so every earlier caller builds as is.
    servers: Mapping[str, ServerConfig] = field(default_factory=dict)
    register_provider: RegisterProvider | None = None
    unregister_provider: UnregisterProvider | None = None
    # Roadmap phase 9: additive, defaulted to none; each composition root fills them.
    royal_jelly: RoyalJellyAccess | None = None
    resident_basket: ResidentBasket | None = None
    # Roadmap phase 9: a Cell's Warden's own blob store and keys (the Hive Stand's: `royal_jelly`).
    basket_store: BasketStore | None = None
    node_signer: Signer | None = None
    node_signer_id: str | None = None
    hive_verifier: Verifier | None = None
    # Roadmap step 11.6: set only by the Swarm's composition root (`hivemind.cli.compose.swarm`).
    device: DeviceDeps | None = None


def device_outage(deps: WardenDeps) -> Outage | None:
    """Return the outage a device Warden's deps carry, or None for every other Warden.

    The one place a tick handler reads it from, so the handlers need not import the offline
    package (which imports them).

    Args:
        deps: The Warden's collaborators.

    Returns:
        `deps.device.outage`, or None when there is no device or it keeps no outage.
    """
    return None if deps.device is None else deps.device.outage


def _default_lane_for_grant(grant_id: str, goal_id: str, tempo: Tempo) -> CallGate:
    """Return an unmetered DirectCallGate, ignoring every argument (WardenDeps's own default).

    A composition root that wants real per-grant Fanner attribution overrides `lane_for_grant`
    with a closure over its own `Fanner` (`hivemind.cli.compose.deps.build_warden_deps`); this
    default keeps every WardenDeps built without one behaving exactly like `call_gate`'s own
    default (`DirectCallGate()`, no metering).
    """
    del grant_id, goal_id, tempo  # Unused: this default carries no attribution or queue.
    return DirectCallGate()
