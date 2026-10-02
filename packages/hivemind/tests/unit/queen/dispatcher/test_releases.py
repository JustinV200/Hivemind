"""Tests for hivemind.queen.dispatcher.releases: Virtual Cells released beside the Queen's tick.

The defect these pin down: a Virtual Cell's release (a graceful stop of up to 5 s, then a teardown
or a pause) ran inside the Queen's tick, so for those seconds her inbox, her Wardens' liveness and
the Entrance's goals all waited (`.claude/phase-10-handoff.md`, "Still open after the patches").
Every scenario hands the lane a release chain that finishes only when the test says so, and checks
that nothing the tick runs waited for it, that placement offers the Cell to no task meanwhile, and
that the Queen's stop awaits it rather than leaving it to race the shutdown.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/releases.py (codingrules section 3), with the two
    callers that start a release (`complete_task`) and await one (`Queen.stop`'s lane close).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.releases for the code under test.
    - tests/unit/queen/dispatcher/test_provisions.py for the provisioning lane this one mirrors.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field

import pytest
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.hive import CellDestroyError
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import drain_releases, start_release, stop_releases
from hivemind.queen.dispatcher.releases import collect_releases
from hivemind.queen.dispatcher.snapshot import build_inventory
from hivemind.queen.ticks.results import complete_task
from waggle.clock import FakeClock
from waggle.ids import CellId, TaskId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskOutcome as WireTaskOutcome
from waggle.messages.task import TaskResult

_TURNS = 20  # Loop turns a release that is free to run needs, at most, to reach its chain.
_DONE = TaskOutcome(status=TaskStatus.CANCELLED, summary="Its goal was withdrawn.")


@dataclass
class _GatedChain:
    """A release chain (`QueenDeps.on_task_finished`) that finishes only once the test opens it."""

    gate: asyncio.Event = field(default_factory=asyncio.Event)
    entered: list[CellId] = field(default_factory=list)
    finished: list[CellId] = field(default_factory=list)
    fail_with: Exception | None = None

    async def __call__(self, cell_id: CellId, outcome: TaskOutcome) -> None:
        """Note the Cell, hold until the gate opens, then finish (or fail as told)."""
        self.entered.append(cell_id)
        await self.gate.wait()
        self.finished.append(cell_id)
        if self.fail_with is not None:
            raise self.fail_with


def _deps(chain: _GatedChain, *, limit: int = 4) -> tuple[QueenDeps, WardenLink]:
    """A Queen's deps whose release chain is `chain`, and her one attached Warden's link."""
    base, link, _end = make_queen_deps(FakeClock())
    deps = dataclasses.replace(base, on_task_finished=chain)
    deps.dispatch.releases.limit = limit
    return deps, link


async def _turns() -> None:
    """Give every release free to run the loop turns it needs to reach its chain."""
    for _ in range(_TURNS):
        await asyncio.sleep(0)


def _cell(n: int) -> CellId:
    """A distinct Cell id per `n`, for releases that are not about any attached Cell."""
    return CellId(f"cell_01DXF6DT00ZF7FMEZ10ZCC41{n:02d}")


async def test_start_release_returns_before_the_release_finishes() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)

    await start_release(deps, link.cell.id, _DONE)
    await _turns()

    assert chain.entered == [link.cell.id]
    assert chain.finished == []
    assert deps.dispatch.releases.releasing() == {link.cell.id}
    chain.gate.set()
    await drain_releases(deps)
    assert chain.finished == [link.cell.id]
    assert deps.dispatch.releases.jobs == {}


async def test_a_release_ending_wakes_the_queen() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)
    deps.wake.clear()

    await start_release(deps, link.cell.id, _DONE)
    chain.gate.set()
    await drain_releases(deps)

    assert deps.wake.is_set()


async def test_complete_task_never_waits_for_the_cells_release() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)
    task_id = await _running_task(deps, link)

    await complete_task(deps, [link], _succeeded(task_id, link), link.warden_id)
    await _turns()

    assert (await deps.chamber.get(task_id)).status is TaskStatus.SUCCEEDED
    assert chain.entered == [link.cell.id]
    assert chain.finished == []
    chain.gate.set()
    await drain_releases(deps)


async def test_placement_offers_no_task_a_cell_being_released() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)
    await start_release(deps, link.cell.id, _DONE)
    await _turns()

    while_releasing = await build_inventory(deps, [link])
    chain.gate.set()
    await drain_releases(deps)
    afterwards = await build_inventory(deps, [link])

    assert [c.cell_id for c in while_releasing.real] == []
    assert [c.cell_id for c in afterwards.real] == [link.cell.id]


async def test_releases_past_the_limit_wait_their_turn_and_none_is_dropped() -> None:
    chain = _GatedChain()
    deps, _link = _deps(chain, limit=2)

    for n in range(3):
        await start_release(deps, _cell(n), _DONE)
    await _turns()

    assert chain.entered == [_cell(0), _cell(1)]
    chain.gate.set()
    await drain_releases(deps)
    assert sorted(chain.finished) == [_cell(0), _cell(1), _cell(2)]


async def test_two_releases_of_one_cell_run_one_after_the_other() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)

    await start_release(deps, link.cell.id, _DONE)
    await start_release(deps, link.cell.id, _DONE)
    await _turns()

    assert chain.entered == [link.cell.id]  # The second waits for the first to finish.
    chain.gate.set()
    await drain_releases(deps)
    assert chain.finished == [link.cell.id, link.cell.id]


async def test_stop_releases_awaits_a_running_release_and_runs_later_ones_inline() -> None:
    chain = _GatedChain()
    deps, link = _deps(chain)
    await start_release(deps, link.cell.id, _DONE)
    await _turns()

    stopping = asyncio.ensure_future(stop_releases(deps))
    await _turns()
    assert not stopping.done()  # Awaited, never cancelled.
    chain.gate.set()
    await stopping
    await start_release(deps, _cell(9), _DONE)

    assert chain.finished == [link.cell.id, _cell(9)]  # The later one ran before returning.
    assert deps.dispatch.releases.jobs == {}


async def test_a_backend_refusing_a_release_never_ends_the_tick() -> None:
    chain = _GatedChain(fail_with=CellDestroyError("docker", "cell_x", "busy"))
    deps, link = _deps(chain)
    await start_release(deps, link.cell.id, _DONE)
    chain.gate.set()
    await _turns()

    await collect_releases(deps)

    assert deps.dispatch.releases.jobs == {}


async def test_an_unexpected_release_failure_is_surfaced_to_the_tick() -> None:
    chain = _GatedChain(fail_with=RuntimeError("a bug in the release chain"))
    deps, link = _deps(chain)
    await start_release(deps, link.cell.id, _DONE)
    chain.gate.set()
    await _turns()

    with pytest.raises(RuntimeError, match="a bug"):
        await collect_releases(deps)


async def test_start_release_is_a_no_op_with_no_release_chain_wired() -> None:
    deps, link, _end = make_queen_deps(FakeClock())

    await start_release(deps, link.cell.id, _DONE)

    assert deps.dispatch.releases.jobs == {}


async def _running_task(deps: QueenDeps, link: WardenLink) -> TaskId:
    """Submit a one-task goal, place it on `link` and start it (RUNNING)."""
    (task,) = await deps.chamber.submit(make_graph_draft({"answer": ()}))
    await deps.chamber.assign(
        task.id,
        link.warden_id,
        link.cell.id,
        "Placed for this test.",
        bound_tier=link.cell.comb_shield,
    )
    await deps.chamber.start(task.id)
    return task.id


def _succeeded(task_id: TaskId, link: WardenLink) -> TaskResult:
    """The Warden's verified TaskResult(SUCCEEDED) for `task_id`."""
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=WireTaskOutcome.SUCCEEDED,
        summary="Done.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=link.warden_id,
        handoff=None,
        spend=0.0,
        reason="Every acceptance criterion held.",
    )
