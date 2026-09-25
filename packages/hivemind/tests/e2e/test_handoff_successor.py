"""End-to-end: a Drone its Warden hands off is resumed by a fresh bee, which finishes the goal.

The Handoff lever means "write a Handoff and stop; a fresh bee resumes the task from it", and a
Warden pulls it on its own when a sub-bee's context passes the handoff threshold. The bee used to
stop and nothing followed it: the task sat RUNNING with no bee until the goal timed out. This
module runs the whole Hive Stand -- the Queen, the Hive Stand's own Warden and a real Drone over
one scripted `FakeLLMProvider`, real SQLite, a real child process -- and pulls the Warden's own
Handoff lever on the Drone while it is busy with a one-second command. The lever stands in for
the context check (the Drone records its tokens only once its tool loop has ended, so a real
crossing cannot be scripted inside the attempt it would end; scenario (e) of
`test_kernel_on_hive_stand` says the same). The first bee writes its Handoff and stops; its
successor resumes from it, writes the three haiku and the goal succeeds.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.alarms for resume_from_handoff, the successor this proves.
    - tests.e2e.test_kernel_on_hive_stand for scenario (e), the Checkpoint lever's own run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    default_worker_turn,
    tool_response,
    tool_round_count,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.supervision import Handoff
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_TIMEOUT_S = 20.0  # Generous: the first bee's one-second command, then one heartbeat.
_BUSY = [sys.executable, "-c", "import time; time.sleep(1)"]  # Long enough for the lever to land.


@pytest.mark.parametrize("capabilities", ("full", "none"))
def test_a_drone_its_warden_hands_off_is_resumed_by_a_fresh_bee_and_finishes(
    tmp_path: Path, capabilities: str
) -> None:
    holder: dict[str, Hive] = {}
    pending: list[asyncio.Task[None]] = []
    hive = build_hive(
        load_manifest(fake_manifest(tmp_path, capabilities=capabilities), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": HaikuScript(_busy_then_write(holder, pending)).responder},
    )
    holder["hive"] = hive

    asyncio.run(_run(hive, pending))


def _busy_then_write(holder: dict[str, Hive], pending: list[asyncio.Task[None]]) -> WorkerTurn:
    """The first bee stays busy and is handed off; the fresh bee resuming it writes the files."""
    first_rounds = {"seen": 0}

    def worker_turn(request: LLMRequest) -> LLMResponse:
        # A round 0 after the first is the successor's own fresh attempt: it does the work.
        if tool_round_count(request) == 0:
            first_rounds["seen"] += 1
            if first_rounds["seen"] == 1:
                pending.append(asyncio.ensure_future(_hand_off(holder["hive"])))
        if first_rounds["seen"] > 1:
            return default_worker_turn(request)
        return tool_response(request, (("busy", "run_command", {"argv": _BUSY}),))

    return worker_turn


async def _hand_off(hive: Hive) -> None:
    """Pull the Hive Stand Warden's own Handoff lever on the goal's one sub-bee."""
    [bee] = hive.warden.sub_bees
    await hive.warden.intervene(bee.worker_id, Handoff(reason="Scripted: a fresh bee resumes."))


async def _run(hive: Hive, pending: list[asyncio.Task[None]]) -> None:
    """Run the goal to completion, then check it was two bees on one attempt, resumed."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
    await asyncio.gather(*pending)
    events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    assert report.succeeded, report
    # The first bee checkpointed and stopped; one more bee, never a retry, finished the task.
    assert "worker.handing_off" in kinds
    assert "memory.checkpoint" in kinds
    assert kinds.count("worker.spawned") == 2, kinds
    assert "alarm.raised" not in kinds, kinds
