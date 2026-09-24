"""Build a quarantine scene: a real Warden running one held sub-bee, with memory worth tainting.

Roadmap step 10.6c's Warden-level tests all start the same way: a real `Warden` over the fakes
`builders.wardens.make_warden_deps` wires (a scriptable `FakeLLMProvider` on the Warden's own slot,
the in-memory memory tables and trail, a `FakeClock`), one sub-bee spawned from a `TaskAssign` and
its `GrantIssued`, and a role that starts and then holds, mid-task, until it is cancelled or told
to finish. `HeldRole` is that role: it records every resume it was handed, can note an Alarm for
its runtime to raise (a SECURITY flag, as a scanner correlation would), and, the moment it is
cancelled, looks at the trail to see whether the quarantine's checkpoint already exists, which is
how a test proves "checkpoint first, then cancel". `start_scene` builds and starts all of it;
`stop_scene` stops the Warden and reaps its loop. `SeamLedger` stands in for the Honey Store's
Nectar ledger (phase 7), so the quarantine's `extra_ledgers` seam is exercised too.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens/quarantine.

Key invariants:
    - Every scene shares one FakeClock across the Warden, its memory and its trail.
    - `start_scene` returns only once the held role has started, so a test's order lands mid-task.

See Also:
    - builders.wardens for make_warden_deps and QueenEnd.
    - builders.taint for SeamLedger.
    - hivemind.wardens.quarantine for the path these scenes exercise.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from builders.taint import SeamLedger
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.guard import GuardPolicy, load_guard_policy
from hivemind.memory import Handoff
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.supervision import EscalationPolicy, load_policy
from hivemind.wardens import Warden, WardenDeps
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock, FakeClock
from waggle.ids import new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign, WorkerRole

_SETTLE_ROUNDS = 400  # Scheduling yields to wait for one condition; never a real sleep.
_DRAIN_ROUNDS = 100  # Scheduling yields for queued envelopes to be handled when none reports.
_STOP_TIMEOUT_S = 5.0  # Bound on reaping a stopped Warden's loop in a test.
_BEE_HEARTBEAT_S = 5.0  # make_warden_deps' own default for its bees' heartbeat interval.

__all__ = [
    "HeldRole",
    "QuarantineScene",
    "drain",
    "make_grant_issued",
    "queen_hears",
    "settle",
    "start_scene",
    "stop_scene",
    "wait_for_event",
]


@dataclass
class HeldRole:
    """A role that starts, may note an Alarm, then holds mid-task until cancelled or finished."""

    note: AlarmKind | None = None  # An Alarm the first attempt notes for its runtime to raise.
    started: asyncio.Event = field(default_factory=asyncio.Event)
    finish: asyncio.Event = field(default_factory=asyncio.Event)  # Set to let an attempt claim.
    cancelled: bool = False  # Whether an attempt was cancelled while it held.
    checkpointed_before_cancel: bool = False  # Whether its task's checkpoint existed by then.
    resumed_from: list[Handoff | None] = field(default_factory=list)  # One per attempt.

    def factory(self, role: WorkerRole) -> ScriptedWorker:
        """Build this role's ScriptedWorker for a spawn (a `WardenDeps.worker_factory`)."""
        return ScriptedWorker(self._run, role=role)

    async def _run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Start, note the Alarm on a first attempt, then hold until finished or cancelled."""
        self.resumed_from.append(resume_from)
        if self.note is not None and resume_from is None:
            ctx.telemetry.note_alarm(self.note, "A scanner correlation flagged this bee.")
        self.started.set()
        try:
            await self.finish.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self.checkpointed_before_cancel = await _checkpointed(ctx, assignment)
            raise
        return make_outcome()


@dataclass
class QuarantineScene:
    """A running Warden, the Queen's end of its link, and the one held sub-bee's assignment."""

    deps: WardenDeps
    queen: QueenEnd
    warden: Warden
    run_task: asyncio.Task[None]
    assignment: TaskAssign
    role: HeldRole
    seam: SeamLedger


async def start_scene(
    role: HeldRole | None = None,
    *,
    policy: EscalationPolicy | None = None,
    guard: GuardPolicy | None = None,
    worker_heartbeat_interval_s: float = _BEE_HEARTBEAT_S,
) -> QuarantineScene:
    """Start a Warden and spawn one held sub-bee, returning once its role has started.

    Args:
        role: The role every spawn runs; a plain HeldRole when omitted.
        policy: The Warden's escalation policy; the shipped one when omitted.
        guard: The Guard policy its Enforcer decides against; the shipped one when omitted.
        worker_heartbeat_interval_s: How often its bees heartbeat.

    Returns:
        The running scene; call `stop_scene` when the test is done with it.
    """
    held = role if role is not None else HeldRole()
    clock = FakeClock()
    # Through the builder, never replaced after it, so its Enforcer is built over `guard`.
    deps, queen, warden_id = make_warden_deps(
        clock,
        worker_factory=held.factory,
        policy=policy if policy is not None else load_policy(),
        guard=guard if guard is not None else load_guard_policy(),
        worker_heartbeat_interval_s=worker_heartbeat_interval_s,
    )
    # The phase 7 seam: a Nectar ledger on the Warden's own trail, reached by every quarantine.
    seam = SeamLedger(deps.trail)
    deps = dataclasses.replace(deps, taint_ledgers=(seam,))
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    assignment = make_assignment(clock=clock)
    await queen.send(assignment)
    await queen.send(make_grant_issued(clock, assignment))
    await asyncio.wait_for(held.started.wait(), timeout=_STOP_TIMEOUT_S)
    return QuarantineScene(deps, queen, warden, run_task, assignment, held, seam)


async def stop_scene(scene: QuarantineScene) -> None:
    """Stop the scene's Warden and reap its loop.

    Args:
        scene: The scene `start_scene` built.
    """
    await scene.warden.stop()
    await asyncio.wait_for(scene.run_task, timeout=_STOP_TIMEOUT_S)


def make_grant_issued(clock: Clock, assignment: TaskAssign, **overrides: object) -> GrantIssued:
    """Build the task's own grant: one WORKER binding, one sub-bee, bound to its task.

    Args:
        clock: Stamps the grant's expiry.
        assignment: The assignment the grant is for; its grant id, task and Cell are reused.
        **overrides: Field values that replace the defaults below (a fresh `grant_id`, say).

    Returns:
        A validated GrantIssued.
    """
    fields: dict[str, object] = {
        "grant_id": assignment.grant_id,
        "holder": new_warden_id(clock),
        "cell_id": assignment.cell_id,
        "task_id": assignment.task_id,
        "revision": 0,
        "allowed": (
            AllowedBinding(
                slot="WORKER",
                source=SourceRef(
                    source_id="local", provider="fake", model="test-model", host_cell_id=None
                ),
                max_effort=WireEffort.MEDIUM,
            ),
        ),
        "seats": (),
        "token_budget": 500_000,
        "spend_budget": 5.0,
        "tokens_spent": 0,
        "spent": 0.0,
        "max_sub_bees": 1,
        "expires_at": clock.now(),
        "reason": "A test grant bound to its task.",
    }
    fields.update(overrides)
    return GrantIssued.model_validate(fields)


async def settle(condition: Callable[[], bool], rounds: int = _SETTLE_ROUNDS) -> None:
    """Yield to the event loop until `condition()` holds, or fail after `rounds` yields.

    Args:
        condition: Checked before each yield.
        rounds: How many scheduling yields to allow before failing.

    Raises:
        AssertionError: `condition()` still did not hold.
    """
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held within the settle budget.")


async def queen_hears(scene: QuarantineScene, condition: Callable[[], bool]) -> None:
    """Pump the Queen's end of the link until `condition()` holds, bounded in real time.

    Args:
        scene: The running scene.
        condition: Checked before each envelope is read off the Queen's end.

    Raises:
        TimeoutError: The Warden never sent what the test waits for (a hang fails, not blocks).
    """
    await asyncio.wait_for(scene.queen.pump_until(condition), timeout=_STOP_TIMEOUT_S)


async def drain(rounds: int = _DRAIN_ROUNDS) -> None:
    """Give the event loop `rounds` scheduling turns, so already-queued messages get handled.

    For an order whose effect is that nothing happens (a repeat, a refusal): there is no report
    to wait on, so the test lets every queued envelope be handled, then looks.

    Args:
        rounds: How many scheduling yields to give.
    """
    for _ in range(rounds):
        await asyncio.sleep(0)


async def wait_for_event(trail: PheromoneTrail, kind: str) -> PheromoneEvent:
    """Yield until an event of `kind` is on `trail`, and return the first one.

    Args:
        trail: The trail to read.
        kind: The event kind to wait for.

    Returns:
        The first event of that kind.

    Raises:
        AssertionError: None appeared within the settle budget.
    """
    for _ in range(_SETTLE_ROUNDS):
        found = await trail.query(TrailQuery(kind=kind))
        if found:
            return found[0]
        await asyncio.sleep(0)
    raise AssertionError(f"No {kind} event reached the trail within the settle budget.")


async def _checkpointed(ctx: WorkerContext, assignment: TaskAssign) -> bool:
    """Whether a `memory.checkpoint` for this task is on the trail yet."""
    query = TrailQuery(kind="memory.checkpoint", subject_id=assignment.task_id)
    return bool(await ctx.trail.query(query))
