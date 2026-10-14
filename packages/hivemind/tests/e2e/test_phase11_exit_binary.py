"""End-to-end test of phase 11's last packaging claim: `pollen` installs on a clean Raspberry Pi OS.

Raspberry Pi OS is Debian 12 (glibc 2.36) with a desktop on top, and a device the Hive borrows has
no Python 3.12 to install the packet with, so the packet is shipped as one PyInstaller file that
`.github/workflows/pollen.yml` builds inside a Debian 12 container. This is that claim run for
real, on the x86-64 stand-in (the ARM64 leg is the workflow's own matrix). The file is built the
workflow's way by `scripts/build_pollen.py` in `python:3.12-bookworm` (kept under a key made of its
inputs, so a rerun builds nothing), then copied onto a clean `debian:bookworm` container that is
shown to have no Python, and driven as the runbook (docs/runbooks/enrolling-a-device.md) has an
operator drive it, as the unprivileged account a person logs in with and with no `--data-dir`:
`pollen version`, `pollen enrol` with an invite from the Hive Stand's door, `pollen install`, then
the command it prints, to run the packet. The Hive is a whole one (Queen, swarm gate, a Level 0
Warden for the device) listening on the docker bridge over `wss://` with a pinned self-signed
certificate; the device is ONLINE in its registry and reports itself as the Debian 12 machine it
is, and a goal pinned to its Cell is run there by a scripted Drone: a command that reports on the
machine, a file, and a read of that report, all through the `PollenSession` the Warden leased; the
Warden verifies the file on the device before the task may succeed.

It then ends the way a person takes a device back: the packet is stopped as a service manager
stops it, `pollen uninstall` removes the unit, the operator deletes the data directory and the
binary, and every file on the device is the one that was there before the binary arrived.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The binary is the one CI would ship (the workflow's commands, in its image), and the device
      is a container with no Python; nothing the packet needs is installed there.
    - The task is placed on the device's Cell alone, and what its command reported says that it
      ran on the device (its operating system and its name), not on the machine running the test,
      and in the device's environment, not the packet's: a frozen packet's own library path must
      not reach the programs it starts.
    - Everything blocking runs off the event loop that the Hive Stand shares, so the packet's
      heartbeats are never held up by the test.

See Also:
    - e2e/swarm/binary/ for the build and the clean device, and e2e/swarm/bridge.py for the Hive
      this dials, the one every phase 11 scenario on a container uses.
    - tests/e2e/test_swarm_level0.py for the same task on a packet in the test's own process.
    - .claude/phase-11-handoff.md section 5 for the exit criterion this serves.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
from e2e.kernel_helpers import assert_kinds_in_order, wait_until
from e2e.swarm import (
    BRIDGE,
    HIVE_HOSTNAME,
    CommandResult,
    FileEntry,
    Janitor,
    daemon_answers,
)
from e2e.swarm.binary import (
    INSTALL_PATH,
    OPERATOR,
    POLLEN_DIR,
    ROOT,
    UNIT_PATH,
    CleanDevice,
    build_binary,
    start_clean,
)
from e2e.swarm.bridge import bridge_hive
from e2e.swarm.drone import DeviceDrone, Script, finished, submit_pinned
from e2e.swarm.hive import enrolled_node, trail_events, warden_is_idle

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cli.compose import Hive, run_hive
from hivemind.swarm import NodeStatus, SwarmNode
from pollen.executors.info import runtime_version
from waggle.envelope import PROTOCOL_VERSION
from waggle.messages.labels import OsFamily

pytestmark = pytest.mark.e2e

_WAIT_S = 60.0  # Each wait for the Hive to see the device; a packet unpacks itself, then dials.
_TASK_S = 90.0  # A goal placed on the device: a lease, two commands and the Warden's check.
_LABEL = "clean-debian-12"
DEBIAN_12 = "Debian GNU/Linux 12 (bookworm)"  # Raspberry Pi OS's base, as /etc/os-release names it.


@pytest.fixture(scope="module")
def binary() -> Path:
    """The single-file packet built from this tree, cached by its inputs; skips with no daemon."""
    if not daemon_answers():
        pytest.skip("no Docker daemon reachable")
    return build_binary()


def test_the_single_file_packet_runs_a_task_on_a_clean_debian_12_and_leaves_nothing_behind(
    tmp_path: Path, binary: Path
) -> None:
    drone, janitor = DeviceDrone(), Janitor()
    try:
        hive = bridge_hive(tmp_path, BRIDGE, Script(drone.turn).responder)
        asyncio.run(_scenario(hive, drone, binary, janitor))
    finally:
        janitor.close()  # The container first, whatever happened: nothing it started outlives us.


async def _scenario(hive: Hive, drone: DeviceDrone, binary: Path, janitor: Janitor) -> None:
    """Take a clean device from nothing to a finished task and back to nothing."""
    async with run_hive(hive):
        device = start_clean(janitor, {HIVE_HOSTNAME: BRIDGE.gateway})
        async with _showing_the_device(hive, device):
            await _assert_a_debian_12_with_no_python(device)
            before = await asyncio.to_thread(device.files)
            await _install_the_binary(device, binary)
            node = await _enrol_and_run(hive, device)
            await _assert_the_hive_knows_the_device(hive, node, device)
            await _assert_a_task_ran_there(hive, node, drone, device)
            await _stop_and_take_it_back(device, before)


@contextlib.asynccontextmanager
async def _showing_the_device(hive: Hive, device: CleanDevice) -> AsyncIterator[None]:
    """Add what the packet wrote, what runs on the device and the trail's end to a failure."""
    try:
        yield
    except Exception as failure:
        events = await trail_events(hive)
        failure.add_note(f"The packet's log:\n{await asyncio.to_thread(device.log)}")
        failure.add_note(f"The device's processes:\n{await asyncio.to_thread(device.processes)}")
        failure.add_note(f"The end of the trail: {[event.kind for event in events[-20:]]}")
        failure.add_note(f"Its last three events: {[dict(event.payload) for event in events[-3:]]}")
        raise


