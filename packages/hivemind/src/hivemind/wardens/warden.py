"""Define Warden: the standard loop shape that supervises Workers on exactly one Cell.

The Warden is the per-Cell supervisor codingrules section 8.8 and README's "Core concepts" section
2 describe: it leases its own Cell, spawns sub-bees within a Forage grant, runs their acceptance
checks (never letting a sub-bee verify its own work, codingrules section 8.12), and handles their
Alarms through autopilot first, an awake episode only when autopilot cannot decide. One tick
(`_tick`, driven by `waggle.loop.TickLoop.run`) drains whatever is ready on the Queen link and every
sub-bee's own link into `hivemind.supervision.attendant.InboxItem`s, orders them with this Warden's
own `Attendant` (`hivemind.wardens.inbox`, autopilot-only, no `TieBreaker`), and dispatches each
through `hivemind.wardens.autopilot.decide` to one `hivemind.wardens.autopilot.WardenAction` --
`NEEDS_JUDGEMENT` runs one stateless `hivemind.wardens.awake.decide_awake` episode instead -- before
sending its own Heartbeat once the interval has elapsed. The actual work each action does lives in
`hivemind.wardens.ticks`, this module's own delegates (not general-purpose classes: they read and
write this class's private state directly, the same way `hivemind.workers.runtime.attempt.
AttemptManager` and `.reporter.Reporter` do for `WorkerRuntime`), split out only so this file and
its `Warden` class stay within codingrules 5.1's size limits.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Constructed by whichever
    composition root builds one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    wardens.make_warden_deps` plus a `QueenEnd` in tests. Calls into `hivemind.cell`,
    `hivemind.guard`, `hivemind.memory` (TriggerEvent), `hivemind.pheromone` (WardenEvent),
    `hivemind.supervision`, `hivemind.wardens.autopilot`, `.awake`, `.inbox`, `.spawn`, `.state`,
    `.ticks` and waggle only.

Key invariants:
    - Every `WardenState` change goes through `hivemind.wardens.state.assert_transition` and is
      followed, in the same call, by its own `warden.*` trail event (codingrules section 12).
    - `stop()` always stops every sub-bee, releases the lease and records `warden.stopped`,
      regardless of which state it was called from.
    - `stop()` sets `waggle.loop.TickLoop`'s own stop flag first, before anything else (this
      dispatch's own shutdown-hygiene fix, superseding this class's own earlier "flag last"
      choice): a tick still concurrently in flight then returns via its own throwaway stop_task
      before it can create a fresh heartbeat or receive task that nothing downstream would ever
      reap, and a heartbeat send already in flight at that exact moment still tolerates a Queen
      link the composition root closes right after `stop()` returns
      (`hivemind.wardens.ticks.heartbeat.send_heartbeat`), so `stop()` itself never raises for
      that reason.
    - `stop()` never returns with a task it owns still pending: every sub-bee's own runtime is
      stopped cooperatively, falling back to a bounded cancel
      (`hivemind.wardens.spawn.spawn.stop_sub_bee`), and every receive task this Warden started
      is reaped (`hivemind.common.tasks.reap_all`) before the method returns -- a shutdown-hygiene
      fix so no `asyncio.Task` is ever destroyed pending once the composition root's event loop
      closes (codingrules section 11).
    - `_run_tick`'s own throwaway `stop_task` is reaped in a `finally`, so a tick cancelled from
      outside (a sub-bee's `runtime_task`, `run_hive`'s `TaskGroup`, a test) never abandons it.
    - The Hive Stand's Warden exists whenever the Queen runs: a `LeaseRefusedError` on `start()`
      moves it to `WATCH` with no lease, never prevents construction (codingrules section 8.8;
      CLAUDE.md's "Wardens never provision Cells").

See Also:
    - .claude/codingrules.md section 8.8 for the kernel/Warden/Attendant/Alarm shape this class
      implements.
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work".
    - .claude/roadmap.md step 3.19 for this class's own roadmap bullet, and 3.18 for acceptance.
    - hivemind.wardens.ticks for the tick handlers this class's dispatch calls into.
    - hivemind.supervision.supervisor for Supervisor, the protocol this class also implements.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pydantic import JsonValue

from hivemind.cell import (
    Cell,
    CellSession,
    LeaseRefusedError,
    LeaseRequest,
    RealCellLease,
)
from hivemind.cell import HoneyClearance as _HoneyClearance
from hivemind.common.tasks import reap, reap_all, reaping
from hivemind.guard import CapabilitySet, ceiling_for
from hivemind.memory import TriggerEvent
from hivemind.pheromone import WardenEvent
from hivemind.supervision import ChildKind, ChildRef, Intervention, to_wire
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens import ticks
from hivemind.wardens.autopilot import SubBeeView, WardenAction, decide
from hivemind.wardens.awake import decide_awake
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.inbox import to_inbox_item, warden_attendant
from hivemind.wardens.local_pool import LocalPool
from hivemind.wardens.spawn import SubBee, stop_sub_bee
from hivemind.wardens.state import WardenState, assert_transition
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import MessageId, WardenId, WorkerId, new_event_id
from waggle.loop import TickLoop
from waggle.messages.forage import GrantIssued
from waggle.messages.supervision import (
    AlarmRaised,
    Answer,
    CompactView,
    ContextTelemetry,
    Heartbeat,
    Intervene,
    Question,
)
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskPause,
    TaskProgress,
    TaskResult,
    TaskResume,
)

_QUEEN_LINK = "queen"  # The receive-task/InboxItem.principal key for the Warden's own Queen link.
_ALARM_ACTIONS = frozenset(
    {WardenAction.RETRY, WardenAction.REBIND, WardenAction.ESCALATE, WardenAction.CANCEL_TASK}
)

__all__ = ["Warden"]


class Warden(TickLoop):
    """One Cell's supervisor: leases it, spawns sub-bees, runs their acceptance, forwards Alarms.

    Owns its own mutable state in place (codingrules section 8.5), documented here: `_state`,
    `_lease`, `_sub_bees` and every other underscored attribute below change as this Warden runs.
    Its `hivemind.wardens.ticks` delegates read and write that state directly.
    """

    def __init__(self, warden_id: WardenId, deps: WardenDeps) -> None:
        """Build a Warden in WardenState.STARTING; call `start()` next to lease its Cell.

        Args:
            warden_id: This Warden's own id, stamped on every event and every envelope it sends.
            deps: Every collaborator this Warden needs.
        """
        super().__init__(deps.clock)
        self._warden_id = warden_id
        self._deps = deps
        self._state = WardenState.STARTING
        self._lease: RealCellLease | None = None
        self._cell: Cell | None = None
        self._session: CellSession | None = None
        self._ceiling: CapabilitySet = CapabilitySet.empty()
        self._sub_bees: dict[WorkerId, SubBee] = {}
        self._sub_bee_iters: dict[WorkerId, AsyncIterator[Envelope]] = {}
        self._local_pool = LocalPool(0)
        self._grants: dict[str, GrantIssued] = {}
        self._pending: dict[str, TaskAssign] = {}
        self._questions: dict[str, WorkerId] = {}
        self._question_envelope_ids: dict[str, MessageId] = {}
        self._attendant = warden_attendant(deps.clock)
        self._queen_iter: AsyncIterator[Envelope] = deps.queen_link.receive()
        self._receive_tasks: dict[str, asyncio.Task[Envelope | None]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> WardenState:
        """This Warden's current WardenState."""
        return self._state

    @property
    def lease(self) -> RealCellLease | None:
        """This Warden's own lease, or None before `start()` succeeds or after `stop()`."""
        return self._lease

    @property
    def sub_bees(self) -> tuple[SubBee, ...]:
        """Every sub-bee this Warden currently supervises."""
        return tuple(self._sub_bees.values())

    async def start(self) -> None:
        """Lease this Warden's Cell and open its own session, or move to WATCH if refused.

        The Hive Stand's Warden exists even when its lease is refused (codingrules section 8.8):
        this method never raises for that case, only for a truly unrecoverable state.
        """
        cells = await self._deps.source.cells()
        if not cells:
            self._state = WardenState.WATCH
            await _record_event(self, "warden.watch")
            return
        cell = cells[0]  # v0: one Warden, one Cell (the Hive Stand's own).
        request = LeaseRequest(
            cell_id=cell.id,
            holder=self._warden_id,
            task_id=None,
            access_level=cell.access_level,
            allowed_paths=(),
        )
        try:
            lease = await self._deps.source.lease(request)
        except LeaseRefusedError:
            self._state = WardenState.WATCH
            await _record_event(self, "warden.watch")
            return
        self._lease = lease
        self._cell = cell
        self._session = await self._deps.source.open_session(lease)
        self._ceiling = ceiling_for(lease.access_level, lease.scratch_root)
        assert_transition(self._state, WardenState.ACTIVE, warden_id=self._warden_id)
        self._state = WardenState.ACTIVE
        await _record_event(self, "warden.started")
        await _record_event(self, "warden.active")

    async def stop(self) -> None:  # type: ignore[override]
        """End the loop first, then stop this Warden's heartbeats, every sub-bee and its lease.

        SAFETY: widens `waggle.loop.TickLoop.stop`'s synchronous signature to async on purpose --
        a Warden's own composition root always awaits this method directly (it is never called
        through a bare `TickLoop` reference that would expect a synchronous call), and the async
        cleanup this override does (stopping every sub-bee, releasing the lease) cannot be
        expressed as a fire-and-forget synchronous call.
        """
        # Set first (this dispatch's shutdown-hygiene fix), not last: a tick still concurrently
        # in flight then sees its own throwaway stop_task win the very same race and returns
        # before ever creating a fresh heartbeat or receive task, so nothing below this line ever
        # races a live tick over one it is concurrently reaping (mirrors hivemind.queen.queen.
        # Queen.stop's own ordering, for the same reason -- codingrules section 11).
        super().stop()
        if self._heartbeat_task is not None:
            await reap(self._heartbeat_task)
        self._heartbeat_task = None
        for sub_bee in tuple(self._sub_bees.values()):
            # Cooperative first, cancel-and-reap only as stop_sub_bee's own bounded fallback:
            # never left cancelled-but-unawaited (codingrules section 11; this dispatch's rule 2).
            await stop_sub_bee(sub_bee, self._deps.clock)
            # This link's own receive task is reaped BEFORE the link closes: closing a link while
            # a task is still suspended inside its receive() generator closes that generator while
            # it is running (codingrules section 11). The reap_all below then covers the queen
            # link's own receive task, and any entry a respawn left behind.
            receive_task = self._receive_tasks.pop(sub_bee.worker_id, None)
            if receive_task is not None:
                await reap(receive_task)
            await sub_bee.link.close()
        # Every receive task this Warden still owns (the queen link's own, and any sub-bee's
        # whose respawn or a slow stop_sub_bee left one outstanding): reaped before stop()
        # returns, so none is ever destroyed pending once the event loop closes (rules 1-3).
        await reap_all(self._receive_tasks.values())
        self._receive_tasks.clear()
        self._sub_bees.clear()
        self._sub_bee_iters.clear()
        if self._lease is not None:
            await self._lease.release()
        self._state = WardenState.STOPPED
        await _record_event(self, "warden.stopped")

    async def _tick(self) -> None:
        """Drain the queen link and every sub-bee link, order and act, then heartbeat."""
        await _run_tick(self)

    # ──────────────────────────────────────────────────────────────────────────
    # Supervisor protocol (hivemind.supervision.supervisor.Supervisor)
    # ──────────────────────────────────────────────────────────────────────────

    async def children(self) -> tuple[ChildRef, ...]:
        """Return one ChildRef per sub-bee this Warden currently supervises."""
        return tuple(
            ChildRef(
                id=sub_bee.worker_id,
                kind=ChildKind.WORKER,
                task_id=sub_bee.task_id,
                state=sub_bee.state.to_wire().value,
            )
            for sub_bee in self._sub_bees.values()
        )

    async def telemetry(self, child: str) -> ContextTelemetry:
        """Return `child`'s last reported ContextTelemetry.

        Raises:
            UnknownSubBeeError: `child` names no current sub-bee, or none has reported yet.
        """
        sub_bee = self._sub_bees.get(WorkerId(child))
        if sub_bee is None or sub_bee.last_telemetry is None:
            raise UnknownSubBeeError(child)
        return sub_bee.last_telemetry

    async def inspect(self, child: str) -> CompactView:
        """Return a CompactView built from `child`'s last reported telemetry.

        Raises:
            UnknownSubBeeError: `child` names no current sub-bee, or none has reported yet.
        """
        sub_bee = self._sub_bees.get(WorkerId(child))
        if sub_bee is None or sub_bee.last_telemetry is None:
            raise UnknownSubBeeError(child)
        return ticks.heartbeat.compact_view(sub_bee.last_telemetry)

    async def intervene(self, child: str, intervention: Intervention) -> None:
        """Send `intervention` to `child` over its own link.

        Raises:
            UnknownSubBeeError: `child` names no current sub-bee.
        """
        sub_bee = self._sub_bees.get(WorkerId(child))
        if sub_bee is None:
            raise UnknownSubBeeError(child)
        action, slot = to_wire(intervention)
        message = Intervene(
            action=action,
            subject=None,
            task_id=sub_bee.task_id,
            slot=slot,
            alarm_id=None,
            reason=intervention.reason,
        )
        hop = Hop(
            sender=self._warden_id, recipient=sub_bee.worker_id, node_id=self._deps.hop.node_id
        )
        await sub_bee.link.send(wrap(message, hop, clock=self._deps.clock))


