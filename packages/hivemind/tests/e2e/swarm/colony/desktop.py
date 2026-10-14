"""Put a task that needs a display to a colonized device, and look at the device while it works.

Roadmap step 11.11 says the Exoskeleton's X11 backends "work on a Linux device unchanged; on a Nuc
they run locally under the device's Warden". The first half is proved across the wire by
`tests/integration/test_exoskeleton_on_device.py`, where the Hive Stand drives the device's display
through its packet. This is the second half as one story told about one machine: a container whose
image carries an X server is enrolled on a whole Hive, the operator moves its Warden onto it
(`hive swarm promote`), and a goal whose only Cell is the device, and whose task needs a display, is
put to the Queen. The Warden that takes the task is the device's own, so it is the one that has to
start Xvfb and a window manager, through its own session, for the scripted Drone to click and press
a key on. The Drone's model is parked between its second and third round, the one moment with the
display up and the work not yet done: the test looks into the container then (its process table,
and where the display's pointer is) and lets the Drone go on to write its report.
`run_desktop_scenario` tells the story and returns a `DesktopRun`, the record
`tests/e2e/test_phase11_exit_exoskeleton.py` makes its claims over.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 Exoskeleton
    scenario; builds on the rest of `e2e.swarm.colony`, `e2e.swarm.bridge`, `e2e.swarm.drone` and
    the operator's own `hive swarm promote`.

Key invariants:
    - The only wish the operator makes is the real command (`hive swarm promote`); everything after
      it is the running Hive acting on its own beat.
    - The display is looked at from inside the container with the cookie the lease wrote, the way
      an application on the device would reach it, never through anything of the Hive's.
    - The parked model call is released well inside the provider's timeout, so the Drone's call
      is answered and not retried on another model.
    - Every wait is bounded and named, a failure carries the device's own logs, and every
      container and network is removed.

See Also:
    - tests/e2e/test_phase11_exit_exoskeleton.py for the claims made over the record.
    - tests/integration/test_exoskeleton_on_device.py for the same backends driven from the Stand.
    - e2e/swarm/colony/run.py for the Nuc story this one is shaped like.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from e2e.scripted_openai import Scenario, Step
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.colony.device import (
    STATE_DIR,
    colonizable,
    device_events,
    device_logs,
    shared_model,
)
from e2e.swarm.colony.models import RUNTIME_PYTHON, hive_tables, local_scenario
from e2e.swarm.colony.waits import goal_has_ended, promoted_to, tasks_of, until, whole_trail
from e2e.swarm.container import Container, Janitor
from e2e.swarm.drone import Script, command_turn, pinned_ceiling, plan_for
from e2e.swarm.enrolled import ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import REPORT_FILE, warden_is_idle
from e2e.swarm.operator import swarm
from e2e.swarm.snapshots import (
    WrittenFile,
    changed_paths,
    files_under,
    operator_files,
    settled_to,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode
from waggle.ids import TaskId

CLICK_AT = (400, 300)  # Where the Drone clicks: well inside the 1280x800 screen.
KEYS = "a"  # The chord the Drone presses after the click.
GUI_ROUNDS = 2  # The click and the key: the rounds the Drone has made when its model parks.
SCRATCH_ROOT = f"{STATE_DIR}/scratch"  # Where the on-device Warden's leases keep their scratch.
X11_DIR = "/tmp/.X11-unix"  # noqa: S108  # SAFETY: a path inside the device, never one on this host.
# The system font cache. A window manager that runs as root rewrites a font cache that the
# container's layers left stale, the first time it draws text: shared space, not the lease's, and
# not particular to a device's Warden. It is the one write outside the packet's directory that the
# scenario explains instead of forbidding.
FONT_CACHE = "/var/cache/fontconfig"
# The ladder wants some free memory before it moves a Warden onto a device, and a container reports
# the host's: this scenario is about the display, not the host.
LADDER = "[swarm.ladder]\nmin_free_memory_bytes = 0\n"
GRACE_S = 900.0  # How long the Queen holds a lost device before she judges it: past the scenario.
GOAL = "click on the borrowed device's display and write a report"
# Bounds, in real seconds. Each is several times what the step took when the scenario was written
# (the whole run is about a minute), so a busy machine slows the story and does not fail it.
UP_S, COLONIZE_S, REST_S, PLAN_S = 90.0, 300.0, 60.0, 60.0
PARK_S, GOAL_S, SETTLE_S = 120.0, 120.0, 30.0
_SEPARATOR = "@@"  # Between the two halves of the look's one command.
# Every command line in the container, then the X servers' sockets: what `_display_of` reads.
_LOOK = f"ps -eo args=; echo {_SEPARATOR}; ls {X11_DIR} 2>/dev/null; true"
_POINTER = re.compile(r"x:(\d+) y:(\d+)")  # `xdotool getmouselocation`: "x:400 y:300 screen:0 ...".
_DISPLAY_SOCKET = re.compile(r"^X(\d+)$")  # An X server's socket in /tmp/.X11-unix is `X<number>`.
# The device's own Bee Bread, read-only: how many flight recordings its Warden has opened (6.6).
_RECORDINGS_READER = (
    "import sqlite3, sys\n"
    "db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)\n"
    "query = \"select count(*) from memory_bee_bread where kind = 'RECORDING'\"\n"
    "print(db.execute(query).fetchone()[0])\n"
    "db.close()\n"
)

__all__ = [
    "CLICK_AT",
    "FONT_CACHE",
    "KEYS",
    "SCRATCH_ROOT",
    "X11_DIR",
    "Attached",
    "DesktopRun",
    "Detached",
    "Display",
    "Rest",
    "desktop_ceiling",
    "run_desktop_scenario",
]


class Rest(NamedTuple):
    """The device with nothing of the Hive's leased on it: what 'as found' is measured against."""

    processes: dict[int, str]  # The container's process table.
    scratch: tuple[str, ...]  # What the Warden's leases hold in scratch: nothing at rest.
    files: dict[str, WrittenFile | str]  # The operator's directories, less the X server's own.
    changed: frozenset[str]  # `docker diff`: everything the container's layer has had written.
    released: int  # How many leases the Warden has given back, by its own trail.


