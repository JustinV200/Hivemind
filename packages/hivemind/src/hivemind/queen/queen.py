"""Define Queen: the standard loop shape that supervises Wardens, and delegates rather than runs.

The Queen is the Hive's single always-on orchestrator, built like an operating-system kernel
(codingrules section 8.8, docs/adr/0019): a thin loop with a prioritised inbox. She holds no
session and no Comb Registry -- her only levers are the six `hivemind.supervision.Intervention`
values, sent to a Warden, never carried out herself. One tick (`_tick`, driven by `waggle.loop.
TickLoop.run`) drains whatever is ready on every attached Warden's own link into `hivemind.
supervision.attendant.InboxItem`s, orders them with her own Attendant (`hivemind.queen.inbox`,
optionally with a model-backed `TieBreaker` for an exact tie), and dispatches each through
`hivemind.queen.autopilot.table.decide` to one `hivemind.queen.autopilot.QueenAction` --
`NEEDS_JUDGEMENT` runs one stateless `hivemind.queen.awake.episode.decide_awake` episode instead --
before checking every attached Warden's own liveness and placing whatever the Brood Chamber now
says is ready. The work each action does lives in `hivemind.queen.ticks`, `hivemind.queen.
dispatcher` and `hivemind.queen.questions`, this module's own delegates, split out only so this
file and its `Queen` class stay within codingrules 5.1's size limits.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Constructed by whichever
    composition root builds one -- the CLI (roadmap step 3.21) in production, `tests.builders.
    queen.make_queen_deps` plus one or more `WardenLink`s in tests. Calls into `hivemind.brood_
    chamber`, `hivemind.cell`, `hivemind.forage.slots`, `hivemind.memory`, `hivemind.queen.*` and
    `hivemind.supervision` and waggle only; never `hivemind.wardens` or `hivemind.workers` (the
    Queen never provisions or spawns, only assigns to a Warden over Waggle).

Key invariants:
    - The Queen holds no `hivemind.cell.CellSession` and no Comb Registry, anywhere in her own
      instance attributes or `QueenDeps`'s own fields (docs/adr/0019; a test introspects both).
    - She never assigns a task to a Worker directly: every assignment goes to a Warden
      (`hivemind.queen.dispatcher.dispatch_ready`).
    - `dispatcher.dispatch_ready` and the Warden-liveness sweep run unconditionally at the end of
      every tick, never gated behind an inbox item: a stuck Alarm elsewhere in the ordered inbox
      must never delay noticing a dead Warden or placing a newly-ready task (this dispatch's own
      report explains why `QueenAction.DISPATCH`/`MARK_WARDEN_OFFLINE` exist in the vocabulary but
      are not reached through this module's own live wiring).
    - `_recoverable_errors` names `InvalidTransitionError` (this dispatch's own fix 4): a chamber
      transition that still fails on a stale status even after fix 4a's own reordering (`hivemind.
      queen.dispatcher._dispatch_one`) is a recoverable tick failure, not one that ends `run()` and
      takes the whole Hive down with it -- `waggle.loop.TickLoop.run` backs off and retries the
      next tick, and `_on_tick_failed` records it as `queen.decided`.
    - `stop()` sets the stop flag before reaping `_receive_tasks` (this dispatch's own shutdown-
      hygiene fix), the opposite order from `hivemind.wardens.warden.Warden.stop`: a tick still in
      flight when `stop()` runs sees its own throwaway `stop_task` win the very same race and
      returns before `_drain_items` ever runs, so nothing here races that tick over a task `stop()`
      is concurrently reaping. `_run_tick`'s own `stop_task` is reaped via `hivemind.common.tasks.
      reaping`, never left pending when a tick is cancelled from outside.

See Also:
    - .claude/codingrules.md section 8.8 for the kernel/Attendant/autopilot/awake shape this class
      implements.
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for why she holds
      no session and no registry.
    - .claude/roadmap.md step 3.20 for this class's own roadmap bullet.
    - hivemind.queen.ticks for the tick handlers this class's dispatch calls into.
    - hivemind.supervision.supervisor for Supervisor, the protocol this class also implements.
"""

from __future__ import annotations

import asyncio
import types
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar

