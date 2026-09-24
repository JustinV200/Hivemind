"""Tests for hivemind.workers.roles.house_bee.honey: aged Bee Bread deposits, and its helpers.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/honey.py (codingrules section 3), split by
    feature (14.2): retired Cell Wax deposits are tested in test_honey_wax.py. Every deposit
    lands in a real SQLite Honey Store (`builders.house_bee.open_honey_access`); Bee Bread and the
    Handoffs live in the in-memory memory store every sweep test uses.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.honey for the module under test.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from builders.cells import make_cell
from builders.house_bee import (
    HoneyHarness,
    RecordedCells,
    memory_context,
    open_honey_access,
    store_bee_bread,
    sweep_deps,
    sweep_window,
    transcript_entry,
)
from builders.memory import make_bee_bread_entry, make_handoff

from hivemind.cell import CellKind, CombShieldLevel, HoneyClearance
from hivemind.honey_store import (
    HoneyAccess,
    HoneyStoreError,
    IntakeResult,
    Nectar,
    NectarIntake,
    NectarOrigin,
    NectarSubmission,
    handoff_source_key,
)
from hivemind.manifest import HoneyRipeningSection, HoneyStoreSection
from hivemind.memory import BeeBreadEntryKind, write_checkpoint
from hivemind.workers.roles.house_bee import (
    BEE_BREAD_WATERMARK,
    GatheredOn,
    bee_or_none,
    deposit_aged_bee_bread,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import (
    CellId,
    EventId,
    TaskId,
    new_event_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages.honey import NectarKind

_AGED_S = 2 * 86_400.0  # Two days: past the manifest's default bee_bread_after_s (a day).


async def _pending(harness: HoneyHarness) -> tuple[Nectar, ...]:
    """Every Nectar row still waiting to ripen, oldest first."""
    return await harness.access.store.pending_nectar(100)


def _stand(clock: FakeClock) -> RecordedCells:
    """Records whose home is a Real (borrowed) Hive Stand Cell, and nothing else."""
    return RecordedCells.of(home=make_cell(kind=CellKind.REAL, clock=clock))


class _WedgedIntake(NectarIntake):
    """A real intake whose `fail_on`-th submission raises a store failure (a wedged store)."""

    def __init__(self, access: HoneyAccess, clock: Clock, fail_on: int) -> None:
        default_label = HoneyClearance.from_wire(access.clearance.default_label)
        super().__init__(access.store, access.identity, clock, HoneyStoreSection(), default_label)
        self._fail_on = fail_on
        self._calls = 0

    async def submit(self, submission: NectarSubmission) -> IntakeResult:
        self._calls += 1
        if self._calls == self._fail_on:
            raise HoneyStoreError("The Honey Store is wedged for this test.")
        return await super().submit(submission)


# ──────────────────────────────────────────────────────────────────────────────
# deposit_aged_bee_bread
# ──────────────────────────────────────────────────────────────────────────────


async def test_deposit_aged_bee_bread_does_nothing_without_a_honey_store() -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    await store_bee_bread(ctx, transcript_entry(clock, None, "an old transcript"))
    clock.advance(_AGED_S)

    assert (
        await deposit_aged_bee_bread(sweep_deps(ctx, None, RecordedCells()), sweep_window(clock))
        == 0
    )


async def test_deposit_aged_bee_bread_deposits_an_aged_transcript_at_its_tasks_cell(
    tmp_path: Path,
) -> None:
    # A MEADOW Virtual Cell the task ran on: not borrowed, so the declared C1 label stands.
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task_id = new_task_id(clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    deps = sweep_deps(ctx, harness.access, RecordedCells.of(cell, placements={task_id: cell.id}))
    entry = transcript_entry(clock, task_id, "The build needs libfoo-dev installed first.")
    await store_bee_bread(ctx, entry)
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(deps, sweep_window(clock))

    assert deposited == 1
    (nectar,) = await _pending(harness)
    assert nectar.origin is NectarOrigin.BEE_BREAD
    assert nectar.kind is NectarKind.TRANSCRIPT
    assert nectar.media_type == "text/plain"
    assert (nectar.task_id, nectar.cell_id) == (task_id, cell.id)
    assert nectar.observed_at == entry.created_at
    assert nectar.clearance is HoneyClearance.C1
    assert nectar.scope == f"task:{task_id}"
    assert await harness.access.store.has_source(f"bee_bread:{entry.id}")
    content = await harness.access.store.nectar_content(nectar.id)
    assert content == b"The build needs libfoo-dev installed first."


async def test_deposit_aged_bee_bread_never_deposits_an_entry_younger_than_the_cutoff(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(ctx, transcript_entry(clock, None, "a fresh transcript"))
    clock.advance(_AGED_S / 4)  # Half a day: still warm.

    deposited = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, _stand(clock)), sweep_window(clock)
    )

    assert deposited == 0
    assert await _pending(harness) == ()
    assert await harness.access.store.get_watermark(BEE_BREAD_WATERMARK) is None


async def test_deposit_aged_bee_bread_advances_the_watermark_so_a_second_sweep_adds_nothing(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    deps = sweep_deps(ctx, harness.access, _stand(clock))
    await store_bee_bread(ctx, transcript_entry(clock, None, "first"))
    clock.advance(1.0)
    await store_bee_bread(ctx, transcript_entry(clock, None, "second"))
    clock.advance(_AGED_S)

    first = await deposit_aged_bee_bread(deps, sweep_window(clock))
    second = await deposit_aged_bee_bread(deps, sweep_window(clock))

    assert (first, second) == (2, 0)
    assert await harness.access.store.get_watermark(BEE_BREAD_WATERMARK) is not None
    assert len(await _pending(harness)) == 2


async def test_deposit_aged_bee_bread_resolves_a_handoff_under_its_shared_key(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task_id = new_task_id(clock)
    worker_id = new_worker_id(clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    deps = sweep_deps(ctx, harness.access, RecordedCells.of(cell, placements={task_id: cell.id}))
    ref = await write_checkpoint(make_handoff(task_id=task_id, written_by=worker_id), task_id, ctx)
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(deps, sweep_window(clock))

    assert deposited == 1
    (nectar,) = await _pending(harness)
    assert nectar.kind is NectarKind.HANDOFF
    assert nectar.media_type == "application/json"
    assert (nectar.bee, nectar.event_id, nectar.task_id) == (worker_id, ref.event_id, task_id)
    assert await harness.access.store.has_source(handoff_source_key(ref.event_id))


async def test_deposit_aged_bee_bread_merges_a_handoff_its_worker_already_deposited(
    tmp_path: Path,
) -> None:
    # The Worker's own Waggle deposit of the Handoff reached intake first (roadmap 7.8); the
    # House Bee's copy carries the same handoff:<event id> key, so it merges into that one row.
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task_id = new_task_id(clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    deps = sweep_deps(ctx, harness.access, RecordedCells.of(cell, placements={task_id: cell.id}))
    ref = await write_checkpoint(make_handoff(task_id=task_id), task_id, ctx)
    await harness.access.intake.submit(_waggle_handoff(ref.event_id, task_id, cell.id, clock))
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(deps, sweep_window(clock))

    assert deposited == 0  # Merged, not a new row.
    assert len(await _pending(harness)) == 1


def _waggle_handoff(
    event_id: EventId, task_id: TaskId, cell_id: CellId, clock: FakeClock
) -> NectarSubmission:
    """The BEE-origin submission a Worker's own Waggle deposit of a Handoff becomes."""
    return NectarSubmission(
        kind=NectarKind.HANDOFF,
        origin=NectarOrigin.BEE,
        media_type="application/json",
        title="Handoff",
        content=b'{"goal": "the same Handoff, as the Worker sent it"}',
        task_id=task_id,
        cell_id=cell_id,
        observed_at=clock.now(),
        declared=HoneyClearance.C1,
        from_borrowed_cell=False,
        tier=CombShieldLevel.MEADOW,
        source_key=handoff_source_key(event_id),
        event_id=event_id,
    )