# ──────────────────────────────────────────────────────────────────────────────
# Tick dispatch: module-level so Warden's own class body stays within codingrules 5.1
# ──────────────────────────────────────────────────────────────────────────────


async def _run_tick(warden: Warden) -> None:
    """Drain what is ready, order it, act on it, then heartbeat -- Warden's one tick."""
    receive_tasks = _receive_tasks_snapshot(warden)
    heartbeat_task = _heartbeat_deadline_task(warden)
    # Throwaway: only wakes this wait early when stop() is called mid-tick.
    stop_task: asyncio.Task[bool] = asyncio.ensure_future(warden._stop.wait())
    waitables: set[asyncio.Future[Any]] = {*receive_tasks.values(), heartbeat_task, stop_task}
    # reaping (not a bare reap after this line) so stop_task is never left pending even when this
    # tick is cancelled from outside -- a sub-bee's own runtime_task, run_hive's TaskGroup tearing
    # down, a test (codingrules section 11; this dispatch's own rule 1).
    async with reaping(stop_task):
        done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        return
    items = _drain_items(warden, receive_tasks, done)
    if heartbeat_task in done:
        warden._heartbeat_task = None
        await ticks.heartbeat.send_heartbeat(warden)
        # Sub-bee staleness is checked on this same cadence: simpler than a second per-sub-bee
        # timer, and generous enough that a sub-bee reporting on its own (shorter) interval never
        # trips it early.
        await ticks.heartbeat.raise_stalled_alarms(warden)
    if items:
        ordered = await warden._attendant.order(tuple(items))
        for item in ordered:
            await _handle_item(warden, item)
    await _update_watch_state(warden)


