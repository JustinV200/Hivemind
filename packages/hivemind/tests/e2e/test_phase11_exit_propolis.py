"""Phase 11 exit criterion 4: Propolis waits for a device's own tunnel, Night Veil never lands.

"A Propolis task placed on a Real Cell enrolled as Propolis is blocked until the device's own
OpenVPN tunnel is verified healthy, VPN-only egress checks pass, and direct non-VPN egress is
denied and trailed; HiveMind changes nothing on the device to get there, and the left-as-found
snapshot holds." The device is a container with `NET_ADMIN`, enrolled as Propolis on a real Hive.
Its operator runs no tunnel yet, so the Warden's lease is refused: the Propolis checks read the
container's own routing table and resolver with fixed read-only commands and find the default route
on `eth0`, the resolver on the bridge and direct egress open. A goal whose ceiling names only that
Cell is put to the Queen with a Propolis task. Then the test plays the operator and brings a tunnel
up the way a VPN client does (`tun0`, an address, the default route through it, a nameserver only
it reaches); the next request for the lease passes every check and the very same task runs.

What it proves: while there is no tunnel the lease is refused with `swarm.shield_refused` carrying
each check's name and result, the lease's scratch is released at once, nothing but the checks' fixed
`cat` commands is sent, the Queen is told once, and the task is neither failed nor started; once the
tunnel exists `swarm.shield_verified` follows, the task is placed and completes; and HiveMind
changed nothing to get there: no command it sent wrote outside scratch, the routes and resolver
are exactly the operator's, the container's writable layer gained nothing outside the packet's
directory after the operator's setup, the scratch is gone and the process table is as it was. The
second scenario is the other half of step 11.8a: a Night Veil goal pinned to the device is denied
by the Guard at the placement point and never placed on it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - The tunnel is the test's, made with the commands an operator would run in the container; the
      Hive never starts, installs or reroutes one.
    - Every container and network is removed, whatever the test did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the Propolis bullet; step 11.8a.
    - hivemind.swarm.shield for the checks, and e2e/swarm/tunnel.py for the operator's side.
    - hivemind.wardens.ticks.assign for the Warden keeping a task whose lease is refused.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from e2e.kernel_helpers import wait_until
from e2e.swarm.bridge import BridgeHive, bridge_hive
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.drone import Script, command_turn, finished, plan_for, submit_pinned
from e2e.swarm.enrolled import DATA_DIR, ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.snapshots import (
    WrittenFile,
    changes_outside,
    files_under,
    operator_files,
    settled_to,
)
from e2e.swarm.tunnel import RESOLVER_FILE, NetworkState, network_state, raise_tunnel
from pydantic import JsonValue

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import CombShieldLevel
from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from waggle.ids import TaskId

pytestmark = pytest.mark.e2e

_WAIT_S = 60.0  # Each wait; the checks and a task on a container take moments.
_REFUSALS = 3  # How many refusals the device is seen to make before its tunnel comes up.
_COMMAND = ("sh", "-c", "echo hello from the Hive")
_FAILING = ["default_route_via_tunnel", "dns_via_tunnel", "direct_egress_refused"]
_NIGHT_VEIL = {"comb_shield": "NIGHT_VEIL", "isolation": "REQUIRED"}


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


class _Found(NamedTuple):
    """The device as it stood before the Hive was asked to do anything on it."""

    processes: dict[int, str]
    files: dict[str, WrittenFile | str]


def test_a_propolis_task_waits_for_the_devices_own_tunnel_and_the_device_is_left_as_found(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        script = Script(command_turn(_COMMAND), plan_for({"comb_shield": "PROPOLIS"}))
        hive = bridge_hive(tmp_path, network, script.responder)

        asyncio.run(_propolis(hive, janitor, image, network))
    finally:
        janitor.close()


async def _propolis(hive: Hive, janitor: Janitor, image: str, network: Network) -> None:
    """Enrol a Propolis container with no tunnel, put a task to it, then bring the tunnel up."""
    async with run_hive(hive):
        options = DeviceOptions(
            network=network, shield=CombShieldLevel.PROPOLIS, capabilities=("NET_ADMIN",)
        )
        device = await enrol_device(BridgeHive(hive), janitor, image, options)
        container = device.container
        await wait_until(lambda: warden_is_idle(hive, device.node), timeout_s=_WAIT_S)
        found = _Found(
            await asyncio.to_thread(container.processes),
            await asyncio.to_thread(operator_files, container),
        )

        goal_id = await submit_pinned(hive, device.node)
        await _blocked_while_there_is_no_tunnel(hive, goal_id)

        await asyncio.to_thread(raise_tunnel, container)
        tunnel = await asyncio.to_thread(network_state, container)
        task = await finished(hive, goal_id)

        assert task.status is TaskStatus.SUCCEEDED, task.outcome
        await _verified_and_placed(hive, device)
        await _left_as_found(container, found, tunnel)


async def _refusals(hive: Hive) -> list[PheromoneEvent]:
    """Every `swarm.shield_refused` on the trail, oldest first."""
    return await trail_events(hive, "swarm.shield_refused")


async def _goal_task(hive: Hive, goal_id: TaskId) -> Task:
    """The goal's one task, as the Brood Chamber holds it now."""
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return task


def _checks(event: PheromoneEvent) -> dict[str, Mapping[str, JsonValue]]:
    """The per-check results a `swarm.shield_*` event carries, by check name."""
    raw = event.payload["checks"]
    assert isinstance(raw, list)
    return {str(check["name"]): check for check in raw if isinstance(check, dict)}


