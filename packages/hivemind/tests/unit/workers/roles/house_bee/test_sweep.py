"""Tests for hivemind.workers.roles.house_bee.sweep: run_sweep's demotion and compaction phases.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/sweep.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.sweep for the module under test.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from builders.cells import make_cell
from builders.house_bee import RecordedCells, open_honey_access
from builders.llm import make_bound, text_response
from builders.memory import (
    make_bee_bread_entry,
    make_note,
    make_task_summary,
    make_wax_proposal_input,
)

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.llm import DirectCallGate, FakeLLMProvider
from hivemind.memory import (
    AlarmSummary,
    BeeBread,
    CellWaxSummary,
    DecisionSummary,
    MemoryContext,
    MemoryIdentity,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
)
from hivemind.memory.bee_bread.entry import BeeBreadEntryKind
from hivemind.memory.cell_wax import WaxState, clear_wax, propose_wax, write_wax
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, PheromoneTrail
from hivemind.workers.roles.house_bee import HouseBeeHoney
from hivemind.workers.roles.house_bee.sweep import SweepDeps, SweepOutcome, SweepWindow, run_sweep
from waggle.clock import FakeClock
from waggle.ids import CellId, new_event_id, new_hive_id, new_node_id, new_task_id
from waggle.messages.cell.wax import WaxDecision

_HOT_WINDOW = timedelta(hours=4)  # Matches DEFAULT_HOT_WINDOW_S's scale.
_SCRIPTED_REPLY = {"summary": "A closed task's history.", "key_facts": [], "open_threads": []}


def _ctx_and_trail(clock: FakeClock) -> tuple[MemoryContext, PheromoneTrail]:
    """Build a MemoryContext plus the same trail its store records events on."""
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryMemoryStore(trail)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=store, identity=identity, clock=clock), trail


def _note_event(clock: FakeClock, subject_id: str) -> MemoryEvent:
    """Build a well-formed `memory.note` event for `add_note`."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="memory.note",
        subject_id=subject_id,
        payload={},
    )


