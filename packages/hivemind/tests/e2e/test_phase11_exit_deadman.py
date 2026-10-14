"""Phase 11 exit criterion 6 on a second machine: cut a non-Nuc device off and it lets itself go.

"Cut the network to a non-Nuc device: the dead-man switch releases the lease and the Queen's
Warden raises an Alarm." A Level 0 device has no Warden on it, so when it loses the Queen it cannot
ask anyone what to do: its Pollen Packet kills what the lease started, restores what it recorded,
removes the scratch and releases the lease by itself once the Queen has been silent longer than
`[swarm] dead_man_s`. The device is a container enrolled on a real Hive that listens on the docker
network's host address; the Drone's long command runs under a lease on it; then the network is
cut with `docker network disconnect`, the cable pulled out.

What it proves: the Hive notices within beats (the node is UNREACHABLE) and the device's Warden,
which stays on the Hive Stand, raises a `CELL_UNREACHABLE` Alarm; the packet, with the Hive still
unable to reach it, kills the command and removes the scratch on its own, and says so in its log;
after `docker network connect` the packet dials again and its queued `cell.lease_released` (cause
`DEAD_MAN`) arrives, the Hive's lease is released with the device's own report, and `cell.released`
is on the trail; and the container's process table is the one taken before the lease.

The second scenario is the same cable pulled and put back inside the Queen's grace, to see what
becomes of the task whose command was running: the command dies with the link, which is the
cut's doing and not the bee's, so the Warden holds the task (`alarm.handled` with `HOLD_FOR_CELL`)
instead of retrying a bee on a session that is gone, and when the device is back it starts the
task again and the task succeeds, with no crash escalated and no second binding refused.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one. It takes about two minutes, nearly all of it the WebSocket keepalive
    (a link whose far end vanished is declared dead after a ping interval and a timeout) before
    the switch's own clock starts.

Key invariants:
    - The device's link is older than a ping interval when the lease opens, because a packet counts
      a link as proof the Queen is alive only after it has been open that long.
    - The Hive releases nothing while the device is cut off: the release the trail shows comes from
      the device's own report, which the Warden waits to hear before it gives the lease back.
    - A device back inside the grace has its task carried on by its own Warden, never failed or
      re-placed by the Queen.
    - Every container and network is removed, whatever the test did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the dead-man bullet.
    - docs/waggle/gateway-contract.md section 13 for the dead-man switch.
    - tests/contracts/test_gateway_contract_resilience.py for the same clause against a packet
      on the Hive Stand's own machine.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

import pytest
from builders.swarm.hive import HubLog
from e2e.kernel_helpers import wait_until
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.drone import Script, command_turn, finished, submit_pinned
from e2e.swarm.enrolled import DATA_DIR, ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.snapshots import files_under, process_args, settled_to

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from hivemind.wardens.ticks.lease import HELD_FOR_CELL
from waggle.ids import TaskId
from waggle.messages.cell import LeaseReleased
from waggle.messages.cell.status import ReleaseCause
from waggle.transport.websocket import PING_INTERVAL_S

pytestmark = pytest.mark.e2e

_DEAD_MAN_S = 5.0  # The limit the device is enrolled with: short, so the switch trips in seconds.
_GRACE_S = 900.0  # The Queen holds a lost device's tasks this long: past the whole scenario.
_LONG = ("sleep", "100")  # The Drone's first command: still running when the cable is pulled.
_QUICK = ("true",)  # Any attempt after it: leaves nothing on the device if the Hive tries again.
_SILENCE = re.compile(r"pollen\.dead_man_tripped silence_ms=(\d+)")
_WAIT_S = 60.0  # Each wait that is not about the cut: a container enrols and runs in seconds.
_ALARM_S = 60.0  # The Hive notices a silent device in a sweep and a few beats.
_DEAD_MAN_WAIT_S = 180.0  # The keepalive's 40 s, the limit, and the release behind a closing link.
_RETURN_S = 120.0  # The packet's redial backoff reaches 30 s before it tries again.


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_a_level0_device_cut_off_mid_command_releases_itself_and_the_wardens_alarm_is_raised(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        terms = BridgeTerms({"dead_man_s": _DEAD_MAN_S, "unreachable_grace_s": _GRACE_S})
        script = Script(command_turn(_LONG, _QUICK))
        hive = bridge_hive(tmp_path, network, script.responder, terms)

        asyncio.run(_scenario(hive, janitor, image, network))
    finally:
        janitor.close()


async def _scenario(hive: Hive, janitor: Janitor, image: str, network: Network) -> None:
    """Run a long command on a container, cut it off, and watch it release itself and come back."""
    assert hive.swarm is not None
    heard = HubLog()
    hive.swarm.listener.hub.subscribe(heard)
    async with run_hive(hive):
        options = DeviceOptions(network=network)
        device = await enrol_device(BridgeHive(hive), janitor, image, options)
        container, node = device.container, device.node
        await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
        await _wait_for_a_proven_link(hive, device)
        before = await asyncio.to_thread(container.processes)

        await submit_pinned(hive, node)
        await wait_until(lambda: _runs(container), timeout_s=_WAIT_S)
        lease_id = await _the_drones_lease(hive)
        cut = hive.clock.now()
        await asyncio.to_thread(network.disconnect, container)

        await _the_wardens_alarm(hive, cut)
        await _the_device_released_itself(hive, container, lease_id)
        await asyncio.to_thread(network.connect, container)
        await _the_release_arrives(hive, heard, lease_id)

        assert await settled_to(container, before) == before


async def _wait_for_a_proven_link(hive: Hive, device: ContainerDevice) -> None:
    """Wait until the device's link is older than a ping interval.

    The packet counts a link as proof the Queen is alive only after it has been open a full ping
    interval (a pong must have come back), and the Queen sends it nothing while a command runs. A
    lease on a younger link, with a limit shorter than that, could trip on a healthy link.
    """
    assert hive.swarm is not None
    hub = hive.swarm.listener.hub

    async def old_enough() -> bool:
        attached = hub.attached_at(device.node.device_id)
        if attached is None:
            return False
        return (hive.clock.now() - attached).total_seconds() > PING_INTERVAL_S + 1

    await wait_until(old_enough, timeout_s=_WAIT_S)


async def _runs(container: Container) -> bool:
    """Whether the Drone's long command is running in the container."""
    table = await asyncio.to_thread(process_args, container)
    return " ".join(_LONG) in table.values()


