"""Define the SwarmRegistry protocol: the Hive's table of enrolled devices and their invites.

The swarm registry is where the Hive remembers which devices the operator enrolled and which
invites are still waiting to admit one. It holds two kinds of row: `SwarmInvite` (the SHA-256 of a
single-use token, the access cap and tier the operator chose, an expiry, a state) and `SwarmNode`
(an enrolled device: its ids, its pinned key, its limits, its present condition). Everything that
moves a row goes through this protocol, so the SQLite registry the Hive runs on and the in-memory
fake the tests use can be held to one contract suite (codingrules 14.3). Redemption is the
guarded step: `redeem` checks the invite, creates the node and spends the invite as one atomic
operation, which is what makes a token single use even when two devices present it at once. Every
method that changes a row takes an optional trail `event`, which the registry writes in the same
transaction as the change (codingrules Appendix C: every transition is an event committed with
its state change), so a crash cannot leave an enrolment without its `swarm.enrolled` record.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.registry`.
    Implemented by `SqliteSwarmRegistry` (the Hive's one database, own migration series
    "swarm") and `MemorySwarmRegistry` (tests and demos). Called by the enrolment door, the
    gateway listener and heartbeat monitor, and the Queen's swarm gate (Layer 6) and the `hive
    swarm` commands (Layer 7).

Key invariants:
    - `redeem` either creates the node and moves the invite to REDEEMED, or changes nothing but,
      for a lapsed invite, the invite's own move to EXPIRED; there is no half state.
    - A lookup that finds nothing returns None; a write that names a missing row raises
      `NodeNotFoundError` or `InviteNotFoundError`.
    - A node's status and rung change only along the tables in `hivemind.swarm.node_state` and
      `hivemind.swarm.level`, and a revoked node accepts no change at all.
    - An `event` passed to a method is recorded if and only if the change was applied.

See Also:
    - docs/waggle/enrolment.md sections 3.2 and 6 for the invite row and the redemption.
    - hivemind.swarm.registry.rules for the pure rules both implementations share.
    - tests/contracts/test_swarm_registry_contract.py for the suite both implementations pass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel
from hivemind.forage import ForageCapacity
from hivemind.pheromone import SwarmEvent
from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.ids import CellId, DeviceId, NodeId
from waggle.messages.swarm import RuntimeLevel

__all__ = ["InviteRegistry", "NodeRegistry", "SwarmRegistry"]


class InviteRegistry(Protocol):
    """The invite half of the registry: single-use tokens, stored as hashes, and their redemption.

    Implementations must be safe to call concurrently: two devices may present one token at once.
    """

    async def put_invite(self, invite: SwarmInvite, event: SwarmEvent | None = None) -> None:
        """Store a freshly minted invite.

        Args:
            invite: An ISSUED invite, keyed by the hash of its token.
            event: The `swarm.invited` event to record with it, if any.

        Raises:
            InviteConflictError: An invite with this token hash is already stored, or `invite`
                is not ISSUED.
        """
        ...

    async def get_invite(self, code_hash: str) -> SwarmInvite | None:
        """Return the invite with this token hash, or None when there is none.

        Args:
            code_hash: The SHA-256 of a presented token, lowercase hex.

        Returns:
            The stored invite in whatever state it is, or None.
        """
        ...

    async def invites(self, state: InviteState | None = None) -> tuple[SwarmInvite, ...]:
        """List invites, oldest first.

        Args:
            state: Only invites in this state; None lists every one.

        Returns:
            The matching invites ordered by creation time.
        """
        ...

    async def expire_invites(self, at: datetime) -> int:
        """Move every ISSUED invite whose time to live lapsed by `at` to EXPIRED.

        Args:
            at: The Hive Stand's clock now.

        Returns:
            How many invites were moved.
        """
        ...

    async def redeem(
        self, code_hash: str, node: SwarmNode, at: datetime, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Spend an invite and create the node it admits, atomically.

        Args:
            code_hash: The SHA-256 of the presented token.
            node: The node to create, built by the enrolment decision; its ids and key are new.
            at: The Hive Stand's clock at presentation; the Queen's clock decides expiry.
            event: The `swarm.enrolled` event to record with it, if any.

        Returns:
            The stored node.

        Raises:
            EnrolmentDeniedError: The invite does not exist, was used, lapsed or was revoked
                (`EnrolRefusal`), or the node's device, node, Cell, Warden or key is already
                enrolled. A lapsed invite is moved to EXPIRED before the error is raised.
        """
        ...

    async def revoke_invite(self, code_hash: str, event: SwarmEvent | None = None) -> SwarmInvite:
        """Cancel an unused invite.

        Args:
            code_hash: The SHA-256 of the invite's token.
            event: The event to record with it, if any.

        Returns:
            The invite, now REVOKED.

        Raises:
            InviteNotFoundError: No invite has this hash.
            InviteConflictError: The invite is no longer ISSUED.
        """
        ...


