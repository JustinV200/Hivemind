"""Tests for hivemind.workers.roles.house_bee.loop: HouseBeeRipening beside the Queen.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/loop.py (codingrules section 3). Every pass runs
    against a real SQLite Honey Store (`builders.house_bee.open_honey_access`), driven by a
    FakeClock so the pause between passes is advanced by hand, never slept through.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.loop for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from pathlib import Path

from builders.cells import make_cell
from builders.honey import make_nectar_submission, make_ripener_deps
from builders.house_bee import HoneyHarness, open_honey_access

from hivemind.cell import CellKind, HoneyClearance
from hivemind.honey_store import (
    Honey,
    HoneyStoreError,
    NectarOrigin,
    PassOutcome,
    ReadFilter,
    Ripener,
    honey_event,
)
from hivemind.manifest import HoneyRipeningSection
from hivemind.workers.roles.house_bee import HouseBeeRipening
from waggle.clock import FakeClock

_INTERVAL_S = 30.0  # The manifest's default ripening interval.
_TURNS = 2_000  # Real-time polls of `_POLL_S`: up to 2 s for a pass (a few thread hops) to land.
_POLL_S = 0.001  # One millisecond between polls: long enough for a store's thread hop to finish.
_STEP_S = 1.0  # A fake-clock step: thirty of them cover one default ripening interval.


async def _propose(harness: HoneyHarness, scope: str, text: str, clock: FakeClock) -> str:
    """Queue one operator note, exactly as the browser's propose path does (roadmap 7.10)."""
    access = harness.access
    event = honey_event(access.identity, clock, "honey.note_proposed", access.identity.hive_id)
    return await access.store.add_proposal(scope, "An operator note", text, event)


async def _rows(harness: HoneyHarness, scope: str) -> tuple[Honey, ...]:
    """Every live Honey row in `scope`, read as the Queen reads (every scope, up to C2)."""
    everything = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    return await harness.access.store.list_honey(everything, scope_prefix=scope, limit=50, offset=0)


async def _settle(condition: Callable[[], bool]) -> None:
    """Yield to the loop until `condition()` holds, or give up after a bounded number of turns."""
    for _ in range(_TURNS):
        if condition():
            return
        await asyncio.sleep(_POLL_S)
    raise AssertionError("The loop never reached the expected state.")


async def _advance_until(clock: FakeClock, condition: Callable[[], bool], step_s: float) -> None:
    """Advance `clock` in small steps, yielding between them, until `condition()` holds.

    One big advance can land before the loop has even registered its pause (a pass still in
    flight), and a FakeClock only wakes sleepers registered before it moves; small steps keep
    moving time forward until the pause, whenever it starts, is over.
    """
    for _ in range(_TURNS):
        if condition():
            return
        clock.advance(step_s)
        await asyncio.sleep(_POLL_S)
    raise AssertionError("The loop never reached the expected state.")


class _CountingRipener(Ripener):
    """A real Ripener that counts its passes and can be told to fail the next one."""

    def __init__(self, harness: HoneyHarness, clock: FakeClock) -> None:
        super().__init__(make_ripener_deps(harness.access.store, clock))
        self.passes = 0
        self.fail_next = False

    async def run_pass(self) -> PassOutcome:
        self.passes += 1
        if self.fail_next:
            self.fail_next = False
            raise HoneyStoreError("The Honey Store is wedged for this pass.")
        return await super().run_pass()


async def test_run_pass_drains_a_proposal_into_human_nectar_and_ripens_it(tmp_path: Path) -> None:
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    stand = make_cell(kind=CellKind.REAL, clock=clock)
    proposal_id = await _propose(harness, "hive", "Deploys go through the staging gate.", clock)
    loop = HouseBeeRipening(harness.access, stand.id, clock)

    report = await loop.run_pass()

    assert report.drained == 1
    assert report.outcome.ripen.ripened == 1
    assert await harness.access.store.pending_proposals(10) == ()
    row = (await _rows(harness, "hive"))[0]
    nectar = await harness.access.store.get_nectar(row.nectar_id)
    assert nectar.origin is NectarOrigin.HUMAN
    assert nectar.cell_id == stand.id
    assert nectar.clearance is HoneyClearance.C2  # A human's note is C2 by construction.
    assert proposal_id  # The store minted an id for the queued note.


async def test_run_pass_lands_a_proposal_in_the_folder_it_was_proposed_in(tmp_path: Path) -> None:
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    device = make_cell(kind=CellKind.REAL, clock=clock)
    await _propose(harness, f"cell:{device.id}", "Its fan is loud under load.", clock)
    loop = HouseBeeRipening(harness.access, device.id, clock)

    await loop.run_pass()

    rows = await _rows(harness, f"cell:{device.id}")
    assert rows
    assert {row.scope for row in rows} == {f"cell:{device.id}"}


async def test_run_pass_passes_over_a_refused_proposal_and_drains_the_rest(tmp_path: Path) -> None:
    # A folder that names no valid scope is refused by intake every time; it must not block the
    # queue behind it.
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    await _propose(harness, "cell:bad/folder", "never lands", clock)
    await _propose(harness, "hive", "lands", clock)
    loop = HouseBeeRipening(harness.access, make_cell(clock=clock).id, clock)

    report = await loop.run_pass()

    assert report.drained == 1
    (still_queued,) = await harness.access.store.pending_proposals(10)
    assert still_queued.scope == "cell:bad/folder"


async def test_run_pass_ripens_nectar_deposited_by_anyone(tmp_path: Path) -> None:
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    await harness.access.intake.submit(make_nectar_submission(clock=clock))
    loop = HouseBeeRipening(harness.access, make_cell(clock=clock).id, clock)

    report = await loop.run_pass()

    assert report.drained == 0
    assert report.outcome.ripen.ripened == 1
    assert report.outcome.ripen.failed == 0
    assert await harness.access.store.pending_nectar(10) == ()


async def test_the_loop_passes_every_interval_and_stops_at_once_mid_pause(tmp_path: Path) -> None:
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    ripener = _CountingRipener(harness, clock)
    access = dataclasses.replace(harness.access, ripener=ripener)
    loop = HouseBeeRipening(access, make_cell(clock=clock).id, clock)

    task = asyncio.create_task(loop.run())
    await _settle(lambda: ripener.passes == 1)  # The first pass runs at once.
    await _advance_until(clock, lambda: ripener.passes == 2, _STEP_S)  # One interval later.
    loop.stop()  # Mid-pause: stop() ends the pause without waiting out the interval.
    await asyncio.wait_for(task, timeout=1.0)

    assert ripener.passes == 2
    assert task.exception() is None


async def test_the_loop_backs_off_after_a_failed_pass_and_carries_on(tmp_path: Path) -> None:
    clock = FakeClock()
    ripening = HoneyRipeningSection(interval_s=_INTERVAL_S)
    harness = await open_honey_access(tmp_path, clock, ripening=ripening)
    ripener = _CountingRipener(harness, clock)
    ripener.fail_next = True
    access = dataclasses.replace(harness.access, ripener=ripener)
    loop = HouseBeeRipening(access, make_cell(clock=clock).id, clock)

    task = asyncio.create_task(loop.run())
    await _settle(lambda: ripener.passes == 1)  # The failing pass.
    # Past waggle.loop's first backoff (half a second), well before a whole interval.
    await _advance_until(clock, lambda: ripener.passes == 2, _STEP_S / 4)
    assert clock.monotonic() < _INTERVAL_S  # Retried after the backoff, not the next interval.
    loop.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert task.exception() is None