async def _released_leases(hive: Hive) -> dict[JsonValue, PheromoneEvent]:
    """Every `cell.released` on the trail, by the lease it ended."""
    return {e.payload["lease_id"]: e for e in await trail_events(hive, "cell.released")}


async def _blocked_while_there_is_no_tunnel(hive: Hive, goal_id: TaskId) -> None:
    """The device refuses again and again; scratch goes at once; the task is held, not failed."""

    async def refused_and_given_back() -> bool:
        # A refusal is recorded before its lease is released, so the newest one may be mid-flight:
        # judge the first few, which have had time to finish.
        first = (await _refusals(hive))[:_REFUSALS]
        released = await _released_leases(hive)
        return len(first) == _REFUSALS and all(e.payload["lease_id"] in released for e in first)

    await wait_until(refused_and_given_back, timeout_s=_WAIT_S)
    released = await _released_leases(hive)
    for event in (await _refusals(hive))[:_REFUSALS]:
        checks = _checks(event)
        failed = [name for name, check in checks.items() if not check["passed"]]
        assert failed == _FAILING, checks
        assert checks["no_direct_default_route"]["reason"] == "no_ipv6_route"
        # The lease the checks ran in was given back, the device left as found.
        assert released[event.payload["lease_id"]].payload["is_restored"] is True

    task = await _goal_task(hive, goal_id)
    assert task.status is TaskStatus.RUNNING  # Assigned, not failed; no bee is running it.
    assert not await trail_events(hive, "worker.spawned")
    alarms = await trail_events(hive, "alarm.raised")
    asked = [a for a in alarms if a.payload["task_id"] == task.id]
    assert [a.payload["kind"] for a in asked] == ["CELL_UNREACHABLE"]  # Told once, not per retry.


async def _verified_and_placed(hive: Hive, device: ContainerDevice) -> None:
    """The checks pass once the tunnel is there, and only fixed reads and scratch were used."""
    [verified] = await trail_events(hive, "swarm.shield_verified")
    assert verified.payload["cell_id"] == device.node.cell_id
    assert all(check["passed"] for check in _checks(verified).values())
    sent = await trail_events(hive, "swarm.command_sent")
    assert not [e for e in sent if e.payload["outside_scratch"]]  # No command left scratch.
    used = Counter((e.payload["operation"], e.payload["program"]) for e in sent)
    attempts = len(await _refusals(hive)) + 1
    # The Warden's own commands are the shield's three fixed reads, once per time it asked...
    assert used.pop(("exec", "cat"), 0) == 3 * attempts
    # ...and everything else is the Drone's: its command and its report, both in scratch.
    assert set(used) == {("exec", "sh"), ("put_file", None), ("get_file", None)}
    assert not await trail_events(hive, "guard.denied")


async def _left_as_found(container: Container, found: _Found, tunnel: NetworkState) -> None:
    """The tunnel is the operator's, and everything else is as it was before the Hive came."""
    assert await asyncio.to_thread(network_state, container) == tunnel
    files = await asyncio.to_thread(operator_files, container)
    # The operator rewrote the resolver to make the tunnel, so it is the one file that differs.
    assert _without_resolver(files) == _without_resolver(found.files)
    # Nothing in the writable layer, from the first refused lease to the finished task, but the
    # packet's own directory (and the init process the janitor adds).
    assert await asyncio.to_thread(changes_outside, container, DATA_DIR) == []
    assert await asyncio.to_thread(files_under, container, f"{DATA_DIR}/leases") == {}
    assert await settled_to(container, found.processes) == found.processes


def _without_resolver(files: dict[str, WrittenFile | str]) -> dict[str, WrittenFile | str]:
    """A snapshot of the operator's files, less the one file the test's tunnel rewrites."""
    return {path: entry for path, entry in files.items() if path != RESOLVER_FILE}


def test_a_night_veil_goal_pinned_to_a_device_is_denied_at_placement_and_never_placed(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        script = Script(command_turn(_COMMAND), plan_for(_NIGHT_VEIL))
        hive = bridge_hive(tmp_path, network, script.responder)

        asyncio.run(_night_veil(hive, janitor, image, network))
    finally:
        janitor.close()


async def _night_veil(hive: Hive, janitor: Janitor, image: str, network: Network) -> None:
    """Pin a Night Veil goal to a device: the Guard denies it at placement and nothing lands."""
    async with run_hive(hive):
        device = await enrol_device(
            BridgeHive(hive), janitor, image, DeviceOptions(network=network)
        )
        await wait_until(lambda: warden_is_idle(hive, device.node), timeout_s=_WAIT_S)
        leases = len(await trail_events(hive, "cell.leased"))

        goal_id = await submit_pinned(hive, device.node)
        task = await finished(hive, goal_id)

        assert task.status is TaskStatus.CANCELLED
        [denied] = await trail_events(hive, "guard.denied")
        assert denied.payload["point"] == "placement"
        assert str(denied.payload["rule"]).startswith("guard.tier_floor.night_veil")
        # Never placed: no assignment, no placement record, no lease, no command on the device.
        assert not await trail_events(hive, "task.assigned")
        assert not await trail_events(hive, "queen.placed")
        assert len(await trail_events(hive, "cell.leased")) == leases
        assert not await trail_events(hive, "swarm.command_sent")
