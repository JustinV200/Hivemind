"""End-to-end: a Night Veil Cell leaves only its skeleton, in every store, on every end path.

Codingrules section 12 run through a whole Hive, the way `test_night_veil_link` runs one: a human's
goal request naming NIGHT_VEIL is planned by the Queen, placed on a fresh Virtual Cell the phase 5
suite's container-spawning fake backend provisions, and worked by a real in-Cell Warden that
reaches the Hive Stand's listener through a fake Tor (`tests.e2e.night_veil_hive`). While the Cell
lives, its whole record (its Warden's shipped segments, the Queen's own detail about it and its
task) waits in its ephemeral segment on the Queen's side and none of it reaches her durable trail;
once it ends, the durable trail holds, for that Cell and its task, only the skeleton section 12
names, cut to its skeleton payloads, plus the purge's own `cell.purged`, and the ephemeral store is
empty. Nothing of it stays in the Hive's other stores either: no memory row (a Queen episode about
the work is seeded before the Cell ends, since this goal writes none), no task words (a task still
running on the Cell when it ends is cancelled first), no ledger or checkpoint row, and no snapshot
image (one is left on a Docker daemon beside the Hive's own backend, `SnapshotHost`).

Each end path is its own scenario: a normal release after the task succeeds, a provision that
fails its Night Veil attestation after the Cell existed, an Absconding, and a restarted Queen that
finds the Cell gone. The last two stop the Queen with the Cell's Worker held at work after its
first round of Capped writes, and each purge still summarises them per tier: the Absconding from
the segment it holds, the restarted Queen from the counts the first Queen checkpointed. A MEADOW
Cell's whole local trail still merges into the durable trail exactly as it was recorded, and its
task keeps its words.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention for the segments, the skeleton and the purge.
    - hivemind.cli.compose.night_veil.side_channels for the stores every purge reaches.
    - tests.e2e.night_veil_hive for the Hive this module runs and what it reads back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.virtual_cells import VirtualCellsTuning, without_shutdown_retire
from e2e.kernel_helpers import wait_until
from e2e.night_veil_hive import (
    EVERYTHING,
    PURGED,
    TASK_WORDS,
    NightVeilRun,
    SnapshotHost,
    TeardownHook,
    abscond,
    counted,
    durable,
    kinds_about,
    leaks,
    left_about,
    night_veil_cells,
    night_veil_hive,
    red_then_green,
    request,
    running,
    seed,
    succeeded,
    tasks,
    watch_teardown,
    working,
    world,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CombShieldLevel
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from waggle.ids import CellId

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Generous: each scenario finishes in a few seconds on loopback.
_CAPPED = TrailQuery(kind="capping.capped")  # One per proposal the Capping gate approved.
_SEEDED = 3  # The seeded episode and image, and the task's own words: each counted when purged.
_DETAIL = {"worker.spawned", "llm.call", "capping.proposed", "capping.capped"}
_WORKING = {"warden.started", "queen.assigned", "worker.spawned", "worker.started"}
_QUEEN_DETAIL = {"cell.provisioning", "cell.ready", "cell.granted", "cell.destroying"}
# What each end path leaves about the Cell itself, in order; the task's rows are checked apart.
_RELEASED = [
    "cell.provisioned",
    "cell.attested",
    "forage.plan_written",
    "cell.destroyed",
    "capping.summary",
    PURGED,
]
_FAILED_ATTESTATION = ["cell.provisioned", "cell.attested", "cell.destroyed", PURGED]
_ENDS = ("cell.destroyed", PURGED)
_SUCCEEDED_TASK = [
    "task.submitted",
    "queen.placed",
    "task.assigned",
    "task.started",
    "task.succeeded",
]


def _seeding(run: NightVeilRun, host: SnapshotHost) -> TeardownHook:
    """Seed each Cell's episode and image the moment its teardown starts, naming every task."""

    async def before(cell_id: CellId) -> None:
        await seed(run, host, cell_id, [task.id for task in await tasks(run)])

    return before


async def _seed_while_working(run: NightVeilRun, host: SnapshotHost) -> int:
    """Seed the held Cell's episode and image; return how many proposals it had Capped."""
    [cell_id] = run.night_veil.segments.held_cells()
    await seed(run, host, cell_id, [task.id for task in await tasks(run)])
    capped = len(await run.night_veil.segments.query(cell_id, _CAPPED))
    assert capped > 0  # Held after its first round, so its purge has Capping to summarise.
    return capped


