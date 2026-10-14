"""Tell the reversible case: a wrong line in /etc/hosts cuts a Nuc's link and its Drone mends it.

"Cut a Nuc's link with a planted, reversible problem (e.g. a wrong resolver entry) within reach of
its access level: its diagnostic Drone finds and fixes it inside the `reconnect_budget`, and the
task completes with no Queen or human involvement." The device is a container climbed to a Nuc, a
task's Drone is at work on its own model, and the container dials the Hive Stand by `hive.test`,
a name only its `/etc/hosts` knows. The story plants a wrong address for that name, resets the
packet's link and the Warden's from the Hive Stand's end (a connection that is open does not look
the name up again) and then does nothing at all: it only reads. Every redial of the packet and the
Warden meets the wrong address; the Warden finds its link gone, sends one Drone from its own pool,
and the Drone, whose rounds are scripted but whose commands run for real, looks the name up, finds
the line and writes the right one back. `mended` returns what the case left behind for the test to
assert over: the name before, after the plant and once mended, when it came right, the Hive's whole
trail, the Nectar that arrived, and the tasks the Queen holds.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests/e2e/test_phase11_exit_diagnose.py`; builds on `e2e.swarm.diagnose.scene`.

Key invariants:
    - After the plant the story issues no operator command and touches neither the Hive nor the
      container except to read; the only writer on the device is the Drone.
    - The device's budget is carved out of its offline limit, and the limit is long enough that
      Clustering cannot be what ends the outage: the case is about the Drone beating the budget.
    - The task outlives the whole outage: it is still running when the link returns, so it is the
      Warden's own task that ends it.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its last bullet, first sentence.
    - e2e/swarm/diagnose/plant.py for the fault and e2e/swarm/diagnose/scripts.py for the Drone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from e2e.swarm.colony.device import scenario_of
from e2e.swarm.colony.models import LOCAL_PROVIDER, local_scenario, shared_scenario
from e2e.swarm.colony.waits import goal_has_ended, has_event, until, whole_trail
from e2e.swarm.diagnose.plant import (
    address_of,
    hosts_text,
    plant_wrong_entry,
    sever,
    wait_for_address,
)
from e2e.swarm.diagnose.reads import Deposit, content_of, deposits, findings_of
from e2e.swarm.diagnose.scene import Scene, Setup
from e2e.swarm.diagnose.scripts import mend_hosts
from e2e.swarm.hive import REPORT_FILE

from hivemind.brood_chamber import Task, TaskFilter
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode

GRACE_S = (
    900.0  # The Queen holds a lost device this long: past the whole case, so she never decides.
)
OFFLINE_S, BUDGET_S, ATTEMPTS = 120.0, 60.0, 2  # The device's wait, and the Drone's share of it.
# The task outlives the outage: three minutes of commands, in few enough rounds that its bee never
# fills its model's window and hands off to a successor (which the script would start over).
TASK_ROUNDS, TASK_SLEEP_S = 6, 30
SUPERVISION = (
    f"[supervision]\nmax_offline_s = {OFFLINE_S}\n"
    f"[supervision.reconnect_budget]\nattempts = {ATTEMPTS}\nwall_clock_s = {BUDGET_S}\n"
)
# Bounds, in real seconds, each a few times what the step took when the case was written.
MEND_S, BACK_S, SHIP_S, ENDED_S = 150.0, 120.0, 120.0, 300.0

__all__ = ["BUDGET_S", "OFFLINE_S", "Mended", "mended", "reversible_setup"]


@dataclass(frozen=True, slots=True)
class Fault:
    """The fault as the story planted it: what the name resolved to, and when the links went.

    Attributes:
        before: What the Hive's name resolved to before the fault was planted.
        broken: What it resolved to once the fault was planted.
        cut_at: When the packet's link and the Warden's were reset.
    """

    before: str | None
    broken: str | None
    cut_at: datetime


@dataclass(frozen=True, slots=True)
class Mended:
    """What the reversible case left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        task: The task whose Drone was at work when the link was cut.
        gateway: The address the Hive's name is meant to resolve to.
        fault: The fault as it was planted.
        mended_at: When the name first resolved to the gateway again, to within a second.
        trail: The Hive's whole trail at the end, the device's merged segment in it.
        arrivals: Every Nectar the Hive stored, oldest first.
        findings: The words of the device Warden's findings Nectar.
        tasks: Every task the Queen holds at the end.
        hosts: The container's `/etc/hosts` at the end.
    """

    node: SwarmNode
    task: str
    gateway: str
    fault: Fault
    mended_at: datetime
    trail: tuple[PheromoneEvent, ...]
    arrivals: tuple[Deposit, ...]
    findings: str
    tasks: tuple[Task, ...]
    hosts: str


def reversible_setup() -> Setup:
    """The case's models and terms: a Nuc whose Drones think locally, and two minutes' patience."""

    def local(gateway: str) -> dict[str, object]:
        return mend_hosts(local_scenario(TASK_ROUNDS, TASK_SLEEP_S, REPORT_FILE), gateway)

    shared = scenario_of(shared_scenario(TASK_ROUNDS, TASK_SLEEP_S, REPORT_FILE))
    return Setup(local, shared, SUPERVISION, GRACE_S, nuc=True)


async def mended(scene: Scene) -> Mended:
    """Climb the device to a Nuc, plant the fault, reset its links and watch the Drone mend it.

    Args:
        scene: The Hive and the container, the device enrolled and idle.

    Returns:
        The record of the case.
    """
    hive, gateway = scene.hive, scene.network.gateway
    await scene.up()
    await scene.climb("COLONIZED")
    await scene.climb("NUC")
    task = await scene.put_task(LOCAL_PROVIDER)

    fault = await _plant(scene)
    # Nothing is done from here on but reading: the Drone mends the name, the Warden reconnects.
    mended_at = await wait_for_address(scene.container, hive.clock, gateway, MEND_S)
    await _await_recovery(scene, fault.cut_at)

    arrivals = await deposits(hive)
    [finding] = findings_of(arrivals, scene.node)
    return Mended(
        node=scene.node,
        task=task,
        gateway=gateway,
        fault=fault,
        mended_at=mended_at,
        trail=tuple(await whole_trail(hive)),
        arrivals=tuple(arrivals),
        findings=await content_of(hive, finding),
        tasks=tuple(await hive.stores.chamber.list(TaskFilter())),
        hosts=await asyncio.to_thread(hosts_text, scene.container),
    )


async def _plant(scene: Scene) -> Fault:
    """Plant the wrong line, then reset the links, so the next dial is the first to meet it."""
    container = scene.container
    before = await asyncio.to_thread(address_of, container)
    await asyncio.to_thread(plant_wrong_entry, container)
    broken = await asyncio.to_thread(address_of, container)
    cut_at = scene.hive.clock.now()
    await sever(scene.hive, scene.node)
    return Fault(before, broken, cut_at)


async def _await_recovery(scene: Scene, cut_at: datetime) -> None:
    """Wait, reading only, for the Warden's return, its verdict, the task's end and its findings."""
    hive, goal = scene.hive, scene.goal
    assert goal is not None, "the story put no task"

    async def back() -> bool:
        return await has_event(hive, "warden.reconnected", cut_at, ceilings=True)

    async def verdict() -> bool:
        return await has_event(hive, "warden.diagnosed", cut_at)

    async def ended() -> bool:
        return await goal_has_ended(hive, goal)

    async def findings() -> bool:
        return bool(findings_of(await deposits(hive), scene.node))

    await until("the Queen to say the Warden is back", back, BACK_S)
    await until("the Warden's verdict to reach the Hive", verdict, SHIP_S)
    await until("the task to end", ended, ENDED_S)
    await until("the findings to be deposited", findings, SHIP_S)
