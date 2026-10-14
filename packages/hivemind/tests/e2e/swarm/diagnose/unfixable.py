"""Tell the unfixable case: a Nuc's network is gone, its Drone gives up, and Clustering follows.

"Cut it with an unfixable problem (egress genuinely gone): the Drone gives up inside budget and
falls through to 11.9's existing Clustering path unchanged." The device is a container climbed to a
Nuc with a task's Drone at work on its own model, and its cable is pulled with `docker network
disconnect`, the container not told. Its Warden finds out when the websocket's keepalive gives up,
goes OFFLINE, and sends one Drone from its own pool; the Drone, whose rounds are scripted but whose
commands run for real, looks at the interfaces, the routes and the gateway, finds a machine with no
network at all and says so. The story reads the device's own record while it is cut off (nothing of
it can reach the Hive), waits for the Warden to cluster fully past its offline limit, notes what its
outbox holds, and only then puts the cable back and waits for what was queued to arrive.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests/e2e/test_phase11_exit_diagnose.py`; builds on `e2e.swarm.diagnose.scene`.

Key invariants:
    - The device's budget is carved out of its offline limit and both are short, so the whole of
      the case, the Clustering included, happens inside the time the cable is out.
    - The task is still running when the limit passes, so Clustering has a bee to pause: a Warden
      with nothing left to cluster would stay OFFLINE and prove nothing about its deadline.
    - The cable goes back only after the Warden has clustered and its outbox has been read, so what
      arrives afterwards was queued while the link was down and not sent in a gap.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its last bullet, second sentence.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the path that follows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from e2e.swarm.colony.device import device_events, device_outbox, scenario_of
from e2e.swarm.colony.models import LOCAL_PROVIDER, local_scenario, shared_scenario
from e2e.swarm.colony.waits import has_event, until, whole_trail
from e2e.swarm.container import Container
from e2e.swarm.diagnose.reads import Deposit, content_of, deposits, findings_of
from e2e.swarm.diagnose.scene import Scene, Setup
from e2e.swarm.diagnose.scripts import lost_network
from e2e.swarm.hive import REPORT_FILE

from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode

GRACE_S = (
    900.0  # The Queen holds a lost device this long: past the whole case, so she never decides.
)
OFFLINE_S, BUDGET_S, ATTEMPTS = 75.0, 37.0, 2  # The device's wait, and the Drone's share of it.
# The task outlives the Clustering: the cable is out for the keepalive's forty-odd seconds, then the
# offline limit. Six minutes of commands, in few enough rounds that its bee never fills its model's
# window and hands off to a successor (which the script would start over).
TASK_ROUNDS, TASK_SLEEP_S = 8, 45
SUPERVISION = (
    f"[supervision]\nmax_offline_s = {OFFLINE_S}\n"
    f"[supervision.reconnect_budget]\nattempts = {ATTEMPTS}\nwall_clock_s = {BUDGET_S}\n"
)
# Bounds, in real seconds, each a few times what the step took when the case was written.
NOTICE_S, VERDICT_S, CLUSTER_S = 150.0, 150.0, 180.0
BACK_S, SHIP_S = 180.0, 150.0

__all__ = ["BUDGET_S", "OFFLINE_S", "Gone", "gone", "unfixable_setup"]


@dataclass(frozen=True, slots=True)
class Gone:
    """What the unfixable case left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        task: The task whose Drone was at work when the cable was pulled.
        cut_at: When the cable was pulled.
        back_at: When it was put back.
        queued: What the device's outbox held, as (kind, task), the moment before it came back.
        trail: The Hive's whole trail at the end, the device's merged segment in it.
        arrivals: Every Nectar the Hive stored, oldest first.
        findings: The words of the device Warden's findings Nectar.
    """

    node: SwarmNode
    task: str
    cut_at: datetime
    back_at: datetime
    queued: tuple[tuple[str, str | None], ...]
    trail: tuple[PheromoneEvent, ...]
    arrivals: tuple[Deposit, ...]
    findings: str


def unfixable_setup() -> Setup:
    """The case's models and terms: a Nuc whose Drones think locally, and little time to wait."""

    def local(gateway: str) -> dict[str, object]:
        return lost_network(local_scenario(TASK_ROUNDS, TASK_SLEEP_S, REPORT_FILE), gateway)

    shared = scenario_of(shared_scenario(TASK_ROUNDS, TASK_SLEEP_S, REPORT_FILE))
    return Setup(local, shared, SUPERVISION, GRACE_S, nuc=True)


async def gone(scene: Scene) -> Gone:
    """Climb the device to a Nuc, pull its cable, and watch the Warden give up and cluster.

    Args:
        scene: The Hive and the container, the device enrolled and idle.

    Returns:
        The record of the case.
    """
    hive = scene.hive
    await scene.up()
    await scene.climb("COLONIZED")
    await scene.climb("NUC")
    task = await scene.put_task(LOCAL_PROVIDER)

    cut_at = hive.clock.now()
    await asyncio.to_thread(scene.network.disconnect, scene.container)
    await _await_clustering(scene, cut_at)
    # Read before the cable returns, so that what is queued now was queued while the link was down.
    queued = tuple(await asyncio.to_thread(device_outbox, scene.container))

    back_at = hive.clock.now()
    await asyncio.to_thread(scene.network.connect, scene.container)
    await _await_arrival(scene, cut_at, back_at)

    arrivals = await deposits(hive)
    [finding] = findings_of(arrivals, scene.node)
    return Gone(
        node=scene.node,
        task=task,
        cut_at=cut_at,
        back_at=back_at,
        queued=queued,
        trail=tuple(await whole_trail(hive)),
        arrivals=tuple(arrivals),
        findings=await content_of(hive, finding),
    )


async def _await_clustering(scene: Scene, cut_at: datetime) -> None:
    """Wait, reading the device's own file, until its Warden went OFFLINE, judged and clustered."""
    for what, kind, bound in (
        ("the Warden to notice the cut", "warden.offline", NOTICE_S),
        ("the Warden to judge the search", "warden.diagnosed", VERDICT_S),
        ("the Warden to cluster fully", "warden.clustered", CLUSTER_S),
    ):
        await until(what, partial(_recorded, scene.container, kind, cut_at), bound)


async def _recorded(container: Container, kind: str, since: datetime) -> bool:
    """Whether the device's own trail holds an event of `kind` recorded since `since`."""
    events = await asyncio.to_thread(device_events, container, kind)
    return any(datetime.fromisoformat(str(event["at"])) >= since for event in events)


async def _await_arrival(scene: Scene, cut_at: datetime, back_at: datetime) -> None:
    """Wait for the Warden's return, then for its record and its findings to reach the Hive."""
    hive = scene.hive

    async def back() -> bool:
        return await has_event(hive, "warden.reconnected", back_at, ceilings=True)

    async def shipped() -> bool:
        return await has_event(hive, "warden.clustered", cut_at)

    async def findings() -> bool:
        return bool(findings_of(await deposits(hive), scene.node))

    await until("the Queen to say the Warden is back", back, BACK_S)
    await until("the device's record to reach the Hive", shipped, SHIP_S)
    await until("the findings to be deposited", findings, SHIP_S)
