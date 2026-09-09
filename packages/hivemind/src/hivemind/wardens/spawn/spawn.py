"""Define spawn_sub_bee: start one new Worker on the Warden's own Cell, within its grant.

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
`WorkerRuntime` exists to record it itself).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Called by `hivemind.wardens.ticks.assign` once a `TaskAssign` has a matching
    `GrantIssued`, and by `hivemind.wardens.ticks.alarms` for a RETRY/REBIND respawn. Calls into
    `hivemind.cell` (NoopSnapshotter, CellIdentity, TaskNeeds), `hivemind.forage.slots` (ModelSlot),
    `hivemind.pheromone` (WorkerEvent), `hivemind.supervision.capping` (CappingGate, GateDeps),
    `hivemind.workers` (everything a Worker's role may use) and waggle only.

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

See Also:
    - .claude/codingrules.md section 8.12 for "nothing lands uncapped", the rule this function's
      `CappingGate` upholds.
    - .claude/codingrules.md section 11 for the owned-task rule the returned runtime task follows.
    - hivemind.wardens.spawn.sub_bee for SubBee, this function's return type.
    - hivemind.workers.context for WorkerContext, the bundle this function builds.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hivemind.cell import Cell, CellSession, NoopSnapshotter, RealCellLease, TaskNeeds
from hivemind.cell.source import CellIdentity
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import Tempo
from hivemind.guard import CapabilitySet
from hivemind.llm.slots import BoundModel
from hivemind.pheromone import WorkerEvent
from hivemind.supervision.capping import CappingGate, GateDeps
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.workers import (
    GrantSlice,
    RuntimeDeps,
    TelemetryTracker,
    WorkerContext,
    WorkerRuntime,
    WorkerState,
    worker_capabilities,
)
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import WardenId, WorkerId, new_event_id, new_worker_id
from waggle.messages.forage import GrantIssued
from waggle.messages.supervision import Answer, Question
from waggle.messages.task import TaskAssign
from waggle.transport.memory import MemoryTransport

__all__ = ["WardenCellContext", "spawn_sub_bee"]


@dataclass(frozen=True, slots=True)
class WardenCellContext:
    """The Warden-and-Cell half of `spawn_sub_bee`'s inputs, grouped to stay within codingrules 5.1.

    Attributes:
        warden_id: This Warden's own id; every sub-bee's own Hop addresses replies back here.
        deps: This Warden's collaborators.
        ceiling: This Warden's own CapabilitySet, the widest anything it spawns may ever hold.
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

    Returns:
        A SubBee in `WorkerState.SPAWNED`, its runtime task already started (owned by the caller)
        and its first TaskAssign already sent.
    """
    deps = ctx.deps
    worker_id = new_worker_id(deps.clock)
    needs = TaskNeeds(tempo=Tempo.from_wire(assignment.tempo))
    capabilities = worker_capabilities(ctx.ceiling, needs, ctx.lease.scratch_root)
    grant_slice = _grant_slice(grant)
    binding_key = binding_override or ModelSlot.from_wire(assignment.slot).manifest_key
    bound = deps.rebind(binding_key)

    worker_ctx = _build_worker_context(ctx, worker_id, bound, grant_slice, capabilities)
    warden_link, runtime_task = _start_runtime(ctx, worker_ctx, worker_id, assignment)

    await _record_spawned(deps, worker_id, assignment)
    assign_hop = Hop(sender=ctx.warden_id, recipient=worker_id, node_id=deps.identity.node_id)
    await warden_link.send(wrap(assignment, assign_hop, clock=deps.clock))

    return SubBee(
        worker_id=worker_id,
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=assignment.attempt,
        state=WorkerState.SPAWNED,
        binding=binding_key,
        last_handoff=assignment.resume_from,
        link=warden_link,
        runtime_task=runtime_task,
    )


def _build_capping_gate(ctx: WardenCellContext) -> CappingGate:
    """Build this sub-bee's own CappingGate (codingrules section 8.12: nothing lands uncapped)."""
    deps = ctx.deps
    return CappingGate(
        GateDeps(
            session=ctx.session,
            snapshotter=NoopSnapshotter(),
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
        )
    )


def _build_worker_context(
    ctx: WardenCellContext,
    worker_id: WorkerId,
    bound: BoundModel,
    grant_slice: GrantSlice,
    capabilities: CapabilitySet,
) -> WorkerContext:
    """Assemble the WorkerContext one sub-bee runs its role inside."""
    deps = ctx.deps
    return WorkerContext(
        worker_id=worker_id,
        cell=ctx.cell,
        session=ctx.session,
        bound=bound,
        grant=grant_slice,
        capabilities=capabilities,
        memory=deps.memory,
        trail=deps.trail,
        clock=deps.clock,
        asker=_NullAsker(),
        identity=deps.identity,
        telemetry=TelemetryTracker(context_window=bound.context_window),
        handoff_threshold=deps.handoff_threshold,
        capping=_build_capping_gate(ctx),
        lease=ctx.lease,
        call_gate=deps.call_gate,
    )


def _start_runtime(
    ctx: WardenCellContext,
    worker_ctx: WorkerContext,
    worker_id: WorkerId,
    assignment: TaskAssign,
) -> tuple[MemoryTransport, asyncio.Task[None]]:
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
    return warden_link, runtime_task


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


async def _record_spawned(deps: WardenDeps, worker_id: WorkerId, assignment: TaskAssign) -> None:
    """Write the one worker.* trail event a WorkerRuntime never writes for itself."""
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
