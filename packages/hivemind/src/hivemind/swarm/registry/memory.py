"""Provide MemorySwarmRegistry: an in-memory SwarmRegistry for tests, demos and `hive doctor`.

`MemorySwarmRegistry` keeps invites and enrolled devices in two dictionaries and applies exactly
the rules `hivemind.swarm.registry.rules` states, so it behaves like the SQLite registry in every
way the contract suite checks while needing no file. It is shipped code, not test-only
(codingrules 14.4: fakes live in `src/`), because `pollen`'s own test harness and the demo paths
use it too. Atomicity is one lock: every method computes the new row, records the optional trail
event and only then stores the row, so an event that cannot be recorded leaves nothing changed,
which is what the SQLite registry's single transaction guarantees. Given a `PheromoneTrail` it
records the events it is handed there (so a test can read them back exactly as it would read the
Hive's); given none it drops them.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.registry`.
    Implements `SwarmRegistry`; constructed directly by tests and demo scripts. Calls into
    hivemind.pheromone, hivemind.swarm.errors, .models, .node_state and .registry.rules only.

Key invariants:
    - Every method holds the registry's one `asyncio.Lock` for its whole body, so a redemption
      and a revocation never interleave.
    - An event is recorded before the row is stored, and a recording failure propagates, so a
      stored change always has its event.
    - Lookups return the stored (frozen) values themselves; nothing here hands out a mutable row.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code".
    - hivemind.swarm.registry.sqlite for the durable implementation this one matches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel
from hivemind.forage import ForageCapacity
from hivemind.pheromone import PheromoneTrail, SwarmEvent
from hivemind.swarm.errors import (
    EnrolmentDeniedError,
    EnrolRefusal,
    InviteConflictError,
    InviteNotFoundError,
    NodeNotFoundError,
)
from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import rules
from waggle.ids import CellId, DeviceId, NodeId
from waggle.messages.swarm import RuntimeLevel

__all__ = ["MemorySwarmRegistry"]


class MemorySwarmRegistry:
    """A SwarmRegistry held in two dictionaries, matching the SQLite one rule for rule."""

    def __init__(self, trail: PheromoneTrail | None = None) -> None:
        """Build an empty registry.

        Args:
            trail: Where the events handed to a method are recorded; None drops them.
        """
        self._trail = trail
        self._invites: dict[str, SwarmInvite] = {}
        self._nodes: dict[DeviceId, SwarmNode] = {}
        # Serialises every method: the dictionaries are only touched under it, and an event's
        # recording (an await) sits between computing a change and storing it.
        self._lock = asyncio.Lock()

    async def put_invite(self, invite: SwarmInvite, event: SwarmEvent | None = None) -> None:
        """Store a freshly minted invite; see `SwarmRegistry.put_invite`."""
        async with self._lock:
            if invite.state is not InviteState.ISSUED:
                raise InviteConflictError(f"A new invite must be ISSUED, got {invite.state.name}.")
            if invite.code_hash in self._invites:
                raise InviteConflictError(
                    f"An invite with the token hash {invite.code_hash[:12]}... already exists."
                )
            await self._record(event)
            self._invites[invite.code_hash] = invite

    async def get_invite(self, code_hash: str) -> SwarmInvite | None:
        """Return the invite with this token hash, or None."""
        async with self._lock:
            return self._invites.get(code_hash)

    async def invites(self, state: InviteState | None = None) -> tuple[SwarmInvite, ...]:
        """List invites, oldest first; see `SwarmRegistry.invites`."""
        async with self._lock:
            chosen = [i for i in self._invites.values() if state is None or i.state is state]
            return tuple(sorted(chosen, key=lambda i: (i.created_at, i.code_hash)))

    async def expire_invites(self, at: datetime) -> int:
        """Move every lapsed ISSUED invite to EXPIRED; see `SwarmRegistry.expire_invites`."""
        async with self._lock:
            lapsed = [
                invite
                for invite in self._invites.values()
                if invite.state is InviteState.ISSUED and invite.expires_at <= at
            ]
            for invite in lapsed:
                self._invites[invite.code_hash] = rules.expired_invite(invite)
            return len(lapsed)

    async def redeem(
        self, code_hash: str, node: SwarmNode, at: datetime, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Spend an invite and create its node atomically; see `SwarmRegistry.redeem`."""
        async with self._lock:
            invite = self._invites.get(code_hash)
            refusal = rules.judge_redemption(invite, at)
            # A lapsed invite is moved to EXPIRED even though this redemption fails, so the
            # registry's own record is as true as the SQLite one's.
            if (
                invite is not None
                and refusal is EnrolRefusal.EXPIRED
                and invite.state is InviteState.ISSUED
            ):
                self._invites[code_hash] = rules.expired_invite(invite)
            if refusal is None and rules.identity_taken(self._nodes.values(), node):
                refusal = EnrolRefusal.IDENTITY_IN_USE
            # `invite is None` always comes with a refusal; the test only narrows the type.
            if invite is None or refusal is not None:
                raise EnrolmentDeniedError(
                    refusal or EnrolRefusal.UNKNOWN_INVITE, node.device_id, node.node_id
                )
            await self._record(event)
            self._invites[code_hash] = rules.redeemed_invite(invite, node.device_id)
            self._nodes[node.device_id] = node
            return node

    async def revoke_invite(self, code_hash: str, event: SwarmEvent | None = None) -> SwarmInvite:
        """Cancel an unused invite; see `SwarmRegistry.revoke_invite`."""
        async with self._lock:
            invite = self._invites.get(code_hash)
            if invite is None:
                raise InviteNotFoundError(code_hash)
            revoked = rules.revoked_invite(invite)
            await self._record(event)
            self._invites[code_hash] = revoked
            return revoked

    async def node(self, device_id: DeviceId) -> SwarmNode | None:
        """Return the enrolled device with this id, or None."""
        async with self._lock:
            return self._nodes.get(device_id)

    async def node_by_node_id(self, node_id: NodeId) -> SwarmNode | None:
        """Return the enrolled device whose frames this node id signs, or None."""
        async with self._lock:
            return next((n for n in self._nodes.values() if n.node_id == node_id), None)

    async def node_by_cell(self, cell_id: CellId) -> SwarmNode | None:
        """Return the enrolled device that is this Real Cell, or None."""
        async with self._lock:
            return next((n for n in self._nodes.values() if n.cell_id == cell_id), None)

    async def nodes(self, status: NodeStatus | None = None) -> tuple[SwarmNode, ...]:
        """List enrolled devices, oldest enrolment first."""
        async with self._lock:
            chosen = [n for n in self._nodes.values() if status is None or n.status is status]
            return tuple(sorted(chosen, key=lambda n: (n.enrolled_at, n.device_id)))

    async def set_status(
        self,
        device_id: DeviceId,
        expected: NodeStatus,
        new: NodeStatus,
        at: datetime,
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Move a node's status, a compare-and-set; see `SwarmRegistry.set_status`."""
        return await self._change(
            device_id, lambda node: rules.with_status(node, expected, new, at), event
        )

    async def set_level(
        self, device_id: DeviceId, level: RuntimeLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Move a node one rung along the ladder; see `SwarmRegistry.set_level`."""
        return await self._change(device_id, lambda node: rules.with_level(node, level), event)

    async def set_desired_level(self, device_id: DeviceId, level: RuntimeLevel | None) -> SwarmNode:
        """Record the operator's pending rung request; see `SwarmRegistry.set_desired_level`."""
        return await self._change(device_id, lambda n: rules.with_desired_level(n, level), None)

    async def set_ceiling(self, device_id: DeviceId, ceiling: RuntimeLevel | None) -> SwarmNode:
        """Set or clear the Queen's ceiling on a device; see `SwarmRegistry.set_ceiling`."""
        return await self._change(device_id, lambda node: rules.with_ceiling(node, ceiling), None)

    async def set_access(
        self,
        device_id: DeviceId,
        level: AccessLevel,
        granted: tuple[str, ...],
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Change a node's access level; see `SwarmRegistry.set_access`."""
        return await self._change(
            device_id, lambda node: rules.with_access(node, level, granted), event
        )

    async def set_shield(
        self, device_id: DeviceId, shield: CombShieldLevel, event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Change a node's Comb Shield tier; see `SwarmRegistry.set_shield`."""
        return await self._change(device_id, lambda node: rules.with_shield(node, shield), event)

    async def set_watch_roots(
        self, device_id: DeviceId, roots: tuple[str, ...], event: SwarmEvent | None = None
    ) -> SwarmNode:
        """Replace the directories a Warden watches; see `SwarmRegistry.set_watch_roots`."""
        return await self._change(device_id, lambda n: rules.with_watch_roots(n, roots), event)

    async def touch(
        self, device_id: DeviceId, at: datetime, runtime_version: str | None = None
    ) -> None:
        """Note that a verified frame arrived from the device; see `SwarmRegistry.touch`."""
        await self._change(device_id, lambda node: rules.touched(node, at, runtime_version), None)

    async def update_facts(
        self, device_id: DeviceId, capabilities: CellCapabilities, capacity: ForageCapacity
    ) -> SwarmNode:
        """Store the latest capability and capacity report; see `SwarmRegistry.update_facts`."""
        return await self._change(
            device_id, lambda node: rules.with_facts(node, capabilities, capacity), None
        )

    async def _change(
        self,
        device_id: DeviceId,
        change: Callable[[SwarmNode], SwarmNode],
        event: SwarmEvent | None,
    ) -> SwarmNode:
        """Apply one pure change to a stored node, recording `event` first, under the lock."""
        async with self._lock:
            current = self._nodes.get(device_id)
            if current is None:
                raise NodeNotFoundError(device_id)
            updated = change(current)
            await self._record(event)
            self._nodes[device_id] = updated
            return updated

    async def _record(self, event: SwarmEvent | None) -> None:
        """Record `event` on the trail, if there is both; a failure leaves the row unchanged."""
        if event is not None and self._trail is not None:
            await self._trail.record(event)
