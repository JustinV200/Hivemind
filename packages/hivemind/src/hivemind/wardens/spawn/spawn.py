"""Define spawn_sub_bee and stop_sub_bee: start and stop one Worker on the Warden's own Cell.

Roadmap step 3.19: "start a sub-bee on the Warden's Cell within the grant; attenuate capabilities;
choose where the runtime process runs." `spawn_sub_bee` is that whole operation: it slices the
Warden's own `CapabilitySet` ceiling down to this one Worker's (`hivemind.workers.capabilities.
worker_capabilities`), carves a `GrantSlice` from the Warden's current `GrantIssued`, resolves the
assignment's own `slot` to a live `BoundModel` through `deps.rebind` (the same seam a later REBIND
reuses, so "the sub-bee's starting binding" and "the sub-bee's rebound binding" are one code path),
builds this sub-bee's own `CappingGate` (codingrules section 8.12: nothing lands uncapped) and
`WorkerContext`, opens a fresh in-process `waggle.transport.memory.MemoryTransport` pair for it, and
starts its `hivemind.workers.runtime.WorkerRuntime` as a tracked, owned `asyncio.Task` -- never a
dropped `asyncio.create_task` handle (codingrules section 11) -- before sending the sub-bee its own
first `TaskAssign` over the Warden's own end of the pair. It writes the one `worker.*` trail event
the runtime itself never does: `worker.spawned` (`hivemind.workers.runtime.reporter.Reporter`
writes every other `worker.*` kind; this is the one transition that happens before a
`WorkerRuntime` exists to record it itself). Roadmap step 10.3 (ADR-0031): the slice is read off
the assignment by `hivemind.wardens.spawn.attenuate` (the task's network scopes and its goal's set
now travel on it, Waggle 1.6), and before anything is started the binding passes the Guard's
`slot_binding` point (`hivemind.wardens.spawn.binding.authorize_binding`); a refused binding
raises `BindingRefusedError` with nothing started. The sub-bee's own `WorkerContext` carries the
Warden's `Enforcer`, which its tools call at `tool_invocation` and `session_outside_scratch`.
`stop_sub_bee` is the one place that runtime is ever
torn down: cooperative first, cancel-and-reap only as a bounded fallback, so `spawn_sub_bee`'s own
owned task is never left cancelled-but-unawaited by whichever caller retires it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. `spawn_sub_bee` is called by `hivemind.wardens.ticks.assign` once a `TaskAssign`
    has a matching `GrantIssued`, and by `hivemind.wardens.ticks.alarms` for a RETRY/REBIND respawn;
    `stop_sub_bee` is called by `hivemind.wardens.warden.Warden.stop` (this dispatch's own
    shutdown-hygiene fix); `hivemind.wardens.ticks.alarms.retire_sub_bee` has the same
    cancel-without-reaping shape on its own respawn path and would want this same helper, but that
    module sat outside this dispatch's own file list. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.common.tasks` (reap),
    `hivemind.forage.slots` (ModelSlot), `hivemind.guard` (the binding point's principals and
    context), `hivemind.pheromone` (WorkerEvent), `hivemind.wardens.errors`
    (BindingRefusedError), `hivemind.wardens.spawn.attenuate` and `.binding`,
    `hivemind.supervision.capping` (CappingGate, GateDeps), `hivemind.workers` (everything a
    Worker's role may use) and waggle only.

Key invariants:
    - Every sub-bee shares the Warden's own single `CellSession` (opened once, at `Warden.start()`)
      rather than a fresh session per spawn: a deliberate simplification (flagged in this
      dispatch's report), because a `hivemind.cell.fake.FakeSession` holds its own in-memory
      files dict per instance, and a
      second, independent session for the same lease would make a sub-bee's write invisible to the
      Warden's own acceptance check on a different session object -- `CellSession`'s own contract
      ("implementations must be safe to call exec on more than once concurrently") is exactly what
      makes sharing one session across the Warden and every sub-bee on its Cell safe.
    - The runtime task this function starts is returned to its caller, which owns it (tracks it,
      awaits or cancels it): never a dropped `asyncio.create_task` handle (codingrules section 11).
      A plain tracked task, not a literal `asyncio.TaskGroup`, mirrors
      `hivemind.workers.runtime.attempt.AttemptManager`'s own `_role_task` pattern, since a
      `TaskGroup` entered once at `Warden.start()` and exited at `Warden.stop()` would need every
      `create_task` in between to run inside the exact asyncio task that opened it -- a constraint
      this Warden's own tick loop cannot promise across an indefinite lifetime.
    - `worker.spawned` is recorded here, and nowhere else (`hivemind.workers.runtime` never writes
      it): the one `worker.*` trail event this package owns.
    - Nothing is started, and no id minted, for a binding the `slot_binding` point refuses.
    - `stop_sub_bee` never returns with `runtime_task` still pending: a cooperative stop that lands
      within `grace_s` is reaped as a clean finish, one that does not is cancelled and reaped
      instead (codingrules section 11's "never left cancelled-but-unawaited").

See Also:
    - .claude/codingrules.md section 8.12 for "nothing lands uncapped", the rule this function's
      `CappingGate` upholds.
    - .claude/codingrules.md section 11 for the owned-task rule the returned runtime task follows,
      and the cooperative-stop-then-reap shape `stop_sub_bee` follows.
    - hivemind.wardens.spawn.sub_bee for SubBee, this function's return type.
    - hivemind.workers.context for WorkerContext, the bundle this function builds.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import AccessLevel, Cell, CellSession, RealCellLease
from hivemind.cell.source import CellIdentity
from hivemind.common.tasks import reap
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import Tempo
from hivemind.guard import CapabilitySet, PolicyContext, PrincipalRef, warden_principal
from hivemind.llm.ladders.gate import CallGate
from hivemind.llm.slots import BoundModel
from hivemind.pheromone import WorkerEvent
from hivemind.supervision.capping import GateDeps
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.spawn.attenuate import sub_bee_capabilities
from hivemind.wardens.spawn.audited_gate import AuditingCappingGate, AuditWiring
from hivemind.wardens.spawn.binding import BindingCheck, authorize_binding
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.workers import (
    GrantSlice,
    RuntimeDeps,
    TelemetryTracker,
    WorkerContext,
    WorkerRuntime,
    WorkerState,
)
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import EventId, WardenId, WorkerId, new_event_id, new_worker_id
from waggle.messages.forage import GrantIssued
from waggle.messages.supervision import Answer, Question
from waggle.messages.task import TaskAssign
from waggle.transport.memory import MemoryTransport

# stop_sub_bee's own default grace period: generous enough for a role's own in-flight tool call to
# notice the runtime's stop flag, checkpoint or finish, and for the CHECKPOINTED/TaskResult wire
# round trip that follows -- past this, the sub-bee is presumed stuck and is cancelled instead.
_DEFAULT_STOP_GRACE_S = 2.0

__all__ = ["WardenCellContext", "binding_check", "spawn_sub_bee", "stop_sub_bee"]


@dataclass(frozen=True, slots=True)
class WardenCellContext:
    """The Warden-and-Cell half of `spawn_sub_bee`'s inputs, grouped to stay within codingrules 5.1.

    Attributes:
        warden_id: This Warden's own id; every sub-bee's own Hop addresses replies back here.
        deps: This Warden's collaborators.
        ceiling: This Warden's own CapabilitySet (`hivemind.guard.warden_set`), the widest
            anything it spawns may ever hold.
        cell: The Cell this Warden owns.
        lease: This Warden's OPEN lease on `cell`.
        session: This Warden's own open CellSession on `cell`, shared with every sub-bee it
            spawns (see the module docstring's "Key invariants" for why it is shared, not fresh).
    """

    warden_id: WardenId
    deps: WardenDeps
    ceiling: CapabilitySet
    cell: Cell
    lease: RealCellLease
    session: CellSession


@dataclass(frozen=True, slots=True)
class _SubBeeGrant:
    """Bundles one sub-bee's GrantSlice and its grant-attributed CallGate (codingrules 5.1).

    Keeps `_build_worker_context` within the 5-parameter limit: `slice` and `call_gate` are
    always built together, from the same `GrantIssued` and `deps.lane_for_grant` call, in
    `spawn_sub_bee`.
    """

    slice: GrantSlice
    call_gate: CallGate


@dataclass(frozen=True, slots=True)
class _SpawnedBeeFacts:
    """Bundles a sub-bee's own id, model binding and CapabilitySet (codingrules 5.1).

    Keeps `_build_worker_context` within the parameter limit once its own `capping_gate` argument
    (roadmap step 5.0c: built ahead of time in `spawn_sub_bee`, now that it also needs
    `assignment.leaves`) is added alongside `ctx` and `sub_bee_grant`.
    """

    worker_id: WorkerId
    bound: BoundModel
    capabilities: CapabilitySet


@dataclass(frozen=True, slots=True)
class _SpawnPlan:
    """What `_plan_spawn` decided a sub-bee runs with, once its binding passed (codingrules 5.1).

    Keeps `_start_sub_bee` within the parameter limit: the binding key the Guard allowed, the
    sub-bee's own capability slice and the write roots its lease is widened to.
    """

    binding_key: str
    capabilities: CapabilitySet
    write_roots: tuple[Path, ...]


class _NullAsker:
    """A placeholder QuestionChannel; `WorkerRuntime.__init__` always replaces it with its own.

    `hivemind.workers.context`'s own module docstring: "`WorkerRuntime.__init__` replaces
    `ctx.asker` with its own transport-backed `QuestionChannel`". Nothing should ever call this
    one for real; it raises loudly rather than hanging if that invariant is ever broken.
    """

    async def ask(self, question: Question) -> Answer:
        """Raise: a real WorkerRuntime always replaces this placeholder before running a role."""
        raise RuntimeError(
            "WorkerContext._NullAsker.ask was called: WorkerRuntime should have replaced this "
            f"placeholder before question {question.question_id} could be asked."
        )


async def spawn_sub_bee(
    ctx: WardenCellContext,
    assignment: TaskAssign,
    grant: GrantIssued,
    binding_override: str | None = None,
    orderer: PrincipalRef | None = None,
) -> SubBee:
    """Start one new sub-bee on `ctx.cell`, within `grant`, running `assignment`.

    Args:
        ctx: This Warden and the Cell it owns.
        assignment: The TaskAssign to run; `assignment.resume_from`, when set, is threaded through
            to the role as its starting Handoff reference via the sub-bee's own TaskAssign.
        grant: The GrantIssued this sub-bee's budgets and allowed bindings are carved from.
        binding_override: A `[llm.slots]` manifest key to resolve instead of the one
            `assignment.slot` names; set by a REBIND respawn, whose target binding (a named
            binding such as `"local_worker"`) need not be any `ModelSlot`'s own manifest key.
        orderer: Who ordered this binding, for the `slot_binding` point: the Queen for her own
            `Intervene(REBIND)`; None (the default) is this Warden itself.

    Returns:
        A SubBee in `WorkerState.SPAWNED`, its runtime task already started (owned by the caller)
        and its first TaskAssign already sent.

    Raises:
        BindingRefusedError: The `slot_binding` point refused the binding (roadmap step 10.3);
            `guard.denied` is already on the trail and nothing was started.
    """
    plan = await _plan_spawn(ctx, assignment, grant, binding_override, orderer)
    return await _start_sub_bee(ctx, assignment, grant, plan)


async def _plan_spawn(
    ctx: WardenCellContext,
    assignment: TaskAssign,
    grant: GrantIssued,
    binding_override: str | None,
    orderer: PrincipalRef | None,
) -> _SpawnPlan:
    """Compute the sub-bee's slice and binding key, and pass the `slot_binding` point for it.

    Raises:
        BindingRefusedError: The Guard refused the binding; nothing has been started yet.
    """
    capabilities, write_roots = sub_bee_capabilities(
        ctx.deps, ctx.ceiling, ctx.lease.scratch_root, assignment
    )
    binding_key = binding_override or ModelSlot.from_wire(assignment.slot).manifest_key
    check = binding_check(ctx, assignment, grant, binding_key, capabilities)
    if orderer is not None:
        check = dataclasses.replace(check, orderer=orderer)
    decision = await authorize_binding(ctx.deps, check)
    if not decision.allowed:
        raise BindingRefusedError(assignment.task_id, binding_key, decision.reason)
    return _SpawnPlan(binding_key=binding_key, capabilities=capabilities, write_roots=write_roots)


async def _start_sub_bee(
    ctx: WardenCellContext, assignment: TaskAssign, grant: GrantIssued, plan: _SpawnPlan
) -> SubBee:
    """Bind, wire and start the planned sub-bee, send it its TaskAssign, and return its row."""
    deps = ctx.deps
    worker_id = new_worker_id(deps.clock)
    bound = deps.rebind(plan.binding_key)
    sub_bee_grant = _build_sub_bee_grant(deps, grant, assignment)
    facts = _SpawnedBeeFacts(worker_id=worker_id, bound=bound, capabilities=plan.capabilities)

    _widen_lease_reachability(ctx, plan.write_roots)
    capping_gate = _build_capping_gate(ctx, assignment)
    worker_ctx = _build_worker_context(ctx, facts, sub_bee_grant, capping_gate)
    warden_link, runtime, runtime_task = _start_runtime(ctx, worker_ctx, worker_id, assignment)

    spawned_event_id = await _record_spawned(deps, worker_id, assignment)
    assign_hop = Hop(sender=ctx.warden_id, recipient=worker_id, node_id=deps.identity.node_id)
    await warden_link.send(wrap(assignment, assign_hop, clock=deps.clock))

    return SubBee(
        worker_id=worker_id,
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=assignment.attempt,
        state=WorkerState.SPAWNED,
        binding=plan.binding_key,
        last_handoff=assignment.resume_from,
        link=warden_link,
        runtime=runtime,
        runtime_task=runtime_task,
        capabilities=plan.capabilities,
        spawned_event_id=spawned_event_id,
    )


def binding_check(
    ctx: WardenCellContext,
    assignment: TaskAssign,
    grant: GrantIssued,
    binding_key: str,
    capabilities: CapabilitySet,
) -> BindingCheck:
    """Build the `slot_binding` check for binding a sub-bee of `assignment` to `binding_key`.

    Args:
        ctx: This Warden and the Cell it owns; the Cell's tier and access level are the context.
        assignment: The sub-bee's TaskAssign; its own slot is what an unknown key is refused as.
        grant: The grant the binding must stay inside.
        binding_key: The `[llm.slots]` key being bound.
        capabilities: The sub-bee's own set.

    Returns:
        A BindingCheck ordered by this Warden; a Queen-ordered rebind replaces `orderer`.
    """
    return BindingCheck(
        orderer=warden_principal(ctx.warden_id),
        capabilities=capabilities,
        grant=grant,
        binding_key=binding_key,
        slot=ModelSlot.from_wire(assignment.slot),
        # Roadmap step 10.3b: the task is bound to its Cell's tier; a rebind is checked against it.
        context=PolicyContext(
            comb_shield=ctx.cell.comb_shield,
            access_level=ctx.cell.access_level,
            bound_tier=ctx.cell.comb_shield,
        ),
    )


async def stop_sub_bee(
    sub_bee: SubBee, clock: Clock, *, grace_s: float = _DEFAULT_STOP_GRACE_S
) -> None:
    """Stop `sub_bee`'s own runtime cooperatively, falling back to cancel if it overruns.

    Sets `runtime`'s own stop flag (`waggle.loop.TickLoop.stop`) so its next tick notices, drains
    its mailbox and returns on its own -- the same clean path `WorkerRuntime._tick` already takes
    for any other stop -- then races `runtime_task` against a `grace_s` deadline on `clock`, never
    a real timer. `reap` (codingrules section 11) is what actually discards `runtime_task` either
    way: idempotent on a task that already finished on its own, and a genuine cancel-then-await for
    one that has not, so this never returns with `runtime_task` left pending.

    Args:
        sub_bee: The sub-bee whose runtime to stop.
        clock: Bounds the cooperative wait; the injected Clock, so a test drives it with a
            FakeClock instead of a real timer.
        grace_s: Seconds to give the runtime to stop on its own before cancelling it instead.
    """
    sub_bee.runtime.stop()
    deadline = asyncio.ensure_future(clock.sleep(grace_s))
    await asyncio.wait({sub_bee.runtime_task, deadline}, return_when=asyncio.FIRST_COMPLETED)
    await reap(deadline)
    await reap(sub_bee.runtime_task)


def _widen_lease_reachability(ctx: WardenCellContext, write_roots: tuple[Path, ...]) -> None:
    """Widen this Warden's lease so `write_roots` are reachable, under FULL access only.

    Roadmap step 5.0e: "make keep_root and the task's declared leaves reachable for a lease only
    in the way the access level already permits." A lease is opened once, at `Warden.start()`,
    before any `TaskAssign` exists, so `RealCellLease.allowed_paths` cannot be sized from a task's
    `leaves` or the manifest's `keep_root` up front; this call (once per spawned sub-bee, on the
    one lease every sub-bee on this Cell shares) is what closes that gap. Only ever widens under
    `AccessLevel.FULL`: at READ_ONLY or SCRATCH the leave policy's own hard rule (roadmap step
    5.0c) always DENYs a leaving regardless, so nothing there would ever need to reach outside
    scratch for this reason, and widening reachability past what a lower level's own capability
    ceiling grants would be exactly the escalation codingrules section 15 forbids.
    """
    if ctx.lease.access_level is not AccessLevel.FULL:
        return
    for root in write_roots:
        ctx.lease.note_allowed_path(root)


def _build_capping_gate(ctx: WardenCellContext, assignment: TaskAssign) -> AuditingCappingGate:
    """Build this sub-bee's own CappingGate (codingrules section 8.12: nothing lands uncapped).

    Roadmap step 4.10: an `AuditingCappingGate`, not a plain `CappingGate`, so a terminal proposal
    at a tier the table marks ungated in real time is still sampled for after-the-fact judge
    review, using this Warden's own `WardenDeps.judge_reviewer`/`.judge_rubrics`/`.audit_sampler`/
    `.findings_sink`/`.audit_rates`. `snapshotter` is this Warden's own `WardenDeps.snapshotter`
    (roadmap step 5.10's own follow-up gap): `NoopSnapshotter()` by default, or a
    `hivemind.wardens.snapshot_relay.RelaySnapshotter` for a Virtual Cell's own Warden. Roadmap
    step 5.0c: it also carries this Warden's own leave-policy fields, plus `assignment.leaves` --
    this one sub-bee's own declared Leavings, never widened beyond what its TaskAssign carries.
    """
    deps = ctx.deps
    return AuditingCappingGate(
        GateDeps(
            session=ctx.session,
            snapshotter=deps.snapshotter,
            cell=ctx.cell,
            tiers=deps.tiers,
            trail=deps.trail,
            identity=CellIdentity(
                hive_id=deps.identity.hive_id,
                node_id=deps.identity.node_id,
                actor=deps.identity.actor,
            ),
            clock=deps.clock,
            checks=deps.checks,
            leave_policy=deps.leave_policy,
            declared_leaves=assignment.leaves,
            keep_root=deps.keep_root,
            leave_home=deps.leave_home,
            disk_reserve_mb=deps.disk_reserve_mb,
        ),
        AuditWiring(
            reviewer=deps.judge_reviewer,
            rubrics=dict(deps.judge_rubrics),
            sampler=deps.audit_sampler,
            sink=deps.findings_sink,
            rates=deps.audit_rates,
        ),
    )


def _build_sub_bee_grant(
    deps: WardenDeps, grant: GrantIssued, assignment: TaskAssign
) -> _SubBeeGrant:
    """Build one sub-bee's own GrantSlice and grant-attributed CallGate (roadmap step 4.8).

    One lane per grant, so every `llm.call` this sub-bee makes carries its own grant and goal id
    (`hivemind.wardens.deps.WardenDeps.lane_for_grant`'s own docstring), ordered in the Fanner's
    queues by the assignment's own tempo rather than a default one; `deps.call_gate`, this
    Warden's own unattributed lane, is untouched, used only for its own awake episodes.
    """
    tempo = Tempo.from_wire(assignment.tempo)
    return _SubBeeGrant(
        slice=_grant_slice(grant),
        call_gate=deps.lane_for_grant(grant.grant_id, assignment.goal_id, tempo),
    )


def _build_worker_context(
    ctx: WardenCellContext,
    facts: _SpawnedBeeFacts,
    sub_bee_grant: _SubBeeGrant,
    capping_gate: AuditingCappingGate,
) -> WorkerContext:
    """Assemble the WorkerContext one sub-bee runs its role inside."""
    deps = ctx.deps
    return WorkerContext(
        worker_id=facts.worker_id,
        cell=ctx.cell,
        session=ctx.session,
        bound=facts.bound,
        grant=sub_bee_grant.slice,
        capabilities=facts.capabilities,
        memory=deps.memory,
        trail=deps.trail,
        clock=deps.clock,
        asker=_NullAsker(),
        identity=deps.identity,
        telemetry=TelemetryTracker(context_window=facts.bound.context_window),
        handoff_threshold=deps.handoff_threshold,
        capping=capping_gate,
        lease=ctx.lease,
        call_gate=sub_bee_grant.call_gate,
        enforcer=deps.enforcer,
        scanner=deps.scanner,  # Roadmap 10.6b: every tool result is scanned on this node's key.
    )


def _start_runtime(
    ctx: WardenCellContext,
    worker_ctx: WorkerContext,
    worker_id: WorkerId,
    assignment: TaskAssign,
) -> tuple[MemoryTransport, WorkerRuntime, asyncio.Task[None]]:
    """Open this sub-bee's transport pair and start its WorkerRuntime as an owned, tracked task."""
    deps = ctx.deps
    warden_link, worker_transport = MemoryTransport.pair(Codec(), Codec())
    runtime_deps = RuntimeDeps(
        transport=worker_transport,
        hop=Hop(sender=worker_id, recipient=ctx.warden_id, node_id=deps.identity.node_id),
        heartbeat_interval_s=deps.worker_heartbeat_interval_s,
        clock=deps.clock,
    )
    worker = deps.worker_factory(assignment.role)
    runtime = WorkerRuntime(worker_ctx, worker, runtime_deps)
    runtime_task: asyncio.Task[None] = asyncio.ensure_future(runtime.run())
    return warden_link, runtime, runtime_task


def _grant_slice(grant: GrantIssued) -> GrantSlice:
    """Carve one sub-bee's GrantSlice from the Warden's own current grant.

    v0 gives one spawned sub-bee the grant's whole remaining budget and its allowed bindings, by
    `hivemind.forage.slots.ModelSlot` wire name (a grant's `AllowedBinding` carries no `[llm.slots]`
    manifest key of its own to attenuate to instead); dividing a grant across several concurrent
    sub-bees is a later roadmap phase's local-pool allocator.
    """
    return GrantSlice(
        grant_id=grant.grant_id,
        spend_budget_usd=max(0.0, grant.spend_budget - grant.spent),
        token_budget=max(0, grant.token_budget - grant.tokens_spent),
        allowed_bindings=tuple(binding.slot for binding in grant.allowed),
    )


async def _record_spawned(deps: WardenDeps, worker_id: WorkerId, assignment: TaskAssign) -> EventId:
    """Write the one worker.* trail event a WorkerRuntime never writes for itself; return its id."""
    event = WorkerEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="worker.spawned",
        subject_id=worker_id,
        payload={"task_id": assignment.task_id, "role": assignment.role.value},
    )
    await deps.trail.record(event)
    return event.id