def _drain_items(
    warden: Warden,
    receive_tasks: dict[str, asyncio.Task[Envelope | None]],
    done: set[asyncio.Future[Any]],
) -> list[InboxItem]:
    """Consume every finished receive task in `done`, returning the InboxItems they carried."""
    items: list[InboxItem] = []
    for link_id, task in receive_tasks.items():
        # A task done() only because `stop()` reaped it out from under this same tick (this
        # dispatch's rule 2) reads as cancelled, never as a real result to drain here.
        if task in done and not task.cancelled():
            warden._receive_tasks.pop(link_id, None)
            envelope = task.result()
            if envelope is None:
                continue  # The link ended; a later phase adds OFFLINE bookkeeping for this.
            items.append(to_inbox_item(envelope, link_id))
    return items


def _receive_tasks_snapshot(warden: Warden) -> dict[str, asyncio.Task[Envelope | None]]:
    """Return this tick's cached receive tasks, starting one for any link that lacks one."""
    if _QUEEN_LINK not in warden._receive_tasks:
        warden._receive_tasks[_QUEEN_LINK] = asyncio.ensure_future(
            _next_or_none(warden._queen_iter)
        )
    for worker_id in warden._sub_bees:
        if worker_id not in warden._receive_tasks:
            iterator = warden._sub_bee_iters[worker_id]
            warden._receive_tasks[worker_id] = asyncio.ensure_future(_next_or_none(iterator))
    return dict(warden._receive_tasks)


