"""Aggregate and send this Warden's own Heartbeat, mirror sub-bee reports, and watch for stalls.

Roadmap step 3.19: "send a Heartbeat to the Queen when the interval has elapsed (own
ContextTelemetry + a ChildTelemetry row per sub-bee, grant id and spend)." `send_heartbeat` builds
that; `record_heartbeat` and `record_progress` are how a sub-bee's own `Heartbeat`/`TaskProgress`
update this Warden's mirrored view of it (codingrules section 8.8's "observe a sub-bee's terminal
state from its Heartbeat.worker_state and TaskProgress stages, not from a TaskResult"), and a
Heartbeat saying the bee has ended with nothing more to send (`SubBee.has_ended`: cancelled,
killed, or stopped after a handoff) retires it through `hivemind.wardens.ticks.alarms.
retire_sub_bee`, the one path every ending takes, so its slot goes to the next assignment --
except a bee that stopped at a Handoff this Warden ordered (`SubBee.awaits_successor`), whose slot
passes to the fresh bee that resumes the task from that Handoff (`hivemind.wardens.ticks.alarms.
resume_from_handoff`), because the lever's meaning is "a fresh bee resumes the task from it";
`raise_stalled_alarms` is the Warden's own watchdog: a sub-bee whose heartbeat has not renewed
within `missed_heartbeats_before_stalled` cycles of this Warden's own heartbeat cadence gets a
synthesised `AlarmKind.WORKER_STALLED`, handed back as an `InboxItem` to the Warden's own tick
dispatch, the exact policy-mapped path a wire `AlarmRaised` takes (every `WardenAction` a policy
row can name, a quarantine included, roadmap step 10.6c).
`hot_state_sources` builds the one `hivemind.memory.HotStateSources` view an awake episode packs
its prompt from, over this Warden's own sub-bee table and memory store. `send_heartbeat` also
tolerates the Queen link already being closed (this dispatch's own fix 4): a heartbeat send that
races `hivemind.wardens.warden.Warden.stop()`'s own teardown finds a `waggle.errors.
TransportClosedError` recoverable, recording `warden.offline` instead of letting it end this
Warden's own tick loop or escape `stop()` itself. Roadmap step 4.6 adds `check_sub_bee_context`,
run on this same heartbeat cadence: it compares every sub-bee's own last reported
`ContextTelemetry` (mirrored here by `record_heartbeat`) against `hivemind.memory.thresholds.
intervention_kind_for` -- the pure rule shared with `hivemind.queen.ticks.context.intervention_for`
-- and sends an `Intervene(COMPACT)`/`Intervene(HANDOFF)` straight to any sub-bee past its own
threshold, mirroring the Queen's own watch over this Warden ("Wardens do the same to sub-bees").
`compact_view` (roadmap step 4.6) now builds a size-capped `CompactView` through `hivemind.memory.
thresholds.capped_compact_view` rather than the raw telemetry fields.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.cell` (HoneyClearance,
    CellIdentity), `hivemind.memory` (the flat hot-state summary models), `hivemind.pheromone`
    (WardenEvent, for `send_heartbeat`'s own `warden.offline` -- this dispatch's own fix 4),
    `hivemind.supervision` (Alarm, record_alarm_event -- a prior dispatch's own
    alarm-reaches-the-trail fix), `hivemind.supervision.attendant` (InboxItem),
    `hivemind.wardens.ticks.alarms` (retire_sub_bee, resume_from_handoff) and
    `hivemind.workers.state` (WorkerState) and waggle only.

Key invariants:
    - Sub-bee staleness is checked on this Warden's own heartbeat cadence (module docstring's
      rationale is in `hivemind.wardens.warden`'s own `_run_tick`), never on a separate timer.
    - `hot_state_sources`'s `open_alarms`/`pending_questions` return empty tuples: v0 keeps no
      running Alarm or Question table of its own beyond what forwarding needs (flagged in this
      dispatch's report); every other category reads live from the sub-bee table or the memory
      store.
    - `raise_stalled_alarms` records `alarm.raised` for the WORKER_STALLED Alarm it synthesises,
      before handing it back to the tick's dispatch (a prior dispatch's own fix: a Warden-raised
      Alarm is now visible on the trail from its very first hop).
    - `send_heartbeat` never raises `TransportClosedError`: the one wire send it makes is wrapped,
      so a heartbeat racing `Warden.stop()`'s own teardown can never crash this Warden's tick loop
      (this dispatch's own fix 4).
    - A sub-bee row never outlives the Heartbeat that says it has ended: `record_heartbeat`
      retires it in the same call that mirrors the state, and starts its successor there too
      when this Warden's own Handoff order stopped it, so the task is never left with no bee.

See Also:
    - .claude/codingrules.md section 8.8 for "observe a sub-bee's terminal state from its
      Heartbeat... not from a TaskResult" and the WORKER_STALLED watchdog rule.
    - hivemind.memory.hot_state for HotStateSources and the flat summary models this builds.
    - hivemind.wardens.warden for `_handle_item`, the dispatch WORKER_STALLED is handed back to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.memory import (
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    Handoff,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.cell_wax import WaxState, cap_wax_for_hot_state
from hivemind.memory.thresholds import (
    InterventionKind,
    Thresholds,
    capped_compact_view,
    intervention_kind_for,
)
from hivemind.pheromone import WardenEvent
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.supervision.attendant import InboxItem, InboxKind
from hivemind.wardens.ticks.alarms import resume_from_handoff, retire_sub_bee
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from hivemind.workers.state import WorkerState
from waggle.envelope import Hop, wrap
from waggle.errors import TransportClosedError
from waggle.ids import CellId, WorkerId, new_alarm_id, new_event_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    Heartbeat,
    Intervene,
    InterventionAction,
)
from waggle.messages.task import TaskProgress, TaskStage

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

RECENT_DECISIONS_LIMIT = 20  # Matches hivemind.memory.hot_state.packing's own default.
NOTES_LIMIT = 50  # Generous: notes are already bounded per author on write.
# Matches hivemind.manifest.schema.supervision.DEFAULT_CELL_WAX_CAP: WardenDeps carries no manifest
# slice for [memory] cell_wax_cap (this dispatch's own files stop at hivemind.wardens; the manifest
# section lives in hivemind.manifest, outside them), so this mirrors that default the same way
# hivemind.memory.hot_state.summaries.ITEM_CAP_CHARS mirrors [memory] item_cap_chars's own default.
_WAX_CAP_PER_CELL = 20
# Roadmap step 4.6: mirrors hivemind.queen.deps.MemoryBudget's own compact_at default. WardenDeps
# already carries a real handoff_threshold field (used to hand a Drone's own attempt off); no such
# field exists yet for the milder compact lever, so this mirrors the Queen's own default the same
# way _WAX_CAP_PER_CELL above mirrors a manifest default no field carries yet.
_COMPACT_AT = 0.5

__all__ = [
    "check_sub_bee_context",
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
    # Roadmap step 4.6: "Wardens do the same to sub-bees" -- on this same cadence, since a
    # sub-bee's own last_telemetry is only ever fresh right after send_heartbeat's own aggregation
    # pass read it into the Heartbeat just sent above.
    await check_sub_bee_context(warden)


async def check_sub_bee_context(warden: Warden) -> None:
    """Order compact or handoff on any sub-bee whose last reported context crossed a threshold.

    Roadmap step 4.6: "Wardens do the same to sub-bees [as the Queen does to Wardens]." A sub-bee
    with no telemetry yet (`last_telemetry is None`) is skipped: there is nothing to compare.
    """
    thresholds = Thresholds(
        compact_at=_COMPACT_AT, handoff_threshold=warden._deps.handoff_threshold
    )
    for sub_bee in tuple(warden._sub_bees.values()):
        if sub_bee.last_telemetry is None:
            continue
        kind = intervention_kind_for(sub_bee.last_telemetry, thresholds)
        if kind is not None:
            await _send_context_intervene(warden, sub_bee, kind)


async def _send_context_intervene(warden: Warden, sub_bee: SubBee, kind: InterventionKind) -> None:
    """Send one Intervene(COMPACT)/Intervene(HANDOFF) straight to `sub_bee`'s own link."""
    action = (
        InterventionAction.HANDOFF
        if kind is InterventionKind.HANDOFF
        else InterventionAction.COMPACT
    )
    message = Intervene(
        action=action,
        subject=None,
        task_id=sub_bee.task_id,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=f"This Warden ordered {action.value.lower()}: context past the {kind.value.lower()} "
        "threshold.",
    )
    if action is InterventionAction.HANDOFF:
        # The bee stops once its Handoff is written; this Warden starts the bee that resumes it.
        sub_bee.handoff_ordered = True
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    await sub_bee.link.send(wrap(message, hop, clock=warden._deps.clock))