class Display(NamedTuple):
    """The device with the Warden's display up: what an application on it could see."""

    programs: frozenset[str]  # The command names in the container's process table.
    pointer: tuple[int, int] | None  # Where the display's pointer is; None if it could not be read.


class Attached(NamedTuple):
    """One `cell.exoskeleton_attached` event of the device Warden's own trail."""

    display: str  # Where the display came from: a lease's own, the Cell's running one, or none.
    peripherals: tuple[str, ...]  # The peripherals attached, by name.
    processes: int  # How many programs the attach started.


class Detached(NamedTuple):
    """One `cell.exoskeleton_detached` event of the device Warden's own trail."""

    stopped: int  # The programs the detach stopped.
    still_running: int  # The programs that would not stop: a leak.


@dataclass(frozen=True, slots=True)
class DesktopRun:
    """What one run of the scenario left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        task: The one task of the goal, whose Drone used the display.
        trail: The Hive's whole trail at the end, oldest first.
        before: The device at rest once its Warden had moved on and given its lease back.
        during: The device with the display up, between the Drone's second and third round.
        after: The device at rest again once the task had ended.
        attached: The device Warden's own `cell.exoskeleton_attached` events, as it recorded them.
        detached: Its `cell.exoskeleton_detached` events.
        recordings: How many flight recordings the Warden opened, as its own Bee Bread says.
    """

    node: SwarmNode
    task: str
    trail: tuple[PheromoneEvent, ...]
    before: Rest
    during: Display
    after: Rest
    attached: tuple[Attached, ...]
    detached: tuple[Detached, ...]
    recordings: int


def run_desktop_scenario(image: str, directory: Path) -> DesktopRun:
    """Run the whole story on a fresh container and Hive, and clean up after it.

    Args:
        image: The device image with an X server (`device_image_or_skip(X11_PACKAGES)`).
        directory: The scenario's own directory; its files are written here.

    Returns:
        The record of the run.
    """
    janitor = Janitor()
    try:
        network = janitor.network()
        model = _desktop_model()
        with shared_model(network, model) as shared_url:
            local = local_scenario(1, 1, REPORT_FILE)  # The device's own server is never started.
            options = colonizable(directory, network, shared_url, local)
            terms = BridgeTerms({"unreachable_grace_s": GRACE_S}, tables=LADDER + hive_tables())
            # The Queen plans the one task that needs a display; the Drone thinks on `model`.
            script = Script(command_turn(("true",)), plan=plan_for({"exoskeleton": True}))
            # Built here, before any loop runs: opening the Hive's stores starts a loop of its own.
            hive = bridge_hive(directory, network, script.responder, terms)
            story = _Story(hive, directory, model, options)
            return asyncio.run(_scenario(janitor, image, story))
    finally:
        janitor.close()


