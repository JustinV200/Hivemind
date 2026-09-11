"""Build valid hivemind.workers test data, a scriptable Worker, and the Warden side of the wire.

Every model builder here returns a real, validated model (codingrules 14.5), with sensible
defaults for every field a test does not care about. `ScriptedWorker` is a `Worker`
(`hivemind.workers.base`) whose `run` delegates to an injected async `RunScript`, so a test writes
exactly the scenario it needs (return an outcome, raise, yield some ticks then hand off, block on
`ctx.asker.ask`, observe `ctx.telemetry.wait_if_paused`) as a small closure, with `yield_then`,
`ask_then` and `pause_aware` covering the common shapes. `WardenEnd` wraps the Warden side of a
`waggle.transport.memory.MemoryTransport` pair (`Codec()` on both ends, unsigned, per the
in-process policy every phase-3 bee link uses): `send` wraps and sends an order
(`TaskAssign`/`TaskCancel`/`TaskPause`/`TaskResume`/`Intervene`/`Answer`), and `pump_until`/
`wait_for_*` read the runtime's own reports off the same pair and sort them into
`heartbeats`/`progress`/`results`/`alarms`/`questions`. Since roadmap step 3.16, `make_context`
also wires a real `hivemind.supervision.capping.CappingGate` (over the same `FakeSession`, a
`NoopSnapshotter`, the shared trail and `supervision/defaults/capping-tiers.toml`), a
`builders.capping.FakeLeaseView` and a bare `hivemind.llm.DirectCallGate`, so a Worker or a tool
test exercises the real gate rather than a stub.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/workers.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - `make_context`'s result satisfies `hivemind.workers.context.WorkerContext` with no further
      overrides needed; `WorkerRuntime` replaces its `asker` on construction regardless, per that
      module's own docstring.
    - `WardenEnd.pump_until` never blocks forever: it gives up and raises `AssertionError` after
      `DEFAULT_PUMP_LIMIT` envelopes, so a test that mis-scripts a wait fails fast instead of
      hanging.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.workers.base for Worker and WorkerOutcome, the shapes ScriptedWorker/make_outcome
      satisfy and build.
    - hivemind.workers.context for WorkerContext, GrantSlice and QuestionChannel.
    - hivemind.workers.runtime.deps for RuntimeDeps, built directly from a WardenEnd's own
      transport half in most runtime tests.
    - waggle.transport.memory for MemoryTransport.pair, the link WardenEnd wraps one end of.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from builders.capping import FakeLeaseView
from builders.cells import make_cell
from builders.llm import make_bound

from hivemind.cell import Cell, CellIdentity, CellKind, HoneyClearance, NoopSnapshotter
from hivemind.cell.fake import FakeSession
from hivemind.guard import CapabilitySet
from hivemind.llm import DirectCallGate
from hivemind.memory import Handoff, InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.supervision.capping import CappingGate, GateDeps, deterministic_checks, load_tiers
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import GrantSlice, WorkerContext
from hivemind.workers.telemetry import TelemetryTracker
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    MessageId,
    NodeId,
    WardenId,
    WorkerId,
    new_grant_id,
    new_hive_id,
    new_node_id,
    new_task_id,
    new_worker_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.labels import AccuracyBar, Postcondition, PostconditionKind, Tempo
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmRaised, Answer, AnswerSource, Heartbeat, Question
from waggle.messages.task import TaskAssign, TaskProgress, TaskResult, TaskStage, WorkerRole
from waggle.transport.memory import MemoryTransport

DEFAULT_PUMP_LIMIT = 50  # Generous cap: a stalled test fails fast instead of hanging forever.
_SCRATCH_DIR = Path("scratch")  # A FakeSession never touches a real filesystem; any path works.
# packages/hivemind/tests/builders/workers.py -> parents[4] is the repo root (matches the same
# climb tests/unit/supervision/capping/test_tiers.py uses, one directory shallower here).

__all__ = [
    "DEFAULT_PUMP_LIMIT",
    "FakeAsker",
    "RunScript",
    "ScriptedWorker",
    "WardenEnd",
    "ask_then",
    "make_assignment",
    "make_context",
    "make_grant_slice",
    "make_outcome",
    "pause_aware",
    "yield_then",
]

# One Worker's run(), delegated to entirely: a test writes exactly the scenario it needs.
RunScript = Callable[["WorkerContext", TaskAssign, "Handoff | None"], Awaitable[WorkerOutcome]]


# ──────────────────────────────────────────────────────────────────────────────
# Model builders
# ──────────────────────────────────────────────────────────────────────────────


def make_grant_slice(clock: Clock | None = None, **overrides: object) -> GrantSlice:
    """Build a valid GrantSlice: a modest budget, one allowed binding.

    Args:
        clock: Source of the minted grant id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated GrantSlice.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "grant_id": new_grant_id(active_clock),
        "spend_budget_usd": 5.0,
        "token_budget": 500_000,
        "allowed_bindings": ("worker",),
    }
    fields.update(overrides)
    return GrantSlice(**fields)