from hivemind.brood_chamber import AnswerSource, InvalidTransitionError, Task, TaskNotFoundError
from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.common.tasks import reap_all, reaping
from hivemind.forage.slots import ModelSlot
from hivemind.memory import TriggerEvent
from hivemind.queen import questions, ticks
from hivemind.queen.autopilot import QueenAction, decide, effort_for
from hivemind.queen.awake import QueenSources, decide_awake
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.errors import UnknownWardenError
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import queen_attendant, to_inbox_item
from hivemind.queen.planner import plan_goal
from hivemind.queen.ticks.alarms import AlarmHandling
from hivemind.queen.ticks.liveness import WardenLiveness
from hivemind.queen.trail import record_event
from hivemind.supervision import (
    Alarm,
    ChildKind,
    ChildRef,
    Intervention,
    record_alarm_event,
    to_wire,
)
from hivemind.supervision.attendant import InboxItem, TieBreaker
from waggle.envelope import Envelope, wrap
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import MessageId, TaskId, WardenId
from waggle.loop import TickLoop
from waggle.messages.supervision import (
    AlarmRaised,
    Answer,
    CompactView,
    ContextTelemetry,
    Heartbeat,
    Intervene,
    Question,
)
from waggle.messages.task import TaskResult

__all__ = ["Queen"]


