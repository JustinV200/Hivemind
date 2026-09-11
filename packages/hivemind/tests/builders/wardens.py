"""Build a WardenDeps over fakes, and QueenEnd: the Queen side of a Warden's own wire.

`make_warden_deps` builds every collaborator `hivemind.wardens.warden.Warden` needs, over fakes
and the shipped default supervision data: a `hivemind.cell.fake.FakeCellSource` seeded with one
REAL Cell (so `Warden.start()` has something to lease), an unsigned `waggle.transport.memory.
MemoryTransport` pair for the Queen link, `hivemind.memory.InMemoryMemoryStore`,
`hivemind.pheromone.trail.memory.MemoryPheromoneTrail`, a `FakeClock` shared by every collaborator,
`supervision/defaults/default-policy.toml` and `capping-tiers.toml` loaded for real (the same tables
production loads), a `worker_factory` returning a `builders.workers.ScriptedWorker` (so a test's
own Worker never depends on the real Drone, roadmap step 3.16), `hivemind.llm.DirectCallGate`
(no metering), and a `hivemind.llm.BoundModel` on `ModelSlot.WARDEN` over a scriptable
`FakeLLMProvider`. `QueenEnd` is the Queen-side mirror of `builders.workers.WardenEnd`: it wraps
the Queen's own end of the pair, `send`s an order (`TaskAssign`/`GrantIssued`/`TaskCancel`/
`Intervene`) or an `answer` to a pending `Question`, and sorts every report the Warden sends back
into `heartbeats`/`results`/`alarms`/`questions`/`forage_requests`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/wardens.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic; `make_warden_deps` shares one clock across
      every collaborator it builds.
    - `make_warden_deps`'s default `worker_factory` never constructs a real `Drone`
      (`hivemind.workers.roles`): a test that needs the Warden's own behaviour, not a role's, gets
      a `ScriptedWorker` it scripts itself.
    - `QueenEnd.pump_until` never blocks forever: it gives up and raises `AssertionError` after
      `DEFAULT_PUMP_LIMIT` envelopes, matching `builders.workers.WardenEnd`'s own contract.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.wardens.deps for WardenDeps, the bundle `make_warden_deps` builds.
    - builders.workers for WardenEnd and ScriptedWorker, the patterns this module mirrors.
    - waggle.transport.memory for MemoryTransport.pair, the link QueenEnd wraps one end of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from pathlib import Path

from builders.cells import make_cell
from builders.workers import ScriptedWorker, make_outcome, yield_then

from hivemind.cell import Cell, CellKind
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.source import CellIdentity
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm import BoundModel, DirectCallGate, FakeLLMProvider
from hivemind.memory import InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.supervision import load_policy
from hivemind.supervision.capping.checks import deterministic_checks
from hivemind.supervision.capping.tiers import load_tiers
from hivemind.wardens import WardenDeps
from hivemind.workers import Worker
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import HiveId, MessageId, NodeId, WardenId, new_hive_id, new_node_id, new_warden_id
from waggle.messages.base import WaggleMessage
from waggle.messages.forage import ForageRequest
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmRaised, Answer, AnswerSource, Heartbeat, Question
from waggle.messages.task import TaskResult, WorkerRole
from waggle.transport.memory import MemoryTransport

DEFAULT_PUMP_LIMIT = 50  # Generous cap: a stalled test fails fast instead of hanging.
_REPO_ROOT = Path(__file__).resolve().parents[4]

__all__ = ["DEFAULT_PUMP_LIMIT", "QueenEnd", "make_warden_deps"]


def make_warden_deps(
    clock: Clock | None = None,
    *,
    cells: Iterable[Cell] | None = None,
    worker_factory: Callable[[WorkerRole], Worker] | None = None,
    fake_provider: FakeLLMProvider | None = None,
    **overrides: object,
) -> tuple[WardenDeps, QueenEnd, WardenId]:
    """Build a WardenDeps over fakes, plus the QueenEnd that talks to it and its own warden id.

    Args:
        clock: Shared by every collaborator built here; a fresh FakeClock when omitted.
        cells: The FakeCellSource's own inventory; one REAL Cell when omitted (None). Pass
            `cells=()` for "no Cell at all" (a refused lease): distinct from "not given".
        worker_factory: Builds a role implementation for a `TaskAssign.role`; a `ScriptedWorker`
            that claims completion after one tick when omitted.
        fake_provider: The provider `bound` is built over; a fresh `FakeLLMProvider` when omitted.
        **overrides: Field values that replace the defaults below, including `warden_id` itself.

    Returns:
        `(deps, queen_end, warden_id)`: build a Warden with `Warden(warden_id, deps)`, then drive
        the Queen side of its link with `queen_end`.
    """
    active_clock = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active_clock)
    memory = InMemoryMemoryStore(trail)
    hive_id, node_id = new_hive_id(active_clock), new_node_id(active_clock)
    warden_id = _resolve_warden_id(overrides, active_clock)
    identity, cell_identity = _build_identities(hive_id, node_id)
    source = _build_source(cells, cell_identity, trail, active_clock)
    bound = _build_bound(fake_provider)
    queen_end, warden_transport = QueenEnd.pair_with(hive_id, warden_id, node_id, active_clock)
    hop = Hop(sender=warden_id, recipient=hive_id, node_id=node_id)
    factory = worker_factory if worker_factory is not None else _default_worker_factory
    fields = _build_fields(
        _FieldInputs(
            source=source,
            queen_link=warden_transport,
            hop=hop,
            memory=memory,
            trail=trail,
            identity=identity,
            clock=active_clock,
            bound=bound,
            worker_factory=factory,
        )
    )
    fields.update(overrides)
    return WardenDeps(**fields), queen_end, warden_id  # type: ignore[arg-type]


def _resolve_warden_id(overrides: dict[str, object], active_clock: Clock) -> WardenId:
    """Pop and normalise an overridden `warden_id`, or mint a fresh one.

    Popped (not merely read) so the later blanket `fields.update(overrides)` in
    `make_warden_deps` never re-applies a raw, unwrapped override on top of this one.
    """
    override_warden_id = overrides.pop("warden_id", None)
    if override_warden_id is not None:
        return WardenId(str(override_warden_id))
    return new_warden_id(active_clock)


def _build_identities(hive_id: HiveId, node_id: NodeId) -> tuple[MemoryIdentity, CellIdentity]:
    """Build the two identity stamps `make_warden_deps` hands to `deps.identity` and its source."""
    identity = MemoryIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    cell_identity = CellIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    return identity, cell_identity


def _build_source(
    cells: Iterable[Cell] | None,
    cell_identity: CellIdentity,
    trail: MemoryPheromoneTrail,
    active_clock: Clock,
) -> FakeCellSource:
    """Build the FakeCellSource, seeded with one REAL Cell by default so `start()` can lease.

    `cells=None` (the omitted default) seeds one REAL Cell; `cells=()` is an explicit, honoured
    choice for "no Cell at all" (a test of the LeaseRefusedError/no-cell WATCH path), distinct
    from omission -- an empty tuple must never fall back to the default.
    """
    source_cells = (
        tuple(cells) if cells is not None else (make_cell(kind=CellKind.REAL, clock=active_clock),)
    )
    return FakeCellSource(source_cells, cell_identity, trail, active_clock)


def _build_bound(fake_provider: FakeLLMProvider | None) -> BoundModel:
    """Build the BoundModel on ModelSlot.WARDEN that `make_warden_deps` hands to `deps.bound`."""
    provider = fake_provider if fake_provider is not None else FakeLLMProvider(name="fake-warden")
    return BoundModel(
        slot=ModelSlot.WARDEN,
        binding="warden",
        provider=provider,
        model="test-model",
        effort=Effort.MEDIUM,
        context_window=provider.capabilities.context_window,
        cost_per_million_input_usd=None,
        cost_per_million_output_usd=None,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _FieldInputs:
    """Grouped inputs to `_build_fields`, kept within codingrules 5.1's five-parameter limit."""

    source: FakeCellSource
    queen_link: MemoryTransport
    hop: Hop
    memory: InMemoryMemoryStore
    trail: MemoryPheromoneTrail
    identity: MemoryIdentity
    clock: Clock
    bound: BoundModel
    worker_factory: Callable[[WorkerRole], Worker]