async def _the_drones_lease(hive: Hive) -> str:
    """The id of the lease the Drone's commands run in: the one `swarm.command_sent` names."""
    commands = await trail_events(hive, "swarm.command_sent")
    assert commands, "no command was ever sent to the device"
    return str(commands[0].payload["lease_id"])


async def _the_wardens_alarm(hive: Hive, cut: datetime) -> None:
    """After the cut the node is UNREACHABLE and its Warden raises one `CELL_UNREACHABLE` Alarm."""
    [alarm] = await _since(hive, cut, "alarm.raised", "CELL_UNREACHABLE")
    assert "link to this Warden's Cell was lost" in str(alarm.payload["detail"])
    # The Queen carries it up the chain to the human, as she does every Alarm the table escalates.
    [escalated] = await _since(hive, cut, "alarm.escalated", "CELL_UNREACHABLE")
    assert escalated.subject_id == alarm.subject_id
    [lost] = await _since(hive, cut, "swarm.unreachable")
    assert lost.payload["reason"] == "missed_heartbeats"


async def _since(
    hive: Hive, cut: datetime, kind: str, alarm: str | None = None, timeout_s: float = _ALARM_S
) -> list[PheromoneEvent]:
    """Wait for an event of `kind` recorded after `cut` (an Alarm of kind `alarm`), and list them.

    An earlier event of the same kind (a flap before the cable was pulled) is not the cut's doing,
    and the Queen and the Warden record theirs a few milliseconds apart, so the lookup waits
    rather than reading once.
    """
    found: list[PheromoneEvent] = []

    async def recorded() -> bool:
        events = [e for e in await trail_events(hive, kind) if e.at >= cut]
        found[:] = [e for e in events if alarm is None or e.payload["kind"] == alarm]
        return bool(found)

    await wait_until(recorded, timeout_s=timeout_s)
    return found


