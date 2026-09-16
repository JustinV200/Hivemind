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

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Called by `hivemind.queen.cluster.tick.run_cluster_tick` and by
    `hivemind.queen.cluster.triggers`'s cost-cap check. Calls into `hivemind.brood_chamber`
    (TaskStatus), `hivemind.cell` (HoneyClearance), `hivemind.memory` (BeeBreadEntryKind),
    `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.dispatcher` (resume_paused),
    `hivemind.queen.state` (ClusterState), `hivemind.queen.trail` (record_event),
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

from hivemind.brood_chamber import TaskStatus
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

__all__ = ["ClusterOutcome", "ResumeOutcome", "cluster", "resume"]


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


async def _pause_affected(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    affected: tuple[tuple[TaskId, WardenId], ...],
    reason: str,
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
        await _send_handoff_intervene(deps, link, task_id, reason)
        await _send_task_pause(deps, link, task_id, reason)
        # The trail must read checkpoint -> PAUSED (docs/adr/0024's own exit criterion): give the
        # affected bee's own cooperative tool loop a bounded number of scheduling turns to notice
        # the HANDOFF and write its checkpoint before this moves the task to PAUSED, rather than
        # racing ahead of it. A bee that never checkpoints within the budget (stuck, or already
        # gone) is still paused regardless -- resume then falls back to no Handoff (docs/adr/0024:
        # "a bee whose Handoff cannot be read resumes from the task's last acceptance state").
        await _wait_for_checkpoint(deps, task_id)
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
        resume_from = await _latest_handoff(deps, task_id)
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


async def _wait_for_checkpoint(deps: QueenDeps, task_id: TaskId) -> None:
    """Yield the event loop until `task_id` has a Bee Bread Handoff entry, or the budget runs out.

    A bounded number of plain scheduling yields (`asyncio.sleep(0)`), never a real delay and
    never a call into `deps.clock` -- this is not a timed wait, just giving whatever already-
    scheduled coroutine is cooperatively checking `handoff_requested` (a Worker's own tool loop)
    the turns it needs to actually reach that check and write its checkpoint.
    """
    for _ in range(_CHECKPOINT_POLL_ATTEMPTS):
        if await _latest_handoff(deps, task_id) is not None:
            return
        await asyncio.sleep(0)


async def _latest_handoff(deps: QueenDeps, task_id: TaskId) -> HandoffRef | None:
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
    await link.transport.send(wrap(message, link.hop, clock=deps.clock))


async def _send_task_pause(deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str) -> None:
    """Send `TaskPause(task_id)` to `link`'s own Warden, for it to relay to the sub-bee."""
    message = TaskPause(task_id=task_id, reason=reason)
    await link.transport.send(wrap(message, link.hop, clock=deps.clock))


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)