def desktop_ceiling(cell_id: str) -> tuple[str, ...]:
    """The capabilities a goal may hold: the device's Cell and no other, and a lease's display.

    Args:
        cell_id: The device's Cell.

    Returns:
        The goal ceiling `pinned_ceiling` gives a Drone on that Cell, plus `exoskeleton:display`,
        which a sub-bee is granted only where the goal's own set allows it too.
    """
    return (*pinned_ceiling(cell_id), "exoskeleton:display")


def _desktop_model() -> Scenario:
    """The Drone's model: click, press a key, then write the report; parked before the report."""
    steps = (
        Step("click", {"x": CLICK_AT[0], "y": CLICK_AT[1]}),
        Step("press", {"keys": KEYS}),
        Step("write_file", {"path": REPORT_FILE, "content": "done"}),
    )
    model = Scenario(steps=steps, closing="Clicked, pressed a key and wrote the report.")
    # The call that follows the two GUI rounds waits until the test has looked at the display.
    model.switches.hold_at = GUI_ROUNDS
    return model


@dataclass
class _Story:
    """The Hive, the model and the device's terms of one run, with a method for each step."""

    hive: Hive
    directory: Path
    model: Scenario
    options: DeviceOptions
    device: ContainerDevice | None = None  # Set once the container has enrolled.

    @property
    def node(self) -> SwarmNode:
        """The device's node."""
        assert self.device is not None
        return self.device.node

    @property
    def container(self) -> Container:
        """The device's container."""
        assert self.device is not None
        return self.device.container

    async def tell(self) -> DesktopRun:
        """Tell the story in order and return its record."""
        idle = lambda: warden_is_idle(self.hive, self.node)  # noqa: E731 - named for the wait
        await until("the Hive Stand's Warden for the device to start and idle", idle, UP_S)
        await self._colonize()
        before = await self._at_rest(0)
        goal, task = await self._put()
        during = await self._look()
        await until("the task to end", lambda: goal_has_ended(self.hive, goal), GOAL_S)
        after = await self._at_rest(before.released + 1, before.processes)
        return DesktopRun(
            node=self.node,
            task=task,
            trail=tuple(await whole_trail(self.hive)),
            before=before,
            during=during,
            after=after,
            attached=tuple(_attached(e) for e in await self._events("cell.exoskeleton_attached")),
            detached=tuple(_detached(e) for e in await self._events("cell.exoskeleton_detached")),
            recordings=await asyncio.to_thread(_recordings, self.container),
        )

    async def _colonize(self) -> None:
        """Say `hive swarm promote NODE`, and wait for the Warden to be on the device."""
        result = await swarm(self.directory / "hive.toml", "promote", self.node.device_id)
        assert result.exit_code == 0, result.stderr
        moved = lambda: promoted_to(self.hive, "COLONIZED")  # noqa: E731 - named for the wait
        await until("the Warden to move onto the device", moved, COLONIZE_S)

    async def _at_rest(self, released: int, processes: Mapping[int, str] | None = None) -> Rest:
        """Wait until the device's Warden has given back `released` leases, then measure the device.

        Args:
            released: The leases the Warden must have given back by now, by its own trail: a
                Warden that has just started has leased the machine and returns it once it finds
                nothing to do, and a task is one more lease.
            processes: The process table to wait for, when the device must be back to one.
        """
        container = self.container

        async def given_back() -> bool:
            count = len(await self._events("cell.released"))
            return count >= max(released, 1) and not await asyncio.to_thread(
                files_under, container, SCRATCH_ROOT
            )

        await until("the device's Warden to hold no lease", given_back, REST_S)
        if processes is not None:
            table = await settled_to(container, dict(processes), SETTLE_S)
            assert table == processes, f"the device's processes did not settle: {table}"
        return await asyncio.to_thread(
            _measure, container, len(await self._events("cell.released"))
        )

    async def _put(self) -> tuple[TaskId, str]:
        """Put the goal to the Queen, its ceiling naming the device and a display; plan it."""
        ceiling = desktop_ceiling(self.node.cell_id)
        goal = await self.hive.queen.submit_goal(
            GOAL, clearance=HoneyClearance.C1, capabilities=ceiling
        )

        async def planned() -> bool:
            return bool(await tasks_of(self.hive, goal))

        await until("the goal to be planned", planned, PLAN_S)
        [task] = (await tasks_of(self.hive, goal)).values()
        return goal, task

    async def _look(self) -> Display:
        """Wait for the Drone's third call to park, look at the display, and let the Drone go on."""
        switches = self.model.switches
        await until("the Drone to have used the display", switches.arrived.is_set, PARK_S)
        try:
            return await asyncio.to_thread(_display_of, self.container)
        finally:
            switches.release.set()  # Whatever the look found, the Drone is not kept waiting.

    async def _events(self, kind: str) -> tuple[Mapping[str, object], ...]:
        """The events of `kind` in the device Warden's own trail, as it recorded them."""
        return tuple(await asyncio.to_thread(device_events, self.container, kind))


