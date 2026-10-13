"""Run a real Pollen Packet in this process and lease it through SwarmSource, for a contract suite.

The CellSession contract (roadmap step 11.5) holds `PollenSession` to the same clauses as the
Hive Stand's own session, and the only honest way to show that is against the real thing on the
far end: the packet from `packages/pollen`, enrolled on a real swarm listener over a real
WebSocket, running its real executors on this machine's disk and process table. This module is
that wiring. `start_device` mints an invite, runs the packet's real enrolment ceremony into a data
directory of the caller's choosing, starts `PollenPacket.run` as a task, waits for it to dial in,
leases the device through `SwarmSource` with the real Guard in front of it, and opens a
`PollenSession`. `PollenDevice.stop` ends all of that in the order a Warden would: session, lease
(the packet must confirm it left the device as found), then the packet itself.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `contracts.test_cell_session_contract`; builds on `builders.swarm.live` for the Hive Stand.

Key invariants:
    - The packet runs at FULL access (the harness asks for it, and the invite caps at it), so
      the packet's own capability wall stays out of the way of the clauses about the Hive-side
      path rules; the packet's wall has its own suite.
    - `stop` always ends the packet, even when the lease would not release.
    - A lease the device cannot confirm released is reported, never swallowed.

See Also:
    - builders.swarm.live for the Hive Stand the packet dials.
    - docs/waggle/gateway-contract.md sections 9 and 12 for what the packet must do.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from builders.cells import make_identity
from builders.swarm.live import LiveHive

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cell.lease import LeaseReleaseReport, LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.guard import (
    Enforcer,
    PolicyContext,
    load_guard_policy,
    warden_principal,
    warden_set,
)
from hivemind.manifest import GuardSection
from hivemind.swarm import SwarmSource
from hivemind.swarm.session import EnforcedCommandGuard, PollenSession
from pollen.agent import PollenPacket
from pollen.build import build_packet
from pollen.enrol import EnrolPlan, enrol_device, parse_invite
from pollen.platform import select_shim
from pollen.state import DataLayout, PacketState
from waggle.clock import SystemClock
from waggle.ids import WardenId

__all__ = ["PollenDevice", "start_device"]

ENROL_TIMEOUT_S = 30.0  # The whole ceremony on loopback takes well under a second.
DIAL_TIMEOUT_S = 30.0  # The packet dials as soon as it runs; this bounds a hang, not a wait.
STOP_TIMEOUT_S = 60.0  # The packet's shutdown sweep releases whatever is still leased.


@dataclass
class PollenDevice:
    """One enrolled packet running in this process, with a lease and a session open on it."""

    state: PacketState
    packet: PollenPacket
    task: asyncio.Task[None]
    source: SwarmSource
    lease: RealCellLease
    session: PollenSession

    async def stop(self) -> LeaseReleaseReport | None:
        """Close the session, release the lease and stop the packet, in that order.

        Returns:
            The device's report of how it left the lease, or None when the lease was already
            released (a test released it itself).
        """
        report: LeaseReleaseReport | None = None
        try:
            await self.session.close()
            if self.lease.state is LeaseState.OPEN:
                async with asyncio.timeout(STOP_TIMEOUT_S):  # Network: the device restores.
                    report = await self.lease.release()
        finally:
            self.packet.stop()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                async with asyncio.timeout(STOP_TIMEOUT_S):  # The packet's shutdown sweep.
                    await self.task
        return report


async def start_device(
    hive: LiveHive, data_dir: Path, allowed_paths: tuple[Path, ...] = ()
) -> PollenDevice:
    """Enrol a packet on `hive`, run it, lease it and open a session on the lease.

    Args:
        hive: The running Hive Stand to enrol with.
        data_dir: The packet's data directory (its key, state, leases and scratch go here).
        allowed_paths: Paths outside scratch the lease allows.

    Returns:
        The running device; the caller awaits `stop()`.

    Raises:
        TimeoutError: The packet did not enrol, or did not dial in, in time.
    """
    clock, layout, shim = SystemClock(), DataLayout(data_dir), select_shim()
    minted = await hive.mint(AccessLevel.FULL, CombShieldLevel.MEADOW)
    invite = parse_invite(minted.invite_string, clock.now())
    async with asyncio.timeout(ENROL_TIMEOUT_S):  # Network: the real ceremony over a socket.
        state = await enrol_device(EnrolPlan(invite, layout, shim, clock))
    packet = build_packet(layout, shim, clock)
    task = asyncio.ensure_future(packet.run())
    try:
        await hive.wait_for(state.device_id, DIAL_TIMEOUT_S)
        node = await hive.node(state.device_id)
        source = SwarmSource(node, hive.hub, hive.recorder, _guard(hive, node.warden_id, layout))
        hive.hub.subscribe(source)  # So a dead-man release the device reports is heard.
        request = LeaseRequest(
            cell_id=node.cell_id,
            holder=node.warden_id,
            task_id=None,
            access_level=AccessLevel.FULL,
            allowed_paths=allowed_paths,
        )
        lease = await source.lease(request)
        opened = await source.open_session(lease)
    except BaseException:
        packet.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    assert isinstance(opened, PollenSession)
    return PollenDevice(state, packet, task, source, lease, opened)


def _guard(hive: LiveHive, warden_id: WardenId, layout: DataLayout) -> EnforcedCommandGuard:
    """Build the real Guard for the device's Warden, holding everything FULL access allows."""
    policy = load_guard_policy(None, GuardSection())
    identity = make_identity(hive.clock, hive_id=hive.party.hive_id, node_id=hive.party.node_id)
    return EnforcedCommandGuard(
        Enforcer(policy, hive.trail, hive.clock, identity),
        hive.recorder,
        warden_principal(warden_id),
        # FULL's set does not depend on the scratch root, which the lease has not made yet.
        warden_set(policy, AccessLevel.FULL, PurePosixPath(layout.root)),
        PolicyContext(comb_shield=CombShieldLevel.MEADOW, access_level=AccessLevel.FULL),
    )
