"""Build a QueenDeps over fakes, one attached WardenLink, and WardenEnd: its own end of the wire.

`make_queen_deps` builds every collaborator `hivemind.queen.queen.Queen` needs, over fakes and the
shipped default supervision data: an in-memory `hivemind.brood_chamber.BroodChamber`
(`MemoryTaskStore`), `hivemind.memory.InMemoryMemoryStore`, `hivemind.pheromone.trail.memory.
MemoryPheromoneTrail`, a `FakeClock` shared by every collaborator, `docs/supervision/default-
policy.toml` loaded for real (the same table production loads), a `hivemind.llm.DirectCallGate`
(no metering), a `ForageMap` with one `"fake-worker"` source declaring `FORAGE_SOURCE_SEATS` seats
(enough to clear QueenDeps' own default `RoyalReserve`, module constant's own comment), and
`bound_for`/`rebind` over a scriptable `FakeLLMProvider`, resolving four `[llm.slots]` rows:
`"queen"`, `"attendant"`, `"worker"` (whose `fallback` is `"worker_fallback"`, so an e2e REBIND
has somewhere to go) and
`"worker_fallback"`, (roadmap step 10.5) an in-memory goal-request table and chat log over the
same trail, and (roadmap step 10.3) a `hivemind.guard.Enforcer` over the shipped Guard
policy, built last so it records to whichever trail the test ended up with (`with_guard_policy`
swaps in an Enforcer over another policy, the same trail and clock). It also builds one
`hivemind.queen.deps.WardenLink` for the test to attach over a fresh
`waggle.transport.memory.MemoryTransport` pair, and `WardenEnd`, the Warden-side half of that same
pair: it wraps the Warden's own end, mirroring `builders.wardens.QueenEnd` with the direction
reversed -- it sends `Heartbeat`/`TaskResult`/`AlarmRaised`/`Question` (what a Warden reports) and
sorts what it receives into `grants`/`assignments`/`answers`/`intervenes`/`forage_replies` (what a
Warden is sent).
`plan_responder` builds a `FakeLLMProvider` `Responder` for a test that only exercises
`hivemind.queen.planner.plan_goal`/`Queen.submit_goal`: it pulls the goal text back out of the
rendered `decompose_goal` system prompt's own `<<<user>>>` section and answers with
`build_plan(goal)`'s JSON.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/queen.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic; `make_queen_deps` shares one clock across
      every collaborator it builds, including the attached `WardenLink`/`WardenEnd`.
    - `WardenEnd.pump_until` never blocks forever: it gives up and raises `AssertionError` after
      `DEFAULT_PUMP_LIMIT` envelopes, matching `builders.wardens.QueenEnd`'s own contract.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.queen.deps for QueenDeps, MemoryBudget and WardenLink, the bundles this module
      builds.
    - builders.wardens for QueenEnd, the pattern WardenEnd mirrors with the direction reversed.
    - waggle.transport.memory for MemoryTransport.pair, the link WardenEnd wraps one end of.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

from builders.cells import make_cell
from builders.forage import make_source

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore
from hivemind.cell import Cell, CellIdentity, CellKind
from hivemind.forage import ForageMap, GoalBudgets, ModelSlot
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort
from hivemind.guard import Enforcer, GuardPolicy, load_guard_policy
from hivemind.llm import BoundModel, DirectCallGate, FakeLLMProvider
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.memory import InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.chat import InMemoryChatLog
from hivemind.queen.deps import MemoryBudget, QueenDeps, WardenLink
from hivemind.queen.intake import InMemoryGoalRequestStore
from hivemind.supervision import load_policy
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    HiveId,
    MessageId,
    NodeId,
    WardenId,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.cell import CellWaxWritten
from waggle.messages.forage import CeilingsSet, ForageReply, GrantIssued, PlanWritten
from waggle.messages.supervision import Answer, Intervene
from waggle.messages.task import TaskAssign, TaskCancel, TaskPause, TaskResume
from waggle.transport.memory import MemoryTransport

DEFAULT_PUMP_LIMIT = 50  # Generous cap: a stalled test fails fast instead of hanging.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PROVIDER_NAME = "fake"
_WORKER_MODEL = "test-model"
_WORKER_FALLBACK_MODEL = "test-model-strong"
# The default "fake-worker" ModelSource's own declared seats (hivemind.forage.allocate's
# `_reachable_seats` caps this at Abundance.seats_free too, `builders.forage.make_source`'s own
# default of 4). A bare `make_source(...)` leaves this at 1, which QueenDeps' own default
# RoyalReserve(seats=1) fully claims: reachable_seats lands at 0 and every fresh grant this
# fixture computes -- through hivemind.queen.dispatcher, the only place that used to send such a
# grant anyway -- comes out at max_sub_bees == 0 (`.claude/phase-4-handoff.md` section 4.2 item 1,
# "the fixture's default reserve has to change with it"). Since the dispatcher's own zero-grant
# fix denies and fails the task at that grant instead of sending it, an ordinary test built over
# this fixture needs a grant of at least one bee to see the assignment it expects; 4 leaves 3 after
# the default reserve claims one, comfortably clearing the headroom margin too (see
# test_overriding_reserve_reaches_the_allocator_and_can_zero_it_out, which exhausts this pool on
# purpose to prove the arithmetic still reaches hivemind.forage.allocate.grant). Public (no
# leading underscore) so a test that needs to exhaust the pool on purpose can import the same
# figure rather than hard-coding a copy of it.
FORAGE_SOURCE_SEATS = 4

__all__ = [
    "DEFAULT_PUMP_LIMIT",
    "FORAGE_SOURCE_SEATS",
    "WardenEnd",
    "make_queen_deps",
    "plan_responder",
    "with_guard_policy",
]


def make_queen_deps(
    clock: Clock | None = None,
    *,
    fake_provider: FakeLLMProvider | None = None,
    cell: Cell | None = None,
    **overrides: object,
) -> tuple[QueenDeps, WardenLink, WardenEnd]:
    """Build a QueenDeps over fakes, plus one attached WardenLink and its own WardenEnd.

    Args:
        clock: Shared by every collaborator built here; a fresh FakeClock when omitted.
        fake_provider: The provider every `[llm.slots]` row binds to; a fresh `FakeLLMProvider`
            (full capabilities) when omitted.
        cell: The attached WardenLink's own Cell; a fresh REAL Cell (`builders.cells.make_cell`)
            when omitted.
        **overrides: Field values that replace the QueenDeps defaults below; an `enforcer`
            override replaces the shipped-policy Enforcer outright.

    Returns:
        `(deps, warden_link, warden_end)`: build a `Queen(deps)`, `await
        queen.attach_warden(warden_link)`, then drive the Warden side with `warden_end`.
    """
    active_clock = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active_clock)
    hive_id, node_id = new_hive_id(active_clock), new_node_id(active_clock)
    warden_id = new_warden_id(active_clock)
    provider = fake_provider or FakeLLMProvider(name=_DEFAULT_PROVIDER_NAME)
    active_cell = cell if cell is not None else make_cell(kind=CellKind.REAL, clock=active_clock)
    link, warden_end = _build_link(hive_id, warden_id, node_id, active_cell, active_clock)

    fields = _build_fields(
        _FieldInputs(
            trail=trail,
            hive_id=hive_id,
            node_id=node_id,
            clock=active_clock,
            provider=provider,
        )
    )
    fields.update(overrides)
    # Roadmap step 10.3: built last, over whichever trail and clock the test ended up with, so a
    # `guard.denied` lands on the very trail the test reads back.
    if "enforcer" not in fields:
        fields["enforcer"] = _build_enforcer(fields, hive_id, node_id)
    return QueenDeps(**fields), link, warden_end  # type: ignore[arg-type]


def with_guard_policy(deps: QueenDeps, policy: GuardPolicy) -> QueenDeps:
    """Return `deps` with an Enforcer over `policy`, recording to the same trail and clock.

    Args:
        deps: A QueenDeps from `make_queen_deps`.
        policy: The Guard policy every enforcement point of the Queen should decide against.

    Returns:
        A copy of `deps` whose `enforcer` (and so every set the Queen computes) uses `policy`.
    """
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
    )
    enforcer = Enforcer(policy, deps.trail, deps.clock, identity)
    return dataclasses.replace(deps, enforcer=enforcer)


def _build_enforcer(fields: dict[str, object], hive_id: HiveId, node_id: NodeId) -> Enforcer:
    """Build an Enforcer over the shipped Guard policy and the test's own trail and clock."""
    identity = CellIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    trail = cast(PheromoneTrail, fields["trail"])
    return Enforcer(load_guard_policy(), trail, cast(Clock, fields["clock"]), identity)


