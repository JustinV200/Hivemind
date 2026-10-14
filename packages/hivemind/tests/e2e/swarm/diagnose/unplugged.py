"""Tell the Hive Stand's case: a colonized device is unplugged, and W0 looks before she decides.

"Symmetrically, unplug a colonized device: `W0` on the Hive Stand spawns its own diagnostic Drone
and deposits a Nectar finding before the grace period's existing 'then decide' fires." The device
is a container climbed one rung, so its Warden runs on it and cannot be reached when its cable is
pulled with `docker network disconnect`, and a task's Drone is at work on it. The Queen notices
when the websocket's keepalive gives up on the Warden's link, holds the device's task for the
grace, and, because the hold holds a task and somebody saw the device go, makes one task for the
Hive Stand's own Warden: a Drone, pinned to the Hive Stand, that looks at its end of the link. The
story waits for her decision about the device (the hold's "then decide") and reads the Hive's trail
and tasks as they stand at that moment, because the order of what led up to it is the claim.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests/e2e/test_phase11_exit_diagnose.py`; builds on `e2e.swarm.diagnose.scene`.

Key invariants:
    - The Hive Stand's Drone is the Hive's own (its model is the Hive's scripted one, answered by
      `e2e.swarm.diagnose.scripts.stand_check`) and the device's side does nothing: a colonized
      Warden has no model of its own, so it sends no Drone, and the case does not depend on it.
    - The grace is long enough for the Hive Stand's Drone to finish and the Queen to deposit what it
      found, and short enough that the case does not wait for minutes; the claim is the order.
    - The story stops at the decision: what the Queen then does with the task is the existing
      path (ADR-0053) and not what the criterion is about.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its last bullet, last sentence.
    - hivemind.queen.swarm_gate.unreachable.hive_stand for the task the hold makes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from e2e.scripted_openai import Scenario
from e2e.swarm.colony.models import SHARED_PROVIDER, local_scenario
from e2e.swarm.colony.waits import has_event, until, whole_trail
from e2e.swarm.diagnose.reads import Deposit, deposits
from e2e.swarm.diagnose.scene import Scene, Setup
from e2e.swarm.hive import REPORT_FILE

from hivemind.brood_chamber import Task, TaskFilter
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode

# Long enough for the Hive Stand's Drone to run its three rounds and for the Queen to deposit what
# it found, with room for a busy host; short enough that the case is not mostly waiting.
GRACE_S = 60.0
BUSY = ("sleep", "3")  # What the task's Drone runs round after round: it never finishes by itself.
# Bounds, in real seconds: the Queen learns of the cut when the keepalive gives up, then the grace.
DECIDED_S = 240.0

__all__ = ["GRACE_S", "Unplugged", "unplugged", "unplugged_setup"]


@dataclass(frozen=True, slots=True)
class Unplugged:
    """What the Hive Stand's case left behind: the record the criterion is asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        task: The task whose Drone was at work when the cable was pulled.
        cut_at: When the cable was pulled.
        trail: The Hive's whole trail at the moment the Queen decided.
        arrivals: Every Nectar the Hive stored by then, oldest first.
        tasks: Every task the Queen holds by then.
    """

    node: SwarmNode
    task: str
    cut_at: datetime
    trail: tuple[PheromoneEvent, ...]
    arrivals: tuple[Deposit, ...]
    tasks: tuple[Task, ...]


def unplugged_setup() -> Setup:
    """The case's models and terms: a colonized device that thinks on the Hive Stand's model."""

    def local(gateway: str) -> dict[str, object]:
        return local_scenario(1, 1, REPORT_FILE)  # Never asked: the device is not a Nuc.

    return Setup(local, Scenario(command=BUSY), supervision="", grace_s=GRACE_S, nuc=False)


async def unplugged(scene: Scene) -> Unplugged:
    """Climb the device one rung, put a task on it, pull its cable and watch for the decision.

    Args:
        scene: The Hive and the container, the device enrolled and idle.

    Returns:
        The record of the case.
    """
    hive = scene.hive
    await scene.up()
    await scene.climb("COLONIZED")
    task = await scene.put_task(SHARED_PROVIDER)

    cut_at = hive.clock.now()
    await asyncio.to_thread(scene.network.disconnect, scene.container)

    async def decided() -> bool:
        return await has_event(hive, "queen.decided", cut_at, reason="unreachable_decision")

    await until("the Queen to decide about the lost device", decided, DECIDED_S)
    return Unplugged(
        node=scene.node,
        task=task,
        cut_at=cut_at,
        trail=tuple(await whole_trail(hive)),
        arrivals=tuple(await deposits(hive)),
        tasks=tuple(await hive.stores.chamber.list(TaskFilter())),
    )