async def _assert_working_behind_the_veil(run: NightVeilRun) -> None:
    """While the Cell works: its record is in its segment, and none of it is on the trail."""
    [cell_id] = run.night_veil.segments.held_cells()
    held = await run.night_veil.segments.query(cell_id, EVERYTHING)
    assert {e.kind for e in held} >= _WORKING
    assert leaks(await durable(run), await tasks(run), run) == []


async def _assert_nothing_is_left(
    run: NightVeilRun, host: SnapshotHost
) -> tuple[PheromoneEvent, ...]:
    """After every end: skeleton rows only, and nothing of the work in any store or segment."""
    events = await durable(run)
    found = await tasks(run)
    assert leaks(events, found, run) == []
    assert [e.kind for e in events if any(w in json.dumps(e.payload) for w in TASK_WORDS)] == []
    assert run.night_veil.segments.held_cells() == ()
    ids = world(events, found, run)
    assert await left_about(run, host, night_veil_cells(events), ids) == []
    # Each in-Cell trail was its Warden's memory alone, gone with the container.
    assert run.in_cell
    assert all(isinstance(deps.trail, MemoryPheromoneTrail) for deps in run.in_cell)
    return events


def _summary(events: tuple[PheromoneEvent, ...], cell_id: str) -> dict[str, object]:
    """The one `capping.summary` a purge recorded for `cell_id`."""
    [summary] = [e for e in events if e.kind == "capping.summary" and e.subject_id == cell_id]
    return dict(summary.payload)


async def test_a_released_night_veil_cell_leaves_only_its_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = SnapshotHost()
    async with night_veil_hive(tmp_path, monkeypatch) as run:
        host.register(run)
        at_teardown = watch_teardown(run, monkeypatch, before=_seeding(run, host))
        async with running(run):
            await request(run)
            await wait_until(lambda: succeeded(run), timeout_s=_TIMEOUT_S)
            await wait_until(lambda: counted(run, PURGED, 1), timeout_s=_TIMEOUT_S)

    events = await _assert_nothing_is_left(run, host)
    [cell_id] = night_veil_cells(events)
    [task] = await tasks(run)
    assert task.status is TaskStatus.SUCCEEDED
    # Until its teardown began, its whole record waited in its segment, none of it on the trail.
    held, durable_then = at_teardown[cell_id]
    assert {e.kind for e in held} >= _DETAIL
    [deps] = run.in_cell
    assert {e.id for e in await deps.trail.query(EVERYTHING)} <= {e.id for e in held}
    assert leaks(durable_then, [task], run) == []
    # Then the skeleton alone: one summary for its one tier, and the purge's own counts.
    assert kinds_about(events, cell_id) == _RELEASED
    assert kinds_about(events, task.id) == _SUCCEEDED_TASK
    capped = len([e for e in held if e.kind == "capping.capped"])
    assert _summary(events, cell_id) == {
        "tier": "SCRATCH_WRITE",
        "approved": capped,
        "rejected": 0,
        "rolled_back": 0,
    }
    [purged] = [e for e in events if e.kind == PURGED]
    assert isinstance(purged.payload["events_purged"], int)
    assert purged.payload["events_purged"] > len(held)  # Its teardown's own rows went too.
    side = purged.payload["side_channel_records_purged"]
    assert isinstance(side, int) and side > _SEEDED  # The seeded rows and its ledger rows.


async def test_a_failed_attestation_purges_the_night_veil_cell_it_had_provisioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = SnapshotHost()
    async with night_veil_hive(tmp_path, monkeypatch, probes=red_then_green()) as run:
        host.register(run)
        watch_teardown(run, monkeypatch, before=_seeding(run, host))
        async with running(run):
            await request(run)
            await wait_until(lambda: succeeded(run), timeout_s=_TIMEOUT_S)
            await wait_until(lambda: counted(run, PURGED, 2), timeout_s=_TIMEOUT_S)

    events = await _assert_nothing_is_left(run, host)
    failed, worked = night_veil_cells(events)
    assert kinds_about(events, failed) == _FAILED_ATTESTATION
    assert kinds_about(events, worked) == _RELEASED
    # The verdict survives per check, PASS or FAIL, never the check's own words.
    attested = next(e for e in events if e.kind == "cell.attested" and e.subject_id == failed)
    assert attested.payload["passed"] is False
    assert attested.payload["red"] == ["tor_healthy"]
    assert attested.payload["tor_healthy"] == {"status": "FAIL"}
    [task] = await tasks(run)
    assert task.status is TaskStatus.SUCCEEDED


