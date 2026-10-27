"""End-to-end: an Overwintered Virtual Cell is scrubbed by its own Warden before it is paused.

Phase 5 open item 8. Before the Overwintering pool pauses a Virtual Cell for reuse, the Queen
orders the Cell's own Warden to release its lease (`hivemind.queen.cell_gate.scrub`): every
sub-bee stopped, every process the lease started killed, the finished task's scratch removed. The
Cell is paused only once that Warden answers that its scratch is gone; a scrub it cannot confirm
tears the Cell down instead. Both runs below build a whole Hive whose `"fake"` backend is
`builders.virtual_cells.ContainerSpawningFakeCellBackend`, a real in-Cell Warden (and Drone) per
Cell over a real loopback WebSocket, each with its own scratch root on this host, so the test can
look inside the Cell's scratch the way an operator would look inside a container's volume.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.scrub for the scrub, and hivemind.wardens.ticks.control for its
      Warden's half.
    - tests.e2e.test_virtual_cells for the phase 5 exit criteria these runs build on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    default_container_script,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.cli.in_cell.basket import BASKET_DIR
from hivemind.cli.in_cell.redial import OUTBOX_DIR
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.cell_gate import SCRUB_FAILED_KIND
from waggle.clock import SystemClock
from waggle.ids import CellId

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 15.0  # Bounds every goal and wait below; each finishes well within it.


def _overwintering_hive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Hive:
    """Build a Hive with Overwintering on, one in-process container per Virtual Cell."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=True)
    )
    # The same one-task plan for every goal: each run writes haiku_1.txt in its own scratch.
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    return build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )


def _backend(hive: Hive) -> ContainerSpawningFakeCellBackend:
    """Return the running Hive's own container-spawning fake backend."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    return backend


async def _events(hive: Hive, cell_id: str) -> list[PheromoneEvent]:
    """Every trail event about `cell_id`, the Queen's and the Cell's own alike, in trail order."""
    return list(await hive.stores.trail.query(TrailQuery(subject_id=cell_id)))


async def _until_count(hive: Hive, cell_id: str, kind: str, count: int) -> None:
    """Wait until the trail holds `count` events of `kind` about `cell_id`."""

    async def _reached() -> bool:
        return sum(event.kind == kind for event in await _events(hive, cell_id)) >= count

    await wait_until(_reached, timeout_s=_TIMEOUT_S)


async def _first_cell(hive: Hive) -> str:
    """The Cell the run's first task was assigned to."""
    events = await hive.stores.trail.query(TrailQuery(kind="queen.assigned"))
    cell_id = events[0].payload.get("cell_id")
    assert isinstance(cell_id, str)
    return cell_id


def test_a_dormant_cell_is_scrubbed_before_its_pause_and_reused_with_its_scratch_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive = _overwintering_hive(tmp_path, monkeypatch)
    asyncio.run(_run_two_goals(hive))


async def _run_two_goals(hive: Hive) -> None:
    """Two goals back to back on one Cell, looking inside its scratch after each pause."""
    backend = _backend(hive)
    try:
        async with run_hive(hive):
            first = await run_goal(
                hive, "goal one", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert first.succeeded, first
            cell_id = await _first_cell(hive)
            await _until_count(hive, cell_id, "cell.overwintered", 1)
            scratch_root = backend.scratch_roots[CellId(cell_id)]
            # Paused with nothing left of goal one's lease: its directory went with the scrub. The
            # resident Basket is the one thing a Cell keeps across a pause (ADR-0048).
            assert _lease_leftovers(scratch_root) == []
            # The Cell's own Warden still runs (a Docker-style pause), its script used up.
            backend.providers[CellId(cell_id)].script(*default_container_script())

            second = await run_goal(
                hive, "goal two", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert second.succeeded, second
            await _until_count(hive, cell_id, "cell.overwintered", 2)
            assert _lease_leftovers(scratch_root) == []
            events = await _events(hive, cell_id)
        _assert_scrubbed_twice_on_its_own_leases(events)
    finally:
        await backend.aclose()


def _assert_scrubbed_twice_on_its_own_leases(events: Sequence[PheromoneEvent]) -> None:
    """Each pause followed its own lease's release; the second goal ran on a fresh lease."""
    kinds = [event.kind for event in events]
    assert kinds.count("cell.provisioning") == 1  # Goal two reused the dormant Cell.
    assert "cell.resumed" in kinds
    assert SCRUB_FAILED_KIND not in kinds
    leases = [event.payload["lease_id"] for event in events if event.kind == "cell.leased"]
    released = [event for event in events if event.kind == "cell.released"]
    assert len(set(leases)) == 2  # A fresh lease, and a fresh scratch, for the second task.
    assert [event.payload["is_restored"] for event in released] == [True, True]
    # Every release, the Cell's own record shipped ahead of its answer, precedes its pause.
    paused = [index for index, kind in enumerate(kinds) if kind == "cell.overwintered"]
    freed = [index for index, kind in enumerate(kinds) if kind == "cell.released"]
    assert all(free < pause for free, pause in zip(freed, paused, strict=True))


def test_a_cell_whose_scrub_cannot_be_confirmed_is_torn_down_never_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Cell's Warden cannot remove its scratch: the Cell is destroyed, not kept dirty."""
    # Every in-process Cell's scratch removal fails, as a full or read-only disk would.
    monkeypatch.setattr("hivemind.cell.in_cell.remove_scratch", lambda _scratch_root: False)
    hive = _overwintering_hive(tmp_path, monkeypatch)
    asyncio.run(_run_one_goal_scrub_failing(hive))


async def _run_one_goal_scrub_failing(hive: Hive) -> None:
    """One goal; its Cell's scrub fails, so the release tears the Cell down."""
    backend = _backend(hive)
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive, "goal one", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert report.succeeded, report
            cell_id = await _first_cell(hive)
            await _until_count(hive, cell_id, "cell.destroyed", 1)
            events = await _events(hive, cell_id)
        kinds = [event.kind for event in events]
        assert "cell.overwintered" not in kinds
        assert backend.pause_calls == []
        failed = kinds.index(SCRUB_FAILED_KIND)
        assert kinds.index("cell.virtual_released") < failed < kinds.index("cell.destroying")
        reason = events[failed].payload["reason"]
        assert isinstance(reason, str) and "could not remove" in reason
    finally:
        await backend.aclose()


def _lease_leftovers(scratch_root: Path) -> list[Path]:
    """What a lease left in a Cell's scratch root: anything but the Cell's own keeping.

    The resident Basket's volume, and the Warden's outbox, which every Cell's mending link keeps
    below its scratch root (ADR-0062), are the Cell's, never a lease's.
    """
    return [e for e in scratch_root.iterdir() if e.name not in (BASKET_DIR, OUTBOX_DIR)]
