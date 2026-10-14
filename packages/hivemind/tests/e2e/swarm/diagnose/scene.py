"""Put a container device on the ladder and in front of a story: a whole Hive, one machine.

The diagnosis scenarios of phase 11's last exit criterion differ in what is done to the device and
in what is asserted of it, and agree on everything before: a whole Hive on its own docker network
(the real Queen, gate, Wardens and trail, its models scripted), a container enrolled on it with the
Hive runtime to install and a manifest for its Warden, the operator's own `hive swarm promote` once
for a colonized device and twice for a Nuc, and a task the Hive has pinned to that device and whose
Drone is at work. `run_case` builds all of that from a `Setup`, hands a `Scene` to the case's story
and cleans up whatever the story did; the story is an `async` function of the scene that returns
the record its test asserts over. The cases differ in `Setup` only by the rounds the two scripted
models make, the `[supervision]` table the device's manifest carries and how long the Queen holds a
lost device before she decides.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 diagnosis
    scenarios; builds on `e2e.swarm.bridge`, `e2e.swarm.colony` and `e2e.swarm.drone`.

Key invariants:
    - The Hive is built before any event loop runs, because opening its stores starts one of its
      own (`e2e.swarm.bridge.bridge_hive`); `run_case` is therefore a plain function.
    - The operator's wishes are the real command (`hive swarm promote`); nothing here moves a
      Warden by hand, and every wait is bounded and named.
    - A story that fails carries the device's own logs as a note on its error.
    - Every container and network is removed, whatever the story did.

See Also:
    - e2e/swarm/colony/run.py for the longer story this was cut from (the Nuc's cable).
    - tests/e2e/test_phase11_exit_diagnose.py for the stories told with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from e2e.scripted_openai import Scenario
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.colony.device import colonizable, device_logs, shared_model
from e2e.swarm.colony.models import hive_tables
from e2e.swarm.colony.trail import grant_of, llm_calls
from e2e.swarm.colony.waits import has_event, promoted_to, tasks_of, until, whole_trail
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.diagnose.scripts import stand_check
from e2e.swarm.drone import Script, busy_turn, is_check_of_a_lost_device, submit_pinned
from e2e.swarm.enrolled import ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import warden_is_idle
from e2e.swarm.operator import swarm

from hivemind.cli.compose import Hive, run_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId

# The ladder wants some free memory before it moves a Warden onto a device, and a container reports
# the host's, which a busy machine may not have: these scenarios are about the cut, not the host.
LADDER = "[swarm.ladder]\nmin_free_memory_bytes = 0\n"
UP_S, CLIMB_S, PLAN_S, TASK_S = 90.0, 300.0, 30.0, 120.0  # Real seconds each step may take.
TASK_CALLS = (
    1  # Model calls the task's Drone has made when it counts as at work: its first command runs.
)

__all__ = ["LADDER", "Scene", "Setup", "run_case"]


@dataclass(frozen=True, slots=True)
class Setup:
    """What one case is made of before it starts (codingrules 5.1's argument group).

    Attributes:
        local: The rounds the device's own model server makes (`e2e.swarm.diagnose.scripts`), given
            the address of the Hive Stand's end of the cable; only a Nuc ever asks it.
        shared: The rounds of the model on the Hive Stand's side of the cable, which a colonized
            device's Drone thinks on until it has a model of its own.
        supervision: The `[supervision]` table of the device's manifest: how long its Warden waits
            for the Queen and what it may spend finding out why.
        grace_s: How long the Queen holds a lost device's tasks before she decides (`[swarm]
            unreachable_grace_s`).
        nuc: Whether the Hive lists the Nuc's model server, which a device must be a Nuc to use.
    """

    local: Callable[[str], dict[str, object]]
    shared: Scenario
    supervision: str
    grace_s: float
    nuc: bool


@dataclass
class Scene:
    """The Hive, the device and the cable of one case, with a method for each shared step."""

    hive: Hive
    directory: Path
    network: Network
    device: ContainerDevice
    goal: TaskId | None = None  # The goal `put_task` submitted, once it has.

    @property
    def node(self) -> SwarmNode:
        """The device's node."""
        return self.device.node

    @property
    def container(self) -> Container:
        """The device's container."""
        return self.device.container

    async def up(self) -> None:
        """Wait until the device's Warden is running at Level 0 and idle."""
        idle = lambda: warden_is_idle(self.hive, self.node)  # noqa: E731 - named for the wait
        await until("the Warden to start and idle", idle, UP_S)

    async def climb(self, level: str) -> None:
        """Wish for the next rung as the operator does; wait for the Queen to put the device on it.

        Args:
            level: `COLONIZED` for the first rung, `NUC` for the second.
        """
        wished = self.hive.clock.now()
        wish = await swarm(self.directory / "hive.toml", "promote", self.node.device_id)
        assert wish.exit_code == 0, wish.stderr
        await until(f"the device to reach {level}", lambda: promoted_to(self.hive, level), CLIMB_S)
        if level == "NUC":
            # The plan that hosts the model's sources is written as the rung is reached; a task
            # bound before it would think elsewhere. (Its revision is 0 on a Hive with no task yet.)
            written = lambda: has_event(self.hive, "forage.plan_written", wished)  # noqa: E731
            await until("the hosting plan to be written", written, CLIMB_S)

    async def put_task(self, provider: str) -> str:
        """Put a one-task goal to the Queen, pinned to the device; wait until its Drone is at work.

        Args:
            provider: The provider the Drone should be thinking on: the device's own for a Nuc,
                the shared one for a colonized device.

        Returns:
            The task's id.
        """
        self.goal = goal = await submit_pinned(self.hive, self.node)

        async def planned() -> bool:
            return bool(await tasks_of(self.hive, goal))

        await until("the goal to be planned", planned, PLAN_S)
        [task] = (await tasks_of(self.hive, goal)).values()

        async def thinking() -> bool:
            trail = await whole_trail(self.hive)
            grant = grant_of(trail, task)
            calls = llm_calls(trail, grant_id=grant, provider=provider) if grant else []
            return len(calls) >= TASK_CALLS

        await until(f"the task's Drone to think on {provider}", thinking, TASK_S)
        return task


type Story[T] = Callable[[Scene], Awaitable[T]]


def run_case[T](image: str, directory: Path, setup: Setup, story: Story[T]) -> T:
    """Run `story` on a fresh container and Hive built from `setup`, and clean up after it.

    Args:
        image: The device image (`e2e.swarm.image.device_image_or_skip`).
        directory: The case's own directory; the manifests and the certificate are written here.
        setup: The models' rounds, the device's `[supervision]` and the Queen's grace.
        story: What is done to the device once it is enrolled, returning what a test asserts over.

    Returns:
        Whatever the story returned.
    """
    janitor = Janitor()
    try:
        network = janitor.network()
        with shared_model(network, setup.shared) as shared_url:
            local = setup.local(network.gateway)
            options = colonizable(directory, network, shared_url, local, setup.supervision)
            tables = LADDER + (hive_tables() if setup.nuc else "")
            terms = BridgeTerms({"unreachable_grace_s": setup.grace_s}, tables=tables)
            script = Script(busy_turn("sleep", "1"))
            # Built here, before any loop runs: opening the Hive's stores starts a loop of its own.
            hive = bridge_hive(directory, network, _responder(script), terms)
            return asyncio.run(_tell(janitor, image, (hive, directory, network, options), story))
    finally:
        janitor.close()


def _responder(script: Script) -> Callable[[LLMRequest], LLMResponse]:
    """The Hive's model: the rig's, except that the Hive Stand's check of a lost device is ours."""

    def respond(request: LLMRequest) -> LLMResponse:
        if is_check_of_a_lost_device(request):
            return stand_check(request)
        return script.responder(request)

    return respond


async def _tell[T](
    janitor: Janitor,
    image: str,
    parts: tuple[Hive, Path, Network, DeviceOptions],
    story: Story[T],
) -> T:
    """Start the Hive, enrol the device and tell the story; a failure carries the device's logs."""
    hive, directory, network, options = parts
    async with run_hive(hive):
        device = await enrol_device(BridgeHive(hive), janitor, image, options)
        try:
            return await story(Scene(hive, directory, network, device))
        except Exception as error:
            error.add_note(await asyncio.to_thread(device_logs, device.container))
            raise