async def _next_or_none(iterator: AsyncIterator[Envelope]) -> Envelope | None:
    """Return the next decoded Envelope, or None once nothing more will ever arrive."""
    try:
        return await anext(iterator)
    except InvalidPayloadError:
        # The pair stays open per the Transport contract; ask for the next frame instead.
        return await _next_or_none(iterator)
    except (StopAsyncIteration, ConnectionLostError, CodecError, SignatureError):
        return None


def _heartbeat_deadline_task(warden: Warden) -> asyncio.Task[None]:
    """Return the in-flight heartbeat-deadline task, starting one if none is pending."""
    if warden._heartbeat_task is None:
        warden._heartbeat_task = asyncio.ensure_future(
            warden._deps.clock.sleep(warden._deps.heartbeat_interval_s)
        )
    return warden._heartbeat_task


async def _update_watch_state(warden: Warden) -> None:
    """Move ACTIVE with zero sub-bees to WATCH, or WATCH with a spawn back to ACTIVE."""
    has_sub_bees = bool(warden._sub_bees)
    if has_sub_bees and warden._state is WardenState.WATCH:
        warden._state = WardenState.ACTIVE
        await _record_event(warden, "warden.active")
    elif not has_sub_bees and warden._state is WardenState.ACTIVE:
        warden._state = WardenState.WATCH
        await _record_event(warden, "warden.watch")