async def _the_device_released_itself(hive: Hive, container: Container, lease_id: str) -> None:
    """With the Hive cut off, the packet kills the command and removes the scratch on its own."""

    async def let_go() -> bool:
        leases = await asyncio.to_thread(files_under, container, f"{DATA_DIR}/leases")
        return not await _runs(container) and not any(lease_id in path for path in leases)

    await wait_until(let_go, timeout_s=_DEAD_MAN_WAIT_S)
    # The Hive could not have done it: it cannot reach the device, and has recorded no release.
    released = await trail_events(hive, "cell.released")
    assert not [e for e in released if e.payload["lease_id"] == lease_id]
    tripped = _SILENCE.search(await asyncio.to_thread(container.logs))
    assert tripped is not None, "the packet never logged that its dead-man switch tripped"
    assert _DEAD_MAN_S * 1000 <= int(tripped.group(1)) < 2 * _DEAD_MAN_S * 1000


async def _the_release_arrives(hive: Hive, heard: HubLog, lease_id: str) -> None:
    """Back on the network, the device's `cell.lease_released` (DEAD_MAN) is heard and trailed."""

    async def released() -> bool:
        events = await trail_events(hive, "cell.released")
        return any(e.payload["lease_id"] == lease_id for e in events)

    await wait_until(released, timeout_s=_RETURN_S)
    told = [
        f.payload
        for f in heard.frames
        if isinstance(f.payload, LeaseReleased) and f.payload.lease_id == lease_id
    ]
    assert [(p.cause, p.is_restored) for p in told] == [(ReleaseCause.DEAD_MAN, True)]
    events = await trail_events(hive, "cell.released")
    [report] = [e for e in events if e.payload["lease_id"] == lease_id]
    killed = report.payload["killed_processes"]
    assert report.payload["is_restored"] is True and isinstance(killed, int) and killed >= 1


def test_a_level0_device_back_inside_the_grace_has_its_cut_off_task_carry_on(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        terms = BridgeTerms({"dead_man_s": _DEAD_MAN_S, "unreachable_grace_s": _GRACE_S})
        script = Script(command_turn(_LONG, _QUICK))
        hive = bridge_hive(tmp_path, network, script.responder, terms)

        asyncio.run(_cut_and_back(hive, janitor, image, network))
    finally:
        janitor.close()


async def _cut_and_back(hive: Hive, janitor: Janitor, image: str, network: Network) -> None:
    """Pull the cable under a running command, put it back inside the grace, wait for the end."""
    async with run_hive(hive):
        options = DeviceOptions(network=network)
        device = await enrol_device(BridgeHive(hive), janitor, image, options)
        container, node = device.container, device.node
        await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
        await _wait_for_a_proven_link(hive, device)
        before = await asyncio.to_thread(container.processes)

        goal = await submit_pinned(hive, node)
        await wait_until(lambda: _runs(container), timeout_s=_WAIT_S)
        cut = hive.clock.now()
        await asyncio.to_thread(network.disconnect, container)

        await _the_task_is_held(hive, cut, goal)
        await asyncio.to_thread(network.connect, container)
        task = await finished(hive, goal)

        assert task.status is TaskStatus.SUCCEEDED
        await _the_cut_was_not_a_crash(hive, cut, task)
        assert await settled_to(container, before) == before


async def _the_task_is_held(hive: Hive, cut: datetime, goal: TaskId) -> None:
    """The command dies with the link, and the Warden holds the task rather than count a crash."""
    # The bee's command is blocked in the device when the link is declared dead, a keepalive's
    # worth of seconds after the cable is pulled; its failure is the cut's doing, not the bee's.
    [handled] = await _since(hive, cut, "alarm.handled", "WORKER_CRASHED", _DEAD_MAN_WAIT_S)
    assert handled.payload["action"] == HELD_FOR_CELL
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal))
    assert task.status is TaskStatus.RUNNING  # Held for the device: not failed, not given away.


async def _the_cut_was_not_a_crash(hive: Hive, cut: datetime, task: Task) -> None:
    """Nothing was escalated or refused, and the task ran as two bees and ended once."""
    escalated = [e for e in await trail_events(hive, "alarm.escalated") if e.at >= cut]
    assert [e for e in escalated if e.payload["kind"] == "WORKER_CRASHED"] == []
    refused = [e for e in await trail_events(hive, "guard.denied") if e.at >= cut]
    assert [e for e in refused if e.payload["point"] == "slot_binding"] == []
    spawned = await trail_events(hive, "worker.spawned")
    # The bee the cut took its command from, and the one started when the device was back.
    assert len([e for e in spawned if e.payload["task_id"] == task.id]) == 2
    ended = [e for e in await trail_events(hive, "task.succeeded") if e.subject_id == task.id]
    assert len(ended) == 1
