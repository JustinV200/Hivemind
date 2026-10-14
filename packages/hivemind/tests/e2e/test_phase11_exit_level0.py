"""Phase 11 exit criterion 1 on a second machine: a device is enrolled, listed, worked on and left.

"Enrol a second machine; `hive cells list` shows it `REAL` with its Warden on the Hive Stand; a
Drone placed on it completes; the device's scratch dir is gone and its process table matches the
pre-lease snapshot." The second machine is a container (its own filesystem, process table and
network namespace) running the Pollen Packet installed from wheels built from this tree; the Hive
is the real one (Queen, Wardens, trail, SQLite), listening on the docker network's host address
over `wss://`, and only its model is scripted. The operator's act is the real one: an invite from
the Hive's door, `pollen enrol` and `pollen run` inside the container, and `hive cells list` over
the Hive's own database. A goal whose ceiling names the device's Cell and no other is submitted;
the scripted Drone runs a command on the device (one that leaves a child running, as a careless
command would) and writes a file there, through the `PollenSession` the device's Warden leased.

What it proves: the listing shows a `REAL` Cell of source `swarm`, its Warden on the Hive Stand,
its access level and its mode; the command really ran in the container (its child is in the
container's process table); the task succeeded, so the Warden found the file on the device; and
afterwards the lease's scratch directory is gone, the child is gone, and the container's process
table is exactly the one taken before the lease. Nothing was written to the container outside the
packet's own data directory.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - Every container and network is removed, whatever the test did: the janitor closes in a
      `finally`.
    - Waits are bounded polls of the Hive's trail or the container's process table.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", first bullet.
    - tests/e2e/test_swarm_level0.py for the same goal on a device in the test's own process.
    - e2e/swarm/ for the container, Hive and Drone scaffolding this uses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest
from e2e.kernel_helpers import assert_kinds_in_order, wait_until
from e2e.swarm.bridge import BridgeHive, bridge_hive
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.drone import Script, command_turn, finished, submit_pinned
from e2e.swarm.enrolled import DATA_DIR, ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.operator import cell_rows
from e2e.swarm.snapshots import changes_outside, files_under, process_args, settled_to

from hivemind.brood_chamber import TaskStatus
from hivemind.cli.compose import Hive, run_hive

pytestmark = pytest.mark.e2e

_WAIT_S = 60.0  # Each wait; a container enrols and runs its task in seconds, a cold start in tens.
# The Drone's command: a child that outlives it, as a careless command would, then a run in the
# foreground long enough that the test, which looks at the container's process table a few times a
# second (slower on a busy machine), sees the command while it is still going.
_STRAY = "sleep 321"
_COMMAND = ("sh", "-c", f"{_STRAY} </dev/null >/dev/null 2>&1 & sleep 4; echo hello from the Hive")


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_an_enrolled_container_is_listed_as_a_real_cell_and_a_drone_on_it_leaves_it_as_found(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        hive = bridge_hive(tmp_path, network, Script(command_turn(_COMMAND)).responder)

        asyncio.run(_scenario(hive, janitor, image, network, tmp_path / "hive.toml"))
    finally:
        janitor.close()


async def _scenario(
    hive: Hive, janitor: Janitor, image: str, network: Network, manifest: Path
) -> None:
    """Enrol a container, list it, run a goal on it, and check it was left as found."""
    async with run_hive(hive):
        options = DeviceOptions(network=network)
        device = await enrol_device(BridgeHive(hive), janitor, image, options)
        # The device's Warden leased it when it started and gave the lease back when it found
        # nothing to do: from here nothing of the Hive's is on the device.
        await wait_until(lambda: warden_is_idle(hive, device.node), timeout_s=_WAIT_S)
        await _the_operator_sees_a_real_cell(manifest, device)
        container = device.container
        processes = await asyncio.to_thread(container.processes)
        leases = await asyncio.to_thread(files_under, container, f"{DATA_DIR}/leases")

        goal_id = await submit_pinned(hive, device.node)
        await wait_until(lambda: _stray_runs(container), timeout_s=_WAIT_S)
        task = await finished(hive, goal_id)

        assert task.status is TaskStatus.SUCCEEDED, task.outcome
        await _the_task_was_placed_on_the_device(hive, device)
        lease_id = await _the_story_in_order(hive)
        await _the_device_was_left_as_found(container, lease_id, processes, leases)
        await _rows_when_resting(manifest)  # The Warden is back in WATCH: the device is idle.
        assert await asyncio.to_thread(changes_outside, container, DATA_DIR) == []


async def _the_operator_sees_a_real_cell(manifest: Path, device: ContainerDevice) -> None:
    """`hive cells list` shows the container as a Real Cell, its Warden on the Stand."""
    cells = {row["source"]: row for row in await _rows_when_resting(manifest)}
    assert set(cells) == {"hive_stand", "swarm"}
    row = cells["swarm"]
    assert (row["id"], row["kind"], row["name"]) == (
        device.node.cell_id,
        "REAL",
        device.node.label,
    )
    assert (row["warden_runs"], row["level"], row["mode"]) == ("hive_stand", "GATEWAY", "WATCH")
    assert (row["access_level"], row["comb_shield"], row["enabled"]) == ("FULL", "MEADOW", True)


async def _rows_when_resting(manifest: Path) -> list[dict[str, object]]:
    """The listing once the device's Warden has recorded WATCH, a moment after it starts."""
    rows: list[dict[str, object]] = []

    async def resting() -> bool:
        rows[:] = await cell_rows(manifest)
        return any(row["source"] == "swarm" and row["mode"] == "WATCH" for row in rows)

    await wait_until(resting, timeout_s=_WAIT_S)
    return rows