async def _scenario(janitor: Janitor, image: str, story: _Story) -> DesktopRun:
    """Start the Hive, enrol the device and tell the story; a failure carries the device's logs."""
    async with run_hive(story.hive):
        story.device = await enrol_device(BridgeHive(story.hive), janitor, image, story.options)
        try:
            return await story.tell()
        except Exception as error:
            error.add_note(await asyncio.to_thread(device_logs, story.container))
            raise


def _attached(event: Mapping[str, object]) -> Attached:
    """Read an attach event's payload: names and counts, as the trail records them."""
    payload = _payload_of(event)
    names = payload["peripherals"]
    assert isinstance(names, list), "an attach names its peripherals in a list"
    return Attached(
        str(payload["display"]), tuple(str(n) for n in names), _count(payload, "processes")
    )


def _detached(event: Mapping[str, object]) -> Detached:
    """Read a detach event's payload: how many programs stopped and how many would not."""
    payload = _payload_of(event)
    return Detached(_count(payload, "stopped"), _count(payload, "still_running"))


def _payload_of(event: Mapping[str, object]) -> Mapping[str, object]:
    """The payload of an event body read from the device's own trail file."""
    payload = event["payload"]
    assert isinstance(payload, dict), "an event's payload is a JSON object"
    return payload


def _count(payload: Mapping[str, object], key: str) -> int:
    """Read one count out of an event's payload."""
    value = payload[key]
    assert isinstance(value, int), f"{key} is a count"
    return value


def _display_of(container: Container) -> Display:
    """Look into the container with the display up: its programs, and where its pointer is.

    Two commands and no more, because the Drone's model call is parked for as long as the look
    takes and its provider gives up on it after `timeout_s` (the device manifest's fifteen).
    """
    seen = container.exec(["sh", "-c", _LOOK]).stdout
    listing, _, sockets = seen.partition(f"{_SEPARATOR}\n")
    rows = listing.splitlines()
    programs = frozenset(PurePosixPath(row.split()[0]).name for row in rows if row.split())
    xvfb = next((shlex.split(row) for row in rows if row.startswith("Xvfb ")), None)
    if xvfb is None:
        return Display(programs, None)
    # The cookie the lease wrote is on Xvfb's own command line, and its socket names the display.
    authority = xvfb[xvfb.index("-auth") + 1]
    numbers = [found.group(1) for name in sockets.split() if (found := _DISPLAY_SOCKET.match(name))]
    if not numbers:
        return Display(programs, None)
    asked = ["env", f"DISPLAY=:{numbers[0]}", f"XAUTHORITY={authority}"]
    shown = container.exec([*asked, "xdotool", "getmouselocation"], check=False)
    located = _POINTER.search(shown.stdout)
    return Display(programs, (int(located[1]), int(located[2])) if located else None)


def _measure(container: Container, released: int) -> Rest:
    """Measure the device: processes, the Warden's scratch, the operator's files, the layer."""
    files = {
        path: entry
        for path, entry in operator_files(container).items()
        if path != X11_DIR and not path.startswith(f"{X11_DIR}/")
    }
    return Rest(
        processes=container.processes(),
        scratch=tuple(files_under(container, SCRATCH_ROOT)),
        files=files,
        changed=frozenset(changed_paths(container)),
        released=released,
    )


def _recordings(container: Container) -> int:
    """How many flight recordings the Warden on the device has opened, from its own Bee Bread."""
    path = f"{STATE_DIR}/warden.sqlite"
    shown = container.exec([RUNTIME_PYTHON, "-c", _RECORDINGS_READER, path], check=False)
    return int(shown.stdout.strip()) if shown.exit_code == 0 else 0
