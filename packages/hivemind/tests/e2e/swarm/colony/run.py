"""Climb one container device up the ladder and through a cut, and keep what the scenario asserts.

The phase 11 exit criterion for a Nuc is one story told about one machine, and it is only true if
it happens in order on that machine: a task is running on a device that has no Warden of its own;
the operator moves the Warden onto it and the task goes on from its Handoff; the operator asks for
a model server on it; a task whose Drone thinks on that server is running when the cable is pulled;
the device finishes it alone, and the one that needs a better model than the device has waits;
the cable goes back and nothing is said twice. `run_nuc_scenario` tells that story against a whole
Hive and a real container and returns a `NucRun`: the Hive's whole trail, what the Queen's door
heard and sent, what the device had queued while it was cut off, and the moments between. The
criteria are then assertions over that one record (`tests/e2e/test_phase11_exit_nuc.py`), each a
claim about order or count, so one slow run proves all of them and a failure names the one that
broke.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 Nuc scenario;
    builds on the rest of `e2e.swarm.colony`, `e2e.swarm.bridge`, `e2e.swarm.drone` and the
    operator's own `hive swarm promote`.

Key invariants:
    - The operator's two wishes are the real command (`hive swarm promote`), and everything after
      them is the running Hive acting on its own beat; nothing here moves a Warden by hand.
    - The network is cut and put back with `docker network disconnect` and `connect`: the
      container is not told, and its kernel finds out the way a device's would.
    - Every wait is bounded and named, and a failure carries the device's own logs.
    - Every container and network is removed, whatever happened.

See Also:
    - tests/e2e/test_phase11_exit_nuc.py for the claims made over the record.
    - e2e/swarm/colony/waits.py for the waits, and trail.py for the reads.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.colony.device import (
    colonizable,
    device_event_ids,
    device_events,
    device_logs,
    device_outbox,
    scenario_of,
    shared_model,
)
from e2e.swarm.colony.models import (
    EXTRA,
    LOCAL_PROVIDER,
    ROGUE,
    SERVER_ID,
    SMALL,
    hive_tables,
    local_scenario,
    shared_scenario,
)
from e2e.swarm.colony.orders import Tap, order, tap
from e2e.swarm.colony.trail import grant_of, llm_calls
from e2e.swarm.colony.waits import (
    goal_has_ended,
    has_event,
    promoted_to,
    tasks_of,
    until,
    whole_trail,
)
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.drone import (
    EXACTING_TITLE,
    ORDINARY_TITLE,
    Script,
    busy_turn,
    exacting_plan,
    submit_pinned,
)
from e2e.swarm.enrolled import ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import REPORT_FILE, warden_is_idle
from e2e.swarm.operator import swarm

from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId
from waggle.messages.swarm import NucAction

# The ladder wants some free memory before it moves a Warden onto a device, and a container reports
# the host's, which a busy machine may not have: this scenario is about the ladder, not the host.
LADDER = "[swarm.ladder]\nmin_free_memory_bytes = 0\n"
GRACE_S = 900.0  # How long the Queen holds a lost device before she judges it: past the scenario.
LONG_COMMAND = ("sleep", "10")  # What the Hive's own Drone runs, round after round, at Level 0.
SHARED_ROUNDS, SHARED_SLEEP_S = 8, 6  # The rounds a Drone makes that thinks on the Hive's model.
# The ordinary task's rounds on the device's model: long enough that it ends while the cable is
# still out (the Warden notices after about forty-five seconds), however the scenario's pace varies.
LOCAL_ROUNDS, LOCAL_SLEEP_S = 8, 14
GOAL = "do the ordinary work and the exacting work"
LEVEL0_CALLS = 2  # Model calls the long task's Drone has made before the operator colonizes.
# Bounds, in real seconds. Each is a few times what the step took when the scenario was written.
UP_S, LEVEL0_S, PLAN_S = 90.0, 90.0, 30.0
COLONIZE_S, NUC_S, LONG_S = 240.0, 180.0, 240.0
MID_TASK_S, NOTICE_S, CUT_WORK_S = 90.0, 150.0, 300.0
BACK_S, ENDED_S, SHIP_S = 240.0, 300.0, 90.0
# What the scenario asks of the Nuc's Warden once the link is back: stop the server the Queen
# started, start it with one model, then ask for the Hive's other model and for the device's own.
ORDERS = (
    (NucAction.DEMOTE, ()),
    (NucAction.PROMOTE, (SMALL,)),
    (NucAction.PROMOTE, (EXTRA,)),
    (NucAction.PROMOTE, (ROGUE,)),
)

__all__ = ["NucRun", "run_nuc_scenario"]


@dataclass(frozen=True, slots=True)
class NucRun:
    """What one run of the Nuc scenario left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        directory: The scenario's own directory, holding the Hive's manifest and the device's.
        trail: The Hive's whole trail at the end, oldest first, the device's merged segment in it.
        tap: Every frame the Queen's door heard from the device's Warden, and every order she sent.
        long: The task that was running at Level 0 when the operator colonized the device.
        ordinary: The task whose Drone thinks on the device's own model.
        exacting: The task whose bar only a shared source clears.
        cut_at: When the cable was pulled.
        back_at: When it was put back.
        queued: What the device's outbox held, as (kind, task), the moment before it came back.
        heard_while_cut: How many events of the device's own the Hive's trail held at that moment.
        recorded_while_cut: The ids of the events the device's Warden recorded while the cable was
            out, read from the device's own file once everything had come back.
    """

    node: SwarmNode
    directory: Path
    trail: tuple[PheromoneEvent, ...]
    tap: Tap
    long: str
    ordinary: str
    exacting: str
    cut_at: datetime
    back_at: datetime
    queued: tuple[tuple[str, str | None], ...]
    heard_while_cut: int
    recorded_while_cut: frozenset[str]


def run_nuc_scenario(image: str, directory: Path) -> NucRun:
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
        shared = scenario_of(shared_scenario(SHARED_ROUNDS, SHARED_SLEEP_S, REPORT_FILE))
        local = local_scenario(LOCAL_ROUNDS, LOCAL_SLEEP_S, REPORT_FILE)
        with shared_model(network, shared) as shared_url:
            options = colonizable(directory, network, shared_url, local)
            terms = BridgeTerms({"unreachable_grace_s": GRACE_S}, tables=LADDER + hive_tables())
            script = Script(busy_turn(*LONG_COMMAND))
            # Built here, before any loop runs: opening the Hive's stores starts a loop of its own.
            hive = bridge_hive(directory, network, script.responder, terms)
            stand = _Stand(hive, directory, network, script, options)
            return asyncio.run(_scenario(janitor, image, stand))
    finally:
        janitor.close()


@dataclass(frozen=True, slots=True)
class _Stand:
    """What one run is made of before it starts: the Hive, its model, the cable and the device."""

    hive: Hive
    directory: Path
    network: Network
    script: Script
    options: DeviceOptions


async def _scenario(janitor: Janitor, image: str, stand: _Stand) -> NucRun:
    """Start the Hive, enrol the device, and climb; a failure carries the device's logs."""
    async with run_hive(stand.hive):
        record = tap(stand.hive)
        device = await enrol_device(BridgeHive(stand.hive), janitor, image, stand.options)
        scene = _Scene(stand.hive, stand.directory, stand.network, stand.script, device, record)
        try:
            return await scene.climb()
        except Exception as error:
            error.add_note(await asyncio.to_thread(device_logs, device.container))
            raise