async def _on(
    device: CleanDevice,
    *argv: str,
    stdin: str | None = None,
    check: bool = True,
    user: str = OPERATOR,
) -> CommandResult:
    """Run a command on the device off the event loop, which the Hive Stand shares."""
    return await asyncio.to_thread(device.exec, argv, stdin=stdin, check=check, user=user)


async def _assert_a_debian_12_with_no_python(device: CleanDevice) -> None:
    """The device is Debian 12 with glibc 2.36, and nothing on it can run a Python program."""
    # `command -v` is how a shell finds a program on its PATH; none of these may be found.
    for program in ("python3", "python", "pip3"):
        found = await _on(device, "sh", "-c", f"command -v {program}", check=False)
        assert found.exit_code != 0, f"{program} is on the clean device: {found.stdout}"
    release = (await _on(device, "cat", "/etc/os-release")).stdout
    assert f'PRETTY_NAME="{DEBIAN_12}"' in release
    # Raspberry Pi OS (bookworm) ships the same C library: the floor the binary was built for.
    assert "2.36" in (await _on(device, "ldd", "--version")).stdout.splitlines()[0]


async def _install_the_binary(device: CleanDevice, binary: Path) -> None:
    """Install the binary as the runbook says, and `version` and `status` answer without Python."""
    await asyncio.to_thread(device.install, binary)

    version = (await _on(device, "pollen", "version")).stdout.strip()
    assert version == f"pollen {runtime_version()} (waggle {PROTOCOL_VERSION})"
    # A device that was never enrolled must say so and exit 1, not crash (the workflow's check).
    never = await _on(device, "pollen", "status", check=False)
    assert never.exit_code == 1 and "not enrolled" in never.stderr


async def _enrol_and_run(hive: Hive, device: CleanDevice) -> SwarmNode:
    """Enrol with the Stand's invite, install, run the printed command and wait for ONLINE."""
    assert hive.swarm is not None
    invite = await hive.swarm.door.mint_invite(
        AccessLevel.SCRATCH, CombShieldLevel.MEADOW, label=_LABEL
    )
    # On standard input, as the runbook has it: the invite is then in no process list.
    enrolled = await _on(device, "pollen", "enrol", "-", stdin=invite.invite_string)
    granted = AccessLevel.SCRATCH.value
    assert re.search(
        rf"Enrolled as device_\S+ on Cell cell_\S+, access {granted}\.", enrolled.stdout
    )
    # There is no systemd user session in a container: install writes the unit and prints the
    # command that starts the packet by hand, which is what is run.
    installed = await _on(device, "pollen", "install")
    manual = [line for line in installed.stdout.splitlines() if line.startswith("  ")]
    assert len(manual) == 1, installed.stdout
    await asyncio.to_thread(device.start, shlex.split(manual[0]))

    node = await enrolled_node(hive)
    await hive.swarm.listener.hub.wait_for(node.device_id, _WAIT_S)
    await wait_until(lambda: _is_online(hive, node), timeout_s=_WAIT_S)
    return node


async def _is_online(hive: Hive, node: SwarmNode) -> bool:
    """Whether the registry holds the device as ONLINE."""
    assert hive.swarm is not None
    held = await hive.swarm.registry.node(node.device_id)
    return held is not None and held.status is NodeStatus.ONLINE