class Queen(TickLoop):
    """The Hive's single orchestrator: her own inbox, autopilot, awake mode, and Supervisor face.

    Owns her own mutable state in place (codingrules section 8.5), documented here: `_wardens`,
    `_liveness`, `_last_heartbeat`, `_open_questions` and `_human_inbox` change as she runs. Her
    `hivemind.queen.ticks`, `.dispatcher` and `.questions` delegates read and write it directly.
    """

    # A chamber transition failing on a status some other hop already moved past her own view of
    # (a stale read racing a real Warden's own reaction over real SQLite I/O) is recoverable, not
    # fatal: codingrules section 8.8 never intends one InvalidTransitionError to take the whole
    # Hive down (this dispatch's own fix 4, "the Queen's own crash surface"). `waggle.loop.
    # TickLoop.run` backs off and retries the next tick rather than letting it propagate.
    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (InvalidTransitionError,)

    def __init__(self, deps: QueenDeps, tie_breaker: TieBreaker | None = None) -> None:
        """Build a Queen with no Warden attached yet; call `attach_warden` before `run()`.

        Args:
            deps: Every collaborator the Queen needs.
            tie_breaker: The Attendant's own model-backed arbiter for an exact score tie; None
                falls back to `(received_at, id)`.
        """
        super().__init__(deps.clock)
        self._deps = deps
        self._wardens: dict[WardenId, WardenLink] = {}
        self._link_iters: dict[WardenId, AsyncIterator[Envelope]] = {}
        self._receive_tasks: dict[WardenId, asyncio.Task[Envelope | None]] = {}
        self._liveness: dict[WardenId, WardenLiveness] = {}
        self._last_heartbeat: dict[WardenId, Heartbeat] = {}
        self._open_questions: dict[MessageId, TaskId] = {}
        # The Brood Chamber's own Question.id -> the *original* wire Question.question_id: the
        # chamber always mints its own id (hivemind.brood_chamber.questions's own module
        # docstring), but a Warden matches an eventual Answer back to its own blocked sub-bee by
        # the original id alone (hivemind.wardens.ticks.questions.forward_answer), so both must be
        # remembered to answer correctly.
        self._question_wire_ids: dict[MessageId, MessageId] = {}
        # The same chamber id -> the arriving Question's own envelope id, so answering it can set
        # the reply Envelope's required correlation_id (waggle.envelope.wrap; a `supervision.answer`
        # is a MessageShape.REPLY), mirroring hivemind.wardens.ticks.questions's own
        # `_question_envelope_ids` for the hop below this one.
        self._question_envelope_ids: dict[MessageId, MessageId] = {}
        # A task's own retry count, since hivemind.brood_chamber.task.state.TRANSITIONS has no
        # edge back from RUNNING that would let `Task.attempt` itself track this (module docstring
        # of hivemind.queen.dispatcher.redispatch); absent means "on its first attempt" (1).
        self._attempts: dict[TaskId, int] = {}
        # The Alarm behind a still-outstanding REBIND/RETRY_TASK, so a later COMPLETE_TASK for the
        # same task can record alarm.resolved (this dispatch's own fix 3d); populated by
        # hivemind.queen.ticks.alarms.handle_alarm, popped here once the task actually succeeds.
        self._pending_alarms: dict[TaskId, Alarm] = {}
        self._human_inbox = HumanInbox()
        self._attendant = queen_attendant(deps.clock, tie_breaker)

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden currently attached, in attachment order."""
        return tuple(self._wardens.values())

    @property
    def liveness(self) -> Mapping[WardenId, WardenLiveness]:
        """A read-only snapshot of every attached Warden's own liveness."""
        return types.MappingProxyType(dict(self._liveness))

    @property
    def human_inbox(self) -> HumanInbox:
        """The pending questions and Alarms currently waiting on the human."""
        return self._human_inbox

    def attach_warden(self, link: WardenLink) -> None:
        """Attach one Warden's own link; the composition root calls this before `run()`.

        The Queen never creates Wardens or Cells (CLAUDE.md): this only records an already-built
        link, and starts draining it on the next tick.

        Args:
            link: The Warden's own address, Cell and Waggle link.
        """
        self._wardens[link.warden_id] = link
        self._link_iters[link.warden_id] = link.transport.receive()
        self._liveness[link.warden_id] = WardenLiveness(
            last_heartbeat_at=None, missed_heartbeats=0, is_offline=False
        )

    async def submit_goal(self, goal: str, *, clearance: HoneyClearance) -> TaskId:
        """Plan `goal` into a task graph, persist it, and place whatever is ready at once.

        Args:
            goal: The goal text, as the human (or a bee on the human's behalf) stated it.
            clearance: The goal's own data-sensitivity ceiling.

        Returns:
            The goal's own id (the first task minted from the plan).
        """
        bound = self._deps.bound_for(ModelSlot.QUEEN)
        draft = await plan_goal(goal, bound, gate=self._deps.call_gate, clearance=clearance)
        minted = await self._deps.chamber.submit(draft)
        goal_id = minted[0].id
        await record_event(self._deps, "queen.planned", goal_id, task_count=len(minted))
        await dispatch_ready(self._deps, self.wardens)
        return goal_id

    async def answer_question(
        self,
        question_id: MessageId,
        text: str,
        *,
        source: AnswerSource = AnswerSource.QUEEN,
        clearance: HoneyClearance = HoneyClearance.C1,
    ) -> Task:
        """Record a human's (or the Queen's own) answer and forward it to the asking Warden.

        See `hivemind.queen.questions`'s own module docstring for why this is the one path a
        caller should use instead of the Brood Chamber's own `answer` directly.

        Args:
            question_id: The question being answered.
            text: The answer text.
            source: Who answered; QUEEN by default.
            clearance: The answer's data-sensitivity label; C1 by default.

        Returns:
            The question's task, now RUNNING again.
        """
        wire_question_id = self._question_wire_ids.pop(question_id, None)
        correlation_id = self._question_envelope_ids.pop(question_id, None)
        # Fix 3: drop _open_questions here too, or sync_answers_from_chamber's own cross-process
        # sweep later mistakes it for "not yet forwarded" and looks for a Note that never comes.
        if wire_question_id is not None:
            self._open_questions.pop(wire_question_id, None)
        answer_input = questions.AnswerInput(
            question_id=question_id,
            text=text,
            source=source,
            clearance=clearance,
            wire_question_id=wire_question_id,
            correlation_id=correlation_id,
        )
        return await questions.answer_question(self._deps, self.wardens, answer_input)

    async def stop(self) -> None:  # type: ignore[override]
        """End the loop, then reap every attached Warden's own receive task (rules 1-3)."""
        await _stop_queen(self)

    async def _tick(self) -> None:
        """Drain every attached Warden's link, order and act, then liveness and dispatch."""
        await _run_tick(self)

    async def _on_tick_failed(self, error: Exception) -> None:
        """Record a recovered tick error on the trail (`waggle.loop.TickLoop`'s own hook)."""
        await _record_recovered_tick_error(self, error)

    # ──────────────────────────────────────────────────────────────────────────
    # Supervisor protocol (hivemind.supervision.supervisor.Supervisor)
    # ──────────────────────────────────────────────────────────────────────────

    async def children(self) -> tuple[ChildRef, ...]:
        """Return one ChildRef per Warden currently attached."""
        return tuple(
            ChildRef(id=w, kind=ChildKind.WARDEN, task_id=None, state=self._state_of(w))
            for w in self._wardens
        )

    async def telemetry(self, child: str) -> ContextTelemetry:
        """Return `child`'s last reported ContextTelemetry.

        Raises:
            UnknownWardenError: `child` names no attached Warden, or none has reported yet.
        """
        heartbeat = self._last_heartbeat.get(WardenId(child))
        if heartbeat is None:
            raise UnknownWardenError(child)
        return heartbeat.telemetry

    async def inspect(self, child: str) -> CompactView:
        """Return a CompactView built from `child`'s last reported telemetry.

        Raises:
            UnknownWardenError: `child` names no attached Warden, or none has reported yet.
        """
        return _compact_view(await self.telemetry(child))

    async def intervene(self, child: str, intervention: Intervention) -> None:
        """Send `intervention` to `child` over its own link.

        Raises:
            UnknownWardenError: `child` names no attached Warden.
        """
        # Module-level (matching _run_tick/_act's own delegate shape) for the class size limit.
        await _send_intervene(self, child, intervention)

    def _state_of(self, warden_id: WardenId) -> str:
        """Return one attached Warden's own state, for a Supervisor.children() row."""
        liveness = self._liveness.get(warden_id)
        if liveness is not None and liveness.is_offline:
            return "OFFLINE"
        heartbeat = self._last_heartbeat.get(warden_id)
        if heartbeat is not None and heartbeat.warden_state is not None:
            return heartbeat.warden_state.value
        return "STARTING"