def _build_fields(inputs: _FieldInputs) -> dict[str, object]:
    """Build the default WardenDeps field values, before `make_warden_deps` applies any override."""
    bound = inputs.bound
    return {
        "source": inputs.source,
        "queen_link": inputs.queen_link,
        "hop": inputs.hop,
        "memory": inputs.memory,
        "trail": inputs.trail,
        "identity": inputs.identity,
        "clock": inputs.clock,
        "policy": load_policy(),
        "tiers": load_tiers(),
        "checks": deterministic_checks(),
        "bound": bound,
        "call_gate": DirectCallGate(),
        "worker_factory": inputs.worker_factory,
        "rebind": lambda key: dataclasses.replace(bound, binding=key),
        "handoff_threshold": 0.66,
        "heartbeat_interval_s": 5.0,
        "worker_heartbeat_interval_s": 5.0,
        "missed_heartbeats_before_stalled": 3,
    }


def _default_worker_factory(role: WorkerRole) -> Worker:
    """Return a ScriptedWorker that claims completion after one tick; never a real Drone."""
    return ScriptedWorker(yield_then(make_outcome), role=role)


class QueenEnd:
    """Wrap the Queen side of a Warden's own MemoryTransport pair: send orders, collect reports.

    Owns its own mutable state in place (codingrules section 8.5): `heartbeats`, `results`,
    `alarms`, `questions` and `forage_requests` grow as envelopes are pumped off the transport.
    """

    def __init__(self, transport: MemoryTransport, hop: Hop, clock: Clock) -> None:
        """Wrap `transport`'s Queen end.

        Args:
            transport: The Queen's own end of the pair (the Warden holds the other end).
            hop: The Queen's address (`sender`) and the Warden's (`recipient`).
            clock: Source of every envelope id and `sent_at` timestamp this end sends.
        """
        self._transport = transport
        self._hop = hop
        self._clock = clock
        self._inbox = transport.receive()
        self.heartbeats: list[Heartbeat] = []
        self.results: list[TaskResult] = []
        self.alarms: list[AlarmRaised] = []
        self.questions: list[Question] = []
        self.forage_requests: list[ForageRequest] = []
        # supervision.answer is a reply: its envelope must carry the correlation_id of the
        # Question envelope it answers, tracked here so `answer` can supply it.
        self._question_envelope_ids: dict[MessageId, MessageId] = {}

    @classmethod
    def pair_with(
        cls, hive_id: HiveId, warden_id: WardenId, node_id: NodeId, clock: Clock
    ) -> tuple[QueenEnd, MemoryTransport]:
        """Build an unsigned MemoryTransport pair and wrap the Queen half as a QueenEnd.

        Args:
            hive_id: The Queen's own address; the QueenEnd's own messages address it as sender.
            warden_id: The Warden's address; the QueenEnd sends to it.
            node_id: The sending node id both ends stamp (phase 3: one process, one node).
            clock: Shared by the QueenEnd and, typically, the Warden side's own WardenDeps.

        Returns:
            `(queen_end, warden_transport)`: the wrapped Queen end, and the raw Warden end a test
            hands to `hivemind.wardens.deps.WardenDeps.queen_link`.
        """
        queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
        queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=node_id)
        return cls(queen_transport, queen_hop, clock), warden_transport

    async def send(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> None:
        """Wrap `payload` and send it to the Warden.

        Args:
            payload: The order to send (TaskAssign/GrantIssued/TaskCancel/Intervene) or any other
                request or event this end originates.
            correlation_id: Passed through to `waggle.envelope.wrap`; None unless `payload`'s own
                kind requires one.
        """
        envelope = wrap(payload, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._transport.send(envelope)

    async def answer(
        self,
        question: Question,
        text: str,
        *,
        source: AnswerSource = AnswerSource.QUEEN,
        clearance: WireHoneyClearance = WireHoneyClearance.C1,
    ) -> None:
        """Send the Answer to a Question this end previously collected.

        Args:
            question: The Question this answers; supplies `question_id` and `task_id`.
            text: The answer text.
            source: Who answered; QUEEN by default (HUMAN answers must be C2, per Answer's own
                validator).
            clearance: The label of `text`; C1 by default.
        """
        await self.send(
            Answer(
                question_id=question.question_id,
                task_id=question.task_id,
                text=text,
                chosen_option=None,
                source=source,
                clearance=clearance,
            ),
            correlation_id=self._question_envelope_ids.get(question.question_id),
        )

    async def pump_until(self, ready: Callable[[], bool], limit: int = DEFAULT_PUMP_LIMIT) -> None:
        """Read and sort envelopes off the transport until `ready()` is true.

        Args:
            ready: Checked before each read; pumping stops once it returns True.
            limit: The most envelopes to read before giving up.

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
                f"QueenEnd.pump_until gave up after {limit} envelopes without the condition "
                "becoming true."
            )

    async def wait_for_heartbeat(self, limit: int = DEFAULT_PUMP_LIMIT) -> Heartbeat:
        """Pump until at least one Heartbeat has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.heartbeats), limit=limit)
        return self.heartbeats[-1]

    async def wait_for_result(self, limit: int = DEFAULT_PUMP_LIMIT) -> TaskResult:
        """Pump until at least one TaskResult has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.results), limit=limit)
        return self.results[-1]

    async def wait_for_alarm(self, limit: int = DEFAULT_PUMP_LIMIT) -> AlarmRaised:
        """Pump until at least one AlarmRaised has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.alarms), limit=limit)
        return self.alarms[-1]

    async def wait_for_question(self, limit: int = DEFAULT_PUMP_LIMIT) -> Question:
        """Pump until at least one Question has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.questions), limit=limit)
        return self.questions[-1]

    async def close(self) -> None:
        """Close this end of the transport, so the Warden's own receive() ends cleanly."""
        await self._transport.close()

    def _sort(self, envelope: Envelope) -> None:
        """Append `envelope`'s payload to the matching bucket; unrecognised kinds are ignored."""
        payload = envelope.payload
        if isinstance(payload, Heartbeat):
            self.heartbeats.append(payload)
        elif isinstance(payload, TaskResult):
            self.results.append(payload)
        elif isinstance(payload, AlarmRaised):
            self.alarms.append(payload)
        elif isinstance(payload, Question):
            self.questions.append(payload)
            self._question_envelope_ids[payload.question_id] = envelope.id
        elif isinstance(payload, ForageRequest):
            self.forage_requests.append(payload)
