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
AttemptManager` and `.reporter.Reporter` do for `WorkerRuntime`), reached through
`hivemind.wardens.ticks.dispatch.act` and split out only so this file and its `Warden` class stay
within codingrules 5.1's size limits.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Constructed by whichever
    composition root builds one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    wardens.make_warden_deps` plus a `QueenEnd` in tests. Calls into `hivemind.cell`,
    `hivemind.guard`, `hivemind.memory` (TriggerEvent), `hivemind.pheromone` (WardenEvent),
    `hivemind.supervision`, `hivemind.wardens.autopilot`, `.awake`, `.inbox`, `.isolation`,
    `.quarantine`, `.spawn`, `.state`, `.ticks`, `.watch.tick` and waggle only.

Key invariants:
    - Every `WardenState` change goes through `hivemind.wardens.state.assert_transition` and is
      followed, in the same call, by its own `warden.*` trail event (codingrules section 12).
    - `stop()` always stops every sub-bee, releases the lease and records `warden.stopped`,
      regardless of which state it was called from; a model server it started (roadmap step 8.3)
      is stopped, and its sources withdrawn from the Queen's view, before the lease is released.
    - `stop()` sets `waggle.loop.TickLoop`'s own stop flag first, before anything else (this
      dispatch's own shutdown-hygiene fix, superseding this class's own earlier "flag last"
      choice): a tick still concurrently in flight then returns via its own throwaway stop_task
      before it can create a fresh heartbeat or receive task that nothing downstream would ever
      reap, and a heartbeat send already in flight at that exact moment still tolerates a Queen
      link the composition root closes right after `stop()` returns
      (`hivemind.wardens.ticks.heartbeat.send_heartbeat`), so `stop()` itself never raises for
      that reason.
    - `stop()` never returns with a task it owns still pending: every sub-bee is retired the one
      way every sub-bee ends (`hivemind.wardens.ticks.alarms.retire_sub_bee`: its runtime stopped
      cooperatively, falling back to a bounded cancel, and its slot freed), and every receive task
      this Warden started is reaped (`hivemind.common.tasks.reap_all`) before the method returns
      -- a shutdown-hygiene fix so no `asyncio.Task` is ever destroyed pending once the
      composition root's event loop closes (codingrules section 11).
    - `_run_tick`'s own throwaway `stop_task` is reaped in a `finally`, so a tick cancelled from
      outside (a sub-bee's `runtime_task`, `run_hive`'s `TaskGroup`, a test) never abandons it.
    - `cell_lost` raises one Alarm per loss and holds off the idle lease release until
      `cell_found` (roadmap step 11.6); neither looks at what kind of Cell this Warden has.
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
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

from pydantic import JsonValue

from hivemind.cell import Cell, CellSession, RealCellLease
from hivemind.cell import HoneyClearance as _HoneyClearance
from hivemind.common.tasks import reap, reap_all, reaping
from hivemind.forage import Ceilings, HostingPlan
from hivemind.guard import CapabilitySet
from hivemind.memory import TriggerEvent
from hivemind.pheromone import WardenEvent
from hivemind.supervision import ChildKind, ChildRef, Intervention, Quarantine
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens import ticks
from hivemind.wardens.autopilot import SubBeeView, WardenAction, decide
from hivemind.wardens.awake import decide_awake
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.inbox import to_inbox_item, warden_attendant
from hivemind.wardens.isolation import admit_resume, taint_own_memory
from hivemind.wardens.local_pool import SubBeeSlots
from hivemind.wardens.local_pool.hosting import HostedCell, ModelServerHost, RunningServer
from hivemind.wardens.offline import is_link_down, reconcile, wake_future
from hivemind.wardens.quarantine import QuarantineRecord, admit_respawn, carry_out, quarantine_child
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.state import WardenState
from hivemind.wardens.watch.tick import watch_tick
from hivemind.workers.tools.comb import CombToolEntry
from waggle.envelope import Envelope
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import MessageId, TaskId, WardenId, WorkerId, new_event_id
from waggle.loop import TickLoop
from waggle.messages.cell import CellTaintOrder
from waggle.messages.forage import GrantIssued, SourceRemovalCause
from waggle.messages.supervision import CompactView, ContextTelemetry
from waggle.messages.task import TaskAssign

