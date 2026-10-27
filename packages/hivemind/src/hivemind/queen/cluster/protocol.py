"""Define cluster and resume: pause and preserve a provider's bees, then resume them from Handoff.

docs/adr/0024-clustering-protocol.md's first decision, made real. `cluster` is triggered per
provider (`hivemind.queen.cluster.tick.run_cluster_tick`, by hand through `hive cluster`, or by
`hivemind.queen.cluster.triggers`'s cost-cap check): it finds every bee bound to that provider,
sends each its Warden an `Intervene(HANDOFF)` then a `TaskPause` (codingrules section 8.9's "one
mechanism, many names" -- Clustering reuses the exact checkpoint-and-Handoff-then-stop lever a
REBIND or TAKEOVER already pulls, so a paused bee's own Worker runtime needs no change of its own
to support this, see this dispatch's own report), moves each affected task to `PAUSED` in the
Brood Chamber, and records one `queen.clustered` naming every affected task. `resume` is the other
half: for each `PAUSED` task still bound to that provider it finds a Handoff (the newest Bee Bread
`HANDOFF` entry for that task, module docstring below explains why that is the cheapest honest
source), re-assigns it through `hivemind.queen.dispatcher.resume_paused` with `resume_from` set so
no work is redone, moves it back to `RUNNING`, and records one `queen.resumed`. Bees bound to
other providers are never touched by either call.

**How affected bees are found** (this dispatch's own documented choice, since the roadmap step
leaves it open): through `deps.ledger.live_grants()` -- the Queen's own live book of every shared
`ForageGrant` currently outstanding (`hivemind.queen.forage.ledger.book.ForageLedger`) -- filtered
to grants whose `task_id` is set and whose `allowed` bindings name a Forage map source
(`deps.map`) whose own `spec.provider` matches. This is the cheapest honest source available: a
grant's `allowed` bindings are exactly what its holding Warden may actually spend its sub-bees'
model calls against (`hivemind.forage.allocate.grant`'s own inputs), so a grant naming a source on
the down provider names a bee that really is bound to it, without the Queen ever having to ask a
Warden what it is doing right now (which `hivemind.wardens.deps.WardenDeps` and `SubBee` never
report up in the first place) or hold a session of her own (docs/adr/0019). The same filter finds
the same task set again for `resume`, since `cluster` never touches grants (module docstring's own
invariant): resume needs no separate bookkeeping of "who did I just pause".

**How a Handoff is found for resume** (this dispatch's own documented choice): `hivemind.memory.
bee_bread.index` has no "newest entry of a kind, by task" read, so this module calls the one read
that does exist, `MemoryStore.list_bee_bread_by_task` (oldest first), and takes the last `HANDOFF`-
kind entry -- the same tier `hivemind.memory.checkpoint.write_checkpoint`'s own `deposit_handoff_
ref` indexes every checkpoint into. A task with no such entry (the paused bee's own Handoff has
not landed, or Bee Bread has already demoted it past this reader's own lookup-by-id-and-task
window) resumes with `resume_from=None` instead of failing outright, matching docs/adr/0024's own
"a bee whose Handoff cannot be read resumes from the task's last acceptance state instead".
That entry is the Queen's own tier, and a Warden that is not hers (a Virtual Cell's, a colonized
device's) keeps its bees' Handoffs in a store of its own, so `resume` asks the recall of
`hivemind.queen.swarm_gate.recall` instead (`_newest_handoff`): it starts from that same entry,
prefers a newer HANDOFF Nectar the bee deposited, and refuses what a recall refuses everywhere (a
Night Veil Cell's, a tainted one, one written under a standing isolation, one above the task's
clearance). The pause's own check that a checkpoint has landed reads both places too
(`_checkpoint_mark`): a remote Warden's bee never writes in her Bee Bread, so waiting there alone
waited out its whole patience for a checkpoint that had long landed (and ended at once when she held
an older one), so it waits for her newest checkpoint or the bee's newest HANDOFF deposit to change.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Called by `hivemind.queen.cluster.tick.run_cluster_tick` and by
    `hivemind.queen.cluster.triggers`'s cost-cap check. Calls into `hivemind.brood_chamber`
    (TaskStatus), `hivemind.cell` (HoneyClearance), `hivemind.memory` (BeeBreadEntryKind),
    `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.dispatcher` (resume_paused),
    `hivemind.queen.state` (ClusterState), `hivemind.queen.swarm_gate.recall` (recall_handoff,
    imported where it is used), `hivemind.queen.trail` (record_event),
    `hivemind.supervision.intervention` (Handoff, to_wire) and waggle only.

Key invariants:
    - `cluster` never awaits a model: it calls `deps.chamber`, `deps.ledger`, `deps.map`,
      `deps.trail` and Warden transports only, never `deps.bound_for`/`deps.rebind`/`call_gate`
      (a test asserts no `LLMProvider.complete`/`.stream` call happens during `cluster`/`resume`).
    - `cluster` never touches a grant: leases and grants stay exactly as they were (module
      docstring; `hivemind.queen.forage.grants` is never called from here), so `resume` can find
      the same bees again through the same ledger read.
    - `cluster`/`resume` are each idempotent per provider: a provider already in `state.
      clustered_providers` (or not in it, for `resume`) still runs its own side effects the first
      time only, reported through `ClusterOutcome.already_clustered`/`ResumeOutcome.
      already_running`.
    - `resume` resumes each task from the newest Handoff the Queen can hold for it, a Handoff only
      the Honey Store has (a remote Warden's) included, under the refusals the recall applies.
    - `pause_on_warden` is `cluster`'s pause for one Warden's tasks, whatever the cause; it records
      no `queen.clustered` and sets no mode, since nothing about a provider changed.
    - A pause waits for a bee's Handoff in scheduling turns and never in real time, unless its
      caller gives `patience_s`: a move does, because the Warden it ends takes its bees with it
      and a bee inside a command writes its Handoff only when the command returns; Clustering
      gives none, so it never waits on a clock.
    - Every affected task id lands in exactly one of `ClusterOutcome.affected_task_ids`/
      `ResumeOutcome.resumed_task_ids`, and `queen.clustered`/`queen.resumed` is recorded exactly
      once per call, after every affected task's own wire messages and chamber transition, never
      per task.

See Also:
    - docs/adr/0024-clustering-protocol.md for the decision this module implements.
    - .claude/codingrules.md section 8.9 for "one mechanism, many names".
    - .claude/codingrules.md section 8.13 for the Clustering summary this module is triggered by.
    - hivemind.queen.cluster.tick for run_cluster_tick, this module's one production caller.
    - hivemind.queen.dispatcher for resume_paused, the dispatcher-path resume goes through so no
      work is redone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.cell import HoneyClearance
from hivemind.forage import ForageGrant, ForageMap, UnknownSourceError
from hivemind.memory import BeeBreadEntryKind
from hivemind.queen.state import ClusterState
from hivemind.queen.trail import record_event
from hivemind.supervision.intervention import Handoff as HandoffLever
from hivemind.supervision.intervention import to_wire
from waggle.envelope import wrap
from waggle.ids import TaskId, WardenId
from waggle.messages import HandoffRef
from waggle.messages.supervision import Intervene
from waggle.messages.task import TaskPause

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.queen.deps now imports this package (for the
    # QueenDeps fields roadmap step 4.9 adds), so a real (non-TYPE_CHECKING) import here would
    # cycle back through queen/cluster/__init__.py -> this module -> queen/deps.py, the same
    # trap hivemind.queen.forage.grants's own module docstring documents and works around.
    # hivemind.queen.dispatcher.resume_paused is imported locally, inside resume() itself, for
    # the same reason: it takes a QueenDeps too, and is a real (not type-only) call this module
    # needs at runtime.
    from hivemind.queen.deps import QueenDeps, WardenLink

_CHECKPOINT_POLL_ATTEMPTS = 2_000  # Scheduling yields, not a timed wait; see _wait_for_checkpoint.
_PATIENCE_STEP_S = 0.1  # How often a patient wait looks again for the Handoff, in real seconds.
# The pause between those looks. It is the real one, and a name of its own so that a test can stand
# in for the time a bee inside a command takes without spending any of it.
_pause = asyncio.sleep
# What the Queen can see of a task's Handoffs: her newest checkpoint's event id and the newest
# HANDOFF Nectar id its bee deposited (`_checkpoint_mark`); a pause waits for it to change.
type _Mark = tuple[str | None, str | None]

__all__ = [
    "ClusterOutcome",
    "ResumeOutcome",
    "cluster",
    "latest_handoff",
    "pause_on_warden",
    "resume",
]


@dataclass(frozen=True, slots=True)
class ClusterOutcome:
    """What one `cluster` call did.

    Attributes:
        provider: The provider this call clustered.
        affected_task_ids: Every task moved to PAUSED by this call; empty when `already_clustered`.
        already_clustered: True when `provider` was already in `state.clustered_providers`: this
            call's own side effects (module docstring's idempotence rule) did not run again.
    """

    provider: str
    affected_task_ids: tuple[TaskId, ...]
    already_clustered: bool


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """What one `resume` call did.

    Attributes:
        provider: The provider this call resumed.
        resumed_task_ids: Every task moved back to RUNNING by this call; empty when
            `already_running`.
        already_running: True when `provider` was not in `state.clustered_providers`: this call's
            own side effects did not run again.
    """

    provider: str
    resumed_task_ids: tuple[TaskId, ...]
    already_running: bool


async def cluster(
    provider: str,
    cause: str,
    deps: QueenDeps,
    state: ClusterState,
    wardens: Sequence[WardenLink],
) -> ClusterOutcome:
    """Pause and preserve every bee bound to `provider`, and record `queen.clustered` once.

    Pure-decision-then-effects (module docstring): `_affected_bees` decides who is affected from
    `deps.ledger`/`deps.map` alone; everything below it is the effect of that decision.

    Args:
        provider: The `[llm.providers.<name>]` key to cluster.
        cause: Why: `"provider_down"`, `"cost_cap"`, or `"operator"` (`hive cluster`); folded into
            every Intervene/TaskPause reason and the recorded event.
        deps: The Queen's collaborators.
        state: The Queen's own mode and clustered-provider set; mutated by this call.
        wardens: Every Warden currently attached; `wardens` rather than a `deps` field because a
            `WardenLink` lives on the running `Queen` instance, never on `QueenDeps` (docs/
            adr/0019: the Queen holds no session; a `WardenLink` is a wire address, not one, but
            `QueenDeps` still carries collaborators only -- see this dispatch's own report for the
            parameter-count deviation from the roadmap step's own signature sketch).

    Returns:
        A ClusterOutcome naming every task this call paused.
    """
    if provider in state.clustered_providers:
        return ClusterOutcome(provider=provider, affected_task_ids=(), already_clustered=True)

    affected = _affected_bees(provider, deps.ledger.live_grants(), deps.map)
    reason = f"Clustering: provider {provider!r} unavailable ({cause})."
    task_ids = await _pause_affected(deps, wardens, affected, reason)

    state.mark_clustered(provider)
    await record_event(
        deps,
        "queen.clustered",
        deps.identity.hive_id,
        provider=provider,
        cause=cause,
        task_ids=list(task_ids),
    )
    return ClusterOutcome(provider=provider, affected_task_ids=task_ids, already_clustered=False)


async def pause_on_warden(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    warden_id: WardenId,
    reason: str,
    patience_s: float = 0.0,
) -> tuple[TaskId, ...]:
    """Checkpoint-then-pause every RUNNING task placed on `warden_id`, as Clustering does.

    The same lever, reused for another cause (codingrules 8.9, "one mechanism, many names"): a
    Warden that is about to move onto its device (ADR-0052) has each of its bees write a Handoff
    and stop, and its tasks wait PAUSED to be resumed from those Handoffs on the Warden that takes
    over. Nothing of the Warden's grants is touched, so the same Warden id finds them again.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; the task's Warden must be among them to be sent
            the orders (a task whose Warden is not is skipped).
        warden_id: The Warden whose running tasks to pause.
        reason: Why, folded into every order and the chamber's own event.
        patience_s: How long, in real seconds and for each task, to wait for a bee that is inside
            a command to come back from it and write its Handoff, once the scheduling turns run
            out. A move gives some, because the Warden it ends takes its bees with it; Clustering
            gives none, because the provider it clusters on is down and its bees are stuck on it.

    Returns:
        The tasks actually paused, oldest first.
    """
    # By status, at the store's widest page: the default page is the oldest thousand tasks of any
    # status, and on a Hive that has finished more than that the running ones are not among them.
    tasks = await deps.chamber.list(
        TaskFilter(status=TaskStatus.RUNNING, limit=MAX_TASK_FILTER_LIMIT)
    )
    affected = tuple((task.id, warden_id) for task in tasks if task.warden_id == warden_id)
    return await _pause_affected(deps, wardens, affected, reason, patience_s)


async def _pause_affected(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    affected: tuple[tuple[TaskId, WardenId], ...],
    reason: str,
    patience_s: float = 0.0,
) -> tuple[TaskId, ...]:
    """Checkpoint-then-pause every `(task_id, warden_id)` pair; return the ones actually paused."""
    task_ids: list[TaskId] = []
    for task_id, warden_id in affected:
        link = _link_for(wardens, warden_id)
        if link is None:
            continue  # Its Warden is no longer attached; nothing to pause it through.
        task = await deps.chamber.get(task_id)
        if task.status is not TaskStatus.RUNNING:
            # Already terminal (or blocked/paused by another path) by the time this ran: its own
            # live grant just has not been revoked yet (module docstring: cluster never touches
            # grants); RUNNING -> PAUSED is the only legal edge (Appendix C's "Task" row), so
            # anything else is left exactly as it is rather than raising.
            continue
        # What she can see of the task's Handoffs before the order: the pause waits for that to
        # change, not merely for one to exist, since an older one is not the checkpoint it asks for.
        before = await _checkpoint_mark(deps, task)
        await _send_handoff_intervene(deps, link, task_id, reason)
        # The trail must read checkpoint -> PAUSED (docs/adr/0024's own exit criterion): give the
        # affected bee's own cooperative tool loop a bounded number of scheduling turns to notice
        # the HANDOFF and write its checkpoint before this moves the task to PAUSED, rather than
        # racing ahead of it. A bee that never checkpoints within the budget (stuck, or already
        # gone) is still paused regardless -- resume then falls back to no Handoff (docs/adr/0024:
        # "a bee whose Handoff cannot be read resumes from the task's last acceptance state").
        await _wait_for_checkpoint(deps, task, before, patience_s)
        # Only now the pause: a Worker the runtime has paused sits at its pause wait and cannot
        # hand off (PAUSED has no edge to HANDING_OFF), so a pause sent beside the HANDOFF kept a
        # real Drone, one that was in a model call when both arrived, from ever checkpointing.
        # Found by the phase 8 offline e2e (a Drone on a real socket); a scripted Worker that
        # checkpoints before the pause is handled is unaffected.
        await _send_task_pause(deps, link, task_id, reason)
        await deps.chamber.pause(task_id, reason)
        task_ids.append(task_id)
    return tuple(task_ids)


async def resume(
    provider: str, deps: QueenDeps, state: ClusterState, wardens: Sequence[WardenLink]
) -> ResumeOutcome:
    """Resume every PAUSED bee bound to `provider` from its Handoff, and record `queen.resumed`.

    Args:
        provider: The provider that recovered, or was named by `hive wake`.
        deps: The Queen's collaborators.
        state: The Queen's own mode and clustered-provider set; mutated by this call.
        wardens: Every Warden currently attached (see `cluster`'s own docstring for why this is a
            parameter rather than a `deps` field).

    Returns:
        A ResumeOutcome naming every task this call resumed.
    """
    # Local, not module-level: see this module's own TYPE_CHECKING block for why.
    from hivemind.queen.dispatcher import resume_paused

    if provider not in state.clustered_providers:
        return ResumeOutcome(provider=provider, resumed_task_ids=(), already_running=True)

    affected = _affected_bees(provider, deps.ledger.live_grants(), deps.map)
    task_ids: list[TaskId] = []
    for task_id, _warden_id in affected:
        task = await deps.chamber.get(task_id)
        if task.status is not TaskStatus.PAUSED:
            continue  # Cancelled, or already resumed by another path; nothing to redo here.
        resume_from = await _newest_handoff(deps, task)
        await resume_paused(deps, wardens, task_id, resume_from, "Clustering resumed.")
        task_ids.append(task_id)

    state.mark_resumed(provider)
    await record_event(
        deps, "queen.resumed", deps.identity.hive_id, provider=provider, task_ids=list(task_ids)
    )
    return ResumeOutcome(provider=provider, resumed_task_ids=tuple(task_ids), already_running=False)


def _affected_bees(
    provider: str, grants: Sequence[ForageGrant], forage_map: ForageMap
) -> tuple[tuple[TaskId, WardenId], ...]:
    """Return every `(task_id, warden_id)` whose live grant names a source on `provider`.

    Module docstring's own documented choice of "cheapest honest source"; a task with no grant
    (never dispatched) or a grant with no `task_id` (not yet used by this dispatch) is never
    affected.
    """
    affected: list[tuple[TaskId, WardenId]] = []
    for grant in grants:
        if grant.task_id is None:
            continue
        if _grant_uses_provider(grant, provider, forage_map):
            affected.append((grant.task_id, grant.holder))
    return tuple(affected)


def _grant_uses_provider(grant: ForageGrant, provider: str, forage_map: ForageMap) -> bool:
    """Return whether any of `grant`'s own allowed bindings names a source on `provider`."""
    for binding in grant.allowed:
        try:
            source = forage_map.get(binding.source_id)
        except UnknownSourceError:
            continue  # A binding naming a source the map no longer carries; never a match.
        if source.spec.provider == provider:
            return True
    return False


async def _newest_handoff(deps: QueenDeps, task: Task) -> HandoffRef | None:
    """Return the newest Handoff the Queen can hold for a paused `task`, or None.

    A Warden that is not the Queen's own keeps its bees' Handoffs in a store of its own, so her Bee
    Bread holds at best the one a move carried, and the newest is only the HANDOFF Nectar its bee
    deposited. The recall finds it under the refusals every recall applies (Night Veil, tainted,
    written under a standing isolation, above the task's clearance), and returns the one she holds
    when nothing newer may be used. A task never placed holds only what she has.
    """
    # Local, not module-level: the swarm gate imports this package (see the TYPE_CHECKING block).
    from hivemind.queen.swarm_gate.recall import recall_handoff

    if task.cell_id is None:
        return await latest_handoff(deps, task.id)
    return await recall_handoff(deps, task.cell_id, task)


async def _wait_for_checkpoint(
    deps: QueenDeps, task: Task, before: _Mark, patience_s: float = 0.0
) -> None:
    """Wait until `task` has a Handoff newer than `before`, or the budget (and patience) runs out.

    First a bounded number of plain scheduling yields (`asyncio.sleep(0)`), never a call into
    `deps.clock` -- this is not a timed wait, just giving whatever already-scheduled coroutine is
    cooperatively checking `handoff_requested` (a Worker's own tool loop) the turns it needs to
    actually reach that check and write its checkpoint. A bee that is inside a command (a
    `sleep` on a device, a build) reaches that check only when the command returns, which no
    number of turns hurries; where the caller has patience the wait goes on in real time, in
    short steps, up to `patience_s`, and ends the moment the Handoff lands.
    """
    for _ in range(_CHECKPOINT_POLL_ATTEMPTS):
        if await _checkpoint_mark(deps, task) != before:
            return
        await asyncio.sleep(0)
    waited = 0.0
    while waited < patience_s:
        await _pause(_PATIENCE_STEP_S)
        waited += _PATIENCE_STEP_S
        if await _checkpoint_mark(deps, task) != before:
            return


async def _checkpoint_mark(deps: QueenDeps, task: Task) -> _Mark:
    """Return the newest Handoff the Queen can see for `task`: hers, and its bee's deposit.

    A remote Warden (a colonized device's, a Virtual Cell's) writes its bees' checkpoints in a
    store of its own, never in her Bee Bread, so the one sign that reaches her of a checkpoint
    landing there is the HANDOFF Nectar its bee deposits (ADR-0053). Either changing means the
    bee has handed off; which one is used to resume is the recall's to decide, not this wait's.
    """
    held = await latest_handoff(deps, task.id)
    deposit = None
    if deps.honey is not None and task.cell_id is not None:
        newest = await deps.honey.store.handoff_nectars(task.id, task.cell_id, 1)
        deposit = newest[0].id if newest else None
    return (held.event_id if held is not None else None, deposit)


async def latest_handoff(deps: QueenDeps, task_id: TaskId) -> HandoffRef | None:
    """Return `task_id`'s newest Bee Bread HANDOFF entry as a HandoffRef, or None.

    See the module docstring's "How a Handoff is found for resume" for why this read, over this
    tier, is the cheapest honest source.
    """
    # C2 (the widest allowance): a Queen-side read of her own bees' bookkeeping, the same
    # allowance hivemind.queen.questions._find_answer_note already uses for the same reason.
    entries = await deps.memory.list_bee_bread_by_task(task_id, HoneyClearance.C2)
    handoffs = [entry for entry in entries if entry.kind is BeeBreadEntryKind.HANDOFF]
    if not handoffs:
        return None
    newest = handoffs[-1]  # list_bee_bread_by_task orders oldest first (its own contract).
    return HandoffRef(
        event_id=newest.ref_ids[0],
        written_at=newest.created_at,
        clearance=newest.clearance.to_wire(),
    )


async def _send_handoff_intervene(
    deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str
) -> None:
    """Send `Intervene(HANDOFF, task_id)` to `link`'s own Warden, for it to relay to the sub-bee.

    HANDOFF (not CHECKPOINT): codingrules section 8.9's "one mechanism, many names" -- the same
    checkpoint-then-stop lever a REBIND/TAKEOVER already pulls, so the paused bee's own Worker
    runtime moves cleanly from RUNNING to a checkpointed, terminal state with no change of its
    own; see this dispatch's own report for why CHECKPOINT (which restarts the same attempt in
    place rather than stopping it) is not used here.
    """
    lever = HandoffLever(reason=reason)
    action, slot = to_wire(lever)
    message = Intervene(
        action=action, subject=None, task_id=task_id, slot=slot, alarm_id=None, reason=reason
    )
    # `_pause_affected` moves the task to PAUSED regardless of whether this order is delivered
    # (it always did, even before this guard existed): an unreachable Warden's bee never sees the
    # HANDOFF, `_wait_for_checkpoint` finds no Handoff and gives up, and `resume` already
    # tolerates that (docs/adr/0024: "a bee whose Handoff cannot be read resumes from the task's
    # last acceptance state instead").
    await link.send(wrap(message, link.hop, clock=deps.clock))


async def _send_task_pause(deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str) -> None:
    """Send `TaskPause(task_id)` to `link`'s own Warden, for it to relay to the sub-bee."""
    message = TaskPause(task_id=task_id, reason=reason)
    # Same rule as _send_handoff_intervene just above: the chamber's own PAUSED transition does
    # not wait on this delivery either.
    await link.send(wrap(message, link.hop, clock=deps.clock))


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)
