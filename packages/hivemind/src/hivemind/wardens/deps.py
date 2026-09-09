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
how it moves a stuck sub-bee to a stronger binding within its own grant (`rebind`), and the cadence
and threshold constants its tick handlers read (`handoff_threshold`, `heartbeat_interval_s`,
`worker_heartbeat_interval_s`, `missed_heartbeats_before_stalled`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Built once per Warden by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    wardens.make_warden_deps` in tests. Calls into `hivemind.cell`, `hivemind.llm.ladders.gate`,
    `hivemind.llm.slots`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.supervision`,
    `hivemind.supervision.capping`, `hivemind.workers` and waggle only.

Key invariants:
    - `WardenDeps` is frozen and slotted (codingrules section 8.5): it is not itself read from or
      written to JSON/TOML, so it is a dataclass, not a pydantic BaseModel, matching `RuntimeDeps`
      and `GateDeps`.
    - `source` is a `RealCellSource`: the Warden leases and releases through it, but never
      provisions (codingrules section 8.8, CLAUDE.md's "Wardens never provision Cells").
    - `bound` is scoped to `hivemind.forage.slots.ModelSlot.WARDEN`, for awake episodes only; a
      sub-bee's own `BoundModel` is resolved separately, per its `TaskAssign.slot`, by
      `hivemind.wardens.spawn.spawn`.

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
from dataclasses import dataclass

from hivemind.cell import RealCellSource
from hivemind.llm.ladders.gate import CallGate
from hivemind.llm.slots import BoundModel
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import EscalationPolicy
from hivemind.supervision.capping.checks import Check
from hivemind.supervision.capping.tiers import TierTable
from hivemind.workers import Worker
from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.messages.capping import CheckKind
from waggle.messages.task import WorkerRole
from waggle.transport.base import Transport

__all__ = ["WardenDeps"]


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