_QUEEN_LINK = "queen"  # The receive-task/InboxItem.principal key for the Warden's own Queen link.

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
        self._warden_id, self._deps = warden_id, deps
        self._state = WardenState.STARTING
        self._lease: RealCellLease | None = None
        self._cell: Cell | None = None
        self._session: CellSession | None = None
        self._ceiling: CapabilitySet = CapabilitySet.empty()
        self._sub_bees: dict[WorkerId, SubBee] = {}
        self._sub_bee_iters: dict[WorkerId, AsyncIterator[Envelope]] = {}
        self._sub_bee_slots = SubBeeSlots(0)
        self._grants: dict[str, GrantIssued] = {}
        self._pending: dict[str, TaskAssign] = {}
        self._lease_retry: dict[str, datetime] = {}  # 11.8a: when to ask for a refused lease again.
        self._questions: dict[str, WorkerId] = {}
        self._question_envelope_ids: dict[str, MessageId] = {}
        # Roadmap step 7.8: which sub-bee asked each Honey query this Warden forwarded, so the
        # Queen's answer, which names only the forwarded envelope, reaches the right one.
        self._honey_relay = ticks.honey.HoneyRelay()
        self._attendant = warden_attendant(deps.clock)
        self._queen_iter: AsyncIterator[Envelope] = deps.queen_link.receive()
        self._receive_tasks: dict[str, asyncio.Task[Envelope | None]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        # Roadmap step 4.9 (Clustering): tasks a Queen-sent Intervene(HANDOFF) paused, dropped on a
        # TaskResume; ticks.assign.settle_after_tick reads it to decide ACTIVE <-> CLUSTERED.
        self._clustered_tasks: set[TaskId] = set()
        # Roadmap 4.8: the Queen's own CeilingsSet/PlanWritten (ticks.control), None until sent.
        self._ceilings: Ceilings | None = None
        self._hosting_plan: HostingPlan | None = None
        # Roadmap step 8.3: the model servers this Warden hosts on its Cell; None until `start()`
        # holds a lease and the manifest declares at least one (`_host_models`).
        self._models: ModelServerHost | None = None
        # Roadmap step 10.6c: every task a quarantine holds until a judge-cleared respawn lifts it.
        self._quarantined: dict[TaskId, QuarantineRecord] = {}
        # Roadmap phase 9: its tools by name; who asked each relayed tool request; Basket transfers
        # in flight; the jobs it owns (reaped in `stop`); whether the Queen isolated this Cell.
        self._comb_entries: dict[str, CombToolEntry] = {}
        self._tool_relay = ticks.tools.ToolRelay()
        self._basket = ticks.basket.BasketTransfers()
        self._tool_jobs: set[asyncio.Task[None]] = set()
        self._isolated, self._cell_lost = False, False  # `_cell_lost`: step 11.6, link is down.
        # A relayed snapshot freezes this Warden with its Cell: announced first, so the Queen
        # never reads the Hive's own freeze as this Warden gone silent (ticks.heartbeat).
        ticks.heartbeat.bind_freeze_announcer(self)

    @property
    def state(self) -> WardenState:
        """This Warden's current WardenState."""
        return self._state

    @property
    def lease(self) -> RealCellLease | None:
        """This Warden's own lease, or None before `start()` succeeds or after `stop()`."""
        return self._lease

    @property
    def hosted_servers(self) -> Mapping[str, RunningServer]:
        """The model servers this Warden currently runs on its Cell, by server id (roadmap 8.3)."""
        return self._models.running if self._models is not None else {}

    @property
    def sub_bees(self) -> tuple[SubBee, ...]:
        """Every sub-bee this Warden currently supervises."""
        return tuple(self._sub_bees.values())

    async def start(self) -> None:
        """Lease this Warden's Cell and open its own session, or move to WATCH if refused.

        The Hive Stand's Warden exists even when its lease is refused (codingrules section 8.8):
        this method never raises for that case, only for a truly unrecoverable state. Roadmap
        step 10.3: the lease is refused, too, when the Guard's `lease_creation` point does not
        allow this Warden's `lease_capability` (`hivemind.wardens.ticks.lease.open_lease`).
        """
        await ticks.lease.open_lease(self)
        # Only once the lease is held (roadmap step 8.3): a server runs on the Cell it leased.
        await _host_models(self)

    async def cell_lost(self, reason: str) -> None:
        """Alarm the Queen that this Warden's Cell link dropped, once per loss (step 11.6)."""
        await ticks.lease.report_cell_lost(self, reason)

    def cell_found(self) -> None:
        """Note this Warden's Cell link is back, so the next loss raises its own Alarm."""
        ticks.lease.clear_cell_lost(self)

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
            # The one path every ending takes (ticks.alarms.retire_sub_bee): stopped cooperatively,
            # cancel-and-reap only as a bounded fallback, its receive task reaped BEFORE its link
            # closes (codingrules section 11; this dispatch's rule 2), and its slot freed.
            await ticks.alarms.retire_sub_bee(self, sub_bee)
        # Background jobs (a scaffold, a sandbox request, a shipment) wait on replies no tick will
        # deliver once the loop has stopped, so they are cancelled and reaped, not awaited.
        await reap_all(tuple(self._tool_jobs))
        self._tool_jobs.clear()
        # Every receive task this Warden still owns (the queen link's own, and any sub-bee's
        # whose respawn or a slow stop_sub_bee left one outstanding): reaped before stop()
        # returns, so none is ever destroyed pending once the event loop closes (rules 1-3).
        await reap_all(self._receive_tasks.values())
        self._receive_tasks.clear()
        # Before the lease is released (left as found, codingrules section 8.7): nothing this
        # Warden started on the Cell may outlive the lease, and the Queen hears each source go.
        if self._models is not None:
            await self._models.stop_all(SourceRemovalCause.LEASE_RELEASED)
        released = await _release_lease(self)
        self._state = WardenState.STOPPED
        await _record_event(self, "warden.stopped", **released)
        # Last of all, so `warden.stopped` itself is inside the shipped segment: a Warden whose
        # trail store dies with its Cell (ADR-0027) gets one final chance to hand the Queen
        # everything it recorded. A closed or half-closed link here is not worth failing a stop
        # over, so this is best-effort, exactly like `ticks.heartbeat.send_heartbeat`'s own
        # tolerance of a link the composition root already closed.
        await _sync_trail(self)

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
        return ticks.heartbeat.compact_view(await self.telemetry(child))

    async def intervene(self, child: str, intervention: Intervention) -> None:
        """Send `intervention` to `child` over its own link.

        Raises:
            UnknownSubBeeError: `child` names no current sub-bee.
        """
        # A quarantine is this Warden's to carry out on its sub-bee, never a lever to relay to it.
        if isinstance(intervention, Quarantine):
            await quarantine_child(self, child, intervention)
            return
        await ticks.control.send_intervention(self, child, intervention)


# ──────────────────────────────────────────────────────────────────────────────
# Tick dispatch: module-level so Warden's own class body stays within codingrules 5.1
# ──────────────────────────────────────────────────────────────────────────────


async def _run_tick(warden: Warden) -> None:
    """Drain what is ready, order it, act on it, then heartbeat -- Warden's one tick."""
    receive_tasks = _receive_tasks_snapshot(warden)
    heartbeat_task = _heartbeat_deadline_task(warden)
    done = await _wait_for_work(warden, {*receive_tasks.values(), heartbeat_task})
    if done is None:
        return  # stop() was called while this tick waited.
    items = _drain_items(warden, receive_tasks, done)
    if heartbeat_task in done:
        warden._heartbeat_task = None
        await ticks.heartbeat.send_heartbeat(warden)
        # Sub-bee staleness is checked on this same cadence: simpler than a second per-sub-bee
        # timer, and generous enough that a sub-bee reporting on its own (shorter) interval never
        # trips it early. Each stalled Alarm takes the path a wire Alarm takes, policy and all.
        for stalled in await ticks.heartbeat.raise_stalled_alarms(warden):
            await _handle_item(warden, stalled)
        # Roadmap step 8.3: a model server that exited on its own is withdrawn from the Hive.
        if warden._models is not None:
            await warden._models.sweep()
        # On the same cadence, and for the same reason the staleness check shares it: one timer,
        # not two. A Warden with no `trail_sync` (the Hive Stand's own) does nothing here.
        await _sync_trail(warden)
    if items:
        ordered = await warden._attendant.order(tuple(items))
        for item in ordered:
            await _handle_item(warden, item)
    if warden._stop.is_set():
        # A Shutdown or CellTeardownRequest handled just above already ran `stop()`, which leaves
        # this Warden STOPPED -- a terminal state `settle_after_tick`'s own `assert_transition`
        # would (correctly) refuse to move out of.
        return
    # After the items, so what the Queen sent on a return (a cancel of a task she re-placed) is
    # acted on before this Warden starts its own paused tasks again; before the settle, so the
    # state it settles from already reflects the link (roadmap 11.9).
    await reconcile(warden)
    await ticks.assign.settle_after_tick(warden)
    await watch_tick(warden)  # Roadmap step 11.10: only while idle in WATCH, once state settled.


async def _wait_for_work(
    warden: Warden, waitables: set[asyncio.Future[Any]]
) -> set[asyncio.Future[Any]] | None:
    """Wait until one of `waitables` is done; None if `stop()` ended the wait instead."""
    # Throwaway: only wakes this wait early when stop() is called mid-tick.
    stop_task: asyncio.Task[bool] = asyncio.ensure_future(warden._stop.wait())
    # Throwaway too (roadmap 11.9): a link or Cell that was lost or came back ends the wait at once,
    # where the heartbeat deadline would only notice it up to one interval later.
    wake_task = wake_future(warden)
    # reaping (not a bare reap after the wait) so both throwaways are never left pending even when
    # this tick is cancelled from outside -- a sub-bee's own runtime_task, run_hive's TaskGroup
    # tearing down, a test (codingrules section 11; this dispatch's own rule 1).
    async with reaping(stop_task), reaping(wake_task):
        done, _pending = await asyncio.wait(
            {*waitables, stop_task, wake_task}, return_when=asyncio.FIRST_COMPLETED
        )
    return None if stop_task in done else done


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
                # The link ended for good: a reconnecting link (`hivemind.wardens.offline`) never
                # ends before it is closed, and an in-process one only when its peer closes it.
                continue
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


async def _handle_item(warden: Warden, item: InboxItem) -> None:
    """Decide and act on one ordered InboxItem, waking a model only for NEEDS_JUDGEMENT."""
    # The sub-bee `item` concerns: by sender for a sub-bee link, by task for the Queen's own
    # (her link carries no sub-bee sender; a None item.task_id matches no real SubBee.task_id).
    if item.principal != _QUEEN_LINK:
        sub_bee = warden._sub_bees.get(WorkerId(item.principal))
    else:
        sub_bee = next((sb for sb in warden._sub_bees.values() if sb.task_id == item.task_id), None)
    view = SubBeeView(state=sub_bee.state, attempt=sub_bee.attempt) if sub_bee is not None else None
    action = decide(item, view, warden._deps.policy)
    binding: str | None = None
    if action is WardenAction.NEEDS_JUDGEMENT:
        sources = ticks.heartbeat.hot_state_sources(warden)
        decision = await decide_awake(warden._deps, _trigger_event(item), sources)
        action, binding = decision.action, decision.binding
    await _act(warden, action, item, sub_bee, binding)


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
    """Carry out one decided WardenAction: the isolation orders here, every other in `ticks`.

    Roadmap phase 10's quarantine (10.6c) and memory taint (10.6a), and the gates a spawn passes
    first, stay in this module rather than `hivemind.wardens.ticks.dispatch`: the quarantine and
    isolation packages import tick handlers themselves, so no tick module imports them back.
    """
    payload = item.payload
    if action is WardenAction.QUARANTINE:
        # The Queen's Intervene(QUARANTINE), or this Warden's own policy row for a sub-bee's
        # Alarm; either way the one code path in hivemind.wardens.quarantine.
        await carry_out(warden, payload, sub_bee)
    elif action is WardenAction.TAINT_MEMORY:
        # Only the Queen isolates, so only her order labels this Cell's own store; one from
        # anyone else is dropped here.
        if isinstance(payload, CellTaintOrder) and item.principal == _QUEEN_LINK:
            await taint_own_memory(warden, payload)
    elif action is WardenAction.SPAWN and isinstance(payload, TaskAssign):
        # A quarantined task spawns only by its one way out (the gate), and nothing resumes from
        # a Handoff this Cell's own store labels tainted.
        if await admit_respawn(warden, payload) and await admit_resume(warden, payload):
            await ticks.dispatch.act(warden, action, item, sub_bee, binding)
    else:
        await ticks.dispatch.act(warden, action, item, sub_bee, binding)


async def _host_models(warden: Warden) -> None:
    """Start the model servers `[llm.servers]` declares, once this Warden holds its Cell.

    A Warden with no lease (WATCH) or no declared server hosts nothing; a Cell that cannot host a
    model declines inside `ModelServerHost` (logged once) rather than failing `start()`.
    """
    cell, session = warden._cell, warden._session
    if warden._lease is None or cell is None or session is None or not warden._deps.servers:
        return
    place = HostedCell(cell=cell, session=session, ceilings_of=lambda: warden._ceilings)
    warden._models = ModelServerHost(warden._deps, warden._warden_id, place)
    await warden._models.start_all()


async def _release_lease(warden: Warden) -> dict[str, JsonValue]:
    """Give the lease back for `stop()`; a Cell that cannot confirm it is recorded, never raised.

    A device that went away mid-run cannot confirm a release, and `stop()` has other things to end
    (the sub-bees, the loop, the trail): the lease stays ORPHANED for the device's own dead-man
    switch or a later sweep to settle, and `warden.stopped` says so. Returns the payload to add.
    """
    lease = warden._lease
    if lease is None:
        return {}
    try:
        await lease.release()
    except ticks.lease.RELEASE_FAILURES as error:
        return {"lease_released": False, "release_error": type(error).__name__}
    return {}


async def _sync_trail(warden: Warden) -> None:
    """Ship this node's own trail segment to the Queen, if this Warden has a sync to ship it with.

    Best-effort by design (codingrules section 12: the merge is idempotent by event id, so a send
    lost to a closing link costs nothing but a repeat next time). A `WardenDeps.trail_sync` of
    `None` -- every composition root but `hivemind.cli.in_cell` -- makes this a no-op, and so is a
    link its Warden knows is down: the reconnection ships the segment (roadmap 11.9), and trying
    meanwhile would only record `warden.offline` again on every heartbeat.
    """
    if warden._deps.trail_sync is None or is_link_down(warden):
        return
    if not await warden._deps.trail_sync.sync():
        # The Queen link is gone; the segment stays local and the next successful sync (or the
        # next Cell's own) re-sends it. Never a reason to end this Warden's tick loop or its stop.
        await _record_event(warden, "warden.offline")


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
