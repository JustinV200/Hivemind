"""Aggregate and send this Warden's own Heartbeat, mirror sub-bee reports, and watch for stalls.

Roadmap step 3.19: "send a Heartbeat to the Queen when the interval has elapsed (own
ContextTelemetry + a ChildTelemetry row per sub-bee, grant id and spend)." `send_heartbeat` builds
that; `record_heartbeat` and `record_progress` are how a sub-bee's own `Heartbeat`/`TaskProgress`
update this Warden's mirrored view of it (codingrules section 8.8's "observe a sub-bee's terminal
state from its Heartbeat.worker_state and TaskProgress stages, not from a TaskResult");
`raise_stalled_alarms` is the Warden's own watchdog: a sub-bee whose heartbeat has not renewed
within `missed_heartbeats_before_stalled` cycles of this Warden's own heartbeat cadence gets a
synthesised `AlarmKind.WORKER_STALLED`, run through the exact same policy-mapped
`hivemind.wardens.ticks.alarms.handle_alarm_action` path a wire `AlarmRaised` takes.
`hot_state_sources` builds the one `hivemind.memory.HotStateSources` view an awake episode packs
its prompt from, over this Warden's own sub-bee table and memory store. `send_heartbeat` also
tolerates the Queen link already being closed (this dispatch's own fix 4): a heartbeat send that
races `hivemind.wardens.warden.Warden.stop()`'s own teardown finds a `waggle.errors.
TransportClosedError` recoverable, recording `warden.offline` instead of letting it end this
Warden's own tick loop or escape `stop()` itself.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.cell` (HoneyClearance,
    CellIdentity), `hivemind.memory` (the flat hot-state summary models), `hivemind.pheromone`
    (WardenEvent, for `send_heartbeat`'s own `warden.offline` -- this dispatch's own fix 4),
    `hivemind.supervision` (Alarm, record_alarm_event -- a prior dispatch's own
    alarm-reaches-the-trail fix), `hivemind.wardens.ticks.alarms` (handle_alarm_action) and
    `hivemind.workers.state` (WorkerState) and waggle only.

Key invariants:
    - Sub-bee staleness is checked on this Warden's own heartbeat cadence (module docstring's
      rationale is in `hivemind.wardens.warden`'s own `_run_tick`), never on a separate timer.
    - `hot_state_sources`'s `open_alarms`/`pending_questions` return empty tuples: v0 keeps no
      running Alarm or Question table of its own beyond what forwarding needs (flagged in this
      dispatch's report); every other category reads live from the sub-bee table or the memory
      store.
    - `raise_stalled_alarms` records `alarm.raised` for the WORKER_STALLED Alarm it synthesises,
      before handing it to `handle_alarm_action` (a prior dispatch's own fix: a Warden-raised
      Alarm is now visible on the trail from its very first hop).
    - `send_heartbeat` never raises `TransportClosedError`: the one wire send it makes is wrapped,
      so a heartbeat racing `Warden.stop()`'s own teardown can never crash this Warden's tick loop
      (this dispatch's own fix 4).

See Also:
    - .claude/codingrules.md section 8.8 for "observe a sub-bee's terminal state from its
      Heartbeat... not from a TaskResult" and the WORKER_STALLED watchdog rule.
    - hivemind.memory.hot_state for HotStateSources and the flat summary models this builds.
    - hivemind.wardens.ticks.alarms for handle_alarm_action, WORKER_STALLED's one handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.memory import (
    AlarmSummary,
    DecisionSummary,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
)
from hivemind.pheromone import WardenEvent
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.supervision.attendant import InboxItem, InboxKind
from hivemind.wardens.autopilot import SubBeeView, WardenAction, decide
from hivemind.wardens.ticks.alarms import handle_alarm_action
from hivemind.workers.state import WorkerState
from waggle.envelope import wrap
from waggle.errors import TransportClosedError
from waggle.ids import WorkerId, new_alarm_id, new_event_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    Heartbeat,
)
from waggle.messages.task import TaskProgress, TaskStage

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

RECENT_DECISIONS_LIMIT = 20  # Matches hivemind.memory.hot_state.packing's own default.
NOTES_LIMIT = 50  # Generous: notes are already bounded per author on write.

__all__ = [
    "compact_view",
    "hot_state_sources",
    "raise_stalled_alarms",
    "record_heartbeat",
    "record_progress",
    "send_heartbeat",
]


async def send_heartbeat(warden: Warden) -> None:
    """Build and send this Warden's own Heartbeat, with one ChildTelemetry row per sub-bee.

    Tolerates the Queen link already being closed (this dispatch's own fix 4): `Warden.stop()`
    cancels this Warden's own heartbeat deadline before anything else, but that only stops a
    heartbeat that has not fired yet -- a send already under way when the composition root
    (`hivemind.cli.compose.hive.run_hive`) closes the link from a separate task can still find a
    closed transport. That is recoverable, recorded as `warden.offline` (the connection to the
    Queen is, in fact, gone), never an exception out of this Warden's own tick loop or `stop()`.
    """
    own_telemetry = ContextTelemetry(
        tokens_used=0,
        context_window=warden._deps.bound.context_window,
        goal="Supervising its Cell.",
        last_actions=(),
        blockers=(),
        spend=0.0,
    )
    children = tuple(
        ChildTelemetry(
            worker_id=sub_bee.worker_id,
            task_id=sub_bee.task_id,
            state=sub_bee.state.to_wire(),
            telemetry=sub_bee.last_telemetry or _empty_telemetry(),
        )
        for sub_bee in warden._sub_bees.values()
    )
    message = Heartbeat(
        telemetry=own_telemetry,
        task_id=None,
        worker_state=None,
        warden_state=warden._state.to_wire(),
        children=children,
        grant_id=None,  # v0: this Warden holds no one standing grant of its own to renew here.
        grant_spend=None,
        interval_s=warden._deps.heartbeat_interval_s,
    )
    try:
        envelope = wrap(message, warden._deps.hop, clock=warden._deps.clock)
        await warden._deps.queen_link.send(envelope)
    except TransportClosedError:
        # Recoverable (this dispatch's own fix 4): logged as this Warden's own connection to the
        # Queen being gone, not raised, so a heartbeat racing Warden.stop()'s own teardown can
        # never crash this Warden's tick loop or propagate out of stop() itself.
        await _record_link_lost(warden)


def record_heartbeat(warden: Warden, worker_id: str, heartbeat: Heartbeat) -> None:
    """Mirror a sub-bee's own reported state and telemetry, and clear its missed-beat count."""
    sub_bee = warden._sub_bees.get(WorkerId(worker_id))
    if sub_bee is None or heartbeat.worker_state is None:
        return
    sub_bee.last_telemetry = heartbeat.telemetry
    sub_bee.missed_heartbeats = 0
    sub_bee.state = WorkerState.from_wire(heartbeat.worker_state)


def record_progress(warden: Warden, worker_id: str, progress: TaskProgress) -> None:
    """Remember a checkpointed sub-bee's own last Handoff reference."""
    sub_bee = warden._sub_bees.get(WorkerId(worker_id))
    if sub_bee is None:
        return
    if progress.stage is TaskStage.CHECKPOINTED and progress.handoff is not None:
        sub_bee.last_handoff = progress.handoff


async def raise_stalled_alarms(warden: Warden) -> None:
    """Raise WORKER_STALLED for every sub-bee whose heartbeat has been missing too long."""
    threshold = warden._deps.missed_heartbeats_before_stalled
    for sub_bee in tuple(warden._sub_bees.values()):
        sub_bee.missed_heartbeats += 1
        if sub_bee.missed_heartbeats < threshold:
            continue
        alarm = AlarmRaised(
            alarm_id=new_alarm_id(warden._deps.clock),
            kind=AlarmKind.WORKER_STALLED,
            severity=AlarmSeverity.WARNING,
            origin=sub_bee.worker_id,
            attempts=sub_bee.missed_heartbeats - threshold,
            raised_at=warden._deps.clock.now(),
            context=AlarmContext(
                task_id=sub_bee.task_id,
                cell_id=warden._cell.id if warden._cell is not None else None,
                worker_id=sub_bee.worker_id,
                event_id=None,
                handoff=sub_bee.last_handoff,
            ),
            detail=f"No heartbeat for {sub_bee.missed_heartbeats} of this Warden's own intervals.",
            clearance=HoneyClearance.C1.to_wire(),
            reason="The sub-bee's own heartbeat has not renewed within the configured limit.",
        )
        # This Warden raises WORKER_STALLED itself (this dispatch's own fix 1: the Alarm's own
        # chain, not just this Warden's later handling of it, belongs on the trail).
        await record_alarm_event(
            warden._deps.trail,
            _cell_identity(warden),
            warden._deps.clock,
            Alarm.from_wire(alarm),
            "alarm.raised",
        )
        # sub_bee.attempt (not missed_heartbeats) is the policy-facing attempt count, for the same
        # reason table.py's own _decide_alarm docstring gives for a wire AlarmRaised: a respawn
        # replaces this SubBee with a fresh one whose own missed_heartbeats restarts at 0, so
        # keying off missed_heartbeats would read "attempt 1" forever and this sub-bee's own task
        # could never reach WORKER_STALLED's ESCALATE row no matter how many times it respawned.
        view = SubBeeView(state=sub_bee.state, attempt=sub_bee.attempt)
        action = decide(_alarm_as_item(alarm), view, warden._deps.policy)
        if action in (
            WardenAction.RETRY,
            WardenAction.REBIND,
            WardenAction.ESCALATE,
            WardenAction.CANCEL_TASK,
        ):
            await handle_alarm_action(warden, sub_bee, alarm, action, None)


def hot_state_sources(warden: Warden) -> _WardenHotState:
    """Build the HotStateSources view an awake episode packs its prompt from."""
    return _WardenHotState(warden=warden)


def compact_view(telemetry: ContextTelemetry) -> CompactView:
    """Build the CompactView a Supervisor.inspect() reply carries, from one ContextTelemetry.

    Lives here, not in `hivemind.wardens.warden`, so that module's own class body stays within
    codingrules 5.1's file-length limit (this module already imports everything it needs).
    """
    return CompactView(
        goal=telemetry.goal,
        progress=f"{telemetry.tokens_used}/{telemetry.context_window} tokens used.",
        decisions=tuple(telemetry.last_actions),
        open_threads=tuple(telemetry.blockers),
    )


def _empty_telemetry() -> ContextTelemetry:
    """Return a placeholder ContextTelemetry for a sub-bee that has not reported yet."""
    return ContextTelemetry(
        tokens_used=0, context_window=1, goal="", last_actions=(), blockers=(), spend=0.0
    )


def _cell_identity(warden: Warden) -> CellIdentity:
    """Build the CellIdentity `raise_stalled_alarms`'s own alarm.raised event is stamped with."""
    identity = warden._deps.identity
    return CellIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor)


