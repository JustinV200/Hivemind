"""Define the keys a Swarm node must sign with, as the registry pinned them at enrolment.

A colonized device's Warden signs two kinds of thing it ships to the Queen with its own node key:
its trail segments (ADR-0007; from Waggle 1.13 the final chunk carries an Ed25519 signature over
the segment's identity and digest, ADR-0053) and the manifest of every Basket blob it ships up
(ADR-0048: a `retain` result, or the package of a cell-scope tool it scaffolded). The key that
counts for both is the one the registry pinned at enrolment: it is the key the device's frames
already verify under, so anything signed by another key is not that node's. This module answers
both questions from the registry. `RegistrySegmentKeys` answers the trail receiver's: for the node
this chunk names, what is its key, and must it sign? Always: a Swarm node's segment must carry a
signature that verifies, so an unsigned one, a forged one, one from a node the registry does not
know and one from a revoked node are all refused (a node with no key left to check against is
answered as a required key with none, which the check turns into `bad_signature`).
`RegistryUplinkKeys` answers the Basket receiver's (`hivemind.queen.deps.UplinkKeys`): for the
node this link proves, what is its key? An enrolled, unrevoked node's pinned key; nothing for a
link that proves no node, or a node the registry does not know or has revoked, so the receiver
holds the Hive key alone and refuses the offer as an unknown signer.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    `RegistrySegmentKeys` is built by the gate and handed to the `TrailSegmentReceiver` every
    device Warden's link merges through; `RegistryUplinkKeys` is built by the composition root
    (`hivemind.cli.compose.deps.queen`) over the Swarm's registry and chained with the Cell gate's
    own lookup into `QueenDeps.uplink_keys`. Calls into `hivemind.queen.deps` (WardenLink),
    `hivemind.queen.trail` (SegmentKey), `hivemind.swarm` (the registry, the node status) and
    waggle only.

Key invariants:
    - A Swarm node's segment is always required to be signed; this never answers an optional key.
    - The key is read from the registry on each export and each offer, so a revocation made after
      a Warden attached is honoured by its very next segment or upload.
    - Neither lookup branches on what kind of Cell a link is: a link either proves a node the
      registry holds a key for, or it does not.
    - Nothing here returns, logs or compares key material as text: the receiver gets raw bytes.

See Also:
    - hivemind.queen.cell_gate.segment_keys for the Virtual Cell counterpart, which is optional.
    - hivemind.queen.cell_gate.provider for the Cell gate's own `uplink_key`, the Virtual Cell
      counterpart this one is chained with.
    - hivemind.queen.trail.signature for the check `RegistrySegmentKeys` feeds.
    - hivemind.queen.ticks.tools.transfer.receive for the verifier `RegistryUplinkKeys` feeds.
"""

from __future__ import annotations

from hivemind.queen.deps import WardenLink
from hivemind.queen.trail import SegmentKey
from hivemind.swarm import NodeStatus, SwarmRegistry
from waggle.messages.swarm import TrailSegmentSync

__all__ = ["RegistrySegmentKeys", "RegistryUplinkKeys"]


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


class RegistryUplinkKeys:
    """Look up the key a Swarm node signs its Basket manifests with: its pinned key, if enrolled."""

    def __init__(self, registry: SwarmRegistry) -> None:
        """Bind the keys to the registry the swarm listener verifies frames against.

        Args:
            registry: Holds each enrolled node's pinned public key and status.
        """
        self._registry = registry

    async def __call__(self, link: WardenLink) -> bytes | None:
        """Return the pinned key of the node `link` proves, or None when the registry holds none.

        Args:
            link: The Queen's link to the Warden offering a blob; `node_id` is the node its frames
                are signed as, or None for a link that proves none.

        Returns:
            The node's pinned key as raw bytes, or None for a link that proves no node, a node the
            registry does not know, or a revoked one: the receiver then trusts the Hive key alone,
            so the offer is refused as an unknown signer rather than verified against a stale key.
        """
        if link.node_id is None:
            return None
        # Read on every offer, never cached at attach: a revocation takes effect on the next upload.
        node = await self._registry.node_by_node_id(link.node_id)
        if node is None or node.status is NodeStatus.REVOKED:
            return None
        return bytes.fromhex(node.public_key_hex)
