"""End-to-end test of the Swarm at Level 0 of the runtime ladder: a real device runs a real task.

The Hive is built from a manifest with `[swarm] enabled = true`, on loopback with no TLS, and a
scripted `FakeLLMProvider` for the model. The operator's act is the real one: the Hive Stand's door
mints an invite, and a real `pollen` packet, running in this process, enrols with it (the whole
ceremony, over a real WebSocket to the Hive's own listener) and then runs its loop. The Queen's
swarm gate sees the device connect (the trail records `swarm.online` for that very first connection,
as enrolment.md section 8 says) and gives it a Level 0 Warden, attached to the Queen. A goal is
submitted whose ceiling allows only that device's Cell, so its one task can be placed nowhere else;
the scripted Drone runs a command and writes a file on the device, through the `PollenSession` the
Warden leased, and the Warden verifies the file there before the task may reach SUCCEEDED.

What it proves is the roadmap's 11.6 and the device half of 11.7: the device was borrowed and is
left exactly as found. The trail records the device's whole story in order (`swarm.enrolled`,
`swarm.online`, `cell.leased`, `swarm.command_sent`, `cell.released`); the lease's scratch
directory is gone from the device once the task is over (the Warden released its lease when it
went idle); no process the lease started is left running on the host; and when the operator
revokes the node, the gate cuts it off, stops its Warden and the Queen forgets it, while the Hive
Stand's own Warden carries on.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md steps 11.6 and 11.7 for what this proves.
    - e2e/swarm/ for the packet and Hive scaffolding this uses.
    - hivemind.queen.swarm_gate for the gate this exercises, and hivemind.cli.compose.swarm for
      the composition that builds it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import psutil
import pytest
from e2e.kernel_helpers import assert_kinds_in_order, wait_until
from e2e.scripted_openai import free_port
from e2e.swarm.device import EnrolledDevice, enrol, running
from e2e.swarm.hive import (
    STRAY,
    attached_warden_ids,
    enrolled_node,
    revoke,
    swarm_hive,
    trail_events,
)

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import AccessLevel, CombShieldLevel, HoneyClearance
from hivemind.cli.compose import Hive, run_hive
from hivemind.queen.swarm_gate import SwarmGate
from hivemind.swarm import SwarmNode
from hivemind.wardens import Warden, WardenState
from waggle.ids import LeaseId

pytestmark = pytest.mark.e2e

_WAIT_S = 30.0  # Each wait; a device enrols, connects and runs its task in a couple of seconds.


def test_a_goal_runs_on_an_enrolled_device_and_leaves_it_as_found(tmp_path: Path) -> None:
    hive = swarm_hive(tmp_path, free_port())

    asyncio.run(_scenario(hive, tmp_path / "device"))


async def _scenario(hive: Hive, data_dir: Path) -> None:
    """Enrol a device, run a goal on it, check it was left as found, then revoke it."""
    assert hive.swarm is not None
    async with run_hive(hive):
        invite = await hive.swarm.door.mint_invite(
            AccessLevel.FULL, CombShieldLevel.MEADOW, label="e2e laptop"
        )
        device = await enrol(invite.invite_string, data_dir, hive.clock)
        node = await enrolled_node(hive)
        async with running(device):
            # The device's first connection is on the trail, and the gate has given it a Warden,
            # attached to the Queen: it leased the device when it started and gave the lease back
            # when it found nothing to do.
            await _wait_for_kind(hive, "swarm.online")
            gate = hive.swarm.gate
            await wait_until(lambda: _idle_warden(gate, node) is not None, timeout_s=_WAIT_S)
            task = await _run_goal_on(hive, node)

            assert task.status is TaskStatus.SUCCEEDED, task.outcome
            lease_id = await _assert_the_story_in_order(hive)
            await _assert_the_device_was_left_as_found(hive, device, lease_id)
            await _assert_revocation_cuts_the_device_off(hive, node)


def _idle_warden(gate: SwarmGate, node: SwarmNode) -> Warden | None:
    """The device's Warden once it is started and holds no lease; None before."""
    served = gate.wardens.get(node.device_id)
    if served is None or served.warden.lease is not None:
        return None
    return served.warden if served.warden.state is WardenState.WATCH else None


async def _wait_for_kind(hive: Hive, kind: str) -> None:
    """Wait until an event of `kind` is on the trail."""

    async def seen() -> bool:
        return bool(await trail_events(hive, kind))

    await wait_until(seen, timeout_s=_WAIT_S)