@dataclass
class _Scene:
    """The Hive, the device and the moments of one run, with a method for each step of the story."""

    hive: Hive
    directory: Path
    network: Network
    script: Script
    device: ContainerDevice
    record: Tap
    goal: TaskId | None = None  # The goal of the two-task plan, once submitted.

    @property
    def node(self) -> SwarmNode:
        """The device's node."""
        return self.device.node

    @property
    def container(self) -> Container:
        """The device's container."""
        return self.device.container

    async def climb(self) -> NucRun:
        """Tell the story in order and return its record."""
        idle = lambda: warden_is_idle(self.hive, self.node)  # noqa: E731 - named for the wait
        await until("the Warden to start and idle", idle, UP_S)
        long = await self._start_long_task()
        await self._colonize(long)
        await self._promote_to_nuc()
        ordinary, exacting = await self._submit_the_goal()
        cut_at = self.hive.clock.now()
        await asyncio.to_thread(self.network.disconnect, self.container)
        queued, heard = await self._wait_cut_off(ordinary, exacting, cut_at)
        back_at = self.hive.clock.now()
        await asyncio.to_thread(self.network.connect, self.container)
        await self._wait_back(back_at)
        ids = await asyncio.to_thread(device_event_ids, self.container)
        recorded = frozenset(event for event, at in ids.items() if cut_at <= at < back_at)
        await self._order_models()
        return NucRun(
            node=self.node,
            directory=self.directory,
            trail=tuple(await whole_trail(self.hive)),
            tap=self.record,
            long=long,
            ordinary=ordinary,
            exacting=exacting,
            cut_at=cut_at,
            back_at=back_at,
            queued=queued,
            heard_while_cut=heard,
            recorded_while_cut=recorded,
        )

    async def _wish(self) -> None:
        """Say `hive swarm promote NODE`: the operator's wish for the device's next rung."""
        result = await swarm(self.directory / "hive.toml", "promote", self.node.device_id)
        assert result.exit_code == 0, result.stderr

    async def _start_long_task(self) -> str:
        """Put a task to the Hive and wait until its Drone is running commands at Level 0."""
        goal = await submit_pinned(self.hive, self.node)

        async def running() -> bool:
            calls = llm_calls(await whole_trail(self.hive), goal_id=str(goal))
            return len(calls) >= LEVEL0_CALLS

        await until("the long task's Drone to run at Level 0", running, LEVEL0_S)
        return str(goal)

    async def _colonize(self, long: str) -> None:
        """Wish for Level 1, and wait for the Warden to move and the task it carried to end."""
        await self._wish()
        moved = lambda: promoted_to(self.hive, "COLONIZED")  # noqa: E731 - named for the wait
        await until("the Warden to move onto the device", moved, COLONIZE_S)
        ended = lambda: goal_has_ended(self.hive, TaskId(long))  # noqa: E731
        await until("the long task to end", ended, LONG_S)

    async def _promote_to_nuc(self) -> None:
        """Wish for Level 2, and wait for the server to be up and the hosting plan written."""
        await self._wish()
        promoted = lambda: promoted_to(self.hive, "NUC")  # noqa: E731 - named for the wait
        await until("the device to become a Nuc", promoted, NUC_S)
        planned = lambda: has_event(self.hive, "forage.plan_written", revision=1)  # noqa: E731
        await until("the hosting plan to be written", planned, NUC_S)

    async def _submit_the_goal(self) -> tuple[str, str]:
        """Put the two-task goal to the Queen and wait until the ordinary Drone uses the device."""
        self.script.plan = exacting_plan()  # The Queen's next planning call gives the two tasks.
        self.goal = goal = await submit_pinned(self.hive, self.node, GOAL)

        async def planned() -> bool:
            return len(await tasks_of(self.hive, goal)) == 2

        await until("both tasks to be planned", planned, PLAN_S)
        tasks = await tasks_of(self.hive, goal)
        ordinary, exacting = tasks[ORDINARY_TITLE], tasks[EXACTING_TITLE]
        await until(
            "the ordinary task to think on the device's model",
            lambda: self._local(ordinary),
            MID_TASK_S,
        )
        return ordinary, exacting

    async def _local(self, task: str) -> bool:
        """Whether `task`'s Drone has made two calls on the device's own model."""
        trail = await whole_trail(self.hive)
        grant = grant_of(trail, task)
        calls = llm_calls(trail, grant_id=grant, provider=LOCAL_PROVIDER) if grant else []
        return len(calls) >= 2

    async def _wait_cut_off(
        self, ordinary: str, exacting: str, cut_at: datetime
    ) -> tuple[tuple[tuple[str, str | None], ...], int]:
        """Wait out the cut: the Warden notices, and the device finishes alone and queues debts."""
        await until("the Nuc's Warden to notice the cut", lambda: self._noticed(cut_at), NOTICE_S)
        owed: set[tuple[str, str | None]] = {
            ("task.result", ordinary),
            ("forage.request", exacting),
        }
        await until(
            "the result and the request to be queued", lambda: self._queued(owed), CUT_WORK_S
        )
        queued = tuple(await asyncio.to_thread(device_outbox, self.container))
        trail = await whole_trail(self.hive)
        heard = [e for e in trail if e.node_id == self.node.node_id and e.at >= cut_at]
        return queued, len(heard)

    async def _noticed(self, cut_at: datetime) -> bool:
        """Whether the device's Warden has recorded going offline since the cable was pulled."""
        events = await asyncio.to_thread(device_events, self.container, "warden.offline")
        return any(_moment(event) >= cut_at for event in events)

    async def _queued(self, owed: set[tuple[str, str | None]]) -> bool:
        """Whether the device's outbox holds everything in `owed`."""
        return owed <= set(await asyncio.to_thread(device_outbox, self.container))

    async def _wait_back(self, back_at: datetime) -> None:
        """Wait for the Warden to report its return and for both tasks of the goal to end."""
        assert self.goal is not None
        goal = self.goal
        back = lambda: has_event(self.hive, "warden.reconnected", back_at, ceilings=True)  # noqa: E731
        await until("the Warden to report its return", back, BACK_S)
        await until("both tasks to end", lambda: goal_has_ended(self.hive, goal), ENDED_S)

    async def _order_models(self) -> None:
        """Send the Warden the orders of `ORDERS`, and wait for its refusal to reach the trail."""
        asked = self.hive.clock.now()
        for action, models in ORDERS:
            server = None if action is NucAction.DEMOTE else SERVER_ID
            await order(self.hive, self.node, action, server, models)
        refused = lambda: has_event(self.hive, "guard.denied", asked, point="slot_binding")  # noqa: E731
        await until("the refusal to reach the Hive's trail", refused, SHIP_S)


def _moment(event: dict[str, object]) -> datetime:
    """The time an event body says it was recorded at."""
    return datetime.fromisoformat(str(event["at"]))
