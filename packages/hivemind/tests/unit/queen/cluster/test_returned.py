"""Tests for hivemind.queen.cluster.returned: a Warden back from a cut that clustered fully.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/returned.py (codingrules section 3). A real Queen runs her
    tick over an in-process Warden link; the Warden's end sends the Heartbeats a Virtual Cell's
    Warden sends once its link is back, and the test reads her trail and what she sends it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.returned for the module under test.
    - hivemind.wardens.offline.freeze.cluster_fully for the Warden's side.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.queen import make_queen_deps, plan_responder
from builders.supervision import make_telemetry

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.attach import detach_warden
from hivemind.queen.queen import Queen
from waggle.ids import WardenId
from waggle.messages.supervision import Heartbeat, WardenState


def _plan(goal: str) -> dict[str, object]:
    """One task, the plan shape the other Queen tests script."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "done.txt", "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _heartbeat(state: WardenState, interval_s: float) -> Heartbeat:
    """A Warden's Heartbeat with no sub-bee row; `interval_s` tells one from the next."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=state,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=interval_s,
    )


async def _handled(queen: Queen, warden_id: WardenId, interval_s: float) -> None:
    """Yield until `queen` has handled the Heartbeat marked `interval_s`."""
    for _ in range(2_000):
        beat = queen._last_heartbeat.get(warden_id)
        if beat is not None and beat.interval_s == interval_s:
            return
        await asyncio.sleep(0)
    raise AssertionError("The Queen never handled the Heartbeat.")


_C, _O, _A = WardenState.CLUSTERED, WardenState.OFFLINE, WardenState.ACTIVE


@pytest.mark.parametrize(
    ("returned", "states", "resumed"),
    [
        (True, (_C, _C), True),  # Clustered fully: resumed once, never again by a later beat.
        (True, (_O, _C), True),  # Still OFFLINE at first: the settled beat is the one read.
        (True, (_A, _C), False),  # Back before the limit: it carries on by itself.
        (False, (_C,), False),  # Never cut off: a CLUSTERED beat alone is not a return.
    ],
    ids=["clustered_fully", "offline_first", "back_in_time", "never_left"],
)
async def test_a_returned_warden_that_clustered_fully_has_its_task_resumed(
    returned: bool, states: tuple[WardenState, ...], resumed: bool
) -> None:
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    task_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    first = await warden_end.wait_for_assignment()
    if returned:
        # The link dropped and the Warden dialled in again: a reconnection.
        await detach_warden(queen, link.warden_id)
        await queen.attach_warden(link)
    running = asyncio.ensure_future(queen.run())

    # One beat at a time, each handled before the next is sent, as a Warden's beats come.
    for number, state in enumerate(states, start=1):
        await warden_end.send(_heartbeat(state, float(number)))
        await _handled(queen, link.warden_id, float(number))
    if resumed:
        # Bounded: a Queen that never resumes must fail the test, not hang it.
        pumped = warden_end.pump_until(lambda: len(warden_end.assignments) > 1)
        await asyncio.wait_for(pumped, timeout=5.0)
        again = warden_end.assignments[-1]
        assert again.task_id == task_id and again.attempt == first.attempt + 1
    await queen.stop()
    await asyncio.wait_for(running, timeout=5.0)

    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    rows = [e.payload for e in decided if e.payload.get("reason") == "resumed_after_cut"]
    assert rows == ([{"reason": "resumed_after_cut", "task_ids": [task_id]}] if resumed else [])
    assert (await deps.chamber.get(task_id)).status is TaskStatus.RUNNING
    await warden_end.close()