async def test_an_absconding_purges_a_night_veil_cell_it_finds_still_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = SnapshotHost()
    async with night_veil_hive(tmp_path, monkeypatch, hold_after=1) as run:
        host.register(run)
        # A crashed Queen: nothing retires the Cell, which is still working when she stops.
        run.hive = without_shutdown_retire(run.hive)
        async with running(run):
            await request(run)
            await wait_until(lambda: working(run), timeout_s=_TIMEOUT_S)
            await _assert_working_behind_the_veil(run)
            capped = await _seed_while_working(run, host)
        summary = await abscond(run.hive)

    assert summary.left_as_found
    assert summary.containers_destroyed == 1
    events = await _assert_nothing_is_left(run, host)
    [cell_id] = night_veil_cells(events)
    assert kinds_about(events, cell_id) == _RELEASED
    assert _summary(events, cell_id)["approved"] == capped
    destroyed = next(e for e in events if e.kind == "cell.destroyed" and e.subject_id == cell_id)
    assert set(destroyed.payload) == {"grants_revoked", "wax_retired", "leavings_removed"}
    # It could never finish without its Cell: cancelled by the purge, its words gone with it.
    [task] = await tasks(run)
    assert task.status is TaskStatus.CANCELLED
    assert kinds_about(events, task.id)[-1] == "task.cancelled"


async def test_a_restarted_queen_purges_a_night_veil_cell_it_finds_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = SnapshotHost()
    async with night_veil_hive(tmp_path, monkeypatch, hold_after=1) as first:
        host.register(first)
        first.hive = without_shutdown_retire(first.hive)
        # The Queen stops with her Cell working, and its host goes with her: a fresh backend
        # below lists nothing of it.
        async with running(first):
            await request(first)
            await wait_until(lambda: working(first), timeout_s=_TIMEOUT_S)
            await _assert_working_behind_the_veil(first)
            capped = await _seed_while_working(first, host)
        # Its record was held in her memory alone, which a real crash takes with her process.
        [gone] = first.night_veil.segments.held_cells()
        restarted = await first.rebuilt()
        host.register(restarted)  # The same Docker daemon, named again by the new Queen.
        async with running(restarted):
            # Starting the Hive reconciled it: the sweep found the Cell gone and purged it.
            assert await counted(restarted, PURGED, 1)

    events = await _assert_nothing_is_left(restarted, host)
    assert night_veil_cells(events) == [gone]
    assert kinds_about(events, gone) == _RELEASED
    # Summarised from the counts the first Queen checkpointed, as her segment held them.
    assert _summary(events, gone)["approved"] == capped
    # Recorded by the sweep: its destruction (never recorded before) and a purge that held no
    # event of it, but found every row the stores kept about it.
    destroyed, purged = [e.payload for e in events if e.subject_id == gone and e.kind in _ENDS]
    assert destroyed == {} and purged["events_purged"] == 0
    side = purged["side_channel_records_purged"]
    assert isinstance(side, int) and side > _SEEDED
    [task] = await tasks(restarted)
    assert task.status is TaskStatus.CANCELLED


async def test_a_meadow_cells_whole_local_trail_still_merges_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tuning = VirtualCellsTuning(overwinter_enabled=False)  # Torn down at once, like Night Veil.
    async with night_veil_hive(tmp_path, monkeypatch, tuning=tuning) as run, running(run):
        await request(run, CombShieldLevel.MEADOW)
        await wait_until(lambda: succeeded(run), timeout_s=_TIMEOUT_S)
        await wait_until(lambda: counted(run, "cell.destroyed", 1), timeout_s=_TIMEOUT_S)

    events = await durable(run)
    [task] = await tasks(run)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.spec.title == TASK_WORDS[0]  # Its words are its own: no Night Veil scrub.
    # Every row the Cell recorded reached the Queen's trail exactly as recorded, detail and all.
    [deps] = run.in_cell
    local = await deps.trail.query(EVERYTHING)
    assert {e.kind for e in local} >= _DETAIL
    by_id = {e.id: e for e in events}
    assert [by_id.get(e.id) for e in local] == list(local)
    # So does the Queen's own detail about the Cell and its task, which a Night Veil Cell withholds.
    [cell_id] = [e.subject_id for e in events if e.kind == "cell.provisioned"]
    assert set(kinds_about(events, cell_id)) >= _QUEEN_DETAIL
    submitted = next(e for e in events if e.kind == "task.submitted")
    assert submitted.payload["title"] == TASK_WORDS[0]
    assert [e.kind for e in events if e.kind in (PURGED, "capping.summary")] == []
    assert run.night_veil.segments.held_cells() == ()
