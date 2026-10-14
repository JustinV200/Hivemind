"""Provide NodeReader: how the operator's desk finds a device and reads it as it is now.

Every act on an enrolled device starts the same way. The operator's words are turned into the one
device they name (`find`, by device id, Cell id or label), and the device is read again from the
registry just before anything is written (`_current`, `_live`), because the running Hive moves rows
under the operator's hand: a link is lost, a colleague revokes the device. That second read is the
one that decides, so a stale reference is judged on what is true now. A revoked enrolment is final,
so the read that every act changing something uses (`_live`) refuses it. `OperatorDesk` is built on
this class and adds the acts; the reads live here so that each file does one job.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    The base of `OperatorDesk`. Calls into the registry and `hivemind.swarm.operator.lookup`.

Key invariants:
    - Nothing here writes: a read never changes a row.
    - `_live` raises `NodeRevokedError` for a revoked device, whatever the operator typed;
      `_current` returns it, which is what `revoke` needs to say that it already was.

See Also:
    - hivemind.swarm.operator.desk for the acts built on these reads.
    - hivemind.swarm.operator.lookup for the rule that turns words into a device.
"""

from __future__ import annotations

from hivemind.swarm.errors import NodeNotFoundError, NodeRevokedError
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator.lookup import find_node
from hivemind.swarm.registry import SwarmRegistry
from waggle.ids import DeviceId

__all__ = ["NodeReader"]


class NodeReader:
    """Finds the device the operator names and reads it from the registry as it is now."""

    def __init__(self, registry: SwarmRegistry) -> None:
        """Build the reader over the registry.

        Args:
            registry: Where devices live; the running Hive reads the same rows.
        """
        self._registry = registry

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