class NodeRegistry(Protocol):
    """The node half of the registry: enrolled devices, and their status, rung and terms.

    Implementations must be safe to call concurrently: the heartbeat monitor, the gateway and an
    operator command may all write at once.
    """

    async def node(self, device_id: DeviceId) -> SwarmNode | None:
        """Return the enrolled device with this id, or None."""
        ...

    async def node_by_node_id(self, node_id: NodeId) -> SwarmNode | None:
        """Return the enrolled device whose frames this node id signs, or None."""
        ...

    async def node_by_cell(self, cell_id: CellId) -> SwarmNode | None:
        """Return the enrolled device that is this Real Cell, or None."""
        ...

    async def nodes(self, status: NodeStatus | None = None) -> tuple[SwarmNode, ...]:
        """List enrolled devices, oldest enrolment first.

        Args:
            status: Only nodes in this status; None lists every one, revoked included.

        Returns:
            The matching nodes.
        """
        ...

    async def set_status(
        self,
        device_id: DeviceId,
        expected: NodeStatus,
        new: NodeStatus,
        at: datetime,
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Move a node's status from `expected` to `new`, a compare-and-set.

        Args:
            device_id: The device.
            expected: The status the caller read; the change is refused if it has moved.
            new: The status to move to.
            at: When it happened; becomes `revoked_at` for a revocation.
            event: The `swarm.online`, `swarm.unreachable` or `swarm.revoked` event to record.

        Returns:
            The node in its new status.

        Raises:
            NodeNotFoundError: No such device.
            NodeStatusConflictError: The node is not in `expected`.
            InvalidNodeTransitionError: `expected` to `new` is not an edge.
        """
        ...

    async def set_level(
        self, device_id: DeviceId, level: RuntimeLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Move a node to an adjacent rung of the runtime ladder.

        Args:
            device_id: The device.
            level: The rung to move to; clears a pending `desired_level` once reached.
            event: The `swarm.promoted` or `swarm.demoted` event to record.

        Returns:
            The node on its new rung.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
            LadderError: `level` is not adjacent to the node's rung.
        """
        ...

    async def set_desired_level(self, device_id: DeviceId, level: RuntimeLevel | None) -> SwarmNode:
        """Record the rung the operator asked for with `promote` or `demote`, or clear it.

        Args:
            device_id: The device.
            level: The wanted rung, or None to withdraw the request.

        Returns:
            The node carrying the request.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
        """
        ...

    async def set_ceiling(self, device_id: DeviceId, ceiling: RuntimeLevel | None) -> SwarmNode:
        """Set the highest rung the Queen may take a device to on her own, or clear it.

        The operator's `demote` pins a device at the rung it lands on and their `promote` lets
        it go; the Queen's own rung decisions never climb past it.

        Args:
            device_id: The device.
            ceiling: The rung to hold the device at or below, or None for no limit.

        Returns:
            The node carrying the ceiling.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
        """
        ...

    async def set_access(
        self,
        device_id: DeviceId,
        level: AccessLevel,
        granted: tuple[str, ...],
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Change a node's access level and the capability strings that go with it.

        Args:
            device_id: The device.
            level: The new access level.
            granted: The capability strings the packet enforces from now on.
            event: The `swarm.access_changed` event to record.

        Returns:
            The node at its new level.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
        """
        ...

    async def set_shield(
        self, device_id: DeviceId, shield: CombShieldLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Change a node's Comb Shield tier, which only the operator does.

        Args:
            device_id: The device.
            shield: MEADOW or PROPOLIS.
            event: The `swarm.shield_changed` event to record.

        Returns:
            The node at its new tier.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
            pydantic.ValidationError: `shield` is NIGHT_VEIL.
        """
        ...

    async def touch(
        self, device_id: DeviceId, at: datetime, runtime_version: str | None = None
    ) -> None:
        """Note that a verified frame arrived from the device at `at`.

        Args:
            device_id: The device.
            at: When the frame arrived.
            runtime_version: The `pollen` version it reported, if it did.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
        """
        ...

    async def update_facts(
        self, device_id: DeviceId, capabilities: CellCapabilities, capacity: ForageCapacity
    ) -> SwarmNode:
        """Store the capabilities and capacity a device's latest report carried.

        Args:
            device_id: The device.
            capabilities: What it reports it can do.
            capacity: Its Forage capacity.

        Returns:
            The node with both replaced.

        Raises:
            NodeNotFoundError: No such device.
            NodeRevokedError: The node is revoked.
        """
        ...


class SwarmRegistry(InviteRegistry, NodeRegistry, Protocol):
    """Store invites and enrolled devices, and move them along their machines atomically.

    The two halves are separate protocols so a collaborator that only mints invites, or only
    reads nodes, can be given just the half it uses; the Hive's registry implements both.
    """