async def test_deposit_aged_bee_bread_passes_over_index_only_entries_but_moves_past_them(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(
        ctx, make_bee_bread_entry(clock=clock, kind=BeeBreadEntryKind.TASK_HISTORY)
    )
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, _stand(clock)), sweep_window(clock)
    )

    assert deposited == 0
    assert await _pending(harness) == ()
    assert await harness.access.store.get_watermark(BEE_BREAD_WATERMARK) is not None


async def test_deposit_aged_bee_bread_passes_over_a_handoff_that_no_longer_exists(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    orphan = make_bee_bread_entry(
        clock=clock,
        kind=BeeBreadEntryKind.HANDOFF,
        ref_ids=(new_event_id(clock),),
        task_id=None,
        text=None,
    )
    await store_bee_bread(ctx, orphan)
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, _stand(clock)), sweep_window(clock)
    )

    assert deposited == 0
    assert await harness.access.store.get_watermark(BEE_BREAD_WATERMARK) is not None


async def test_deposit_aged_bee_bread_caps_one_sweep_and_resumes_from_the_watermark(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    ripening = HoneyRipeningSection(max_bee_bread_per_sweep=2)
    harness = await open_honey_access(tmp_path, clock, ripening=ripening)
    deps = sweep_deps(ctx, harness.access, _stand(clock))
    # Three entries one second apart, all aged by the time the sweeps run.
    for text in ("one", "two", "three"):
        await store_bee_bread(ctx, transcript_entry(clock, None, text))
        clock.advance(1.0)
    clock.advance(_AGED_S)

    first = await deposit_aged_bee_bread(deps, sweep_window(clock))
    second = await deposit_aged_bee_bread(deps, sweep_window(clock))

    assert (first, second) == (2, 1)


async def test_deposit_aged_bee_bread_skips_a_night_veil_cells_material(tmp_path: Path) -> None:
    clock = FakeClock()
    task_id = new_task_id(clock)
    veiled = GatheredOn(
        cell_id=make_cell(clock=clock).id,
        from_borrowed_cell=False,
        tier=CombShieldLevel.NIGHT_VEIL,
    )
    records = RecordedCells(cells={veiled.cell_id: veiled}, task_cells={task_id: veiled.cell_id})
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(ctx, transcript_entry(clock, task_id, "what happened behind the veil"))
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, records), sweep_window(clock)
    )

    assert deposited == 0
    assert await _pending(harness) == ()


