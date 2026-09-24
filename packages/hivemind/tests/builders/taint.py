"""Build taint test data: a memory context, a scripted taint judge, and the phase 7 seam's ledger.

Roadmap step 10.6d's tests need the memory tables over a trail they can read back, a
`ModelTaintJudge` over a `FakeLLMProvider` scripted to answer CLEAR or KEEP, a clearer the Guard
allows (the Queen, over the shipped policy), and something that holds Nectar and Honey items so
the phase 7 seam can be exercised before the Honey Store exists. `SeamLedger` is that last one: an
honest `TaintLedger` over NECTAR and HONEY items, kept here under tests/ on purpose, because the
Honey Store's real ledger is phase 7's to build and a stand-in shipped in src/ would be a second,
untested Honey Store.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/memory/taint and the taint contract suite.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock` (a fresh FakeClock
      by default), so a test run is deterministic.
    - `SeamLedger.write_taint` checks the label's transition table exactly as the memory stores do.

See Also:
    - hivemind.memory.taint for the label, the setter and the clearer these build around.
    - builders.memory for the Handoff, episode and Bee Bread builders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from builders.llm import make_bound

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.guard import (
    CapabilitySet,
    Enforcer,
    PrincipalRef,
    load_guard_policy,
    queen_principal,
    role_set,
)
from hivemind.llm import DirectCallGate, FakeLLMProvider, StopReason, TextPart, Usage
from hivemind.llm.models import LLMResponse
from hivemind.memory import InMemoryMemoryStore, MemoryContext, MemoryIdentity
from hivemind.memory.errors import TaintTargetNotFoundError
from hivemind.memory.taint import (
    ModelTaintJudge,
    TaintableItem,
    TaintedKind,
    TaintMarker,
    TaintScope,
    TaintSource,
    TaintStamp,
    TaintTarget,
    assert_transition,
    is_refused,
)
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, PheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.ids import TaskId, new_event_id, new_hive_id, new_node_id

__all__ = [
    "SeamLedger",
    "TaintWorld",
    "make_stamp",
    "make_taint_judge",
    "make_taint_world",
    "queen_clearer",
    "taint_verdict_response",
]


@dataclass(frozen=True, slots=True)
class TaintWorld:
    """One test's memory tables, their trail, and the Enforcer a clearing is checked by."""

    ctx: MemoryContext  # The in-memory tables, identity and clock.
    trail: MemoryPheromoneTrail  # What the tables and the Enforcer record to.
    enforcer: Enforcer  # The shipped Guard policy's Enforcer, on the same trail.
    clock: FakeClock  # Shared by all of the above.


def make_taint_world(clock: FakeClock | None = None) -> TaintWorld:
    """Build in-memory tables, a trail and an Enforcer that share one clock.

    Args:
        clock: Shared by every collaborator; a fresh FakeClock when omitted.

    Returns:
        A TaintWorld ready to seed, taint, clear and assemble against.
    """
    active_clock = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active_clock)
    hive_id, node_id = new_hive_id(active_clock), new_node_id(active_clock)
    identity = MemoryIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    ctx = MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=active_clock)
    cell_identity = CellIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    enforcer = Enforcer(load_guard_policy(), trail, active_clock, cell_identity)
    return TaintWorld(ctx=ctx, trail=trail, enforcer=enforcer, clock=active_clock)


def make_stamp(source: TaintSource = TaintSource.QUARANTINE, **overrides: object) -> TaintStamp:
    """Build a valid TaintStamp: a quarantine with a short, id-only reason.

    Args:
        source: Which setter is labelling.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TaintStamp.
    """
    fields: dict[str, object] = {"source": source, "reason": "Quarantined after a Guard report."}
    fields.update(overrides)
    return TaintStamp(**fields)  # type: ignore[arg-type]  # a pydantic model; see builders/llm


def taint_verdict_response(judgement: str, reasons: tuple[str, ...] = ()) -> LLMResponse:
    """Build the JSON a full-capability FakeLLMProvider returns for one taint review.

    Args:
        judgement: "CLEAR" or "KEEP".
        reasons: The reasons the judge gives.

    Returns:
        An LLMResponse the NATIVE rung parses straight into the judge's private schema.
    """
    body = {"judgement": judgement, "reasons": list(reasons)}
    return LLMResponse(
        parts=(TextPart(text=json.dumps(body)),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=10, output_tokens=5),
        model="test-model",
    )


