"""Chaos: a Virtual Cell dies mid-task, and every Cell's link to the Queen drops mid-task.

Roadmap 13.6's Cell half, in process. The Cells are the fake backend's in-process "containers",
each running the real in-Cell Warden over a real loopback WebSocket to the Queen's real listener.
The task's model call is held so the chaos lands while the task is running. Killed: the Cell's
container task is cancelled outright, as a container dies (its Warden never says goodbye); the
goal must still end explicitly. Dropped: the Queen's listener goes down and comes back on the same
port, as a network blip would leave it; the Cell's Warden must redial and its task finish. Either
way nothing may be left open once the Hive is down, and no Cell may be left on the backend.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_requeening.py for the Queen's death; this is the Cell's.
    - hivemind.cli.in_cell.redial for the link that mends.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from builders.llm import judge_approve_response
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.chaos.rig import assert_nothing_left_open, bounded, hive_on
from e2e.kernel_helpers import plan_response, text_response, wait_until

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]

_GOAL = "write a haiku about bees"
_RUN_S = 60.0  # run_goal's own bound: a redial backs off a few seconds, the rest is local.
_LOST_AFTER_S = 2.0  # How long the Queen waits for a Cell's Warden that went away.


def _respond(request: LLMRequest) -> LLMResponse:
    """The Hive Stand's own models: one task that must run on a Virtual Cell."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, single_haiku_plan(needs={"isolation": "REQUIRED"}))
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return text_response(json.dumps({}))


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch) -> list[ContainerSpawningFakeCellBackend]:
    """Holds the one fake backend the Hive builds (lazily, once it runs), its calls held."""
    built: list[ContainerSpawningFakeCellBackend] = []

    def build(*args: object, **kwargs: object) -> ContainerSpawningFakeCellBackend:
        built.append(ContainerSpawningFakeCellBackend(*args, **kwargs))  # type: ignore[arg-type]
        built[0].hold = asyncio.Event()
        return built[0]

    monkeypatch.setattr("hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", build)
    return built


def _hive(tmp_path: Path) -> Hive:
    """A Hive whose only task runs on a Virtual Cell, quick to give up on a Warden that is gone.

    `lost_after_s` is `[supervision] max_offline_s` plus the redial grace in a real Hive, minutes;
    here a Warden away two seconds is lost, still far longer than the dropped link's blip.
    """
    tuning = VirtualCellsTuning(heartbeat_interval_s=0.2)
    hive = hive_on(virtual_cells_manifest(tmp_path, tuning=tuning), _respond)
    hive.queen_deps.requeening.lost_after_s = _LOST_AFTER_S
    return hive


def test_a_cell_killed_mid_task_still_ends_the_goal_with_nothing_left_open(
    tmp_path: Path, made: list[ContainerSpawningFakeCellBackend]
) -> None:
    hive = _hive(tmp_path)

    report, destroyed = asyncio.run(bounded("cell killed", _kill_mid_task(hive, made)))

    # Its Warden lost, the task went back to PENDING and finished on a fresh Cell; the dead one
    # was torn down while the Hive still ran.
    assert report.succeeded, report
    assert len(made[0].provision_calls) == 2
    assert destroyed
    asyncio.run(assert_nothing_left_open(hive))


async def _kill_mid_task(
    hive: Hive, made: list[ContainerSpawningFakeCellBackend]
) -> tuple[GoalReport, bool]:
    """Kill the Cell's container mid-call; the report, and whether the dead Cell went meanwhile."""
    async with run_hive(hive):
        [backend] = made  # Built by the Hive's start (its reconcile asks the registry for it).
        try:
            goal = asyncio.ensure_future(
                run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)
            )
            await asyncio.wait_for(backend.holding.wait(), _RUN_S)
            [(dead, doomed)] = backend.container_tasks.items()
            doomed.cancel()  # The container dies: no goodbye, no trail flush, the socket just ends.
            assert backend.hold is not None
            backend.hold.set()  # A fresh Cell, if one is placed, works unhindered.
            report = await goal
            return report, dead in backend.destroy_calls
        finally:
            await backend.aclose()


def test_a_dropped_link_mid_task_is_redialled_and_the_task_finishes(
    tmp_path: Path, made: list[ContainerSpawningFakeCellBackend]
) -> None:
    hive = _hive(tmp_path)

    report = asyncio.run(bounded("link dropped", _drop_mid_task(hive, made)))

    assert report.succeeded, report
    assert len(made[0].provision_calls) == 1  # Redialled, not taken back onto a fresh Cell.
    asyncio.run(assert_nothing_left_open(hive))


async def _drop_mid_task(hive: Hive, made: list[ContainerSpawningFakeCellBackend]) -> GoalReport:
    """Take the Queen's listener down and back up while the Cell's Worker is mid-call."""
    parts = hive.virtual_cells
    assert parts is not None
    async with run_hive(hive):
        [backend] = made
        assert backend.hold is not None
        try:
            goal = asyncio.ensure_future(
                run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)
            )
            await asyncio.wait_for(backend.holding.wait(), _RUN_S)
            attached = len(hive.queen.wardens)
            await parts.listener.stop()  # Every Virtual Cell's link drops at once.
            await wait_until(lambda: len(hive.queen.wardens) < attached, timeout_s=_RUN_S)
            await parts.listener.start(hive.queen)  # Back on the port the Cells dial.
            await wait_until(lambda: len(hive.queen.wardens) == attached, timeout_s=_RUN_S)
            backend.hold.set()
            return await goal
        finally:
            await backend.aclose()