async def test_deposit_aged_bee_bread_skips_task_less_material_with_no_home_cell(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(
        ctx, transcript_entry(clock, None, "nobody can say where this was written")
    )
    clock.advance(_AGED_S)

    deposited = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, RecordedCells()), sweep_window(clock)
    )

    assert deposited == 0
    assert await _pending(harness) == ()


async def test_deposit_aged_bee_bread_labels_a_borrowed_home_cells_material_c2(
    tmp_path: Path,
) -> None:
    # The Hive Stand is a Real (borrowed) Cell: everything written there gets the C2 floor.
    clock = FakeClock()
    stand = make_cell(kind=CellKind.REAL, clock=clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(ctx, transcript_entry(clock, None, "a Queen-level transcript"))
    clock.advance(_AGED_S)

    await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, RecordedCells.of(home=stand)), sweep_window(clock)
    )

    (nectar,) = await _pending(harness)
    assert nectar.cell_id == stand.id
    assert nectar.clearance is HoneyClearance.C2
    assert nectar.scope == "hive"


async def test_deposit_aged_bee_bread_treats_an_unrecorded_cell_as_borrowed(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    task_id = new_task_id(clock)
    gone = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    records = RecordedCells(task_cells={task_id: gone.id})  # Placed there; no live record now.
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await store_bee_bread(ctx, transcript_entry(clock, task_id, "from a Cell since destroyed"))
    clock.advance(_AGED_S)

    await deposit_aged_bee_bread(sweep_deps(ctx, harness.access, records), sweep_window(clock))

    (nectar,) = await _pending(harness)
    assert nectar.cell_id == gone.id
    assert nectar.clearance is HoneyClearance.C2


async def test_deposit_aged_bee_bread_stops_at_a_store_failure_and_keeps_its_place(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    wedged = dataclasses.replace(
        harness.access, intake=_WedgedIntake(harness.access, clock, fail_on=2)
    )
    records = _stand(clock)
    for text in ("one", "two", "three"):
        await store_bee_bread(ctx, transcript_entry(clock, None, text))
        clock.advance(1.0)
    clock.advance(_AGED_S)

    first = await deposit_aged_bee_bread(sweep_deps(ctx, wedged, records), sweep_window(clock))
    resumed = await deposit_aged_bee_bread(
        sweep_deps(ctx, harness.access, records), sweep_window(clock)
    )

    assert first == 1  # "one" landed; "two" hit the failure and stopped the batch there.
    assert resumed == 2  # The next sweep resumed at "two", not after "three".


# ──────────────────────────────────────────────────────────────────────────────
# GatheredOn and bee_or_none
# ──────────────────────────────────────────────────────────────────────────────


def test_gathered_on_reads_a_live_cells_own_record() -> None:
    cell = make_cell(kind=CellKind.REAL)

    gathered = GatheredOn.of(cell)

    assert gathered == GatheredOn(cell.id, from_borrowed_cell=True, tier=cell.comb_shield)


def test_gathered_on_treats_an_unrecorded_cell_as_borrowed_at_meadow() -> None:
    cell_id = make_cell().id

    gathered = GatheredOn.unrecorded(cell_id)

    assert gathered == GatheredOn(cell_id, from_borrowed_cell=True, tier=CombShieldLevel.MEADOW)


def test_bee_or_none_names_a_worker_or_a_warden_and_nothing_else() -> None:
    clock = FakeClock()
    worker, warden = new_worker_id(clock), new_warden_id(clock)

    assert bee_or_none(worker) == worker
    assert bee_or_none(warden) == warden
    assert bee_or_none("worker_not-a-ulid") is None
    assert bee_or_none("drone") is None
    assert bee_or_none(None) is None