def _bee_bread_event(clock: FakeClock, subject_id: str) -> MemoryEvent:
    """Build a well-formed `memory.bee_bread_deposited` event for `add_bee_bread_entry`."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="memory.bee_bread_deposited",
        subject_id=subject_id,
        payload={},
    )


def _unscripted_deps(ctx: MemoryContext, **overrides: object) -> SweepDeps:
    """Build a SweepDeps whose RIPENER binding is never expected to be called."""
    fields: dict[str, object] = {
        "memory": ctx,
        "bee_bread": BeeBread(ctx.store),
        "bound": make_bound(slot=ModelSlot.RIPENER),
        "gate": DirectCallGate(),
    }
    fields.update(overrides)
    return SweepDeps(**fields)  # type: ignore[arg-type]


class _FakeSources:
    """A minimal HotStateSources: one caller-given tuple of active tasks, nothing else."""

    def __init__(self, tasks: tuple[TaskSummary, ...] = ()) -> None:
        self._tasks = tasks

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        return self._tasks

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        return ()

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        return ()

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        return ()

    async def pins(self) -> tuple[Pin, ...]:
        return ()

    async def notes(self) -> tuple[Note, ...]:
        return ()

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        return ()


async def test_run_sweep_demotes_an_aged_out_note_into_bee_bread() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    note = make_note(clock=clock)
    await ctx.store.add_note(note, _note_event(clock, note.id))
    now = note.written_at + _HOT_WINDOW + timedelta(seconds=1)
    deps = _unscripted_deps(ctx)
    window = SweepWindow(now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.demoted == 1
    remaining = await ctx.store.list_notes(None, HoneyClearance.C2, 10)
    assert note.id not in {n.id for n in remaining}
    archived = await deps.bee_bread.between(note.written_at, now, HoneyClearance.C2)
    assert any(e.kind == BeeBreadEntryKind.NOTE and e.ref_ids == (note.id,) for e in archived)


async def test_run_sweep_expires_a_written_wax_note_past_its_deadline() -> None:
    """Roadmap step 4.2a exit criterion: "an expired note leaves hot state on the next sweep"."""
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    expires_at = clock.now() + timedelta(hours=1)
    proposed = await propose_wax(
        make_wax_proposal_input(clock=clock, expires_at=expires_at), 4_000, ctx
    )
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the Warden's own cap.", ctx)
    deps = _unscripted_deps(ctx)
    now = expires_at + timedelta(seconds=1)  # Past the deadline.
    window = SweepWindow(now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.expired_wax == 1
    # Gone from hot state: a WRITTEN-only query no longer finds it.
    still_written = await ctx.store.list_wax(
        written.cell_id, frozenset({WaxState.WRITTEN}), HoneyClearance.C2
    )
    assert written.id not in {w.id for w in still_written}
    reloaded = await ctx.store.get_wax(written.id)
    assert reloaded.state is WaxState.EXPIRED


async def test_run_sweep_never_expires_a_wax_note_not_yet_due() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    expires_at = clock.now() + timedelta(hours=1)
    proposed = await propose_wax(
        make_wax_proposal_input(clock=clock, expires_at=expires_at), 4_000, ctx
    )
    await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the Warden's own cap.", ctx)
    deps = _unscripted_deps(ctx)
    now = expires_at - timedelta(seconds=1)  # Still within its own deadline.
    window = SweepWindow(now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.expired_wax == 0


async def test_run_sweep_never_expires_standing_wax_with_no_deadline() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    proposed = await propose_wax(make_wax_proposal_input(clock=clock, expires_at=None), 4_000, ctx)
    await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the Warden's own cap.", ctx)
    deps = _unscripted_deps(ctx)
    # Far in the future: only expires_at (unset here) could ever expire standing wax.
    window = SweepWindow(
        now=clock.now() + timedelta(days=365), hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2
    )

    outcome = await run_sweep(deps, window)

    assert outcome.expired_wax == 0


async def test_run_sweep_keeps_a_note_still_within_the_hot_window() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    note = make_note(clock=clock)
    await ctx.store.add_note(note, _note_event(clock, note.id))
    now = note.written_at + _HOT_WINDOW - timedelta(seconds=1)
    deps = _unscripted_deps(ctx)
    window = SweepWindow(now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.demoted == 0


async def test_run_sweep_compacts_bee_bread_entries_for_a_closed_task() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    task_id = new_task_id(clock)
    old_entry = make_bee_bread_entry(clock=clock, task_id=task_id, clearance=HoneyClearance.C1)
    await ctx.store.add_bee_bread_entry(old_entry, _bee_bread_event(clock, old_entry.id))
    now = old_entry.created_at + _HOT_WINDOW + timedelta(seconds=1)
    provider = FakeLLMProvider()
    provider.script(text_response(json.dumps(_SCRIPTED_REPLY)))
    deps = _unscripted_deps(ctx, bound=make_bound(provider=provider, slot=ModelSlot.RIPENER))
    window = SweepWindow(
        now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C1, active_tasks=frozenset()
    )

    outcome = await run_sweep(deps, window)

    assert outcome.compacted_entries == 1
    assert outcome.compacted_batches == 1
    summaries = await ctx.store.list_bee_bread_by_task(task_id, HoneyClearance.C1)
    assert any(entry.kind == BeeBreadEntryKind.SUMMARY for entry in summaries)


async def test_run_sweep_never_compacts_entries_for_a_still_active_task() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    task_id = new_task_id(clock)
    old_entry = make_bee_bread_entry(clock=clock, task_id=task_id, clearance=HoneyClearance.C1)
    await ctx.store.add_bee_bread_entry(old_entry, _bee_bread_event(clock, old_entry.id))
    now = old_entry.created_at + _HOT_WINDOW + timedelta(seconds=1)
    deps = _unscripted_deps(ctx)  # No scripted reply: compact() must never be called.
    window = SweepWindow(
        now=now,
        hot_window=_HOT_WINDOW,
        allowance=HoneyClearance.C1,
        active_tasks=frozenset({task_id}),
    )

    outcome = await run_sweep(deps, window)

    assert outcome.compacted_entries == 0
    assert outcome.compacted_batches == 0


async def test_run_sweep_never_compacts_a_summary_entry_again() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    task_id = new_task_id(clock)
    summary_entry = make_bee_bread_entry(
        clock=clock,
        task_id=task_id,
        clearance=HoneyClearance.C1,
        kind=BeeBreadEntryKind.SUMMARY,
        text=None,
        payload="a prior summary",
    )
    await ctx.store.add_bee_bread_entry(summary_entry, _bee_bread_event(clock, summary_entry.id))
    now = summary_entry.created_at + _HOT_WINDOW + timedelta(seconds=1)
    deps = _unscripted_deps(ctx)  # No scripted reply: compact() must never be called.
    window = SweepWindow(now=now, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C1)

    outcome = await run_sweep(deps, window)

    assert outcome.compacted_entries == 0


async def test_run_sweep_returns_an_all_zero_outcome_when_there_is_nothing_to_do() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    deps = _unscripted_deps(ctx)
    window = SweepWindow(now=clock.now(), hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome == SweepOutcome(
        demoted=0, compacted_entries=0, compacted_batches=0, ripened=0, spend_usd=0.0
    )


async def test_run_sweep_demotes_from_injected_sources_when_given() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    task = make_task_summary(clock=clock)
    deps = _unscripted_deps(ctx, sources=_FakeSources((task,)))
    # `task` is not in window.active_tasks: should_demote reads that as TASK_CLOSED, even though
    # the injected `sources.active_tasks()` above returned it as a candidate (module docstring:
    # a real implementation's own candidate pool is broader than window.active_tasks's own truth).
    window = SweepWindow(now=task.updated_at, hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.demoted == 1


async def test_run_sweep_deposits_aged_bee_bread_and_retired_wax_into_honey(
    tmp_path: Path,
) -> None:
    """Roadmap steps 7.6 and 7.9a: the fourth phase deposits both, and `ripened` counts them."""
    # One aged transcript and one cleared wax note; the sweep runs two days later, so the
    # transcript is past the manifest's default bee_bread_after_s (a day).
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    stand = make_cell(clock=clock)
    harness = await open_honey_access(tmp_path, clock)
    entry = make_bee_bread_entry(
        clock=clock, kind=BeeBreadEntryKind.TRANSCRIPT, task_id=None, text=None, payload="done"
    )
    await ctx.store.add_bee_bread_entry(entry, _bee_bread_event(clock, entry.id))
    proposed = await propose_wax(make_wax_proposal_input(clock=clock, cell_id=stand.id), 4_000, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)
    await clear_wax(written, "Fixed.", ctx)
    honey = HouseBeeHoney(access=harness.access, cells=RecordedCells.of(stand, home=stand))
    deps = _unscripted_deps(ctx, honey=honey)
    clock.advance(timedelta(days=2).total_seconds())
    window = SweepWindow(now=clock.now(), hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(deps, window)

    assert outcome.ripened == 2
    assert len(await harness.access.store.pending_nectar(10)) == 2


async def test_run_sweep_deposits_nothing_with_no_honey_store() -> None:
    clock = FakeClock()
    ctx, _trail = _ctx_and_trail(clock)
    entry = make_bee_bread_entry(
        clock=clock, kind=BeeBreadEntryKind.TRANSCRIPT, task_id=None, text=None, payload="done"
    )
    await ctx.store.add_bee_bread_entry(entry, _bee_bread_event(clock, entry.id))
    clock.advance(timedelta(days=2).total_seconds())
    window = SweepWindow(now=clock.now(), hot_window=_HOT_WINDOW, allowance=HoneyClearance.C2)

    outcome = await run_sweep(_unscripted_deps(ctx), window)

    assert outcome.ripened == 0