# ──────────────────────────────────────────────────────────────────────────────
# Tick dispatch: module-level so Queen's own class body stays within codingrules 5.1
# ──────────────────────────────────────────────────────────────────────────────


async def _stop_queen(queen: Queen) -> None:
    """End `queen`'s loop, then reap every attached Warden's own receive task (`Queen.stop`'s body).

    SAFETY: widens `waggle.loop.TickLoop.stop`'s synchronous signature to async, matching
    `hivemind.wardens.warden.Warden.stop` -- the reap below needs to await. The stop flag is set
    first (unlike `Warden.stop`, which has sub-bees and a lease to release before it): the
    currently in-flight tick's own `_run_tick`, if any, sees its throwaway `stop_task` win the
    very same race and returns before ever touching `_receive_tasks`, so nothing here ever races
    that tick's own `_drain_items` (this dispatch's own shutdown-hygiene fix).
    """
    TickLoop.stop(queen)  # Same as super().stop() would from inside Queen.stop's own body.
    await reap_all(queen._receive_tasks.values())
    queen._receive_tasks.clear()


async def _send_intervene(queen: Queen, child: str, intervention: Intervention) -> None:
    """Send `intervention` to `child` over its own link (`Queen.intervene`'s own body).

    Raises:
        UnknownWardenError: `child` names no attached Warden.
    """
    link = queen._wardens.get(WardenId(child))
    if link is None:
        raise UnknownWardenError(child)
    action, slot = to_wire(intervention)
    message = Intervene(
        action=action,
        subject=None,
        task_id=None,
        slot=slot,
        alarm_id=None,
        reason=intervention.reason,
    )
    await link.transport.send(wrap(message, link.hop, clock=queen._deps.clock))


async def _run_tick(queen: Queen) -> None:
    """Drain what is ready, order it, act on it, then check liveness and dispatch."""
    receive_tasks = _receive_tasks_snapshot(queen)
    # Throwaway: only wakes this wait early when stop() is called mid-tick.
    stop_task: asyncio.Task[bool] = asyncio.ensure_future(queen._stop.wait())
    waitables: set[asyncio.Future[Any]] = {*receive_tasks.values(), stop_task}
    # reaping (not a bare reap after this line) so stop_task is never left pending even when this
    # tick is cancelled from outside (this dispatch's own rule 1).
    async with reaping(stop_task):
        done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        return
    items = _drain_items(queen, receive_tasks, done)
    if items:
        ordered = await queen._attendant.order(tuple(items))
        for item in ordered:
            await _handle_item(queen, item)
    await ticks.liveness.check_liveness(
        queen._deps, queen.wardens, queen._liveness, queen._human_inbox
    )
    await dispatch_ready(queen._deps, queen.wardens)
    # This dispatch's own fix 3: the tick now calls the exact same retry-safe function hive run's
    # own poll loop calls (hivemind.cli.compose.run_goal), instead of a separate sweep that used
    # to drop _open_questions the moment a task left BLOCKED for any reason -- see
    # hivemind.queen.questions's own module docstring for the race that closed.
    await questions.sync_answers_from_chamber(queen)