def make_outcome(**overrides: object) -> WorkerOutcome:
    """Build a valid, claimed WorkerOutcome (work done, no handoff).

    Args:
        **overrides: Field values that replace the defaults below; pass `claimed=False` and a
            `handoff` together to build a handoff outcome instead.

    Returns:
        A validated WorkerOutcome.
    """
    fields: dict[str, object] = {
        "summary": "Did the thing.",
        "clearance": HoneyClearance.C1,
        "artifacts": (),
        "claimed": True,
        "handoff": None,
        "spend_usd": 0.01,
    }
    fields.update(overrides)
    return WorkerOutcome(**fields)


def make_assignment(clock: Clock | None = None, **overrides: object) -> TaskAssign:
    """Build a valid TaskAssign: a DRONE role, attempt 1, one FILE_EXISTS acceptance criterion.

    Args:
        clock: Source of every minted id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated TaskAssign.
    """
    active_clock = clock if clock is not None else FakeClock()
    task_id = new_task_id(active_clock)
    fields: dict[str, object] = {
        "task_id": task_id,
        "goal_id": task_id,
        "cell_id": make_cell(kind=CellKind.REAL, clock=active_clock).id,
        "role": WorkerRole.DRONE,
        "slot": "WORKER",
        "objective": "Write the report to scratch/output.txt.",
        "acceptance": (
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS,
                subject="scratch/output.txt",
                argv=(),
                expected=None,
            ),
        ),
        "tempo": Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        "clearance": WireHoneyClearance.C1,
        "grant_id": new_grant_id(active_clock),
        "attempt": 1,
        "resume_from": None,
        "reason": "Assigned by a test.",
    }
    fields.update(overrides)
    return TaskAssign(**fields)


def make_context(clock: Clock | None = None, **overrides: object) -> WorkerContext:
    """Build a valid WorkerContext over fakes: a REAL Cell, a FakeSession, an in-memory store.

    Args:
        clock: Source of every minted id; a fresh FakeClock when omitted, shared by every
            collaborator this builds so a test's own `FakeClock.advance()` affects all of them.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated WorkerContext. `WorkerRuntime` replaces `asker` with its own mailbox on
        construction regardless of what is passed here (`hivemind.workers.context`'s own
        docstring), so `FakeAsker()` is a safe, inert default.
    """
    active_clock = clock if clock is not None else FakeClock()
    # Resolved from `overrides` first (falling back to the usual default) rather than built
    # unconditionally, so a test that overrides `session` (or `cell`/`trail`) still gets a
    # `capping`/`lease` wired to that same object, never a stale default one (module docstring:
    # "over the same FakeSession").
    trail = _pick(overrides, "trail", lambda: MemoryPheromoneTrail(active_clock))
    cell = _pick(overrides, "cell", lambda: make_cell(kind=CellKind.REAL, clock=active_clock))
    session = _pick(
        overrides, "session", lambda: FakeSession(scratch_dir=_SCRATCH_DIR, clock=active_clock)
    )
    hive_id, node_id = new_hive_id(active_clock), new_node_id(active_clock)
    cell_identity = CellIdentity(hive_id=hive_id, node_id=node_id, actor="system")
    fields: dict[str, object] = {
        "worker_id": new_worker_id(active_clock),
        "cell": cell,
        "session": session,
        "bound": make_bound(),
        "grant": make_grant_slice(clock=active_clock),
        "capabilities": CapabilitySet.parse(
            f"fs:write:{_SCRATCH_DIR.as_posix()}/**", "fs:read:**", "exec:*", "tool:*"
        ),
        "memory": InMemoryMemoryStore(trail),
        "trail": trail,
        "clock": active_clock,
        "asker": FakeAsker(),
        "identity": MemoryIdentity(hive_id=hive_id, node_id=node_id, actor="system"),
        "telemetry": TelemetryTracker(context_window=128_000),
        "handoff_threshold": 0.66,
        "capping": _make_capping_gate(cell, session, trail, active_clock, cell_identity),
        "lease": FakeLeaseView(session.scratch_dir),
        "call_gate": DirectCallGate(),
    }
    fields.update(overrides)
    return WorkerContext(**fields)  # type: ignore[arg-type]  # a plain dataclass; see builders/llm.py


