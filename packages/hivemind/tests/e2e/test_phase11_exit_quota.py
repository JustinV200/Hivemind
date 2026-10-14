"""Phase 11 step 11.4 on a second machine: a device lease is held to the scratch the Queen allows.

"Scratch dir under the quota the node's `Ceilings` carry, enforced the way 3.11 does on the Hive
Stand." The second machine is a container running the Pollen Packet installed from wheels built
from this tree; the Hive is the real one (Queen, Wardens, trail, SQLite) and only its model is
scripted. A scripted Drone fills the lease's scratch past a limit on the device, through the
`PollenSession` the device's Warden leased, in two goals on one device. The first is held to the
Hive's own default for a device lease (`[swarm] scratch_quota_mb`, 64 here, not the 512 MiB the
packet would fall back on), because the Queen's first ceilings for the Warden name no figure. Then
the Queen changes that Warden's ceilings (`forage.ceilings_set`, a decision on the trail) so one
lease may hold 2 MiB, and the second goal's lease is held to her figure.

What it proves: a command that writes past the quota is stopped by the device's watchdog and does
not run on (it was told to wait thirty seconds after filling the disk, and the lease ends in a few
seconds), and the Capping gate sees the exit of a killed command and rolls the proposal back; a
write under the quota in the same lease is not touched (so it is the quota that stopped the other,
not a device that refuses every write); the figure that decided is the Queen's once she has set
one, and the manifest's before; and afterwards the scratch directory and the processes are gone,
the device holding nothing the packet did not put there.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - Every container and network is removed, whatever the test did: the janitor closes in a
      `finally`.
    - Waits are bounded polls of the Hive's trail or the container's process table.
    - The report file is written in the Drone's first round and a filling command runs in a round
      of its own after it, so the over-quota files (which stay in scratch until the release)
      cannot take the room the report needs.

See Also:
    - .claude/roadmap.md step 11.4 and 11.6 for the lease and the quota.
    - tests/e2e/test_phase11_exit_level0.py for the same device, lease and release without a quota.
    - docs/waggle/gateway-contract.md section 9.1 for the quota the packet enforces.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from e2e.kernel_helpers import (
    WorkerTurn,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.container import Janitor, Network
from e2e.swarm.drone import Script, finished, submit_pinned
from e2e.swarm.enrolled import DATA_DIR, DeviceOptions, enrol_device
from e2e.swarm.hive import HELLO, REPORT_FILE, trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.snapshots import changes_outside, files_under, settled_to

from hivemind.brood_chamber import TaskStatus
from hivemind.cli.compose import Hive, run_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.queen.forage import change_ceilings
from hivemind.swarm import SwarmNode

pytestmark = pytest.mark.e2e

_WAIT_S = 60.0  # Each wait; a container enrols and runs its task in seconds, a cold start in tens.
_MIB = 1024 * 1024
_MANIFEST_QUOTA_MB = 64  # `[swarm] scratch_quota_mb`: neither the packet's 512 MiB nor the Queen's.
_QUEEN_QUOTA_BYTES = 2 * _MIB  # What the Queen allows one lease once she changes the ceilings.
_HOLD_S = 30  # How long a filling command would go on for if nothing stopped it.
# A lease that is stopped ends in the few seconds a goal takes, far short of the command's hold.
_STOPPED_WITHIN = timedelta(seconds=_HOLD_S * 2 / 3)
_KILLED = -9  # The exit a command the device killed reports, as a local session reports a kill.


def _fill(name: str, size: int, *, then_hold: bool) -> tuple[str, ...]:
    """A command that writes `size` zero bytes to `name` in scratch, then optionally goes on."""
    # The hold keeps the command alive while the device's watchdog, which looks a few times a
    # second, measures the scratch: a write that ended before the first look would go unseen.
    tail = f"; sleep {_HOLD_S}" if then_hold else ""
    return ("sh", "-c", f"head -c {size} /dev/zero > {name}{tail}")


@dataclass(slots=True)
class _Commands:
    """The commands the next goal's Drone runs on the device; the test sets them between goals."""

    argvs: tuple[tuple[str, ...], ...] = ()

    def drone(self) -> WorkerTurn:
        """The Drone: write the report, then run `argvs` one round each, then say so."""

        def turn(request: LLMRequest) -> LLMResponse:
            # One call a round: the calls of a round run at the same time, so the report would
            # race a filling command for the room, and the one that fills must not start before
            # the report is on the device.
            done = tool_round_count(request)
            if done == 0:
                return tool_response(request, (write_call(REPORT_FILE, HELLO),))
            if done <= len(self.argvs):
                argv = list(self.argvs[done - 1])
                return tool_response(request, ((f"fill_{done}", "run_command", {"argv": argv}),))
            return text_response("Wrote the report and filled the scratch.")

        return turn


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_a_device_lease_is_held_to_the_scratch_the_queen_allows_and_stopped_past_it(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        commands = _Commands()
        terms = BridgeTerms(swarm={"scratch_quota_mb": _MANIFEST_QUOTA_MB})
        hive = bridge_hive(tmp_path, network, Script(commands.drone()).responder, terms)

        asyncio.run(_scenario(hive, janitor, image, network, commands))
    finally:
        janitor.close()


async def _scenario(
    hive: Hive, janitor: Janitor, image: str, network: Network, commands: _Commands
) -> None:
    """Enrol a container, fill its scratch past each quota in turn, and check it is as found."""
    async with run_hive(hive):
        device = await enrol_device(
            BridgeHive(hive), janitor, image, DeviceOptions(network=network)
        )
        container, node = device.container, device.node
        await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
        processes = await asyncio.to_thread(container.processes)

        # The Queen's first ceilings for the Warden name no scratch figure, so the manifest's holds:
        # a write of about 95 MiB passes the packet's 512 MiB default but not this 64 MiB.
        commands.argvs = (_fill("fill.bin", 100_000_000, then_hold=True),)
        first = await _run_goal(hive, node)
        assert await _exits_of_rolled_back(hive) == [_KILLED]
        assert await _lease_length(hive, first) < _STOPPED_WITHIN

        # Then she decides: one lease may hold 2 MiB. A write under it is let through, and the
        # next one, which takes the scratch to 5 MiB, is stopped.
        await _the_queen_sets_the_scratch_ceiling(hive, node)
        commands.argvs = (
            _fill("under.bin", _MIB, then_hold=False),
            _fill("over.bin", 4 * _MIB, then_hold=True),
        )
        second = await _run_goal(hive, node)
        # One kill per goal: had the 1 MiB write been refused as well there would be three exits.
        assert await _exits_of_rolled_back(hive) == [_KILLED, _KILLED]
        assert await _lease_length(hive, second) < _STOPPED_WITHIN

        assert await asyncio.to_thread(files_under, container, f"{DATA_DIR}/leases") == {}
        assert await settled_to(container, processes) == processes
        assert await asyncio.to_thread(changes_outside, container, DATA_DIR) == []


async def _run_goal(hive: Hive, node: SwarmNode) -> str:
    """Put a goal to the device, wait for it and its lease to end, and return the lease's id."""
    before = await _lease_ids(hive)
    goal_id = await submit_pinned(hive, node)
    task = await finished(hive, goal_id)
    assert task.status is TaskStatus.SUCCEEDED, task.outcome  # The report was written first.
    [lease_id] = [lease for lease in await _lease_ids(hive) if lease not in before]

    async def released() -> bool:
        ended = await trail_events(hive, "cell.released")
        return any(event.payload["lease_id"] == lease_id for event in ended)

    # The Warden gives the lease back the tick after its task ends.
    await wait_until(released, timeout_s=_WAIT_S)
    return lease_id


async def _lease_ids(hive: Hive) -> list[str]:
    """The leases commands were sent in, in the order they first were."""
    sent = await trail_events(hive, "swarm.command_sent")
    return list(dict.fromkeys(str(event.payload["lease_id"]) for event in sent))


async def _lease_length(hive: Hive, lease_id: str) -> timedelta:
    """How long the lease was held, from the Hive's `cell.leased` to its `cell.released`."""
    leased = await trail_events(hive, "cell.leased")
    released = await trail_events(hive, "cell.released")
    [opened] = [event for event in leased if event.payload["lease_id"] == lease_id]
    [ended] = [event for event in released if event.payload["lease_id"] == lease_id]
    return ended.at - opened.at


async def _exits_of_rolled_back(hive: Hive) -> list[object]:
    """The exit of every command the Capping gate rolled back, oldest first."""
    rolled_back = await trail_events(hive, "capping.rolled_back")
    return [event.payload.get("exit_code") for event in rolled_back]


async def _the_queen_sets_the_scratch_ceiling(hive: Hive, node: SwarmNode) -> None:
    """The Queen changes the Warden's ceilings, on the trail, to allow 2 MiB a lease."""
    assert hive.swarm is not None
    link = hive.swarm.gate.wardens.link_of(node.warden_id)
    current = hive.queen_deps.ledger.decisions.ceilings_for(node.warden_id)
    assert link is not None and current is not None  # Set at the first dispatch.
    assert current.scratch_disk_bytes_per_lease == 0  # She named no figure then.
    tighter = current.model_copy(update={"scratch_disk_bytes_per_lease": _QUEEN_QUOTA_BYTES})

    await change_ceilings(link, tighter, hive.queen_deps)

    decided = await trail_events(hive, "forage.ceilings_set")
    assert [event.payload["revision"] for event in decided] == [0, 1]
