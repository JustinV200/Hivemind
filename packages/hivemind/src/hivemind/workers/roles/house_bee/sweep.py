"""Define run_sweep: one House Bee pass over hot state, Cell Wax and Bee Bread (roadmap step 4.3).

A sweep is routine maintenance, not an emergency response (codingrules section 8.9): it moves items
that no longer belong in hot state (the always-loaded, bounded slice of memory) into Bee Bread (the
warm tier), the same `hivemind.memory.demote` rule the ADR fixes; expires every WRITTEN Cell Wax
note (a Queen-written caution about one Cell) whose own `expires_at` has passed
(`hivemind.memory.cell_wax.expire_wax`, roadmap step 4.2a's own named wax-expiry hook); then folds
Bee Bread entries older than the sweep's own window into one new summary per closed task, through
`hivemind.memory.compact` (never a previous summary -- docs/adr/0022's "one level" rule, enforced
by `compact` itself). A fourth phase, ripening Bee Bread (and cleared/expired Cell Wax) into Honey
(the cold tier), is a named no-op hook until phase 7's Honey Store exists. `run_sweep` is
deliberately decoupled from the Worker protocol
(`hivemind.workers.base.Worker`) and from `hivemind.workers.context.WorkerContext`: it takes
`SweepDeps`/`SweepWindow`, two plain bundles, so a future composition root (a Warden or the Queen,
on a timer driven by `hivemind.workers.roles.house_bee.schedule.SweepSchedule`) can call it directly
without going through a `TaskAssign`, while `hivemind.workers.roles.house_bee.role.HouseBee` -- the
Worker-protocol adapter for today's dispatcher, which only knows how to run assigned tasks -- builds
one `SweepDeps`/`SweepWindow` pair from its own `WorkerContext` and calls this function underneath.

`SweepDeps.sources`, when given, extends demotion beyond what a House Bee can see on its own
(`ctx.memory`'s Notes, the one hot-state row `hivemind.memory` itself stores): a real
`hivemind.memory.HotStateSources` implementation over Brood Chamber and the trail, wired by
whichever composition root has that access (`hivemind.workers.context.WorkerContext` carries none
directly, matching `hivemind.workers.roles.drone`'s own "no manifest reference" reasoning). Left
`None` (today's `HouseBee.run` adapter, this dispatch's own default), a sweep still safely demotes
every Note past the manifest's hot window: a Note carries no task or Alarm linkage
(`hivemind.memory.demote._linked_task` returns `None` for one), so the empty `active_tasks`/
`resolved_alarms` sets `HouseBee.run` supplies can never wrongly demote a task or Alarm it has no
way to check.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Called by
    `hivemind.workers.roles.house_bee.role.HouseBee.run` today, and by a future timer-driven
    supervisor directly (module docstring). Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.llm` (BoundModel, CallGate), `hivemind.memory` (BeeBread, BeeBreadEntry,
    BeeBreadEntryKind, HotStateSources, MemoryContext, Scorable, compact/CompactionRequest/
    CompactionDeps, demote, expire_wax, should_demote, MAX_REF_IDS), `hivemind.memory.cell_wax`
    (WaxState) and waggle only.

Key invariants:
    - Compaction never sees a `BeeBreadEntryKind.SUMMARY` entry as a source (filtered out before
      `compact` is ever called), and never a batch larger than `hivemind.memory.bee_bread.entry.
      MAX_REF_IDS` (chunked): both are also `compact`'s own refusals, enforced here first so a
      sweep never has to catch and recover from either.
    - `_expire_wax_past_deadline` only ever moves a note WRITTEN -> EXPIRED; a note without an
      `expires_at` (standing wax) is never touched by a sweep.
    - `_ripen_bee_bread_into_honey` always returns 0: phase 7's Honey Store does not exist yet, so
      `SweepOutcome.ripened` is honest about doing nothing rather than pretending to.
    - `run_sweep` never marks a task SUCCEEDED and never raises for "nothing to do": an empty sweep
      (nothing to demote, nothing old enough to compact) returns a `SweepOutcome` of all zeros.

See Also:
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency".
    - .claude/roadmap.md step 4.3 for this module's spec verbatim.
    - hivemind.memory.demote for should_demote/demote, this module's demotion-phase collaborators.
    - hivemind.memory.compact for compact, this module's compaction-phase collaborator.
    - hivemind.workers.roles.house_bee.role for HouseBee, the Worker-protocol adapter around this
      function.
    - hivemind.workers.roles.house_bee.schedule for SweepSchedule, the timer a future supervisor
      checks before calling this function.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from hivemind.cell import HoneyClearance
from hivemind.llm import BoundModel, CallGate
from hivemind.memory import (
    BeeBread,
    BeeBreadEntry,
    BeeBreadEntryKind,
    CompactionDeps,
    CompactionRequest,
    HotStateSources,
    MemoryContext,
    Scorable,
    compact,
    demote,
    expire_wax,
    should_demote,
)
from hivemind.memory.bee_bread import MAX_REF_IDS
from hivemind.memory.cell_wax import WaxState
from waggle.ids import AlarmId, TaskId

# Generous ceilings for one sweep's own Note/decision reads: a sweep runs often (the manifest's
# own sweep_interval_s default is an hour, DEFAULT_SWEEP_INTERVAL_S), so it never needs to read an
# unbounded backlog in one pass -- a slow week just takes a few more sweeps to catch up.
SWEEP_NOTE_LIMIT = 500
SWEEP_DECISION_LIMIT = 200
# The earliest possible UtcDatetime: BeeBread.between's own lower bound for "everything up to the
# compaction cutoff", never itself a real record's timestamp.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

__all__ = ["SweepDeps", "SweepOutcome", "SweepWindow", "run_sweep"]


@dataclass(frozen=True, slots=True)
class SweepDeps:
    """The collaborators one sweep call needs: memory access and the RIPENER binding.

    Attributes:
        memory: Where demotion and compaction write; also read directly for Notes (module
            docstring).
        bee_bread: The lookup surface a sweep reads old, closed-task entries from.
        bound: The `ModelSlot.RIPENER` binding `compact` calls on.
        gate: The seam that call passes through.
        sources: A broader `HotStateSources` view for demotion, when the caller has one (module
            docstring); `None` demotes Notes only.
    """

    memory: MemoryContext
    bee_bread: BeeBread
    bound: BoundModel
    gate: CallGate
    sources: HotStateSources | None = None


@dataclass(frozen=True, slots=True)
class SweepWindow:
    """Timing and task-closure facts one sweep call needs, injected so `run_sweep` stays testable.

    Attributes:
        now: The reference time both phases measure age against (a `FakeClock`-derived timestamp
            in tests).
        hot_window: How long an item may sit unlinked before demotion ages it out
            (`hivemind.manifest.schema.supervision.MemorySection.hot_window_s`); also the
            compaction cutoff (module docstring: entries older than this, for closed tasks).
        allowance: The sweeping principal's own clearance ceiling.
        active_tasks: Ids of tasks still open; empty when the caller has no task visibility (module
            docstring).
        resolved_alarms: Ids of Alarms already resolved; empty for the same reason.
    """

    now: datetime
    hot_window: timedelta
    allowance: HoneyClearance
    active_tasks: frozenset[TaskId] = field(default_factory=frozenset)
    resolved_alarms: frozenset[AlarmId] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """One sweep's counts: how much moved, how much was folded together, how much ripened.

    Attributes:
        demoted: Hot-state items archived into Bee Bread this sweep.
        compacted_entries: Bee Bread source entries folded into a summary this sweep.
        compacted_batches: New `SUMMARY` entries written this sweep (each at most `MAX_REF_IDS`
            sources).
        expired_wax: WRITTEN Cell Wax notes past their own `expires_at`, moved to EXPIRED this
            sweep (roadmap step 4.2a; the House Bee sweep's own named wax-expiry hook).
        ripened: Always 0 until phase 7's Honey Store exists (module docstring).
        spend_usd: What compaction's own model calls cost this sweep, summed.
    """

    demoted: int
    compacted_entries: int
    compacted_batches: int
    expired_wax: int = 0
    ripened: int = 0
    spend_usd: float = 0.0


async def run_sweep(deps: SweepDeps, window: SweepWindow) -> SweepOutcome:
    """Run one House Bee sweep: demote, expire Cell Wax, compact, then (a no-op today) ripen.

    Args:
        deps: The memory access and RIPENER binding this sweep writes and calls with.
        window: The timing and task-closure facts this sweep measures against.

    Returns:
        A SweepOutcome summarising what moved, what expired, what was folded together, and what
        ripened.
    """
    demoted = await _demote_candidates(deps, window)
    expired_wax = await _expire_wax_past_deadline(deps, window)
    compacted_entries, compacted_batches, spend_usd = await _compact_closed_tasks(deps, window)
    ripened = _ripen_bee_bread_into_honey(deps)
    return SweepOutcome(
        demoted=demoted,
        compacted_entries=compacted_entries,
        compacted_batches=compacted_batches,
        expired_wax=expired_wax,
        ripened=ripened,
        spend_usd=spend_usd,
    )


async def _demote_candidates(deps: SweepDeps, window: SweepWindow) -> int:
    """Demote every candidate past its own rule: Notes always, plus `deps.sources` when given."""
    notes = await deps.memory.store.list_notes(None, window.allowance, SWEEP_NOTE_LIMIT)
    count = await _demote_matching(notes, deps, window)
    if deps.sources is None:
        # No broader hot-state view wired in (module docstring): Notes are everything a House Bee
        # can safely see and demote on its own.
        return count
    count += await _demote_matching(await deps.sources.active_tasks(), deps, window)
    count += await _demote_matching(await deps.sources.open_alarms(), deps, window)
    count += await _demote_matching(await deps.sources.pending_questions(), deps, window)
    count += await _demote_matching(
        await deps.sources.recent_decisions(SWEEP_DECISION_LIMIT), deps, window
    )
    return count


async def _demote_matching(items: Sequence[Scorable], deps: SweepDeps, window: SweepWindow) -> int:
    """Demote every item in `items` that `should_demote` gives a reason for; return how many."""
    count = 0
    for item in items:
        reason = should_demote(
            item, window.now, window.active_tasks, window.resolved_alarms, window.hot_window
        )
        if reason is not None:
            await demote(item, deps.memory)
            count += 1
    return count


async def _expire_wax_past_deadline(deps: SweepDeps, window: SweepWindow) -> int:
    """Expire every WRITTEN Cell Wax note whose own `expires_at` has passed (roadmap step 4.2a).

    The House Bee sweep's own named wax-expiry hook: `hivemind.memory.cell_wax.expire_wax` moves
    each one WRITTEN -> EXPIRED and records its `memory.wax_expired` event; an expired note stops
    being a `hivemind.memory.hot_state.summaries.HotStateSources.wax` candidate at once, since that
    query only ever asks for the WRITTEN state (docs/adr/0022 exit criterion: "an expired note
    leaves hot state on the next sweep"). `cell_id=None` scans every Cell in one pass, matching
    `list_wax`'s own contract for the sweep's use case (module docstring's "Store" bullet).

    Cleared and expired wax "hands to ripening once phase 7 lands" (roadmap step 4.2a): that hand-
    off is `_ripen_bee_bread_into_honey`'s own named no-op today, not repeated here.
    """
    written = await deps.memory.store.list_wax(
        None, frozenset({WaxState.WRITTEN}), window.allowance
    )
    count = 0
    for wax in written:
        if wax.expires_at is not None and wax.expires_at <= window.now:
            await expire_wax(wax, deps.memory)
            count += 1
    return count


async def _compact_closed_tasks(deps: SweepDeps, window: SweepWindow) -> tuple[int, int, float]:
    """Compact Bee Bread entries older than `window.hot_window`, for tasks not in `active_tasks`.

    Returns:
        `(entries_compacted, batches_written, spend_usd)`.
    """
    cutoff = window.now - window.hot_window
    candidates = await deps.bee_bread.between(_EPOCH, cutoff, window.allowance)
    closed = [
        entry
        for entry in candidates
        if entry.task_id is not None
        and entry.task_id not in window.active_tasks
        # A summary is never itself a source (docs/adr/0022's "one level"); compact() also
        # refuses one, but filtering here means a sweep never has to catch that refusal.
        and entry.kind is not BeeBreadEntryKind.SUMMARY
    ]
    if not closed:
        return 0, 0, 0.0

    pins = await deps.memory.store.list_pins(window.allowance)
    entries_compacted = 0
    batches_written = 0
    spend_usd = 0.0
    for task_id, group in _group_by_task(closed).items():
        for batch in _chunked(group, MAX_REF_IDS):
            request = CompactionRequest(
                sources=tuple(batch), pins=pins, clearance=window.allowance, task_id=task_id
            )
            compaction_deps = CompactionDeps(bound=deps.bound, gate=deps.gate, ctx=deps.memory)
            result = await compact(request, compaction_deps)
            entries_compacted += len(batch)
            batches_written += 1
            spend_usd += result.cost_usd
    return entries_compacted, batches_written, spend_usd


def _group_by_task(entries: list[BeeBreadEntry]) -> dict[TaskId, list[BeeBreadEntry]]:
    """Group `entries` by their own `task_id`, preserving first-seen task order.

    `entries` is pre-filtered to entries with a task_id (the caller's own list comprehension
    above); an entry with none is skipped rather than trusted, so this stays correct even if a
    future caller forgets to filter first.
    """
    grouped: dict[TaskId, list[BeeBreadEntry]] = {}
    for entry in entries:
        if entry.task_id is None:
            continue  # Defensive: the caller already filters these out.
        grouped.setdefault(entry.task_id, []).append(entry)
    return grouped


def _chunked(items: Iterable[BeeBreadEntry], size: int) -> Iterable[list[BeeBreadEntry]]:
    """Yield `items` in order, in chunks of at most `size`."""
    chunk: list[BeeBreadEntry] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _ripen_bee_bread_into_honey(deps: SweepDeps) -> int:
    """Ripen Bee Bread into Honey; a named no-op until phase 7's Honey Store exists.

    TODO(house_bee): wire this to hivemind.honey_store's ripening pipeline once phase 7 lands
    (roadmap step 4.3: "...and ripens Bee Bread into Honey once phase 7 lands"). Takes `deps`
    already so that future implementation's signature does not have to change.
    """
    del deps  # Unused until phase 7; named to keep this function's future signature stable.
    return 0