async def _record_link_lost(warden: Warden) -> None:
    """Record `warden.offline` when `send_heartbeat` finds the Queen link already closed.

    This dispatch's own fix 4: `pheromone.events.families.WardenEvent.KINDS` already reserves
    `warden.offline` for exactly this ("its connection to the Queen was lost"), so a heartbeat
    send racing `Warden.stop()`'s own teardown reuses it rather than needing a new kind added to a
    file outside this dispatch's own list.
    """
    event = WardenEvent(
        id=new_event_id(warden._deps.clock),
        hive_id=warden._deps.identity.hive_id,
        node_id=warden._deps.identity.node_id,
        at=warden._deps.clock.now(),
        actor=warden._deps.identity.actor,
        kind="warden.offline",
        subject_id=warden._warden_id,
        payload={"reason": "A heartbeat send found the Queen link already closed."},
    )
    await warden._deps.trail.record(event)


def _alarm_as_item(alarm: AlarmRaised) -> InboxItem:
    """Wrap `alarm` as the InboxItem shape `hivemind.wardens.autopilot.table.decide` reads.

    `InboxItem.severity` is `hivemind.supervision.alarm.AlarmSeverity` (the mirrored, hivemind-side
    type `Alarm.severity` carries), not the wire `waggle.messages.AlarmSeverity` `alarm.severity`
    itself is -- `Alarm.from_wire` is what converts one to the other (the same conversion
    `hivemind.wardens.inbox.weights._classify` does for a real, received AlarmRaised).
    """
    return InboxItem(
        id=alarm.alarm_id,
        kind=InboxKind.ALARM,
        received_at=alarm.raised_at,
        principal=alarm.origin,
        severity=Alarm.from_wire(alarm).severity,
        task_id=alarm.context.task_id,
        latency_budget_s=None,
        payload_kind="supervision.alarm_raised",
        payload=alarm,
    )