@dataclasses.dataclass(frozen=True, slots=True)
class _FieldInputs:
    """Grouped inputs to `_build_fields`, kept within codingrules 5.1's five-parameter limit."""

    trail: MemoryPheromoneTrail
    hive_id: HiveId
    node_id: NodeId
    clock: Clock
    provider: FakeLLMProvider


def _build_fields(inputs: _FieldInputs) -> dict[str, object]:
    """Build the default QueenDeps field values, before `make_queen_deps` applies any override."""
    identity = MemoryIdentity(hive_id=inputs.hive_id, node_id=inputs.node_id, actor="system")
    chamber_identity = ChamberIdentity(
        hive_id=inputs.hive_id, node_id=inputs.node_id, actor="system"
    )
    bindings, bindings_by_key = _default_bindings()
    providers = {_DEFAULT_PROVIDER_NAME: inputs.provider}
    return {
        "chamber": BroodChamber(MemoryTaskStore(inputs.trail), inputs.clock, chamber_identity),
        "memory": InMemoryMemoryStore(inputs.trail),
        "trail": inputs.trail,
        "identity": identity,
        "clock": inputs.clock,
        "policy": load_policy(),
        "bound_for": _bound_for(providers, bindings_by_key),
        "rebind": _rebind(providers, bindings_by_key),
        "bindings": bindings,
        "call_gate": DirectCallGate(),
        "map": _build_forage_map(inputs.clock),
        "budgets": GoalBudgets(spend_cap_usd=5.0, token_budget=2_000_000, max_sub_bees=4),
        "heartbeat_interval_s": 5.0,
        "heartbeat_miss_limit": 3,
        "alarm_attempt_limit": 3,
        "memory_budget": MemoryBudget(budget_fraction=0.6, output_reserve_tokens=4_096),
        # roadmap step 5.0b: never resolved against a real manifest in a unit test, but the field
        # is required (QueenDeps carries the Hive Stand's own resolved scratch_root); a caller
        # that needs a specific value for the leaves-vs-scratch rule passes scratch_root=... in
        # **overrides.
        "scratch_root": Path("/hive-stand/scratch"),
        # Roadmap step 10.5: the Queen's goal requests and chat log, over the same trail, so a
        # test reads every queen.goal_request_* and chat event back from where the rest land.
        "goal_requests": InMemoryGoalRequestStore(inputs.trail),
        "chat": InMemoryChatLog(inputs.trail),
    }