def make_taint_judge(*judgements: str) -> tuple[ModelTaintJudge, FakeLLMProvider]:
    """Build a ModelTaintJudge on the JUDGE slot whose provider answers `judgements` in order.

    Args:
        *judgements: "CLEAR" or "KEEP", one per review the test expects.

    Returns:
        The judge, and its provider (whose `calls` show exactly what the judge was shown).
    """
    provider = FakeLLMProvider(name="judge-provider")
    for judgement in judgements:
        provider.script(taint_verdict_response(judgement, (f"Judged {judgement}.",)))
    judge = ModelTaintJudge(
        bound=make_bound(slot=ModelSlot.JUDGE, provider=provider), gate=DirectCallGate()
    )
    return judge, provider


def queen_clearer(ctx: MemoryContext) -> tuple[PrincipalRef, CapabilitySet]:
    """Return the Queen as a clearer: her principal and her shipped role set (honey:clearance:c2).

    Args:
        ctx: The world's memory context; its Hive id is the Queen's principal id.

    Returns:
        `(principal, held)` for a `TaintClearRequest`.
    """
    policy = load_guard_policy()
    return queen_principal(ctx.identity.hive_id), role_set(policy, "queen", None)


@dataclass
class _SeamItem:
    """One Nectar or Honey item the seam ledger holds."""

    target: TaintTarget
    text: str
    clearance: HoneyClearance
    author: str | None
    task_id: TaskId | None
    at: datetime
    marker: TaintMarker | None = None


@dataclass
class SeamLedger:
    """A TaintLedger over Nectar and Honey items: the phase 7 seam's stand-in, for tests only.

    Records every label write's event on `trail` before changing the label, as the memory stores
    do, and checks the transition table against the stored label first.
    """

    trail: PheromoneTrail
    items: dict[str, _SeamItem] = field(default_factory=dict)

    def add(self, kind: TaintedKind, text: str, clock: Clock, **facts: object) -> TaintTarget:
        """Hold one new item of `kind` (NECTAR or HONEY), unlabelled; return its target."""
        target = TaintTarget(kind=kind, item_id=new_event_id(clock))
        self.items[target.item_id] = _SeamItem(
            target=target,
            text=text,
            clearance=facts.get("clearance", HoneyClearance.C1),  # type: ignore[arg-type]
            author=facts.get("author"),  # type: ignore[arg-type]
            task_id=facts.get("task_id"),  # type: ignore[arg-type]
            at=clock.now(),
        )
        return target

    def marker_of(self, target: TaintTarget) -> TaintMarker | None:
        """Return the current label of `target`."""
        return self.items[target.item_id].marker

    async def find_taintable(self, scope: TaintScope) -> tuple[TaintTarget, ...]:
        """See `TaintLedger.find_taintable`."""
        return tuple(
            item.target
            for item in sorted(self.items.values(), key=lambda held: (held.at, held.target.item_id))
            if not is_refused(item.marker)
            and scope.covers(item.target.kind, item.author, item.task_id, item.at)
        )

    async def read_taintable(self, target: TaintTarget) -> TaintableItem:
        """See `TaintLedger.read_taintable`."""
        item = self._item(target)
        return TaintableItem(
            target=target, marker=item.marker, clearance=item.clearance, content=item.text
        )

    async def write_taint(
        self, target: TaintTarget, marker: TaintMarker, event: MemoryEvent
    ) -> None:
        """See `TaintLedger.write_taint`."""
        item = self._item(target)
        before = item.marker.state if item.marker is not None else None
        assert_transition(before, marker.state, target.item_id)
        await self.trail.record(event)
        item.marker = marker

    def _item(self, target: TaintTarget) -> _SeamItem:
        """Return the held item, or raise as a ledger does for an unknown one."""
        if target.item_id not in self.items:
            raise TaintTargetNotFoundError(target.kind.value, target.item_id)
        return self.items[target.item_id]