async def _run_goal_on(hive: Hive, node: SwarmNode) -> Task:
    """Submit a goal that can only be placed on `node`'s Cell, and wait for its one task to end."""
    # A ceiling that names the device's Cell and not the Hive Stand's `cell:hive_stand`, so the
    # Queen's placement can choose nothing else. The rest is what the scripted Drone needs.
    ceiling = (
        f"cell:real:{node.cell_id}",
        "cell:comb_shield:*",
        "llm:*",
        "tool:*",
        "fs:read:**",
        "fs:write:**",
        "exec:*",
        "question:human",
    )
    goal_id = await hive.queen.submit_goal(
        "write a report on the borrowed device", clearance=HoneyClearance.C1, capabilities=ceiling
    )
    filter_ = TaskFilter(goal_id=goal_id)

    async def finished() -> bool:
        tasks = await hive.stores.chamber.list(filter_)
        return bool(tasks) and all(is_terminal(t.status) for t in tasks)

    await wait_until(finished, timeout_s=_WAIT_S)
    [task] = await hive.stores.chamber.list(filter_)
    return task


async def _assert_the_story_in_order(hive: Hive) -> str:
    """The trail holds the device's whole story: enrolled, online, leased, commanded, released.

    Returns:
        The id of the lease the Drone's commands ran in, whose release is the end of the story.
    """
    # The lease the Drone's commands ran in: leased for the task, released when it was over. (The
    # Hive Stand's own lease, and the device's start-up lease, come and go around it.)
    commands = await trail_events(hive, "swarm.command_sent")
    assert commands, "no command was ever sent to the device"
    lease_id = str(commands[0].payload["lease_id"])

    async def released() -> bool:
        return any(
            e.payload.get("lease_id") == lease_id for e in await trail_events(hive, "cell.released")
        )

    # The Warden releases its lease the tick after the task ends, which is just after the task's
    # own terminal status lands, so the release is on the trail a moment after the task is over.
    await wait_until(released, timeout_s=_WAIT_S)
    events = await trail_events(hive)
    kinds = [event.kind for event in events]
    assert_kinds_in_order(kinds, ["swarm.enrolled", "swarm.online"])
    mine = [event for event in events if event.payload.get("lease_id") == lease_id]
    mine_kinds = [event.kind for event in mine]
    assert_kinds_in_order(mine_kinds, ["cell.leased", "swarm.command_sent", "cell.released"])
    assert mine_kinds[-1] == "cell.released", "a command was sent after the lease was released"
    assert kinds.index("swarm.online") < events.index(mine[0]), "the lease predates the device"
    # The Guard denied nothing on the way: every command was held by the Warden's own set.
    assert "guard.denied" not in kinds
    return lease_id


async def _assert_the_device_was_left_as_found(
    hive: Hive, device: EnrolledDevice, lease_id: str
) -> None:
    """Every lease's scratch directory is gone from the device, and nothing it started runs."""
    leased = await trail_events(hive, "cell.leased")

    async def scratch_gone() -> bool:
        return not any(
            device.layout.scratch(LeaseId(str(event.payload["lease_id"]))).exists()
            for event in leased
        )

    await wait_until(scratch_gone, timeout_s=_WAIT_S)
    # The scripted command left a child running; ending it was the release's work, and the
    # packet reported the kill. Nothing of the lease is left alive on the host.
    [released] = [
        e for e in await trail_events(hive, "cell.released") if e.payload["lease_id"] == lease_id
    ]
    killed = released.payload["killed_processes"]
    assert isinstance(killed, int) and killed >= 1
    assert released.payload["is_restored"] is True
    assert not _processes_running(STRAY)


def _processes_running(marker: str) -> Sequence[psutil.Process]:
    """Every live process on this host whose command line mentions `marker`."""
    found: list[psutil.Process] = []
    for process in psutil.process_iter(["cmdline"]):
        if any(marker in part for part in process.info["cmdline"] or ()):
            found.append(process)
    return found


async def _assert_revocation_cuts_the_device_off(hive: Hive, node: SwarmNode) -> None:
    """Revoking the node closes its link, stops its Warden and makes the Queen forget it."""
    assert hive.swarm is not None
    gate = hive.swarm.gate
    served = gate.wardens.get(node.device_id)
    assert served is not None
    warden = served.warden
    assert hive.swarm.listener.hub.link(node.device_id) is not None

    await revoke(hive, node.device_id)

    # Stopped, detached from the Queen and cut off, in the gate's own order and time: the Warden
    # goes first (it releases its lease over the link) and the link is closed after it, so each
    # is waited for in turn rather than the second read the moment the first holds.
    hub = hive.swarm.listener.hub
    await wait_until(lambda: node.warden_id not in attached_warden_ids(hive), timeout_s=_WAIT_S)
    await wait_until(lambda: hub.link(node.device_id) is None, timeout_s=_WAIT_S)
    assert gate.wardens.get(node.device_id) is None
    assert warden.state is WardenState.STOPPED
    # The Hive Stand's own Warden carries on.
    assert hive.warden_link.warden_id in attached_warden_ids(hive)
