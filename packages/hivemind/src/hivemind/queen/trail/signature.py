"""Check a trail segment's signature against the key registered for the node that sent it.

ADR-0007's merge settles order and duplicates, and says nothing about authenticity: a hijacked
connection or a faulty node could put events into the audit record under another node's name.
ADR-0053 closes that with `TrailSegmentSync.segment_signature` (Waggle 1.13), an Ed25519 signature
by the sending node's own key over the segment's identity and digest, built from
`waggle.messages.swarm.segment_signing_bytes`. This module is the Queen's half: given the final
chunk of an export (the one that carries the signature and everything it signs) and what the
Queen knows about the sender's key, it either returns or raises a typed error. What she knows is a
`SegmentKey`: the node's raw public key and whether its segments *must* be signed. A Swarm node's
must (the registry holds its key from enrolment); a Virtual Cell's need not (its Warden has no
registered node key to sign with beyond the one its handshake proved), but one that arrives signed
is verified, so a signature that is present is never taken on trust.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.trail`.
    Called by `hivemind.queen.trail.sync.TrailSegmentReceiver` on the final chunk of every export,
    before anything is merged; the keys come from a `SegmentKeys` each gate supplies (the swarm
    registry's, or the Cell gate's). Calls into `hivemind.queen.trail.errors` and waggle (signing,
    the swarm messages) only.

Key invariants:
    - Pure and synchronous: no I/O here, so a refusal is decided from the chunk and the key alone.
    - A segment from a node whose key is required is refused when it is unsigned, and when its
      signature does not verify, and when the Queen holds no key to verify it with.
    - A signature that is present is always checked, required or not, whenever a key is held.
    - Nothing here reads or reports key material or the signature's bytes: node ids only.

See Also:
    - waggle.messages.swarm.colonized for `segment_signing_bytes`, the one definition of what is
      signed, and `TrailSegmentSync.segment_signature`.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the rule.
    - hivemind.queen.attach.refusals for how a refusal is recorded as `guard.segment_refused`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hivemind.queen.trail.errors import BadSegmentSignatureError, UnsignedSegmentError
from waggle.errors import SignatureError
from waggle.messages.swarm import TrailSegmentSync, segment_signing_bytes
from waggle.signing import Ed25519Verifier

__all__ = ["SegmentKey", "SegmentKeys", "check_segment_signature"]


@dataclass(frozen=True, slots=True)
class SegmentKey:
    """What the Queen knows about the key a node signs its trail segments with.

    Attributes:
        public_key: The node's raw Ed25519 public key; None when the Queen holds none for it (an
            unknown or revoked node), which a required key then refuses outright.
        is_required: Whether this node's segments must carry a signature at all.
    """

    public_key: bytes | None
    is_required: bool


class SegmentKeys(Protocol):
    """Look up the key a segment's sending node signs with; one per gate."""

    async def key_for(self, message: TrailSegmentSync) -> SegmentKey | None:
        """Return what is known of the key that signs `message`'s segment.

        Args:
            message: The final chunk of an export; its `node_id` and `cell_id` name the sender.

        Returns:
            The key and whether it is required, or None when this gate has no opinion about the
            sender (nothing is checked then, signed or not).
        """
        ...


def check_segment_signature(message: TrailSegmentSync, key: SegmentKey | None) -> None:
    """Return only if `message`'s signature is acceptable for what the Queen knows of its sender.

    Args:
        message: The final chunk of an export (it carries the signature and the digest).
        key: The sender's key, from a `SegmentKeys`; None checks nothing.

    Raises:
        UnsignedSegmentError: The sender's segments must be signed and this one is not.
        BadSegmentSignatureError: The signature is present but not the sender's: it does not
            verify, or the Queen holds no key to verify it with.
    """
    if key is None:
        return  # No gate has an opinion about this sender: its segments merge as they always did.
    signature = message.segment_signature
    if signature is None:
        if key.is_required:
            raise UnsignedSegmentError(message.node_id)
        return  # A signature is optional for this sender and none was offered.
    # The signature is on the final chunk only, which also carries the digest it covers; the
    # model's own validator guarantees `sha256` is set exactly then.
    if key.public_key is None or message.sha256 is None:
        raise BadSegmentSignatureError(message.node_id, "the Queen holds no key to verify it with")
    signed = segment_signing_bytes(
        message.node_id,
        message.first_event_id,
        message.last_event_id,
        message.event_count,
        message.sha256,
    )
    try:
        Ed25519Verifier({message.node_id: key.public_key}).verify(
            message.node_id, signed, signature
        )
    except SignatureError as error:
        # The type only: the signature and the key are never echoed (module invariant).
        raise BadSegmentSignatureError(message.node_id, type(error).__name__) from error