def _build_forage_map(clock: Clock) -> ForageMap:
    """Build a ForageMap with one source matching the default "worker" binding.

    `seats=FORAGE_SOURCE_SEATS` (module constant): see its own comment for why a bare
    `make_source(...)`'s single seat is not enough to clear QueenDeps' own default RoyalReserve.
    """
    source = make_source(
        source_id="fake-worker",
        provider=_DEFAULT_PROVIDER_NAME,
        model=_WORKER_MODEL,
        grade=3,
        seats=FORAGE_SOURCE_SEATS,
    )
    return ForageMap([source], clock=clock)


def _default_bindings() -> tuple[tuple[SlotBinding, ...], dict[str, SlotBinding]]:
    """Build the four default [llm.slots] rows: queen, attendant, worker, worker_fallback."""
    rows = (
        SlotBinding(
            key="queen",
            provider=_DEFAULT_PROVIDER_NAME,
            model=_WORKER_MODEL,
            fallback=None,
            effort=Effort.HIGH,
        ),
        SlotBinding(
            key="attendant",
            provider=_DEFAULT_PROVIDER_NAME,
            model=_WORKER_MODEL,
            fallback=None,
            effort=Effort.LOW,
        ),
        SlotBinding(
            key="worker",
            provider=_DEFAULT_PROVIDER_NAME,
            model=_WORKER_MODEL,
            fallback="worker_fallback",
            effort=Effort.MEDIUM,
        ),
        SlotBinding(
            key="worker_fallback",
            provider=_DEFAULT_PROVIDER_NAME,
            model=_WORKER_FALLBACK_MODEL,
            fallback=None,
            effort=Effort.MEDIUM,
        ),
    )
    return rows, {row.key: row for row in rows}