async def record_heartbeat(warden: Warden, worker_id: str, heartbeat: Heartbeat) -> None:
    """Mirror a sub-bee's own reported state and telemetry; retire it, or succeed it, once ended.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        worker_id: The sub-bee the Heartbeat came from (its link's own principal).
        heartbeat: What it reported.
    """
    sub_bee = warden._sub_bees.get(WorkerId(worker_id))
    if sub_bee is None or heartbeat.worker_state is None:
        return
    sub_bee.last_telemetry = heartbeat.telemetry
    sub_bee.missed_heartbeats = 0
    sub_bee.state = WorkerState.from_wire(heartbeat.worker_state)
    if sub_bee.awaits_successor:
        # It stopped at this Warden's own Handoff order: the task carries on in a fresh bee.
        await resume_from_handoff(warden, sub_bee)
    elif sub_bee.has_ended:
        # Nothing else would ever end this row (no TaskResult or Alarm follows), so it goes now:
        # stopped, its link closed and its slot freed for a parked assignment (module docstring).
        await retire_sub_bee(warden, sub_bee)


async def record_progress(warden: Warden, worker_id: str, progress: TaskProgress) -> None:
    """Remember a checkpointed sub-bee's last Handoff; ship the trail when one reports it paused.

    Roadmap step 10.6a: the Queen isolating this Cell waits a bounded time for each bee's
    `worker.paused`, which only reaches her trail when this Cell's segment ships, so a pause is
    shipped at once rather than on the next heartbeat.
    """
    sub_bee = warden._sub_bees.get(WorkerId(worker_id))
    if sub_bee is None:
        return
    if progress.stage is TaskStage.CHECKPOINTED and progress.handoff is not None:
        sub_bee.last_handoff = progress.handoff
    elif progress.stage is TaskStage.PAUSED:
        await ship_trail_before_result(warden)