async def _stray_runs(container: Container) -> bool:
    """Whether the Drone's command has left its child running in the container's process table."""
    table = await asyncio.to_thread(process_args, container)
    return any(args == _STRAY for args in table.values())


async def _the_task_was_placed_on_the_device(hive: Hive, device: ContainerDevice) -> None:
    """The Queen assigned the task to the device's Cell and its Warden, and to nothing else."""
    [assigned] = await trail_events(hive, "task.assigned")
    node = device.node
    assert (assigned.payload["cell_id"], assigned.payload["warden_id"]) == (
        node.cell_id,
        node.warden_id,
    )


async def _the_story_in_order(hive: Hive) -> str:
    """The trail tells the lease's story in order, and nothing was denied on the way.

    Returns:
        The id of the lease the Drone's commands ran in.
    """
    commands = await trail_events(hive, "swarm.command_sent")
    assert commands, "no command was ever sent to the device"
    lease_id = str(commands[0].payload["lease_id"])

    async def released() -> bool:
        events = await trail_events(hive, "cell.released")
        return any(e.payload["lease_id"] == lease_id for e in events)

    # The Warden gives the lease back the tick after its task ends, a moment after the task's own
    # terminal status lands.
    await wait_until(released, timeout_s=_WAIT_S)
    events = await trail_events(hive)
    kinds = [event.kind for event in events]
    assert_kinds_in_order(kinds, ["swarm.enrolled", "swarm.online"])
    mine = [event.kind for event in events if event.payload.get("lease_id") == lease_id]
    assert_kinds_in_order(mine, ["cell.leased", "swarm.command_sent", "cell.released"])
    assert mine[-1] == "cell.released", "a command was sent after the lease was released"
    assert "guard.denied" not in kinds
    [report] = [
        e for e in events if e.kind == "cell.released" and e.payload["lease_id"] == lease_id
    ]
    assert report.payload["is_restored"] is True
    killed = report.payload["killed_processes"]
    assert isinstance(killed, int) and killed >= 1  # The child the command left behind.
    return lease_id


async def _the_device_was_left_as_found(
    container: Container, lease_id: str, processes: dict[int, str], leases: Mapping[str, object]
) -> None:
    """Scratch is gone from the container and its process table is the one taken before."""
    assert not await asyncio.to_thread(container.exists, f"{DATA_DIR}/leases/{lease_id}")
    assert await asyncio.to_thread(files_under, container, f"{DATA_DIR}/leases") == leases
    assert await settled_to(container, processes) == processes