async def _handle_item(warden: Warden, item: InboxItem) -> None:
    """Decide and act on one ordered InboxItem, waking a model only for NEEDS_JUDGEMENT."""
    sub_bee = _sub_bee_for_item(warden, item)
    view = SubBeeView(state=sub_bee.state, attempt=sub_bee.attempt) if sub_bee is not None else None
    action = decide(item, view, warden._deps.policy)
    binding: str | None = None
    if action is WardenAction.NEEDS_JUDGEMENT:
        sources = ticks.heartbeat.hot_state_sources(warden)
        decision = await decide_awake(warden._deps, _trigger_event(item), sources)
        action, binding = decision.action, decision.binding
    await _act(warden, action, item, sub_bee, binding)


def _sub_bee_for_item(warden: Warden, item: InboxItem) -> SubBee | None:
    """Return the sub-bee `item` concerns: by sender for a sub-bee link, by task for the Queen's.

    The Queen's link carries no sub-bee sender, so her TaskCancel/Pause/Resume and Intervene
    items resolve through the task id the inbox classifier read off the payload.
    """
    if item.principal != _QUEEN_LINK:
        return warden._sub_bees.get(WorkerId(item.principal))
    # A None task_id matches no real SubBee.task_id, so this falls through to None on its own.
    task_id = item.task_id
    return next((sb for sb in warden._sub_bees.values() if sb.task_id == task_id), None)


def _trigger_event(item: InboxItem) -> TriggerEvent:
    """Build the TriggerEvent an awake episode assembles its prompt around, from one InboxItem."""
    return TriggerEvent(
        kind=item.payload_kind,
        summary=f"{item.payload_kind} from {item.principal}",
        payload_ref=item.id,
        clearance=_HoneyClearance.C1,
    )


async def _act(
    warden: Warden,
    action: WardenAction,
    item: InboxItem,
    sub_bee: SubBee | None,
    binding: str | None,
) -> None:
    """Carry out one decided WardenAction."""
    payload = item.payload
    if action is WardenAction.SPAWN and isinstance(payload, TaskAssign):
        await ticks.assign.handle_assign(warden, payload)
    elif action is WardenAction.RECORD:
        await _record_routine(warden, item, payload)
    elif action is WardenAction.ACCEPT and isinstance(payload, TaskResult) and sub_bee is not None:
        await ticks.results.handle_accept(warden, sub_bee, payload)
    elif action in _ALARM_ACTIONS and sub_bee is not None and isinstance(payload, AlarmRaised):
        await ticks.alarms.handle_alarm_action(warden, sub_bee, payload, action, binding)
    elif action is WardenAction.FORWARD_QUESTION and isinstance(payload, Question):
        await ticks.questions.forward_question(warden, MessageId(item.id), payload)
    elif action is WardenAction.FORWARD_ANSWER and isinstance(payload, Answer):
        await ticks.questions.forward_answer(warden, payload)
    elif action is WardenAction.FORWARD_CONTROL and isinstance(
        payload, TaskCancel | TaskPause | TaskResume | Intervene
    ):
        await ticks.control.forward_control(warden, sub_bee, payload)


async def _record_routine(warden: Warden, item: InboxItem, payload: object) -> None:
    """Handle a RECORD-only item: a grant, a heartbeat, or routine progress."""
    if isinstance(payload, GrantIssued):
        await ticks.assign.handle_grant(warden, payload)
    elif isinstance(payload, Heartbeat):
        ticks.heartbeat.record_heartbeat(warden, item.principal, payload)
    elif isinstance(payload, TaskProgress):
        ticks.heartbeat.record_progress(warden, item.principal, payload)


async def _record_event(warden: Warden, kind: str, **payload: JsonValue) -> None:
    """Build and record one warden.* WardenEvent, subject to this Warden's own id."""
    event = WardenEvent(
        id=new_event_id(warden._deps.clock),
        hive_id=warden._deps.identity.hive_id,
        node_id=warden._deps.identity.node_id,
        at=warden._deps.clock.now(),
        actor=warden._deps.identity.actor,
        kind=kind,
        subject_id=warden._warden_id,
        payload=payload,
    )
    await warden._deps.trail.record(event)