def _pick[T](overrides: dict[str, object], key: str, default: Callable[[], T]) -> T:
    """Return `overrides[key]` when a test supplied it, else build the usual default.

    Lets `make_context` build `capping`/`lease` from whichever `session`/`cell`/`trail` a test
    actually asked for, before `fields.update(overrides)` applies the rest. The override is
    trusted to be `T`-shaped (the same trust `WorkerContext(**fields)` already extends to every
    entry in `overrides` two lines down); `cast` only tells mypy that, it checks nothing at
    runtime.
    """
    if key in overrides:
        return cast(T, overrides[key])
    return default()


def _make_capping_gate(
    cell: Cell,
    session: FakeSession,
    trail: MemoryPheromoneTrail,
    clock: Clock,
    identity: CellIdentity,
) -> CappingGate:
    """Build the real CappingGate `make_context` wires by default (module docstring)."""
    return CappingGate(
        GateDeps(
            session=session,
            snapshotter=NoopSnapshotter(),
            cell=cell,
            tiers=load_tiers(),
            trail=trail,
            identity=identity,
            clock=clock,
            checks=deterministic_checks(),
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# A scriptable Worker
# ──────────────────────────────────────────────────────────────────────────────


class FakeAsker:
    """A QuestionChannel that records every Question asked and answers from a scripted queue."""

    def __init__(self) -> None:
        """Create a FakeAsker with nothing scripted yet."""
        self.questions: list[Question] = []
        self._answers: deque[Answer] = deque()

    def script(self, *answers: Answer) -> None:
        """Queue `answers`, FIFO, one per future `ask` call.

        Args:
            answers: The Answers to return, in order.
        """
        self._answers.extend(answers)

    async def ask(self, question: Question) -> Answer:
        """Record `question` and pop the next scripted Answer.

        Args:
            question: The question asked.

        Returns:
            The next scripted Answer.

        Raises:
            IndexError: Nothing was scripted for this call.
        """
        self.questions.append(question)
        return self._answers.popleft()


class ScriptedWorker:
    """A Worker whose `run` delegates entirely to an injected async RunScript."""

    def __init__(self, script: RunScript, role: WorkerRole = WorkerRole.DRONE) -> None:
        """Build a ScriptedWorker that runs `script` on every `run` call.

        Args:
            script: What `run` does; see the module docstring for the shapes `yield_then`,
                `ask_then` and `pause_aware` cover.
            role: The WorkerRole this Worker reports; DRONE by default.
        """
        self._script = script
        self._role = role
        self.calls: list[tuple[WorkerContext, TaskAssign, Handoff | None]] = []

    @property
    def role(self) -> WorkerRole:
        """This Worker's role."""
        return self._role

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Record the call and run this Worker's own script.

        Args:
            ctx: See `hivemind.workers.base.Worker.run`.
            assignment: See `hivemind.workers.base.Worker.run`.
            resume_from: See `hivemind.workers.base.Worker.run`.

        Returns:
            Whatever the injected script returns.
        """
        self.calls.append((ctx, assignment, resume_from))
        return await self._script(ctx, assignment, resume_from)


def yield_then(outcome: Callable[[], WorkerOutcome], ticks: int = 1) -> RunScript:
    """Build a script that yields the event loop `ticks` times, then returns `outcome()`.

    Args:
        outcome: Builds the WorkerOutcome to return; a callable (not a value) so a fresh
            WorkerOutcome is built on every call.
        ticks: How many `asyncio.sleep(0)` yields to make first; enough for the runtime's own
            tick loop to notice a TaskCancel/TaskPause/Intervene sent meanwhile.

    Returns:
        A RunScript.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        for _ in range(ticks):
            await asyncio.sleep(0)
        return outcome()

    return script


def ask_then(
    question: Callable[[TaskAssign], Question], outcome: Callable[[Answer], WorkerOutcome]
) -> RunScript:
    """Build a script that asks a Question and returns `outcome(answer)`.

    Args:
        question: Builds the Question to ask from the active TaskAssign.
        outcome: Builds the WorkerOutcome to return from the Answer received.

    Returns:
        A RunScript.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        answer = await ctx.asker.ask(question(assignment))
        return outcome(answer)

    return script


def pause_aware(outcome: Callable[[], WorkerOutcome]) -> RunScript:
    """Build a script that awaits `ctx.telemetry.wait_if_paused()` once, then returns `outcome()`.

    Args:
        outcome: Builds the WorkerOutcome to return once unblocked.

    Returns:
        A RunScript.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        await ctx.telemetry.wait_if_paused()
        return outcome()

    return script


# ──────────────────────────────────────────────────────────────────────────────
# The Warden side of the wire
# ──────────────────────────────────────────────────────────────────────────────


class WardenEnd:
    """Wrap the Warden side of a MemoryTransport pair: send orders, collect reports.

    Owns its own mutable state in place (codingrules section 8.5): `heartbeats`, `progress`,
    `results`, `alarms` and `questions` grow as envelopes are pumped off the transport.
    """

    def __init__(self, transport: MemoryTransport, hop: Hop, clock: Clock) -> None:
        """Wrap `transport`'s Warden end.

        Args:
            transport: The Warden's own end of the pair (the Worker holds the other end).
            hop: The Warden's address (`sender`) and the Worker's (`recipient`).
            clock: Source of every envelope id and `sent_at` timestamp this end sends.
        """
        self._transport = transport
        self._hop = hop
        self._clock = clock
        self._inbox = transport.receive()
        self.heartbeats: list[Heartbeat] = []
        self.progress: list[TaskProgress] = []
        self.results: list[TaskResult] = []
        self.alarms: list[AlarmRaised] = []
        self.questions: list[Question] = []
        # supervision.answer is a reply (waggle.messages.registry): its envelope must carry the
        # correlation_id of the request's own envelope, a different id than Question.question_id
        # (which survives re-wrapping at each hop and is never any envelope's id). Tracked here so
        # `answer` can supply it.
        self._question_envelope_ids: dict[MessageId, MessageId] = {}

    @classmethod
    def pair_with(
        cls, worker_id: WorkerId, warden_id: WardenId, node_id: NodeId, clock: Clock
    ) -> tuple[WardenEnd, MemoryTransport]:
        """Build an unsigned MemoryTransport pair and wrap the Warden half as a WardenEnd.

        Args:
            worker_id: The Worker's address; the WardenEnd's own messages address it.
            warden_id: The Warden's address; the WardenEnd sends as this.
            node_id: The sending node id both ends stamp (phase 3: one process, one node).
            clock: Shared by the WardenEnd and, typically, the Worker side's own RuntimeDeps.

        Returns:
            `(warden_end, worker_transport)`: the wrapped Warden end, and the raw Worker end a
            test hands to `hivemind.workers.runtime.RuntimeDeps.transport`.
        """
        warden_transport, worker_transport = MemoryTransport.pair(Codec(), Codec())
        warden_hop = Hop(sender=warden_id, recipient=worker_id, node_id=node_id)
        return cls(warden_transport, warden_hop, clock), worker_transport

    async def send(
        self, payload: WaggleMessage, *, correlation_id: MessageId | None = None
    ) -> None:
        """Wrap `payload` and send it to the Worker.

        Args:
            payload: The order to send (TaskAssign/TaskCancel/TaskPause/TaskResume/Intervene) or
                an Answer to a pending Question.
            correlation_id: Passed through to `waggle.envelope.wrap`; None unless `payload`'s own
                kind requires one.
        """
        envelope = wrap(payload, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._transport.send(envelope)

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
                f"WardenEnd.pump_until gave up after {limit} envelopes without the condition "
                "becoming true."
            )

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

    async def wait_for_progress(
        self, stage: TaskStage, limit: int = DEFAULT_PUMP_LIMIT
    ) -> TaskProgress:
        """Pump until a TaskProgress reporting `stage` has arrived, and return it."""
        await self.pump_until(lambda: any(p.stage == stage for p in self.progress), limit=limit)
        return next(p for p in self.progress if p.stage == stage)

    async def wait_for_heartbeat(self, limit: int = DEFAULT_PUMP_LIMIT) -> Heartbeat:
        """Pump until at least one Heartbeat has arrived, and return the latest one."""
        await self.pump_until(lambda: bool(self.heartbeats), limit=limit)
        return self.heartbeats[-1]

    async def close(self) -> None:
        """Close this end of the transport, so the Worker's own receive() ends cleanly."""
        await self._transport.close()

    async def answer(
        self,
        question: Question,
        text: str,
        *,
        source: AnswerSource = AnswerSource.WARDEN,
        clearance: WireHoneyClearance = WireHoneyClearance.C1,
    ) -> None:
        """Send the Answer to `question`.

        Args:
            question: The Question this answers; supplies `question_id` and `task_id`.
            text: The answer text.
            source: Who answered; WARDEN by default (HUMAN answers must be C2, per Answer's own
                validator).
            clearance: The label of `text`; C1 by default.
        """
        # supervision.answer is a reply (waggle.envelope.MessageShape.REPLY): its envelope must
        # carry the Question envelope's own id as correlation_id, tracked by _sort below.
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

    def _sort(self, envelope: Envelope) -> None:
        """Append `envelope`'s payload to the matching bucket; unrecognised kinds are ignored."""
        payload = envelope.payload
        if isinstance(payload, Heartbeat):
            self.heartbeats.append(payload)
        elif isinstance(payload, TaskProgress):
            self.progress.append(payload)
        elif isinstance(payload, TaskResult):
            self.results.append(payload)
        elif isinstance(payload, AlarmRaised):
            self.alarms.append(payload)
        elif isinstance(payload, Question):
            self.questions.append(payload)
            self._question_envelope_ids[payload.question_id] = envelope.id
