"""Pull the cable under a Nuc as its task ends, and keep what the Queen made of the result.

A cable pulled out from under a device leaves its connection looking alive until the websocket's
keepalive gives up, twenty to forty seconds on, and everything the device sends in that time is
"sent" and lost. The Warden's result for a task that ends just after the pull is such a frame: it
goes into the connection nobody knows is dead and is not queued either, since the Warden does not
yet know to queue it. The Nuc scenario (`e2e.swarm.colony.run`) cannot meet that, because its task
ends after the Warden has noticed; this one is built to. `run_unnoticed_scenario` tells the story
once on one container device, climbed to a Nuc as the Nuc scenario climbs it (a Level 1 Warden, then
a model server of its own): a first task is put to it and ends, its result heard in the ordinary
way; a second is put, and the moment its Drone has thought on the device's own model, so that
its one command of ten seconds is running, the cable is pulled; the command ends and the Warden
sends the result well inside the keepalive's patience; the Warden notices the loss and the cable is
put back at once. It returns a `UnnoticedRun`: the Hive's trail, what the Queen's door heard,
what her replay window dropped, and the moments between, for
`tests/e2e/test_phase11_exit_unnoticed.py` to make its claims over.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 unnoticed-
    window scenario; builds on the rest of `e2e.swarm.colony`, `e2e.swarm.bridge` and the
    operator's own `hive swarm promote`.

Key invariants:
    - The first task ends and is heard before the cable is pulled, so that what the Warden sends
      again afterwards includes a frame the Queen already holds, and the scenario can show that
      she dropped it and handled the other.
    - The cable is pulled while the second task's command is running, a few seconds before the
      Warden would send the result and long before it could have noticed: the keepalive cannot give
      up in under twenty seconds, and the command is ten.
    - The cable is put back as soon as the Warden says it noticed, so what the Queen hears next is
      what the Warden sends on its own, and the outage is far inside the replay window.
    - Every wait is bounded and named, a failure carries the device's own logs, and every
      container and network is removed.

See Also:
    - tests/e2e/test_phase11_exit_unnoticed.py for the claims made over the record.
    - e2e/swarm/colony/window.py and orders.py for what is heard at the Queen's door.
    - docs/adr/0053 for the amendment this scenario proves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.colony.device import (
    colonizable,
    device_events,
    device_logs,
    device_outbox,
    scenario_of,
    shared_model,
)
from e2e.swarm.colony.models import LOCAL_PROVIDER, hive_tables, local_scenario, shared_scenario
from e2e.swarm.colony.orders import Tap, tap
from e2e.swarm.colony.trail import grant_of, llm_calls
from e2e.swarm.colony.waits import (
    goal_has_ended,
    has_event,
    promoted_to,
    tasks_of,
    until,
    whole_trail,
)
from e2e.swarm.colony.window import window_drops
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.drone import Script, command_turn, submit_pinned
from e2e.swarm.enrolled import ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import REPORT_FILE, warden_is_idle
from e2e.swarm.operator import swarm

from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId
from waggle.transport.limits import ConnectionRefusal

# The ladder wants free memory before it moves a Warden onto a device, and a container reports the
# host's: this scenario is about the cable, not the host.
LADDER = "[swarm.ladder]\nmin_free_memory_bytes = 0\n"
GRACE_S = 900.0  # How long the Queen holds a lost device before she judges it: past the scenario.
COMMAND_S = 10  # The one command each task's Drone runs: what makes the result come after the pull.
# Bounds, in real seconds. Each is a few times what the step took when the scenario was written.
UP_S, PLAN_S, GOAL_S, COMMAND_WAIT_S = 90.0, 60.0, 180.0, 120.0
COLONIZE_S, NUC_S = 240.0, 180.0
NOTICE_S, BACK_S, ENDED_S = 150.0, 240.0, 180.0
# A duplicate is dropped by the transport as it is read, and a second handling of a result would be
# the Queen's next tick: this is a few of them, after everything the scenario waits for has held.
SETTLE_S = 5.0

__all__ = ["UnnoticedRun", "run_unnoticed_scenario"]


@dataclass(frozen=True, slots=True)
class UnnoticedRun:
    """What one run left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        trail: The Hive's whole trail at the end, oldest first, the device's merged segment in it.
        tap: Every frame the Queen's door heard from the device's Warden, with its envelope id.
        dropped: Every refusal the Queen's transport limits made, in order, the silent ones too.
        first: The task that ended while the cable was in.
        second: The task whose result was sent after the cable was pulled and before the loss was
            noticed.
        cut_at: When the cable was pulled.
        noticed_at: When the device's Warden recorded that its link was gone.
        back_at: When it was put back.
        queued: What the device's outbox held, as (kind, task), the moment before it came back.
    """

    node: SwarmNode
    trail: tuple[PheromoneEvent, ...]
    tap: Tap
    dropped: tuple[ConnectionRefusal, ...]
    first: str
    second: str
    cut_at: datetime
    noticed_at: datetime
    back_at: datetime
    queued: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class _Stand:
    """What one run is made of before it starts: the Hive, the cable and the device's terms."""

    hive: Hive
    directory: Path
    network: Network
    options: DeviceOptions


