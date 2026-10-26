"""Define RegistrySegmentKeys: the key a Swarm node's trail segment must be signed with.

A colonized device's Warden records its own trail while it works and ships the segment to the
Queen (ADR-0007), and from Waggle 1.13 the final chunk carries an Ed25519 signature by the node's
own key over the segment's identity and digest (ADR-0053). The key that counts is the one the
registry pinned at enrolment: it is the key the device's frames already verify under, so a segment
signed by anything else is not that node's. `RegistrySegmentKeys` answers the receiver's one
question from the registry: for the node this chunk names, what is its key, and must it sign?
Always: a Swarm node's segment must carry a signature that verifies, so an unsigned one, a forged
one, one from a node the registry does not know and one from a revoked node are all refused (a
node with no key left to check against is answered as a required key with none, which the check
turns into `bad_signature`).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by the gate and handed to the `TrailSegmentReceiver` every device Warden's link merges
    through. Calls into `hivemind.queen.trail` (SegmentKey), `hivemind.swarm` (the registry, the
    node status) and waggle only.

Key invariants:
    - A Swarm node's segment is always required to be signed; this never answers an optional key.
    - The key is read from the registry on each export, so a revocation made after a Warden
      attached is honoured by its very next segment.
    - Nothing here returns, logs or compares key material as text: the receiver gets raw bytes.

See Also:
    - hivemind.queen.cell_gate.trust.segment_keys for the Virtual Cell counterpart, an optional
      one.
    - hivemind.queen.trail.signature for the check this feeds.
"""

from __future__ import annotations

from hivemind.queen.trail import SegmentKey
from hivemind.swarm import NodeStatus, SwarmRegistry
from waggle.messages.swarm import TrailSegmentSync

__all__ = ["RegistrySegmentKeys"]


class RegistrySegmentKeys:
    """Look up a Swarm node's pinned key in the registry; always required."""

    def __init__(self, registry: SwarmRegistry) -> None:
        """Bind the keys to the registry the swarm listener verifies frames against.

        Args:
            registry: Holds each enrolled node's pinned public key and status.
        """
        self._registry = registry

    async def key_for(self, message: TrailSegmentSync) -> SegmentKey:
        """Return the key `message`'s node must have signed its segment with.

        Args:
            message: The final chunk of a segment export; its `node_id` names the sender.

        Returns:
            The node's pinned key as a required one; a required key with no bytes when the node is
            unknown or revoked, so the segment is refused rather than merged unchecked.
        """
        node = await self._registry.node_by_node_id(message.node_id)
        if node is None or node.status is NodeStatus.REVOKED:
            return SegmentKey(public_key=None, is_required=True)
        return SegmentKey(public_key=bytes.fromhex(node.public_key_hex), is_required=True)