async def _assert_the_hive_knows_the_device(
    hive: Hive, node: SwarmNode, device: CleanDevice
) -> None:
    """The registry holds what the binary reported at enrolment: this Debian 12 machine."""
    assert hive.swarm is not None
    held = await hive.swarm.registry.node(node.device_id)
    assert held is not None
    platform = held.platform
    assert platform.os is OsFamily.LINUX
    assert platform.distribution == DEBIAN_12
    # The machine says what it is (`uname -m`: x86_64 here, aarch64 on an ARM host), and so does
    # the report; Debian's package manager is apt.
    assert platform.architecture == (await _on(device, "uname", "-m")).stdout.strip()
    assert platform.package_manager == "apt"
    # A frozen build carries an interpreter of its own that no tool on the device can reach.
    assert platform.python_version is None
    assert held.hostname == (await _on(device, "hostname")).stdout.strip()
    assert held.runtime_version == runtime_version()
    shown = (await _on(device, "pollen", "status")).stdout
    assert "running, connected" in shown, shown


async def _assert_a_task_ran_there(
    hive: Hive, node: SwarmNode, drone: DeviceDrone, device: CleanDevice
) -> None:
    """A goal pinned to the device's Cell completes: a command and a file, through the session."""
    # The gate gave the device a Level 0 Warden when it connected; the goal waits until she is idle.
    await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
    task = await finished(hive, await submit_pinned(hive, node), _TASK_S)
    assert task.status is TaskStatus.SUCCEEDED, task.outcome

    # What the command printed says where it ran: the device's system and name, not this host's,
    # and in the environment of a program on the device, with no loader path of the packet's.
    host = (await _on(device, "hostname")).stdout.strip()
    assert f"os={DEBIAN_12}" in drone.printed, drone.printed
    assert f"host={host}" in drone.printed, drone.printed
    assert "loader=[]" in drone.printed, drone.printed
    # And the packet run by a command in the lease starts as an instance of its own: it does not
    # take itself for a worker of the packet that started it, which has no loader path to give.
    nested = f"nested=pollen {runtime_version()} (waggle {PROTOCOL_VERSION})"
    assert nested in drone.printed, drone.printed
    await _assert_the_story_and_the_scratch(hive, device)


async def _assert_the_story_and_the_scratch(hive: Hive, device: CleanDevice) -> None:
    """The trail shows the command and the file sent to the device; its leases directory is bare."""
    commands = await trail_events(hive, "swarm.command_sent")
    lease_id = commands[0].payload["lease_id"]
    sent = {c.payload["operation"] for c in commands if c.payload["lease_id"] == lease_id}
    assert {"exec", "put_file", "get_file"} <= sent, sent

    async def released() -> bool:
        events = await trail_events(hive, "cell.released")
        return any(e.payload.get("lease_id") == lease_id for e in events)

    # The Warden gives the lease back the tick after the task ends, so the release trails it.
    await wait_until(released, timeout_s=_WAIT_S)
    events = await trail_events(hive)
    # The Hive Stand's own lease, and the device's start-up lease, come and go around this one:
    # only the events of the lease the Drone's commands ran in tell its story.
    mine = [event for event in events if event.payload.get("lease_id") == lease_id]
    assert_kinds_in_order(
        [event.kind for event in mine], ["cell.leased", "swarm.command_sent", "cell.released"]
    )
    kinds = [event.kind for event in events]
    assert_kinds_in_order(kinds, ["swarm.enrolled", "swarm.online"])
    assert kinds.index("swarm.online") < events.index(mine[0]), "the lease predates the device"
    assert "guard.denied" not in kinds
    [release] = [event for event in mine if event.kind == "cell.released"]
    assert release.payload["is_restored"] is True  # The device confirmed it was put back.
    leases = await _on(device, "find", f"{POLLEN_DIR}/leases", "-mindepth", "1", check=False)
    assert leases.stdout.strip() == "", f"the lease left files on the device: {leases.stdout}"


async def _stop_and_take_it_back(device: CleanDevice, before: Mapping[str, FileEntry]) -> None:
    """Stop the packet, uninstall, do the runbook's last two steps; the device is as it was."""
    stopped = await asyncio.to_thread(device.stop)
    assert stopped == 0, f"the packet exited {stopped} on SIGTERM"

    uninstalled = await _on(device, "pollen", "uninstall")
    assert "The unit was removed." in uninstalled.stdout
    assert not await asyncio.to_thread(device.container.exists, UNIT_PATH)
    # By design uninstall keeps the enrolment: only deleting the data directory forgets it.
    assert await asyncio.to_thread(device.container.exists, POLLEN_DIR)

    # The runbook's last words: delete the data directory, and the binary is the operator's own.
    await _on(device, "rm", "-rf", POLLEN_DIR)
    await _on(device, "rm", INSTALL_PATH, user=ROOT)
    after = await asyncio.to_thread(device.files)
    assert after == before, _difference(before, after)


def _difference(before: Mapping[str, FileEntry], after: Mapping[str, FileEntry]) -> str:
    """Say which files the device gained, lost or changed between two snapshots."""
    added = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    changed = sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
    return f"the device is not as it was: gained {added}, lost {gone}, changed {changed}"
