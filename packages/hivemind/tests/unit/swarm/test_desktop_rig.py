"""Unit: the parts of the Exoskeleton scenario's rig that need no docker daemon.

The phase 11 Exoskeleton scenario (`tests/e2e/test_phase11_exit_exoskeleton.py`) proves the rig
whole against a real container, in a minute or so. What can go wrong before the container exists
is checked here in milliseconds, on every run: that the Drone's scripted model calls tools the
Exoskeleton really offers, with the arguments those tools require, and parks where the test looks;
that the plan it is given is one the planner takes; that the goal's ceiling names the device's Cell
and a display and no other Cell; and how the test reads a container with the display up (the Xvfb
command line, the server's socket, where the pointer is), the events the Warden recorded, and what
it counts as the device's own files.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `e2e.swarm.colony.desktop`; nothing here starts a container or touches a network.

Key invariants:
    - A container is replaced by a table of canned answers, so no test needs a daemon.
    - No test sleeps.

See Also:
    - tests/unit/swarm/test_colony_rig.py for the rig this one is shaped like.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest
from e2e.swarm import snapshots
from e2e.swarm.colony.desktop import (
    CLICK_AT,
    GUI_ROUNDS,
    KEYS,
    SCRATCH_ROOT,
    X11_DIR,
    _attached,
    _desktop_model,
    _detached,
    _display_of,
    _measure,
    _recordings,
    desktop_ceiling,
)
from e2e.swarm.container import CommandResult, Container, FileEntry
from e2e.swarm.drone import plan_for
from e2e.swarm.hive import REPORT_FILE

from hivemind.exoskeleton.attach import DEFAULT_SCREEN
from hivemind.guard import Capability, CapabilitySet
from hivemind.llm import ToolDefinition
from hivemind.queen.planner.schema import PlanSchema
from hivemind.workers.tools.exoskeleton.desktop import CLICK_DEFINITION, PRESS_DEFINITION
from hivemind.workers.tools.exoskeleton.offer import ACTION_TOOL_NAMES
from hivemind.workers.tools.session import WRITE_FILE_DEFINITION
from waggle.clock import SystemClock
from waggle.ids import new_cell_id

COOKIE = "/var/lib/pollen/runtime/state/scratch/lease/exoskeleton/home/.Xauthority"
XVFB = f"Xvfb -displayfd 1 -screen 0 1280x800x24 -nolisten tcp -auth {COOKIE} -noreset"
PROCESSES = {1: "sleep", 20: "Xvfb", 21: "openbox", 30: "python"}
# `find -printf '%y %s %T@ %p'`: the rows `operator_files` reads. The X server's directory and its
# socket are the server's own; the dotfile is the operator's; the last is only named alike.
LISTING = "\n".join(
    [
        f"d 4096 1.0 {X11_DIR}",
        f"s 0 1.0 {X11_DIR}/X0",
        "f 12 5.0 /root/.bashrc",
        f"d 4096 1.0 {X11_DIR}-not-the-servers",
    ]
)


class DesktopBox:
    """Stands in for a `Container` with (or without) a display up: answers the look's commands."""

    name = "box"

    def __init__(
        self,
        *,
        arguments: Sequence[str] = ("sleep infinity", XVFB, "openbox --sm-disable"),
        sockets: str = "X0\n",
        pointer: str = "x:400 y:300 screen:0 window:7\n",
        listing: str = "",
        scratch: dict[str, FileEntry] | None = None,
    ) -> None:
        """Answer `ps` with `arguments`, `ls` with `sockets`, `xdotool` with `pointer`."""
        self.commands: list[tuple[str, ...]] = []
        self.reader: CommandResult | None = None
        self._arguments = arguments
        self._sockets = sockets
        self._pointer = pointer
        self._listing = listing
        self._scratch = scratch

    def processes(self) -> dict[int, str]:
        """The process table the test gave."""
        return dict(PROCESSES)

    def exists(self, path: str) -> bool:
        """Whether the test gave the Warden's scratch directory a tree."""
        return path == SCRATCH_ROOT and self._scratch is not None

    def file_tree(self, path: str) -> dict[str, FileEntry]:
        """The tree the test gave the scratch directory."""
        return dict(self._scratch or {})

    def exec(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        """Record the command and answer it from the table."""
        key = tuple(argv)
        self.commands.append(key)
        if key[0] == "sh":  # The look: every command line, a separator, the servers' sockets.
            lines = "\n".join(self._arguments)
            return CommandResult(key, 0, f"{lines}\n@@\n{self._sockets}", "")
        if key[0] == "env":  # xdotool, run against the display.
            return CommandResult(key, 0, self._pointer, "")
        if key[0] == "find":  # The operator's directories.
            return CommandResult(key, 0, self._listing, "")
        assert self.reader is not None, f"unexpected command {key}"
        return self.reader  # The packet's interpreter reading the Warden's Bee Bread.

    @property
    def box(self) -> Container:
        """This fake, for the functions that take a Container."""
        return cast(Container, self)


def _required(definition: ToolDefinition) -> set[str]:
    """The argument names a tool's schema requires."""
    required = definition.parameters["required"]
    assert isinstance(required, list)
    return {str(name) for name in required}


def test_the_look_reads_the_pointer_of_the_display_with_the_cookie_the_lease_wrote() -> None:
    box = DesktopBox()

    display = _display_of(box.box)

    # The display is the one the X server's socket names, reached the way an application on the
    # device would reach it: its number, and the cookie on Xvfb's own command line.
    assert display.programs == {"sleep", "Xvfb", "openbox"}
    assert display.pointer == CLICK_AT
    asked = ("env", "DISPLAY=:0", f"XAUTHORITY={COOKIE}", "xdotool", "getmouselocation")
    assert asked in box.commands


@pytest.mark.parametrize(
    ("box", "why"),
    [
        (DesktopBox(arguments=("sleep infinity",)), "no X server is running"),
        (DesktopBox(sockets=""), "the server has no socket in the shared directory"),
        (DesktopBox(sockets="README\n"), "nothing in the directory is a server's socket"),
        (DesktopBox(pointer="xdotool: cannot open display\n"), "xdotool answered something else"),
    ],
)
def test_the_look_has_no_pointer_to_report_when_it_cannot_be_read(
    box: DesktopBox, why: str
) -> None:
    display = _display_of(box.box)

    assert display.pointer is None, why
    assert "sleep" in display.programs  # The command lines are still read.


def test_a_look_takes_two_commands_because_the_drones_call_is_parked_while_it_lasts() -> None:
    box = DesktopBox()

    _display_of(box.box)

    assert [command[0] for command in box.commands] == ["sh", "env"]


def test_a_look_with_no_server_runs_nothing_against_a_display() -> None:
    box = DesktopBox(arguments=("sleep infinity",))

    _display_of(box.box)

    assert not [command for command in box.commands if command[0] == "env"]


def test_an_attach_event_is_read_as_the_peripherals_and_programs_it_names() -> None:
    payload = {"peripherals": ["compound_eye", "antennae"], "display": "LEASE", "processes": 2}

    attached = _attached({"payload": payload})

    assert (attached.display, attached.peripherals, attached.processes) == (
        "LEASE",
        ("compound_eye", "antennae"),
        2,
    )


def test_a_detach_event_is_read_as_what_stopped_and_what_would_not() -> None:
    payload = {"stopped": 2, "still_running": 0, "peripherals": ["compound_eye", "antennae"]}

    detached = _detached({"payload": payload})

    assert (detached.stopped, detached.still_running) == (2, 0)


@pytest.mark.parametrize(
    ("result", "count"),
    [
        (CommandResult(("python",), 0, "1\n", ""), 1),
        (CommandResult(("python",), 0, "0\n", ""), 0),
        (CommandResult(("python",), 1, "", "unable to open database file"), 0),
    ],
)
def test_the_recordings_are_counted_from_the_wardens_bee_bread_or_none_without_it(
    result: CommandResult, count: int
) -> None:
    box = DesktopBox()
    box.reader = result  # No state file yet is a Warden that opened no recording, not an error.

    assert _recordings(box.box) == count


def test_the_measure_leaves_out_the_x_servers_own_directory_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = DesktopBox(listing=LISTING, scratch={"lease": FileEntry("d", "700", 4096)})
    diff = "A /tmp/.X11-unix\nC /tmp"
    monkeypatch.setattr(
        snapshots, "docker", lambda *args, **options: CommandResult(("docker", *args), 0, diff, "")
    )

    rest = _measure(box.box, released=2)

    # The server's directory and socket are shared space no lease could tidy; a file named alike
    # but outside the directory, and the operator's own, are measured. Scratch is listed by name.
    assert set(rest.files) == {"/root/.bashrc", f"{X11_DIR}-not-the-servers"}
    assert rest.scratch == ("lease",)
    assert rest.changed == {"A /tmp/.X11-unix", "C /tmp"}
    assert (rest.processes, rest.released) == (PROCESSES, 2)


def test_the_drones_model_clicks_presses_and_writes_the_report_and_parks_after_the_gui() -> None:
    model = _desktop_model()
    assert model.steps is not None
    click, press, write = model.steps

    # The calls are to tools the Exoskeleton and the session really offer, with what they require.
    assert (click.tool, press.tool, write.tool) == (
        CLICK_DEFINITION.name,
        PRESS_DEFINITION.name,
        WRITE_FILE_DEFINITION.name,
    )
    assert {click.tool, press.tool} <= ACTION_TOOL_NAMES
    assert set(click.arguments) >= _required(CLICK_DEFINITION)
    assert set(press.arguments) >= _required(PRESS_DEFINITION)
    assert (click.arguments["x"], click.arguments["y"]) == CLICK_AT
    assert press.arguments["keys"] == KEYS
    assert write.arguments["path"] == REPORT_FILE
    # The point is on the screen the display is started with, and the model parks its next call
    # after exactly the two GUI rounds, which is when the test looks.
    assert 0 <= CLICK_AT[0] < DEFAULT_SCREEN.width and 0 <= CLICK_AT[1] < DEFAULT_SCREEN.height
    assert model.switches.hold_at == GUI_ROUNDS == len(model.steps) - 1


def test_the_plan_is_one_task_that_needs_a_display_and_the_planner_takes_it() -> None:
    plan = PlanSchema.model_validate(plan_for({"exoskeleton": True}))

    [task] = plan.tasks
    assert task.needs.exoskeleton is True
    assert [criterion.subject for criterion in task.acceptance] == [REPORT_FILE]


def test_the_goals_ceiling_names_the_devices_cell_and_a_display_and_no_other_cell() -> None:
    clock = SystemClock()
    cell, other = new_cell_id(clock), new_cell_id(clock)

    ceiling = CapabilitySet.parse(*desktop_ceiling(cell))

    assert ceiling.allows(Capability.parse(f"cell:real:{cell}"))
    assert not ceiling.allows(Capability.parse(f"cell:real:{other}"))
    assert ceiling.allows(Capability.parse("exoskeleton:display"))
    assert not ceiling.allows(Capability.parse("exoskeleton:real_display"))  # The operator's own.
