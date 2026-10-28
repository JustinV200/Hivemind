"""Chaos: the Queen's inbox is flooded mid-goal, and a goal spends its whole grant.

Roadmap 13.6's pressure half, on the Hive Stand. Flooded: a rogue Warden link is attached to the
running Queen and pours thousands of Alarms at her while a goal runs; her per-link queue holds the
line (the flood waits, it is never dropped silently or allowed to starve her tick), the goal still
succeeds and every Alarm is eventually read. Exhausted: every Drone call reports a cost far past
the goal's spend cap; the Queen's cost-cap trigger must cluster the provider, so the task pauses at
a Handoff rather than spending on, and stopping the Hive then leaves nothing open.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_flood.py for the prompt-budget flood (ten thousand items packed, none lost).
    - hivemind.queen.cluster.triggers for the cost-cap trigger.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.cli import fake_manifest
from builders.queen import WardenEnd, make_warden_link
from e2e.chaos.rig import assert_nothing_left_open, bounded, hive_on
from e2e.kernel_helpers import (
    HaikuScript,
    default_worker_turn,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import CellKind, HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, Usage
from hivemind.pheromone import TrailQuery
from waggle.ids import new_alarm_id, new_warden_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]

_GOAL = "write three haiku about bees to separate files"
_RUN_S = 30.0  # run_goal's own bound for the flood; it finishes in seconds.
_FLOOD = 3_000  # Alarms the rogue link sends: far past any one link's queue.
_PAUSED_S = 8.0  # How long the over-budget goal is followed; it pauses within a tick or two.
_ROUNDS = 200  # The over-budget Drone's rounds: far more than the cap lets it reach.
_OVER_CAP_USD = 1_000.0  # Each Drone call's reported cost: far past the default $5 goal cap.


def test_a_flood_of_alarms_mid_goal_neither_starves_the_queen_nor_hangs(tmp_path: Path) -> None:
    hive = hive_on(fake_manifest(tmp_path), HaikuScript(default_worker_turn).responder)

    report, sent = asyncio.run(bounded("inbox flood", _flooded(hive)))

    assert report.succeeded, report
    assert sent == _FLOOD  # Every Alarm went in: the queue held it back, never dropped the sender.
    asyncio.run(assert_nothing_left_open(hive))


async def _flooded(hive: Hive) -> tuple[GoalReport, int]:
    """Attach a rogue Warden, flood her from it while the goal runs, then detach it."""
    async with run_hive(hive):
        end = await _rogue(hive)
        flood = asyncio.ensure_future(_pour(hive, end))
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)
        sent = await flood
        await end.close()
        return report, sent


async def _rogue(hive: Hive) -> WardenEnd:
    """A Warden on a Cell of its own, attached to the running Queen, never placed on."""
    clock, ids = hive.clock, hive.manifest.hive
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    link, end = make_warden_link(ids.id, new_warden_id(clock), ids.node_id, cell, clock)
    await hive.queen.attach_warden(link)
    return end


async def _pour(hive: Hive, end: WardenEnd) -> int:
    """Send `_FLOOD` Alarms about no task, as fast as the link takes them; count them."""
    clock = hive.clock
    for sent in range(_FLOOD):
        alarm = AlarmRaised(
            alarm_id=new_alarm_id(clock),
            kind=AlarmKind.WORKER_FAILED,
            severity=AlarmSeverity.WARNING,
            origin=new_worker_id(clock),
            attempts=0,
            raised_at=clock.now(),
            context=AlarmContext(
                task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
            ),
            detail="Flooding.",
            clearance=WireHoneyClearance.C1,
            reason="Chaos.",
        )
        await end.send(alarm)
        if sent % 100 == 0:
            await asyncio.sleep(0)  # Let the goal's own work interleave, as a socket would.
    return _FLOOD


def test_a_goal_that_spends_its_whole_grant_is_clustered_and_leaves_nothing_open(
    tmp_path: Path,
) -> None:
    hive = hive_on(fake_manifest(tmp_path), _ExpensiveScript().responder)

    report = asyncio.run(bounded("grant exhausted", _over_budget(hive)))

    assert not report.succeeded, report
    [task] = report.tasks
    assert task.status is TaskStatus.PAUSED  # Held at its Handoff, not spending on.
    kinds = [e.kind for e in asyncio.run(hive.stores.trail.query(TrailQuery(limit=1_000)))]
    assert kinds.index("memory.checkpoint") < kinds.index("task.paused")
    assert "queen.clustered" in kinds
    asyncio.run(assert_nothing_left_open(hive))


def _slow_worker_turn(request: LLMRequest) -> LLMResponse:
    """Write one file a round for many rounds: work long enough for a cap to land mid-way."""
    rounds = tool_round_count(request)
    if rounds >= _ROUNDS:
        return text_response("Done.")
    name = f"haiku_{rounds % 3 + 1}.txt"
    return tool_response(request, (write_call(name),))


class _ExpensiveScript(HaikuScript):
    """A long-working Drone, every one of its calls billed far past the goal's spend cap."""

    def __init__(self) -> None:
        super().__init__(_slow_worker_turn)

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Answer as usual, with the WORKER call's usage priced over the cap."""
        response = super().responder(request)
        if request.slot is not ModelSlot.WORKER:
            return response
        usage = Usage(input_tokens=10, output_tokens=10, cost_usd=_OVER_CAP_USD)
        return response.model_copy(update={"usage": usage})


async def _over_budget(hive: Hive) -> GoalReport:
    """Run the goal until its task pauses, then follow it no further."""
    async with run_hive(hive):
        goal = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_PAUSED_S)
        )
        await wait_until(lambda: _paused(hive), timeout_s=_PAUSED_S)
        return await goal


async def _paused(hive: Hive) -> bool:
    """Whether the goal's task has paused."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is TaskStatus.PAUSED