def run_unnoticed_scenario(image: str, directory: Path) -> UnnoticedRun:
    """Run the whole story on a fresh container and Hive, and clean up after it.

    Args:
        image: The device image (`e2e.swarm.image.device_image_or_skip`).
        directory: The scenario's own directory; its files are written here.

    Returns:
        The record of the run.
    """
    janitor = Janitor()
    try:
        network = janitor.network()
        shared = scenario_of(shared_scenario(1, 1, REPORT_FILE))
        local = local_scenario(1, COMMAND_S, REPORT_FILE)
        with shared_model(network, shared) as shared_url, window_drops() as dropped:
            options = colonizable(directory, network, shared_url, local)
            terms = BridgeTerms({"unreachable_grace_s": GRACE_S}, tables=LADDER + hive_tables())
            # The Drones that run here think on the device's own model, not on the Hive's script.
            script = Script(command_turn(("true",)))
            # Built here, before any loop runs: opening the Hive's stores starts a loop of its own.
            hive = bridge_hive(directory, network, script.responder, terms)
            stand = _Stand(hive, directory, network, options)
            return asyncio.run(_scenario(janitor, image, stand, dropped))
    finally:
        janitor.close()


async def _scenario(
    janitor: Janitor, image: str, stand: _Stand, dropped: list[ConnectionRefusal]
) -> UnnoticedRun:
    """Start the Hive, enrol the device and tell the story; a failure carries the device's logs."""
    async with run_hive(stand.hive):
        record = tap(stand.hive)
        device = await enrol_device(BridgeHive(stand.hive), janitor, image, stand.options)
        scene = _Scene(stand.hive, stand.directory, stand.network, device)
        try:
            return await scene.tell(record, dropped)
        except Exception as error:
            error.add_note(await asyncio.to_thread(device_logs, device.container))
            raise