async def raise_stalled_alarms(warden: Warden) -> tuple[InboxItem, ...]:
    """Raise WORKER_STALLED for every sub-bee whose heartbeat has been missing too long.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).

    Returns:
        One InboxItem per Alarm raised, for the Warden's own tick to dispatch exactly as it
        dispatches a wire AlarmRaised: through its policy, whatever action a row names.
    """
    threshold = warden._deps.missed_heartbeats_before_stalled
    raised: list[InboxItem] = []
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
        # Dispatched by the tick exactly like a wire AlarmRaised from this sub-bee (principal =
        # its own id), so the policy keys on sub_bee.attempt, not missed_heartbeats: a respawn's
        # fresh SubBee restarts that count at 0, which would read "attempt 1" forever and never
        # reach WORKER_STALLED's ESCALATE row however many times the task respawned.
        raised.append(_alarm_as_item(alarm))
    return tuple(raised)


def hot_state_sources(warden: Warden) -> _WardenHotState:
    """Build the HotStateSources view an awake episode packs its prompt from."""
    return _WardenHotState(warden=warden)


def compact_view(telemetry: ContextTelemetry) -> CompactView:
    """Build the size-capped CompactView a Supervisor.inspect() reply carries (roadmap step 4.6).

    Lives here, not in `hivemind.wardens.warden`, so that module's own class body stays within
    codingrules 5.1's file-length limit (this module already imports everything it needs).
    """
    return capped_compact_view(telemetry)


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
                tainted=episode.tainted,  # Roadmap 10.6d: assemble refuses a TAINTED one outright.
            )
            for episode in episodes
        )

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin visible at this Warden's own principal clearance."""
        return await self.warden._deps.memory.list_pins(HoneyClearance.C1)

    async def notes(self) -> tuple[Note, ...]:
        """Return this Warden's own recent notes."""
        return await self.warden._deps.memory.list_notes(None, HoneyClearance.C1, NOTES_LIMIT)

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """Return WRITTEN Cell Wax for `cells`, capped per Cell (roadmap step 4.2a).

        No caller passes a non-empty `cells` this dispatch (`hivemind.wardens.awake.episode.
        decide_awake`, outside this dispatch's own files, never sets `AssembleRequest.
        cells_in_play`); implemented fully regardless, matching `hivemind.queen.awake.episode.
        QueenSources.wax`, so a future caller only has to pass the set, not build this method.
        """
        items: list[CellWaxSummary] = []
        for cell_id in cells:
            written = await self.warden._deps.memory.list_wax(
                cell_id, frozenset({WaxState.WRITTEN}), HoneyClearance.C1
            )
            for wax in cap_wax_for_hot_state(written, _WAX_CAP_PER_CELL):
                items.append(
                    CellWaxSummary(
                        id=wax.id,
                        cell_id=wax.cell_id,
                        severity=wax.severity.value,
                        text=wax.text[:500],
                        clearance=wax.clearance,
                        written_at=wax.decided_at or wax.proposed_at,
                    )
                )
        return tuple(items)

    async def handoff(self) -> Handoff | None:
        """Return None: a Warden's own awake episode never resumes from a Worker's own Handoff."""
        return None
