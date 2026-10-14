"""Lease a connected gateway through SwarmSource, with the real Guard in front, and open a session.

A Warden reaches a borrowed device in three steps: it asks the Hive's `SwarmSource` for a lease on
the device's Cell, the Guard (the Hive's gatekeeper for every command and file touch) wraps the
session that comes back, and the `PollenSession` is the terminal the Worker uses. Tests that run a
real packet, in this process or in a container, need exactly that and nothing scripted in between.
`lease_device` does it for a node that is already connected to a live Hive Stand: the Guard is the
real `Enforcer` over the shipped default policy, holding the set a FULL-access Warden gets, so
whatever the session refuses is the product's own rule and whatever the packet refuses is the
packet's.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the CellSession suite's
    Pollen harness and by the container tests in `e2e.swarm`; builds on `builders.swarm.live`.

Key invariants:
    - The lease asks for FULL access, so the packet's capability wall stays out of the way of the
      clauses about the Hive-side path rules; the packet's wall has its own suite.
    - The source is subscribed to the hub, so a dead-man release the device reports is heard.
    - The Guard's FULL set does not depend on the scratch root, which the lease has not made yet;
      `root` only names the device's data directory.

See Also:
    - hivemind.swarm.source for what leasing a device does.
    - hivemind.swarm.session for the session the Worker holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from builders.cells import make_identity
from builders.swarm.live import LiveHive

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.guard import (
    Enforcer,
    PolicyContext,
    load_guard_policy,
    warden_principal,
    warden_set,
)
from hivemind.manifest import GuardSection
from hivemind.swarm import SwarmSource
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.session import EnforcedCommandGuard, PollenSession
from waggle.ids import WardenId

__all__ = ["LeasedDevice", "lease_device", "real_guard"]


@dataclass(frozen=True, slots=True)
class LeasedDevice:
    """A device leased to its Warden: the source that leased it, the lease and the session."""

    source: SwarmSource
    lease: RealCellLease
    session: PollenSession


async def lease_device(
    hive: LiveHive, node: SwarmNode, root: PurePosixPath, allowed_paths: tuple[Path, ...] = ()
) -> LeasedDevice:
    """Lease a connected device to its own Warden and open a session on the lease.

    Args:
        hive: The live Hive Stand the device is connected to.
        node: The device's node, whose Cell and Warden the lease is for.
        root: The device's data directory, as the device spells it.
        allowed_paths: Paths outside scratch the lease allows the session to touch.

    Returns:
        The source, the lease and the session; the caller closes the session and releases the
        lease.
    """
    guard = real_guard(hive, node.warden_id, root)
    source = SwarmSource(node, hive.hub, hive.recorder, guard)
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
    assert isinstance(opened, PollenSession)
    return LeasedDevice(source, lease, opened)


def real_guard(hive: LiveHive, warden_id: WardenId, root: PurePosixPath) -> EnforcedCommandGuard:
    """Build the real Guard for a device's Warden, holding everything FULL access allows."""
    policy = load_guard_policy(None, GuardSection())
    identity = make_identity(hive.clock, hive_id=hive.party.hive_id, node_id=hive.party.node_id)
    return EnforcedCommandGuard(
        Enforcer(policy, hive.trail, hive.clock, identity),
        hive.recorder,
        warden_principal(warden_id),
        warden_set(policy, AccessLevel.FULL, root),
        PolicyContext(comb_shield=CombShieldLevel.MEADOW, access_level=AccessLevel.FULL),
    )