async def _record_recovered_tick_error(queen: Queen, error: Exception) -> None:
    """Record that one tick raised a `_recoverable_errors` member and was backed off, not fatal.

    `waggle.loop.TickLoop.run` calls this once per recovered failure, before its own backoff
    sleep; `queen.decided` (rather than a new kind) is reused because it already covers "the Queen
    acted on a decision" broadly, and `hivemind.pheromone.events.families` is outside this
    dispatch's own files to add a kind to.
    """
    await record_event(
        queen._deps,
        "queen.decided",
        queen._deps.identity.hive_id,
        action="RECOVERED_TICK_ERROR",
        error=type(error).__name__,
    )


def _receive_tasks_snapshot(queen: Queen) -> dict[WardenId, asyncio.Task[Envelope | None]]:
    """Return this tick's cached receive tasks, starting one for any link that lacks one."""
    for warden_id in queen._wardens:
        if warden_id not in queen._receive_tasks:
            iterator = queen._link_iters[warden_id]
            queen._receive_tasks[warden_id] = asyncio.ensure_future(_next_or_none(iterator))
    return dict(queen._receive_tasks)


async def _next_or_none(iterator: AsyncIterator[Envelope]) -> Envelope | None:
    """Return the next decoded Envelope, or None once nothing more will ever arrive."""
    try:
        return await anext(iterator)
    except InvalidPayloadError:
        # The pair stays open per the Transport contract; ask for the next frame instead.
        return await _next_or_none(iterator)
    except (StopAsyncIteration, ConnectionLostError, CodecError, SignatureError):
        return None


def _drain_items(
    queen: Queen,
    receive_tasks: dict[WardenId, asyncio.Task[Envelope | None]],
    done: set[asyncio.Future[Any]],
) -> list[InboxItem]:
    """Consume every finished receive task in `done`, returning the InboxItems they carried."""
    items: list[InboxItem] = []
    for warden_id, task in receive_tasks.items():
        # A task done() only because stop() reaped it out from under this same tick (this
        # dispatch's rule 2) reads as cancelled, never as a real result to drain here.
        if task not in done or task.cancelled():
            continue
        queen._receive_tasks.pop(warden_id, None)
        envelope = task.result()
        if envelope is None:
            continue  # The link ended; a later phase adds OFFLINE bookkeeping for this.
        items.append(to_inbox_item(envelope, warden_id))
    return items


async def _handle_item(queen: Queen, item: InboxItem) -> None:
    """Decide and act on one ordered InboxItem, waking a model only for NEEDS_JUDGEMENT."""
    if isinstance(item.payload, Heartbeat):
        warden_id = WardenId(item.principal)
        queen._last_heartbeat[warden_id] = item.payload
        ticks.liveness.record_heartbeat(queen._liveness, warden_id, item.received_at)
        return
    task = await _task_for_item(queen._deps, item)
    # Read from the Queen's own counter, never Task.attempt: see hivemind.queen.dispatcher.
    # redispatch's own docstring for why a RUNNING task's attempt count cannot live in the chamber.
    attempts = queen._attempts.get(task.id, 1) if task is not None else 0
    action = decide(item, task, attempts, queen._deps.policy, queen._deps.alarm_attempt_limit)
    came_from_awake = False
    if action is QueenAction.NEEDS_JUDGEMENT:
        action = await _run_awake(queen, item)
        came_from_awake = True
    if came_from_awake or action is QueenAction.ESCALATE_TO_HUMAN:
        subject = task.id if task is not None else queen._deps.identity.hive_id
        await record_event(queen._deps, "queen.decided", subject, action=action.value)
    await _act(queen, action, item, task, WardenId(item.principal))


async def _run_awake(queen: Queen, item: InboxItem) -> QueenAction:
    """Run one stateless awake episode for `item`, record that it happened, and return its action.

    The decision's own `binding` (a REBIND hint) is not read here: `hivemind.queen.ticks.alarms`
    always resolves the fallback key itself from `deps.bindings`, the one source of truth for a
    task's own slot chain, so the model's own suggestion is advisory only.
    """
    sources = QueenSources(queen._deps.chamber, queen._deps.memory, queen._human_inbox)
    effort = effort_for(item.kind)
    event = TriggerEvent(
        kind=item.payload_kind,
        summary=f"{item.payload_kind} from {item.principal}",
        payload_ref=item.id,
        clearance=HoneyClearance.C2,
    )
    decision = await decide_awake(queen._deps, event, sources, effort)
    await record_event(
        queen._deps, "queen.awake", queen._deps.identity.hive_id, event_kind=item.payload_kind
    )
    return decision.action