@dataclass
class _Scene:
    """The Hive and the device of one run, with a method for each step of the story."""

    hive: Hive
    directory: Path
    network: Network
    device: ContainerDevice

    @property
    def node(self) -> SwarmNode:
        """The device's node."""
        return self.device.node

    @property
    def container(self) -> Container:
        """The device's container."""
        return self.device.container

    async def tell(self, record: Tap, dropped: list[ConnectionRefusal]) -> UnnoticedRun:
        """Tell the story in order and return its record."""
        await self._climb()
        goal, first = await self._put("write the first report")
        await until("the first task to end", lambda: goal_has_ended(self.hive, goal), GOAL_S)
        goal, second = await self._put("write the second report")
        running = lambda: self._commanding(second)  # noqa: E731 - named for the wait
        await until("the second task's command to run", running, COMMAND_WAIT_S)

        cut_at = self.hive.clock.now()
        await asyncio.to_thread(self.network.disconnect, self.container)
        noticed_at = await self._noticed(cut_at)
        queued = tuple(await asyncio.to_thread(device_outbox, self.container))
        back_at = self.hive.clock.now()
        await asyncio.to_thread(self.network.connect, self.container)
        await self._wait_back(back_at, goal)
        trail = tuple(await whole_trail(self.hive))
        return UnnoticedRun(
            node=self.node,
            trail=trail,
            tap=record,
            dropped=tuple(dropped),
            first=first,
            second=second,
            cut_at=cut_at,
            noticed_at=noticed_at,
            back_at=back_at,
            queued=queued,
        )

    async def _climb(self) -> None:
        """Wait for the Warden, then move it onto the device and give the device a model server."""
        idle = lambda: warden_is_idle(self.hive, self.node)  # noqa: E731 - named for the wait
        await until("the Warden to start and idle", idle, UP_S)
        await self._promote("COLONIZED", COLONIZE_S)
        wished_at = self.hive.clock.now()
        await self._promote("NUC", NUC_S)
        # The first plan this Hive writes is the Nuc's: no task has been put to it yet (the Nuc
        # scenario's has a long task running, and so waits for its second revision).
        planned = lambda: has_event(self.hive, "forage.plan_written", wished_at)  # noqa: E731
        await until("the hosting plan to be written", planned, NUC_S)

    async def _promote(self, level: str, bound_s: float) -> None:
        """Say `hive swarm promote NODE`, the operator's wish for the next rung, and wait for it."""
        result = await swarm(self.directory / "hive.toml", "promote", self.node.device_id)
        assert result.exit_code == 0, result.stderr
        await until(f"the device to reach {level}", lambda: promoted_to(self.hive, level), bound_s)

    async def _put(self, words: str) -> tuple[TaskId, str]:
        """Put a one-task goal to the Queen, pinned to the device, and return it and its task."""
        goal = await submit_pinned(self.hive, self.node, words)

        async def planned() -> bool:
            return bool(await tasks_of(self.hive, goal))

        await until("the goal to be planned", planned, PLAN_S)
        [task] = (await tasks_of(self.hive, goal)).values()
        return goal, task

    async def _commanding(self, task: str) -> bool:
        """Whether the task's Drone has thought on the device's model, so its command has begun."""
        trail = await whole_trail(self.hive)
        grant = grant_of(trail, task)
        where = {"grant_id": grant, "provider": LOCAL_PROVIDER, "slot": "WORKER"}
        return grant is not None and bool(llm_calls(trail, **where))  # type: ignore[arg-type]

    async def _noticed(self, cut_at: datetime) -> datetime:
        """Wait for the Warden to record that its link is gone; return the moment it recorded."""
        found: list[datetime] = []

        async def recorded() -> bool:
            events = await asyncio.to_thread(device_events, self.container, "warden.offline")
            found[:] = [moment for event in events if (moment := _moment(event)) >= cut_at]
            return bool(found)

        await until("the Nuc's Warden to notice the cut", recorded, NOTICE_S)
        return min(found)

    async def _wait_back(self, back_at: datetime, goal: TaskId) -> None:
        """Wait for the Warden to report its return and the second task to end; let it settle."""
        back = lambda: has_event(self.hive, "warden.reconnected", back_at, ceilings=True)  # noqa: E731
        await until("the Warden to report its return", back, BACK_S)
        await until("the second task to end", lambda: goal_has_ended(self.hive, goal), ENDED_S)
        # Whatever the Queen would do twice, she does on a tick after the first time.
        await asyncio.sleep(SETTLE_S)


def _moment(event: dict[str, object]) -> datetime:
    """The time an event body says it was recorded at."""
    return datetime.fromisoformat(str(event["at"]))
