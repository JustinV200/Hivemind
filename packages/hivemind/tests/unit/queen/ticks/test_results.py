"""Tests for hivemind.queen.ticks.results: the verified outcome's deposit into the Honey Store.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/results.py (codingrules section 3). What COMPLETE_TASK,
    RETRY_TASK and FAIL_TASK do to the chamber is covered through the Queen's own tick in
    tests/unit/queen/test_queen_results.py; this module covers the phase 7 addition, the
    TASK_OUTCOME deposit, against a real SQLite Honey Store (`builders.house_bee`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.results for the module under test.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from builders.cells import make_cell
from builders.house_bee import HoneyHarness, open_honey_access
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskGraphDraft, TaskStatus
from hivemind.brood_chamber import TaskOutcome as ChamberOutcome
from hivemind.cell import Cell, CellKind, CombShieldLevel, HoneyClearance
from hivemind.honey_store import (
    HoneyAccess,
    HoneyStoreError,
    IntakeResult,
    NectarIntake,
    NectarOrigin,
    NectarSubmission,
)
from hivemind.manifest import HoneyStoreSection
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.results import complete_task
from waggle.clock import FakeClock
from waggle.ids import CellId, TaskId
from waggle.messages.honey import NectarKind
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskOutcome, TaskResult

_SUMMARY = "The widget service listens on port 48213."


async def _running_task(
    deps: QueenDeps, link: WardenLink, clearance: HoneyClearance = HoneyClearance.C1
) -> TaskId:
    """Submit a one-task goal at `clearance`, place it on `link` and start it (RUNNING)."""
    base = make_graph_draft({"answer": ()})
    draft = base.tasks[0].model_copy(
        update={"title": "Find the widget port", "clearance": clearance}
    )
    (task,) = await deps.chamber.submit(TaskGraphDraft(tasks=(draft,)))
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "Placed for this test.")
    await deps.chamber.start(task.id)
    return task.id


def _verified(task_id: TaskId, link: WardenLink) -> TaskResult:
    """The Warden's verified TaskResult(SUCCEEDED) for `task_id`."""
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=TaskOutcome.SUCCEEDED,
        summary=_SUMMARY,
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=link.warden_id,
        handoff=None,
        spend=0.0,
        reason="Every acceptance criterion held.",
    )


async def _setup(tmp_path: Path, cell: Cell) -> tuple[QueenDeps, WardenLink, HoneyHarness]:
    """A Queen with a real Honey Store and one Warden link on `cell`."""
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    deps, link, _end = make_queen_deps(clock, cell=cell, honey=harness.access)
    return deps, link, harness


class _WedgedIntake(NectarIntake):
    """A real intake whose every submission fails as a wedged store would."""

    def __init__(self, access: HoneyAccess, clock: FakeClock) -> None:
        default_label = HoneyClearance.from_wire(access.clearance.default_label)
        super().__init__(access.store, access.identity, clock, HoneyStoreSection(), default_label)

    async def submit(self, submission: NectarSubmission) -> IntakeResult:
        raise HoneyStoreError("The Honey Store is wedged for this test.")


async def test_complete_task_deposits_the_verified_outcome_as_a_task_outcome_finding(
    tmp_path: Path,
) -> None:
    device = make_cell(kind=CellKind.REAL, clock=FakeClock(), name="widget-box")
    deps, link, harness = await _setup(tmp_path, device)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.origin is NectarOrigin.TASK_OUTCOME
    assert nectar.kind is NectarKind.FINDING
    assert nectar.scope == "hive"
    assert (nectar.task_id, nectar.cell_id) == (task_id, device.id)
    assert nectar.title == "Find the widget port"
    # A Real Cell is borrowed: the C2 floor outranks the task's own C1 label.
    assert nectar.clearance is HoneyClearance.C2
    assert await harness.access.store.has_source(f"task_outcome:{task_id}")
    content = (await harness.access.store.nectar_content(nectar.id)).decode()
    assert f"Verified outcome: {_SUMMARY}" in content
    assert "Objective:" in content
    assert "FILE_EXISTS" in content  # The acceptance criteria, as the Warden checked them.
    assert f"Cell: widget-box ({device.capabilities.os.value})" in content


async def test_complete_task_keeps_the_tasks_own_label_on_a_virtual_cell(tmp_path: Path) -> None:
    deps, link, harness = await _setup(
        tmp_path, make_cell(kind=CellKind.VIRTUAL, clock=FakeClock())
    )
    task_id = await _running_task(deps, link, HoneyClearance.C1)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.clearance is HoneyClearance.C1


async def test_complete_task_deposits_nothing_for_a_night_veil_task(tmp_path: Path) -> None:
    veiled = make_cell(
        kind=CellKind.VIRTUAL, clock=FakeClock(), comb_shield=CombShieldLevel.NIGHT_VEIL
    )
    deps, link, harness = await _setup(tmp_path, veiled)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    assert await harness.access.store.pending_nectar(10) == ()
    assert (await deps.chamber.get(task_id)).status is TaskStatus.SUCCEEDED


async def test_complete_task_still_completes_when_the_deposit_fails(tmp_path: Path) -> None:
    deps, link, harness = await _setup(tmp_path, make_cell(kind=CellKind.REAL, clock=FakeClock()))
    wedged = dataclasses.replace(harness.access, intake=_WedgedIntake(harness.access, FakeClock()))
    deps = dataclasses.replace(deps, honey=wedged)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    task = await deps.chamber.get(task_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.outcome is not None and task.outcome.summary == _SUMMARY


async def test_complete_task_deposits_before_the_cell_is_told_the_task_finished(
    tmp_path: Path,
) -> None:
    # A Virtual Cell's release takes its link with it, so the deposit must read the link first.
    seen: list[int] = []
    deps, link, harness = await _setup(
        tmp_path, make_cell(kind=CellKind.VIRTUAL, clock=FakeClock())
    )

    async def on_task_finished(cell_id: CellId, outcome: ChamberOutcome) -> None:
        seen.append(len(await harness.access.store.pending_nectar(10)))

    deps = dataclasses.replace(deps, on_task_finished=on_task_finished)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    assert seen == [1]


async def test_complete_task_deposits_nothing_with_no_honey_store() -> None:
    deps, link, _end = make_queen_deps(FakeClock())
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), link.warden_id)

    assert (await deps.chamber.get(task_id)).status is TaskStatus.SUCCEEDED


async def test_complete_task_finds_the_cell_from_the_chambers_record_with_no_warden_named(
    tmp_path: Path,
) -> None:
    device = make_cell(kind=CellKind.REAL, clock=FakeClock())
    deps, link, harness = await _setup(tmp_path, device)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _verified(task_id, link), warden_id=None)

    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.cell_id == device.id
    assert nectar.clearance is HoneyClearance.C2
