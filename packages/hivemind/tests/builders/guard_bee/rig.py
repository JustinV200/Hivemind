"""Build a Guard Bee over fakes, with a recording Queen door and a scriptable judge slot.

`make_guard_bee` builds what `hivemind.workers.roles.guard_bee.build_guard_bee` builds, over an
in-memory trail and a FakeClock shared by everything: the shipped rules (or a `[guard]` section
with overrides), the shipped tier table and Guard policy (or others), a `RecordingDoor` standing
in for the Queen's door (another roadmap step builds hers), an in-memory C2 sink, and a judge slot
bound to a FakeLLMProvider a test scripts with `judge_reply`. `GuardBeeRig` carries every part a
test drives or inspects, and a `TrailSeeder` over the same trail. `restart_guard_bee` builds the
same Hive's Guard Bee again over a rig's trail, clock and node, as a restart of the Hive Stand does.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`.

Key invariants:
    - `RecordingDoor` refuses a report that is not a request, as the Protocol says the Queen does.
    - A restarted rig shares its trail, clock and node identity with the rig it restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from builders.cells import make_identity
from builders.guard_bee.seeds import TrailSeeder
from builders.llm import make_bound

from hivemind.cell import CellIdentity
from hivemind.forage import ModelSlot
from hivemind.guard import GuardConfidence, GuardPolicy, GuardReport, load_guard_policy
from hivemind.llm import (
    BoundModel,
    DirectCallGate,
    FakeLLMProvider,
    LLMResponse,
    StopReason,
    TextPart,
    Usage,
)
from hivemind.manifest.schema.guard import GuardSection
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.supervision.capping import TierTable, load_tiers
from hivemind.workers.roles.guard_bee import (
    GuardBee,
    GuardBeeInputs,
    InMemoryGuardReportSink,
    Response,
    build_guard_bee,
)
from waggle.clock import FakeClock

__all__ = [
    "GuardBeeRig",
    "RecordingDoor",
    "RigOptions",
    "judge_reply",
    "make_guard_bee",
    "restart_guard_bee",
]

_EVERYTHING = TrailQuery(limit=10_000)  # More than any test here records.


class RecordingDoor:
    """A GuardRequestDoor that keeps every request filed through it."""

    def __init__(self) -> None:
        """Build a door nothing has been filed through yet."""
        self.filed: list[GuardReport] = []
        self.shown: list[GuardReport] = []

    async def file_guard_request(self, report: GuardReport) -> None:
        """Keep `report`; refuse one that asks the Queen for nothing, as her door does."""
        if not report.is_request:
            raise ValueError(f"{report.id} is not a request; it is a guard.alert alone.")
        self.filed.append(report)

    async def report_to_human(self, report: GuardReport) -> None:
        """Keep `report`; refuse one below CRITICAL, as her door does (roadmap step 10.6a)."""
        if report.confidence is not GuardConfidence.CRITICAL:
            raise ValueError(f"{report.id} is not CRITICAL; only a CRITICAL report is shown.")
        self.shown.append(report)


def judge_reply(confidence: str, action: str) -> LLMResponse:
    """Build the JSON reply a full-capability FakeLLMProvider gives for one Guard review."""
    body = {"confidence": confidence, "action": action}
    return LLMResponse(
        parts=(TextPart(text=json.dumps(body)),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=10, output_tokens=5),
        model="test-model",
    )


@dataclass(slots=True)
class GuardBeeRig:
    """A Guard Bee over fakes, and everything a test inspects or drives around it."""

    bee: GuardBee
    trail: PheromoneTrail  # In memory; a Night Veil test's is the boundary's VeiledTrail over it.
    clock: FakeClock
    identity: CellIdentity
    door: RecordingDoor
    sink: InMemoryGuardReportSink
    judge: FakeLLMProvider
    seed: TrailSeeder
    interval_s: float = field(default=5.0)

    async def round(self) -> tuple[Response, ...]:
        """Advance the clock by one interval and run the round that makes due."""
        self.clock.advance(self.interval_s)
        return await self.bee.tick()

    async def alerts(self) -> list[PheromoneEvent]:
        """Every guard.alert on the trail, oldest first."""
        return await self.kinds("guard.alert")

    async def kinds(self, kind: str) -> list[PheromoneEvent]:
        """Every event of `kind` on the trail, oldest first."""
        return [event for event in await self.trail.query(_EVERYTHING) if event.kind == kind]


@dataclass(frozen=True, slots=True)
class RigOptions:
    """What a test changes: `[guard]`, tiers, policy, and a shared clock, trail or node."""

    guard: GuardSection = field(default_factory=GuardSection)
    tiers: TierTable | None = None
    policy: GuardPolicy | None = None
    clock: FakeClock | None = None
    trail: PheromoneTrail | None = None
    identity: CellIdentity | None = None


def make_guard_bee(options: RigOptions | None = None) -> GuardBeeRig:
    """Build a Guard Bee over fakes; see the module docstring for what each part is.

    Args:
        options: What differs from the defaults; `restart_guard_bee` passes another rig's
            clock, trail and identity to build the same Hive's Guard Bee after a restart.

    Returns:
        The rig: the Guard Bee, its trail, clock, identity, door, sink, judge provider, seeder.
    """
    active = options if options is not None else RigOptions()
    clock = active.clock if active.clock is not None else FakeClock()
    trail = active.trail if active.trail is not None else MemoryPheromoneTrail(clock)
    # A fresh Hive and node unless restarting one: the node is what marks an alert as its own.
    identity = active.identity if active.identity is not None else make_identity(clock)
    door, sink = RecordingDoor(), InMemoryGuardReportSink()
    judge = FakeLLMProvider(name="judge-provider")
    inputs = GuardBeeInputs(
        trail=trail,
        clock=clock,
        identity=identity,
        door=door,
        guard=active.guard,
        tiers=active.tiers if active.tiers is not None else load_tiers(),
        policy=active.policy if active.policy is not None else load_guard_policy(),
        bound_for=_judge_only(make_bound(slot=ModelSlot.JUDGE, binding="judge", provider=judge)),
        call_gate=DirectCallGate(),
        sink=sink,
    )
    return GuardBeeRig(
        bee=build_guard_bee(inputs),
        trail=trail,
        clock=clock,
        identity=identity,
        door=door,
        sink=sink,
        judge=judge,
        seed=TrailSeeder(trail, clock, identity),
        interval_s=active.guard.bee.interval_s,
    )


def restart_guard_bee(rig: GuardBeeRig, options: RigOptions | None = None) -> GuardBeeRig:
    """Build `rig`'s Guard Bee again, as a restart does: same trail, clock and node, fresh state.

    Args:
        rig: The Guard Bee before the restart; nothing of its in-memory state is carried over.
        options: What else differs after the restart (a new `[guard]` section, say).

    Returns:
        A new rig over `rig`'s trail, clock and identity, with its own door and sink.
    """
    active = options if options is not None else RigOptions()
    return make_guard_bee(replace(active, clock=rig.clock, trail=rig.trail, identity=rig.identity))


def _judge_only(bound: BoundModel) -> _SlotResolver:
    """Resolve the judge slot to `bound` and refuse every other, as an unbound manifest does."""
    return _SlotResolver(bound)


class _SlotResolver:
    """A `bound_for` that knows only the judge slot."""

    def __init__(self, bound: BoundModel) -> None:
        self._bound = bound

    def __call__(self, slot: ModelSlot) -> BoundModel:
        if slot is not ModelSlot.JUDGE:
            raise KeyError(slot)
        return self._bound