async def _task_for_item(deps: QueenDeps, item: InboxItem) -> Task | None:
    """Look up the task `item` concerns, or None when it names none or one that cannot be found."""
    if item.task_id is None:
        return None
    try:
        return await deps.chamber.get(item.task_id)
    except TaskNotFoundError:
        return None


async def _act(
    queen: Queen, action: QueenAction, item: InboxItem, task: Task | None, warden_id: WardenId
) -> None:
    """Carry out one decided QueenAction; a payload-type mismatch (a stale wire kind) is a no-op."""
    payload = item.payload
    if isinstance(payload, TaskResult):
        await _act_on_task_result(queen, action, payload)
    elif isinstance(payload, AlarmRaised):
        handling = AlarmHandling(
            human_inbox=queen._human_inbox,
            warden_id=warden_id,
            payload=payload,
            action=action,
            attempts=queen._attempts,
            pending_alarms=queen._pending_alarms,
        )
        await ticks.alarms.handle_alarm(queen._deps, queen.wardens, handling)
    elif action is QueenAction.BLOCK_ON_QUESTION and isinstance(payload, Question):
        chamber_question = await questions.handle_question(queen._deps, payload)
        queen._open_questions[payload.question_id] = payload.task_id
        queen._question_wire_ids[chamber_question.id] = payload.question_id
        queen._question_envelope_ids[chamber_question.id] = MessageId(item.id)
    elif isinstance(payload, Answer):
        pass  # ROUTE_ANSWER: no wire path produces this in v0 (see hivemind.queen.questions).


async def _act_on_task_result(queen: Queen, action: QueenAction, payload: TaskResult) -> None:
    """Carry out COMPLETE_TASK, RETRY_TASK or FAIL_TASK for a TaskResult."""
    if action is QueenAction.COMPLETE_TASK:
        await ticks.results.complete_task(queen._deps, queen.wardens, payload)
        await _resolve_pending_alarm(queen, payload.task_id)
    elif action is QueenAction.RETRY_TASK:
        attempt = _next_attempt(queen, payload.task_id)
        await ticks.results.retry_task(queen._deps, queen.wardens, payload.task_id, attempt)
    elif action is QueenAction.FAIL_TASK:
        await ticks.results.fail_task(queen._deps, payload.task_id, payload.reason)


def _next_attempt(queen: Queen, task_id: TaskId) -> int:
    """Advance and return `task_id`'s own retry counter, starting from 1 (its first attempt)."""
    next_value = queen._attempts.get(task_id, 1) + 1
    queen._attempts[task_id] = next_value
    return next_value


async def _resolve_pending_alarm(queen: Queen, task_id: TaskId) -> None:
    """Record alarm.resolved when `task_id` succeeds with a REBIND/RETRY_TASK still outstanding.

    This dispatch's own fix 3d: `hivemind.queen.ticks.alarms.handle_alarm` remembers the Alarm
    behind a REBIND or RETRY_TASK on `queen._pending_alarms`; once the rebound or retried attempt
    actually reaches COMPLETE_TASK, the Alarm that prompted it is finally settled.
    """
    alarm = queen._pending_alarms.pop(task_id, None)
    if alarm is None:
        return  # No outstanding Alarm for this task (it never needed a rebind or a retry).
    identity = CellIdentity(
        hive_id=queen._deps.identity.hive_id,
        node_id=queen._deps.identity.node_id,
        actor=queen._deps.identity.actor,
    )
    await record_alarm_event(
        queen._deps.trail, identity, queen._deps.clock, alarm, "alarm.resolved"
    )


def _compact_view(telemetry: ContextTelemetry) -> CompactView:
    """Build the CompactView a Supervisor.inspect() reply carries, from one ContextTelemetry."""
    return CompactView(
        goal=telemetry.goal,
        progress=f"{telemetry.tokens_used}/{telemetry.context_window} tokens used.",
        decisions=tuple(telemetry.last_actions),
        open_threads=tuple(telemetry.blockers),
    )
