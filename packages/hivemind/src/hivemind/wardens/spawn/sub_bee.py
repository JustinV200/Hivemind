"""Define SubBee and LocalRun: one Warden's bookkeeping for a sub-bee it has spawned.

A `SubBee` is not a boundary value (it never crosses a network boundary itself, and it owns its own
mutable state in place, codingrules section 8.5): it is the Warden's own row for one Worker it is
supervising, mirroring that Worker's `hivemind.workers.state.WorkerState` from the last `Heartbeat`
or `TaskProgress` it reported, the binding key it last ran on (for a REBIND's "next binding in the
grant's allowed_bindings" search), its last `HandoffRef` (for a RETRY's `resume_from`), and its own
`hivemind.workers.runtime.WorkerRuntime` plus the `asyncio.Task` running it inside the Warden's own
`TaskGroup`, plus (roadmap step 10.3) the capability set its spawn computed, which the Warden's
question-routing and rebinding checks read, and (roadmap step 6.4) the Exoskeleton attached for its
task, which `stop_sub_bee` detaches whichever way the bee retires. `missed_heartbeats` is the
Warden's own watchdog counter, incremented once per heartbeat interval that passes with nothing
heard, reset the moment a fresh `Heartbeat` arrives; crossing
`WardenDeps.missed_heartbeats_before_stalled` is what turns into a synthesised
`AlarmKind.WORKER_STALLED`. `has_ended` is how the Warden knows a row is done with: a bee that
reports KILLED, or DONE without a claim, has nothing more to send (no TaskResult, no Alarm), and
neither has a FAILED one once a cancel has reached it (`cancelled`); its Warden retires it the
moment it hears so, freeing its slot for the next assignment. `awaits_successor` is the one ending
that is not the task's end: a bee its own Warden ordered to hand off (`handoff_ordered`, for its
context size) writes its Handoff and stops DONE, and the Handoff lever means "a fresh bee resumes
the task from it", so its Warden starts that bee in the same slot rather than leaving the task
RUNNING with no bee at all. `LocalRun` marks the one kind of row whose task the Warden asked for
itself, with no Queen in it (a cut-off Warden's diagnostic Drone, ADR-0056): the bee's ending is
kept on the row for the Warden to read, and is never accepted, retried or sent up as a task of
hers. `reap_runtime` is how the Warden awaits a row's runtime task on its way out: the one thing
a runtime that died by itself leaves there, a `WorkerRuntimeError`, is read, logged and let go.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Built
    by `hivemind.wardens.spawn.spawn.spawn_sub_bee`; read and mutated by
    `hivemind.wardens.warden.Warden` and its `hivemind.wardens.ticks` handlers on every report a
    sub-bee sends; `reap_runtime` is called by `stop_sub_bee`. Calls into `hivemind.cell`
    (RequestOrigin), `hivemind.common` (logging, reap), `hivemind.exoskeleton`
    (ExoskeletonHandle), `hivemind.guard` (CapabilitySet), `hivemind.workers` (WorkerState,
    WorkerRuntime, WorkerRuntimeError) and waggle only.

Key invariants:
    - `state` only ever moves along `hivemind.workers.state.TRANSITIONS`; a SubBee's own state is
      a mirror of the runtime's real state, never a second source of truth the Warden invents.
    - `link` is the Warden's own end of the `waggle.transport.memory.MemoryTransport` pair; the
      Worker's own runtime holds the other end. Closed exactly once, when the sub-bee reaches a
      terminal state (or is killed) and the Warden is done with it, after `runtime_task` is
      stopped and reaped (`hivemind.wardens.spawn.spawn.stop_sub_bee`), never before.
    - `has_ended` never counts a FAILED row the Queen may still act on: a crash's Alarm names
      the action that ends it (a respawn, a rebind, an escalation), unless a cancel came first.
    - `awaits_successor` holds only for a DONE bee whose stop this Warden ordered itself and no
      cancel followed: a stop the Queen ordered (Clustering, isolation) is hers to resume.
    - A row with a `local` is never reported to the Queen: its claim and its Alarm stay on the row,
      and the task id it carries is one the Queen has never heard of.
    - `reap_runtime` raises nothing a runtime stored in its task: a `WorkerRuntimeError` is logged
      with ids and the class of its cause. The runtime converts everything that escapes its loop
      into that one error, so nothing else is expected there and anything else would propagate.

See Also:
    - .claude/codingrules.md section 8.5 for the mutable-state-documented-here rule this class
      follows.
    - .claude/codingrules.md section 11 for the cooperative-stop-then-reap rule `stop_sub_bee`
      follows.
    - hivemind.workers.state for WorkerState, the mirrored machine this record's `state` follows.
    - hivemind.wardens.spawn.spawn for spawn_sub_bee and stop_sub_bee, this record's builder and
      its own stop path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hivemind.cell import RequestOrigin
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.exoskeleton import ExoskeletonHandle
from hivemind.guard import CapabilitySet
from hivemind.workers.errors import WorkerRuntimeError
from hivemind.workers.runtime import WorkerRuntime
from hivemind.workers.state import WorkerState, is_terminal
from hivemind.workers.tools.comb import CombToolBundle
from waggle.ids import EventId, TaskId, WorkerId
from waggle.messages import HandoffRef
from waggle.messages.supervision import AlarmRaised, ContextTelemetry
from waggle.messages.task import TaskAssign, TaskResult
from waggle.transport.memory import MemoryTransport

__all__ = ["LocalRun", "SubBee", "reap_runtime"]

log = get_logger(__name__)

# Ends a bee with nothing left to send: KILLED (a TaskCancel, a Cancel lever, a lost link) carries
# no TaskResult or Alarm, and neither does DONE after a stop-handoff (a claimed DONE's TaskResult
# travels ahead of any later Heartbeat on the same ordered link, so acceptance retires it first).
_ENDED_QUIETLY = frozenset({WorkerState.KILLED, WorkerState.DONE})


@dataclass(slots=True)
class LocalRun:
    """What a Warden keeps of a sub-bee it started for itself, whose ending stays with it.

    Attributes:
        origin: Who asked for the task: the Warden, from its own judgement (never the Queen, never
            a human), which is what the Guard's initiation floors read.
        claim: The TaskResult the bee claimed, held here instead of being accepted and reported.
        alarm: The Alarm that ended the bee, held here instead of being retried or escalated.
    """

    origin: RequestOrigin = RequestOrigin.WARDEN
    claim: TaskResult | None = None
    alarm: AlarmRaised | None = None

    @property
    def has_ended(self) -> bool:
        """Whether the bee has said how it ended: it claimed a result or raised an Alarm."""
        return self.claim is not None or self.alarm is not None


@dataclass(slots=True)
class SubBee:
    """One sub-bee's bookkeeping row, owned and mutated in place by its Warden.

    Attributes:
        worker_id: This sub-bee's own id, the key the Warden's sub-bee table is keyed by.
        task_id: The task this sub-bee is (or was last) assigned.
        assignment: The TaskAssign this sub-bee is running; replaced whole on a respawn or rebind.
        attempt: The current attempt number, echoed on `assignment.attempt` and incremented by
            every RETRY/REBIND respawn.
        state: This sub-bee's own WorkerState, mirrored from its last Heartbeat/TaskProgress.
        last_telemetry: This sub-bee's own ContextTelemetry as of its last Heartbeat; None before
            its first one arrives. The source `Supervisor.inspect`'s CompactView is built from.
        binding: The `[llm.slots]` manifest key this sub-bee last ran on (a REBIND's starting
            point for "the next stronger binding" search).
        last_handoff: The last HandoffRef this sub-bee's runtime reported (a TaskProgress
            CHECKPOINTED stage), or None before its first checkpoint; a RETRY's `resume_from`.
        link: The Warden's own end of this sub-bee's transport pair.
        runtime: This sub-bee's own WorkerRuntime, so its owner can `stop()` it cooperatively
            (`hivemind.wardens.spawn.spawn.stop_sub_bee`) before falling back to cancelling
            `runtime_task`; never driven directly (its own `run()`, wrapped as `runtime_task`, is
            the one thing that ticks it).
        runtime_task: The asyncio.Task running `runtime`, owned by the Warden's own TaskGroup;
            awaited or cancelled by the Warden, never dropped.
        missed_heartbeats: How many heartbeat intervals have elapsed with nothing heard since the
            last Heartbeat; reset to 0 the moment a fresh one arrives.
        exoskeleton: The Exoskeleton attached for this sub-bee's task (roadmap step 6.4), None
            for a terminal-only task; detached by `stop_sub_bee`, whichever way it retires.
        capabilities: The sub-bee's own set, as its spawn computed it (roadmap step 10.3); read
            when the Warden routes its question to the Queen or rebinds it. Defaults to empty,
            which allows nothing, for a row built outside `spawn_sub_bee`.
        spawned_event_id: The `worker.spawned` trail event that started this attempt (roadmap
            step 10.6c): where a quarantine by this Warden's own policy row takes the bee's
            memory to be suspect from when the Alarm names no event of its own. None for a row
            built outside `spawn_sub_bee`.
        cancelled: True once a cancel has been sent to this bee (`hivemind.wardens.ticks.
            control.forward_control`): whatever terminal state it reports next ends it.
        handoff_ordered: True once this Warden itself ordered the bee to hand off and stop (its
            context check, or its own Handoff lever); cleared when the Queen pauses the task.
            Read by `awaits_successor`.
        comb_tools: The bundle of Comb tools this sub-bee's `WorkerContext` reads (roadmap 9.7),
            the one thing its Warden may change under it: `hivemind.wardens.ticks.tools.install`
            replaces its entries when a tool is promoted. None for a Warden with no Basket.
        local: Set only for a task the Warden asked for itself (roadmap 11.9a): where its ending is
            kept. None for every task the Queen assigned.
    """

    worker_id: WorkerId
    task_id: TaskId
    assignment: TaskAssign
    attempt: int
    state: WorkerState
    binding: str
    last_handoff: HandoffRef | None
    link: MemoryTransport
    runtime: WorkerRuntime
    runtime_task: asyncio.Task[None]
    last_telemetry: ContextTelemetry | None = field(default=None)
    missed_heartbeats: int = field(default=0)
    exoskeleton: ExoskeletonHandle | None = field(default=None)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet.empty)
    spawned_event_id: EventId | None = field(default=None)
    cancelled: bool = field(default=False)
    handoff_ordered: bool = field(default=False)
    comb_tools: CombToolBundle | None = field(default=None)
    local: LocalRun | None = field(default=None)

    @property
    def has_ended(self) -> bool:
        """Whether this bee has finished with nothing more to send its Warden (module docstring)."""
        # A cancel is the Queen's last word on a FAILED row an escalation had left waiting on her.
        return self.state in _ENDED_QUIETLY or (self.cancelled and is_terminal(self.state))

    @property
    def awaits_successor(self) -> bool:
        """Whether this bee stopped after a handoff its own Warden ordered (module docstring)."""
        # DONE only: a KILLED bee was stopped for good, and a FAILED one is its Alarm's to handle.
        return self.handoff_ordered and self.state is WorkerState.DONE and not self.cancelled


async def reap_runtime(sub_bee: SubBee) -> None:
    """Await `sub_bee`'s runtime task (cancelling it first if it still runs), and let go of its end.

    A runtime that died by itself leaves its error stored in the task its Warden owns, and
    awaiting the task would raise it into whatever was retiring the bee: the Queen's fresh
    assignment superseding it, or the Warden stopping. The runtime converts anything that escapes
    its loop into one `WorkerRuntimeError`, so that is the one thing read here; it is logged with
    ids and the class of the cause, never its text, and the bee is retired as it would have been.

    Args:
        sub_bee: The sub-bee whose runtime task to discard; `stop_sub_bee` has already asked the
            runtime to stop and waited its grace period.
    """
    try:
        await reap(sub_bee.runtime_task)
    except WorkerRuntimeError as ended:
        log.warning(
            "wardens.sub_bee.runtime_failed",
            worker_id=sub_bee.worker_id,
            task_id=sub_bee.task_id,
            code=ended.code,
            error=type(ended.__cause__).__name__,
        )