def _bound_for(
    providers: dict[str, FakeLLMProvider], bindings_by_key: dict[str, SlotBinding]
) -> Callable[[ModelSlot], BoundModel]:
    """Build the `bound_for` QueenDeps takes: resolve a slot's own manifest key."""

    def bound_for(slot: ModelSlot) -> BoundModel:
        return _build_bound(slot, bindings_by_key[slot.manifest_key], providers)

    return bound_for


def _rebind(
    providers: dict[str, FakeLLMProvider], bindings_by_key: dict[str, SlotBinding]
) -> Callable[[str, ModelSlot], BoundModel]:
    """Build the `rebind` QueenDeps takes: resolve a named binding key for a given slot."""

    def rebind(key: str, slot: ModelSlot) -> BoundModel:
        return _build_bound(slot, bindings_by_key[key], providers)

    return rebind


def _build_bound(
    slot: ModelSlot, binding: SlotBinding, providers: dict[str, FakeLLMProvider]
) -> BoundModel:
    """Build one BoundModel from a SlotBinding row, over the matching fake provider."""
    provider = providers[binding.provider]
    return BoundModel(
        slot=slot,
        binding=binding.key,
        provider=provider,
        model=binding.model,
        effort=binding.effort,
        context_window=provider.capabilities.context_window,
        cost_per_million_input_usd=None,
        cost_per_million_output_usd=None,
    )


def _build_link(
    hive_id: HiveId, warden_id: WardenId, node_id: NodeId, cell: Cell, clock: Clock
) -> tuple[WardenLink, WardenEnd]:
    """Build one WardenLink (the Queen's own end) and the WardenEnd wrapping the other."""
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=node_id)
    warden_hop = Hop(sender=warden_id, recipient=hive_id, node_id=node_id)
    link = WardenLink(warden_id=warden_id, cell=cell, transport=queen_transport, hop=queen_hop)
    return link, WardenEnd(warden_transport, warden_hop, clock)