@dataclass(frozen=True, slots=True)
class _WardenHotState:
    """Adapt one Warden's own live sub-bee table and memory store into HotStateSources."""

    warden: Warden

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Return one TaskSummary per sub-bee this Warden currently supervises."""
        return tuple(
            TaskSummary(
                id=sub_bee.task_id,
                title=sub_bee.assignment.objective[:200],
                status=sub_bee.state.value,
                objective=sub_bee.assignment.objective[:500],
                updated_at=self.warden._deps.clock.now(),
                clearance=HoneyClearance.from_wire(sub_bee.assignment.clearance),
            )
            for sub_bee in self.warden._sub_bees.values()
        )

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Return no candidates: v0 keeps no running Alarm table of its own (module docstring)."""
        return ()

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Return no candidates: v0 keeps no running Question-text table (module docstring)."""
        return ()

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return up to `limit` recent episodes, from this Warden's own memory store."""
        episodes = await self.warden._deps.memory.list_episodes(None, HoneyClearance.C1, limit)
        return tuple(
            DecisionSummary(
                episode_id=episode.id,
                at=episode.at,
                decision=episode.decision[:500],
                action=episode.action[:500],
                clearance=episode.clearance,
            )
            for episode in episodes
        )

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin visible at this Warden's own principal clearance."""
        return await self.warden._deps.memory.list_pins(HoneyClearance.C1)

    async def notes(self) -> tuple[Note, ...]:
        """Return this Warden's own recent notes."""
        return await self.warden._deps.memory.list_notes(None, HoneyClearance.C1, NOTES_LIMIT)
