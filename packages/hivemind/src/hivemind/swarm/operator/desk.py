"""Provide OperatorDesk: the operator's acts on an enrolled device, each with its trail event.

The operator (the human running the Hive) has four acts on a device that is already enrolled, and
`hive swarm` is their terminal. They *revoke* it (its enrolment is final from then on and the
Queen cuts it off), *change its access level* (how much of the device the Hive may touch), *change
its Comb Shield tier* (the security tier the operator vouches for; only the operator ever sets a
Real Cell's tier), and *ask for a rung on the runtime ladder* (`promote` or `demote`, which moves
the device's Warden, its supervisor, onto the device or off it, or gives the device a model
server of its own). The desk writes each act into the registry and, in the same transaction, the
trail event that records it (`swarm.revoked`, `swarm.access_changed`, `swarm.shield_changed`).
It never talks to a device: the running Hive's swarm gate reads the registry on every beat and
does the work (closing a revoked device's link, sending the device its new terms, moving its
Warden), so the same commands work whether or not a Hive is running. A rung request is only a
wish written on the node (`desired_level`); nothing is recorded until the Queen acts on it,
because only then has anything happened. The Queen also moves devices on her own (relief, a task
that must survive a lost link), and the operator always wins against her: a `demote` also sets
the node's `ceiling` to the rung the device lands on, which pins it there so she does not climb
back by herself (on the bottom rung it pins the device as a gateway and asks for nothing), and a
`promote` lifts the ceiling.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    Called by the `hive swarm` commands (Layer 7) with the registry the running Hive also opens.
    Calls into the registry, the recorder, the ladder's table and the capability strings of
    `hivemind.swarm.enrolment`.

Key invariants:
    - Every act reads the node again before it writes, so a stale reference (the status moved, a
      colleague revoked it first) is judged on what is true now.
    - A change that is already true is not made: the result says `is_changed=False`, nothing is
      written and no event is recorded, so a repeated command leaves the trail alone.
    - A revoked device accepts no change at all; `revoke` on one is the only act that returns.
    - A rung request is always one step from the rung the device is on, up or down, never a jump.
    - A `demote` sets the ceiling before it writes the wish, so a Queen reading between the two
      writes finds the device pinned, never free to climb back.
    - An event carries ids, tier and level names, and a reason code; never a key or a token.

See Also:
    - docs/waggle/enrolment.md sections 10 and 11 for what a change and a revocation do next.
    - hivemind.queen.swarm_gate.reconcile for how the running Hive applies what is written here.
    - hivemind.swarm.registry for the writes this desk makes.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.enrolment import granted_capabilities
from hivemind.swarm.errors import NodeNotFoundError, NodeRevokedError, NodeStatusConflictError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator.errors import TierRefusedError
from hivemind.swarm.operator.lookup import find_node
from hivemind.swarm.operator.rung import ask_for_rung
from hivemind.swarm.registry import SwarmRegistry
from waggle.ids import DeviceId

# How many times a revocation re-reads and retries when the node's status moved under it. The
# heartbeat monitor flips a device between ONLINE and UNREACHABLE only when its link changes, so a
# collision is rare and a second look settles it; three tries cover a flapping link.
STATUS_ATTEMPTS = 3
REVOKED_REASON = "operator"  # The reason code `swarm.revoked` carries: a person revoked it.

__all__ = ["REVOKED_REASON", "STATUS_ATTEMPTS", "NodeChange", "OperatorDesk"]


@dataclass(frozen=True, slots=True)
class NodeChange:
    """What an act on a device did.

    Attributes:
        node: The device as the registry holds it after the act (or as it already was).
        is_changed: Whether the act wrote anything. False means the device already was as asked,
            nothing was written and no event was recorded.
    """

    node: SwarmNode
    is_changed: bool


class OperatorDesk:
    """The operator's acts on enrolled devices, written to the registry with their trail events."""

    def __init__(self, registry: SwarmRegistry, recorder: SwarmRecorder) -> None:
        """Build the desk over the registry and the recorder that stamps its events.

        Args:
            registry: Where devices live; the running Hive reads the same rows.
            recorder: The trail, clock and identity events are built with. The identity's actor
                is who the trail says did it, so a command passes `human`.
        """
        self._registry = registry
        self._recorder = recorder

    async def find(self, reference: str) -> SwarmNode:
        """Return the one enrolled device the operator's words name.

        Args:
            reference: A device id, a Cell id or a label.

        Returns:
            The device, whatever its status.

        Raises:
            UnknownNodeError: Nothing matches.
            AmbiguousNodeError: A label matches several devices.
        """
        return find_node(await self._registry.nodes(), reference)

    async def find_live(self, reference: str) -> SwarmNode:
        """Return the one enrolled device the operator's words name, if it can still be used.

        Args:
            reference: A device id, a Cell id or a label.

        Returns:
            The device, as the registry holds it now, not revoked.

        Raises:
            UnknownNodeError: Nothing matches.
            AmbiguousNodeError: A label matches several devices.
            NodeRevokedError: The device is revoked, so no work can be given to it.
        """
        found = await self.find(reference)
        return await self._live(found.device_id)

    async def revoke(self, node: SwarmNode) -> NodeChange:
        """Revoke a device: its enrolment becomes final and `swarm.revoked` is recorded.

        Args:
            node: The device to revoke, as found by `find`.

        Returns:
            The revoked device; `is_changed` is False when it already was revoked.

        Raises:
            NodeNotFoundError: The device is no longer in the registry.
            NodeStatusConflictError: The status kept moving under every attempt.
        """
        # A compare-and-set refuses a write made on a stale status, so each attempt reads the
        # device afresh; only the last attempt's conflict, if any, reaches the caller.
        for _ in range(STATUS_ATTEMPTS - 1):
            try:
                return await self._revoke_once(node.device_id)
            except NodeStatusConflictError:
                continue
        return await self._revoke_once(node.device_id)

    async def set_access(self, node: SwarmNode, level: AccessLevel) -> NodeChange:
        """Change a device's access level, and the capability strings its packet enforces.

        Args:
            node: The device, as found by `find`.
            level: The new access level. The operator may grant any level: the invite's cap
                limited the first grant, and this is a later decision of the same person.

        Returns:
            The device at its new level; `is_changed` is False when it already was there.

        Raises:
            NodeNotFoundError: The device is no longer in the registry.
            NodeRevokedError: The device is revoked.
        """
        current = await self._live(node.device_id)
        if current.access_level is level:
            return NodeChange(current, is_changed=False)
        event = self._recorder.swarm_event(
            "swarm.access_changed",
            current.device_id,
            {
                "device_id": current.device_id,
                "cell_id": current.cell_id,
                "from_level": current.access_level.value,
                "to_level": level.value,
            },
        )
        # The strings are recomputed for the new level from the Guard's own table, the way
        # enrolment computes them, so the packet is never told more than the level allows.
        granted = granted_capabilities(level, current.cell_id)
        changed = await self._registry.set_access(current.device_id, level, granted, event)
        return NodeChange(changed, is_changed=True)

    async def set_shield(self, node: SwarmNode, shield: CombShieldLevel) -> NodeChange:
        """Change a device's Comb Shield tier, the one thing only the operator decides.

        Args:
            node: The device, as found by `find`.
            shield: MEADOW, or PROPOLIS for a device that already runs a VPN.

        Returns:
            The device at its new tier; `is_changed` is False when it already was there.

        Raises:
            NodeNotFoundError: The device is no longer in the registry.
            NodeRevokedError: The device is revoked.
            TierRefusedError: `shield` is NIGHT_VEIL, which exists only for Virtual Cells.
        """
        # Refused before anything is read or written, whatever the device's state: a borrowed
        # device is never torn down, and Night Veil is a promise of teardown.
        if shield is CombShieldLevel.NIGHT_VEIL:
            raise TierRefusedError(node.device_id)
        current = await self._live(node.device_id)
        if current.comb_shield is shield:
            return NodeChange(current, is_changed=False)
        event = self._recorder.swarm_event(
            "swarm.shield_changed",
            current.device_id,
            {
                "device_id": current.device_id,
                "cell_id": current.cell_id,
                "from_tier": current.comb_shield.value,
                "to_tier": shield.value,
            },
        )
        changed = await self._registry.set_shield(current.device_id, shield, event)
        return NodeChange(changed, is_changed=True)

    async def move(self, node: SwarmNode, *, promote: bool) -> NodeChange:
        """Ask for the rung above (or below) the one the device is on, and set its ceiling.

        Only the wish is written (`desired_level`); the running Hive's swarm gate acts on it, and
        records `swarm.promoted` or `swarm.demoted` when it does, so nothing is recorded here. The
        ceiling (the highest rung the Queen may take the device to on her own) goes with it: a
        `promote` lifts it, a `demote` sets it to the rung the device is being lowered to. A
        `demote` on the bottom rung has nothing to lower: it pins the device there.

        Args:
            node: The device, as found by `find`.
            promote: True to climb (`promote`), False to descend (`demote`).

        Returns:
            The device carrying the wish and the ceiling; `is_changed` is False when both
            already were as asked.

        Raises:
            NodeNotFoundError: The device is no longer in the registry.
            NodeRevokedError: The device is revoked.
            RungUnavailableError: A `promote` of a device already on the top rung.
        """
        current = await self._live(node.device_id)
        found, is_changed = await ask_for_rung(self._registry, current, promote=promote)
        return NodeChange(found, is_changed=is_changed)

    async def _revoke_once(self, device_id: DeviceId) -> NodeChange:
        """Read the device and revoke it on the status just read; one attempt of `revoke`."""
        current = await self._current(device_id)
        if current.status is NodeStatus.REVOKED:
            return NodeChange(current, is_changed=False)
        event = self._recorder.swarm_event(
            "swarm.revoked",
            current.device_id,
            {
                "device_id": current.device_id,
                "cell_id": current.cell_id,
                "from_status": current.status.value,
                "reason": REVOKED_REASON,
            },
        )
        revoked = await self._registry.set_status(
            current.device_id, current.status, NodeStatus.REVOKED, self._recorder.clock.now(), event
        )
        return NodeChange(revoked, is_changed=True)

    async def _current(self, device_id: DeviceId) -> SwarmNode:
        """Read the device as the registry holds it now."""
        found = await self._registry.node(device_id)
        if found is None:
            raise NodeNotFoundError(device_id)
        return found

    async def _live(self, device_id: DeviceId) -> SwarmNode:
        """Read the device as the registry holds it now, refusing a revoked one."""
        found = await self._current(device_id)
        # A revoked enrolment is final: no change applies to it, whatever the operator typed.
        if found.status is NodeStatus.REVOKED:
            raise NodeRevokedError(device_id)
        return found