class WardenEnd:
    """Wrap the Warden side of one attached link: send reports, collect orders.

    Owns its own mutable state in place (codingrules section 8.5): `grants`, `assignments`,
    `answers`, `intervenes`, `forage_replies`, `ceilings_sets` and `plans_written` (roadmap step
    4.8) grow as envelopes are pumped off the transport.
    """

    def __init__(self, transport: MemoryTransport, hop: Hop, clock: Clock) -> None:
        """Wrap `transport`'s Warden end.

        Args:
            transport: The Warden's own end of the pair (the Queen holds the other, as a
                `WardenLink`).
            hop: The Warden's address (`sender`) and the Queen's (`recipient`).
            clock: Source of every envelope id and `sent_at` timestamp this end sends.
        """
        self._transport = transport
        self._hop = hop
        self._clock = clock
        self._inbox = transport.receive()
        self.grants: list[GrantIssued] = []
        self.assignments: list[TaskAssign] = []
        self.answers: list[Answer] = []
        self.intervenes: list[Intervene] = []
        self.forage_replies: list[ForageReply] = []
        self.ceilings_sets: list[CeilingsSet] = []  # Roadmap step 4.8.
        self.plans_written: list[PlanWritten] = []  # Roadmap step 4.8's own wiring step (4.10).
        self.wax_written: list[CellWaxWritten] = []  # Roadmap step 4.2a.
        self.task_pauses: list[TaskPause] = []  # Roadmap step 4.9 (Clustering).
        self.task_resumes: list[TaskResume] = []  # Roadmap step 4.9 (Clustering).
        self.task_cancels: list[TaskCancel] = []  # Roadmap step 10.5: a revocation's cancel.
        # One short label per envelope, in arrival order, so a test can assert relative ordering
        # (e.g. a GrantIssued always arriving before the TaskAssign it precedes) without needing
        # a separate timestamp comparison.
        self.received_kinds: list[str] = []

    async def send(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> None:
        """Wrap `payload` (a Heartbeat/TaskResult/AlarmRaised/Question) and send it to the Queen."""
        envelope = wrap(payload, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._transport.send(envelope)

    async def pump_until(self, ready: Callable[[], bool], limit: int = DEFAULT_PUMP_LIMIT) -> None:
        """Read and sort envelopes off the transport until `ready()` is true.

        Raises:
            AssertionError: `ready()` was still False after `limit` envelopes.
        """
        count = 0
        while not ready() and count < limit:
            envelope = await anext(self._inbox)
            self._sort(envelope)
            count += 1
        if not ready():
            raise AssertionError(
                f"WardenEnd.pump_until gave up after {limit} envelopes without the condition "
                "becoming true."
            )

    async def wait_for_grant(self, limit: int = DEFAULT_PUMP_LIMIT) -> GrantIssued:
        """Pump until at least one GrantIssued has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.grants), limit=limit)
        return self.grants[-1]

    async def wait_for_assignment(self, limit: int = DEFAULT_PUMP_LIMIT) -> TaskAssign:
        """Pump until at least one TaskAssign has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.assignments), limit=limit)
        return self.assignments[-1]

    async def wait_for_answer(self, limit: int = DEFAULT_PUMP_LIMIT) -> Answer:
        """Pump until at least one Answer has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.answers), limit=limit)
        return self.answers[-1]

    async def wait_for_intervene(self, limit: int = DEFAULT_PUMP_LIMIT) -> Intervene:
        """Pump until at least one Intervene has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.intervenes), limit=limit)
        return self.intervenes[-1]

    async def wait_for_forage_reply(self, limit: int = DEFAULT_PUMP_LIMIT) -> ForageReply:
        """Pump until at least one ForageReply has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.forage_replies), limit=limit)
        return self.forage_replies[-1]

    async def wait_for_ceilings_set(self, limit: int = DEFAULT_PUMP_LIMIT) -> CeilingsSet:
        """Pump until at least one CeilingsSet has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.ceilings_sets), limit=limit)
        return self.ceilings_sets[-1]

    async def wait_for_plan_written(self, limit: int = DEFAULT_PUMP_LIMIT) -> PlanWritten:
        """Pump until at least one PlanWritten has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.plans_written), limit=limit)
        return self.plans_written[-1]

    async def wait_for_wax_written(self, limit: int = DEFAULT_PUMP_LIMIT) -> CellWaxWritten:
        """Pump until at least one CellWaxWritten has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.wax_written), limit=limit)
        return self.wax_written[-1]

    async def wait_for_task_pause(self, limit: int = DEFAULT_PUMP_LIMIT) -> TaskPause:
        """Pump until at least one TaskPause has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.task_pauses), limit=limit)
        return self.task_pauses[-1]

    async def wait_for_task_resume(self, limit: int = DEFAULT_PUMP_LIMIT) -> TaskResume:
        """Pump until at least one TaskResume has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.task_resumes), limit=limit)
        return self.task_resumes[-1]

    async def wait_for_task_cancel(self, limit: int = DEFAULT_PUMP_LIMIT) -> TaskCancel:
        """Pump until at least one TaskCancel has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.task_cancels), limit=limit)
        return self.task_cancels[-1]

    async def close(self) -> None:
        """Close this end of the transport, so the Queen's own receive() ends cleanly."""
        await self._transport.close()

    def _sort(self, envelope: Envelope) -> None:
        """Append `envelope`'s payload to the matching bucket; unrecognised kinds are ignored."""
        payload = envelope.payload
        if self._sort_forage(payload) or self._sort_task_control(payload):
            return  # Split out by message family to stay under C901's limit.
        if isinstance(payload, GrantIssued):
            self.grants.append(payload)
            self.received_kinds.append("grant")
        elif isinstance(payload, TaskAssign):
            self.assignments.append(payload)
            self.received_kinds.append("assignment")
        elif isinstance(payload, Answer):
            self.answers.append(payload)
            self.received_kinds.append("answer")
        elif isinstance(payload, Intervene):
            self.intervenes.append(payload)
            self.received_kinds.append("intervene")
        elif isinstance(payload, CellWaxWritten):
            self.wax_written.append(payload)
            self.received_kinds.append("wax_written")

    def _sort_task_control(self, payload: object) -> bool:
        """Sort a task control order (TaskPause/TaskResume/TaskCancel); True if it matched."""
        if isinstance(payload, TaskPause):
            self.task_pauses.append(payload)
            self.received_kinds.append("task_pause")
        elif isinstance(payload, TaskResume):
            self.task_resumes.append(payload)
            self.received_kinds.append("task_resume")
        elif isinstance(payload, TaskCancel):
            self.task_cancels.append(payload)
            self.received_kinds.append("task_cancel")
        else:
            return False
        return True

    def _sort_forage(self, payload: object) -> bool:
        """Sort a Forage-family payload (ForageReply/CeilingsSet/PlanWritten); True if it matched.

        Split out of `_sort` (codingrules 5.1: cyclomatic complexity) purely by which Waggle
        message family a kind belongs to, not by any behaviour of its own.
        """
        if isinstance(payload, ForageReply):
            self.forage_replies.append(payload)
            self.received_kinds.append("forage_reply")
        elif isinstance(payload, CeilingsSet):
            self.ceilings_sets.append(payload)
            self.received_kinds.append("ceilings_set")
        elif isinstance(payload, PlanWritten):
            self.plans_written.append(payload)
            self.received_kinds.append("plan_written")
        else:
            return False
        return True


def plan_responder(
    build_plan: Callable[[str], dict[str, object]],
) -> Callable[[LLMRequest], LLMResponse]:
    """Build a FakeLLMProvider Responder that answers a planning call with `build_plan(goal)`.

    Args:
        build_plan: Given the goal text pulled back out of the rendered `decompose_goal` system
            prompt, returns a `hivemind.queen.planner.PlanSchema`-shaped JSON object.

    Returns:
        A `hivemind.llm.Responder` for `FakeLLMProvider(responder=...)`.
    """

    def responder(request: LLMRequest) -> LLMResponse:
        return text_response(json.dumps(build_plan(_goal_from_request(request))))

    return responder


def _goal_from_request(request: LLMRequest) -> str:
    """Pull the goal text back out of the rendered `<<<user>>>...<<<end user>>>` section."""
    system = request.system or ""
    start_tag, end_tag = "<<<user>>>", "<<<end user>>>"
    start = system.find(start_tag)
    end = system.find(end_tag)
    if start == -1 or end == -1:
        return ""
    return system[start + len(start_tag) : end].strip()
