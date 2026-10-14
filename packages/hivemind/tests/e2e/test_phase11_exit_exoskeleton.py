"""Phase 11 step 11.11 on a second machine: a colonized device's Warden drives its own display.

"Through `PollenSession` the X11 backends from phase 6 work on a Linux device unchanged; on a Nuc
they run locally under the device's Warden." The first half is `tests/integration/
test_exoskeleton_on_device.py`, where the Hive Stand drives a device's display through its packet.
This is the second half: a task that needs a display is put to a device whose Warden has moved onto
it (Level 1, the rung a Nuc stands on), and it is that Warden, with nothing between it and the
machine, that starts the display, equips its Drone with it and takes it down again.

The device is a container whose image carries an X server, enrolled on a whole Hive (the real Queen,
gate, Wardens and trail) and colonized by the operator's own `hive swarm promote`. The goal's
ceiling names the device's Cell and no other, so the Queen can place the task nowhere else.
`e2e.swarm.colony.desktop` tells the story once: the Drone's model clicks, presses a key and writes
a report, and is parked after the key so that the device can be looked at with its display up. Each
test below is one sentence of the criterion, asserted over the record that run left, so one run of
about a minute proves all of them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one; the first run builds the device image variant with an X server (a few
    minutes, shared with the integration test). The waits are the Hive's own beats, none of them a
    sleep that hopes.

Key invariants:
    - The claims are about what ran where and what was left, never about a number of seconds.
    - The display is looked at from inside the container, the way an application on it would.
    - Every container and network is removed, whatever the run did.

See Also:
    - .claude/roadmap.md step 11.11, and phase 6 (the Exoskeleton, ADR-0031).
    - docs/adr/0054 for what an access level lets a bee on a device do.
    - tests/e2e/swarm/colony/desktop.py for the run and the reads used here.
"""

from __future__ import annotations

import pytest
from e2e.phase6_exit_helpers import assert_no_screenshot_bytes, payloads_text
from e2e.swarm.colony.desktop import (
    CLICK_AT,
    FONT_CACHE,
    X11_DIR,
    DesktopRun,
    run_desktop_scenario,
)
from e2e.swarm.colony.trail import about, after, named
from e2e.swarm.enrolled import DATA_DIR
from e2e.swarm.image import X11_PACKAGES, device_image_or_skip
from e2e.swarm.snapshots import unexplained

pytestmark = pytest.mark.e2e

_DISPLAY_PROGRAMS = frozenset({"Xvfb", "openbox"})  # What a lease's own desktop is made of.


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> DesktopRun:
    """Tell the whole story once; every test below asserts over what it left behind."""
    image = device_image_or_skip(X11_PACKAGES)
    return run_desktop_scenario(image, tmp_path_factory.mktemp("desktop"))


def test_the_task_that_needs_a_display_is_placed_on_the_device_and_succeeds_once(
    run: DesktopRun,
) -> None:
    mine = about(run.trail, run.task)
    [assigned] = named(mine, "task.assigned")
    [done] = named(mine, "task.succeeded")

    # The Queen put it on the device's Cell and its Warden (her ceiling left her no other Cell),
    # and it succeeded once: the Warden found the report the Drone wrote on the device.
    assert (assigned.payload["cell_id"], assigned.payload["warden_id"]) == (
        run.node.cell_id,
        run.node.warden_id,
    )
    assert assigned.at < done.at
    assert not named(run.trail, "task.failed", "task.cancelled", "guard.denied")


def test_the_display_is_the_devices_own_and_the_drones_click_landed_on_it(run: DesktopRun) -> None:
    # With the Drone's click and key made and its report not yet written, the device's own process
    # table holds the desktop, and the pointer of that display is where the Drone clicked.
    assert run.during.programs >= _DISPLAY_PROGRAMS
    assert run.during.pointer == CLICK_AT


def test_the_wardens_bee_ran_on_the_device_and_nothing_went_through_its_packet(
    run: DesktopRun,
) -> None:
    [attached] = run.attached
    [detached] = run.detached
    [moved] = [
        e for e in named(run.trail, "swarm.promoted") if e.payload["to_level"] == "COLONIZED"
    ]

    # The device's Warden recorded the attach and the detach itself, in its own trail: a display of
    # the lease's own (Xvfb and a window manager) that it started and stopped, and from the moment
    # it moved onto the device the Hive Stand sent the packet no command at all.
    assert not after(named(run.trail, "swarm.command_sent"), moved.at)
    assert attached.display == "LEASE"
    assert attached.peripherals == ("compound_eye", "antennae")
    assert attached.processes == len(_DISPLAY_PROGRAMS)
    assert detached.still_running == 0
    assert detached.stopped == attached.processes


def test_what_the_drone_did_on_the_display_was_recorded_by_the_wardens_flight_recorder(
    run: DesktopRun,
) -> None:
    # The Warden opened one recording for the task, and kept the frames in its own memory: Bee
    # Bread holds the reference an episode reaches them by, and no frame is on the Hive's trail.
    assert run.recordings == 1
    assert_no_screenshot_bytes(payloads_text(run.trail), "the Hive's trail")


def test_the_device_is_left_as_found(run: DesktopRun) -> None:
    made = sorted(run.after.changed - run.before.changed)

    # No display process is left, the lease's scratch is gone, the operator's directories are as
    # they were, and the container's layer gained nothing outside the packet's own directory but
    # what shared space explains: the empty directory an X server leaves behind, and the system
    # font cache the first program to draw text refreshes.
    assert run.after.processes == run.before.processes
    assert not (_DISPLAY_PROGRAMS & set(run.after.processes.values()))
    assert run.after.scratch == ()
    assert run.after.files == run.before.files
    assert unexplained(made, DATA_DIR, X11_DIR, FONT_CACHE) == []
