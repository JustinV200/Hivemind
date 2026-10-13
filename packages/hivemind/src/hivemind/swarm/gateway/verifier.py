"""Provide KeyRing and SwarmVerifier: who may sign what on the swarm listener.

Every frame on the swarm listener is signed (codingrules 15), and the listener's `Codec` asks a
`Verifier` to check each signature before it parses the payload. `SwarmVerifier` is that
verifier, built around a `KeyRing`: an in-memory table of `node_id -> public key and the bee
addresses that key may speak for`, which the listener keeps in step with the swarm registry
(it adds a node when the door enrols it and removes it when the operator revokes it). A device's
node key signs only for its `device_id` and its Cell's `warden_id` (the spec's address-binding
rule, section 6), so the verifier checks that too, on every frame, and a frame whose `sender` is
not one of those closes the connection like a bad signature does.

The one frame the ring cannot verify is the first an unknown device ever sends. Its key is not
yet known to anyone: it is inside the frame. So for an unknown `node_id` the verifier accepts
exactly one kind, `swarm.enrol_request`, whose payload's own `node_id` equals the envelope's and
whose `sender` is the payload's own `device_id`, and verifies the signature against the
`public_key_hex` inside that same payload. That is proof of possession and nothing more: only the
holder of the private key could have produced the signature. The door then decides, by the invite
token, whether to trust the key (docs/waggle/enrolment.md section 5.3). It is the same shape as
`hivemind.queen.cell_gate.listener._GateVerifier`, which reads the unverified canonical bytes of a
first frame to learn which key to check.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Built by the listener and handed to its `waggle.codec.Codec`; the ring is updated by the
    listener from the registry. Calls into waggle (codec, errors, messages, signing) only.

Key invariants:
    - A frame from a known node verifies against the key pinned for that node, always, so a
      forged `swarm.enrol_request` claiming a known node's id fails like any other forgery.
    - An unknown node's only acceptable frame is a self-consistent `swarm.enrol_request`; every
      other frame from an unknown node raises `UnknownSignerError`.
    - A verified frame's `sender` is bound to its node, or the frame raises `UnknownSignerError`.
    - Nothing here reads a payload beyond the five fields it names, and none is logged.

See Also:
    - docs/waggle/enrolment.md section 5.3 for the first-frame rule.
    - docs/waggle/spec.md section 6 for the address-binding rule.
    - hivemind.queen.cell_gate.listener for the verifier this one is modelled on.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.errors import UnknownSignerError
from waggle.messages.registry import kind_for
from waggle.messages.swarm import EnrolRequest
from waggle.signing import Ed25519Verifier, public_key_from_hex

ENROL_REQUEST_KIND = kind_for(EnrolRequest)  # "swarm.enrol_request": the one unknown-node frame.

__all__ = ["ENROL_REQUEST_KIND", "KeyBinding", "KeyRing", "SwarmVerifier"]


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """One node's pinned key and the bee addresses that key may sign for."""

    key: bytes  # The raw 32-byte Ed25519 public key.
    senders: frozenset[str]  # The device id and the Warden id the Queen registered to the node.


class KeyRing:
    """The node keys the listener trusts, kept in step with the registry by its owner.

    Concurrency model: not locked. Every method is synchronous and awaits nothing, so tasks on
    one event loop never interleave inside it.
    """

    def __init__(self) -> None:
        """Create an empty ring: no node is trusted until it is added."""
        self._bindings: dict[str, KeyBinding] = {}

    def add(self, node: SwarmNode) -> None:
        """Trust `node`: pin its key and bind its device and Warden addresses to it.

        Args:
            node: An enrolled, non-revoked node. A node already present is replaced.
        """
        self._bindings[node.node_id] = KeyBinding(
            key=public_key_from_hex(node.public_key_hex),
            senders=frozenset({node.device_id, node.warden_id}),
        )

    def remove(self, node_id: str) -> None:
        """Stop trusting a node: its next frame fails with `UnknownSignerError`.

        Args:
            node_id: The node to forget; an unknown id is ignored.
        """
        self._bindings.pop(node_id, None)

    def sync(self, nodes: Iterable[SwarmNode]) -> None:
        """Bring the ring in step with the registry: trust every live node, drop every revoked one.

        A node the ring holds that `nodes` does not mention is kept, so a device enrolled while
        the registry was being read is never dropped by a snapshot taken a moment too early.

        Args:
            nodes: Nodes read from the registry.
        """
        for node in nodes:
            if node.status is NodeStatus.REVOKED:
                self.remove(node.node_id)
            else:
                self.add(node)

    def binding(self, node_id: str) -> KeyBinding | None:
        """Return the pinned key and bound addresses for `node_id`, or None when unknown."""
        return self._bindings.get(node_id)

    def __contains__(self, node_id: object) -> bool:
        """Return whether `node_id` is trusted."""
        return node_id in self._bindings

    def __len__(self) -> int:
        """Return how many nodes are trusted."""
        return len(self._bindings)


class SwarmVerifier:
    """The `waggle.codec.Verifier` for the swarm listener: the ring's keys, plus enrolment.

    Satisfies the codec's structural `Verifier` protocol. Holds the ring by reference, so a node
    added after the codec was built is trusted at once.
    """

    def __init__(self, ring: KeyRing) -> None:
        """Hold the ring this verifier checks every frame against.

        Args:
            ring: The listener's key ring.
        """
        self._ring = ring

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Check `signature` over `canonical` for `node_id`, and that its sender is bound to it.

        Args:
            node_id: The envelope's `node_id`: which key signed it.
            canonical: The canonical bytes of the frame as received, signature excluded.
            signature: The envelope's `signature` field.

        Raises:
            UnknownSignerError: The node is unknown and the frame is not a self-consistent
                `swarm.enrol_request`, or the frame's `sender` is not an address bound to the
                node.
            InvalidSignatureError: The signature does not verify against the node's key.
        """
        wire = _read_wire(canonical)
        binding = self._ring.binding(node_id)
        if binding is None:
            binding = _enrolment_binding(node_id, wire)
        # The signature first: an attacker's claim about who sent a frame is worth nothing until
        # the key it names has signed that exact claim.
        Ed25519Verifier({node_id: binding.key}).verify(node_id, canonical, signature)
        if wire.get("sender") not in binding.senders:
            raise UnknownSignerError(
                f"A frame signed by node {node_id} names a sender that node is not registered "
                "to speak for."
            )


def _read_wire(canonical: bytes) -> dict[str, object]:
    """Parse the canonical bytes into the envelope's JSON object, or raise `UnknownSignerError`."""
    try:
        wire = json.loads(canonical)
    except ValueError as exc:
        raise UnknownSignerError("The frame's canonical bytes are not a JSON object.") from exc
    if not isinstance(wire, dict):
        raise UnknownSignerError("The frame's canonical bytes are not a JSON object.")
    return wire


def _enrolment_binding(node_id: str, wire: dict[str, object]) -> KeyBinding:
    """Return the binding a self-consistent `swarm.enrol_request` proves, or raise.

    Raises:
        UnknownSignerError: The node is unknown and the frame is anything but an enrolment
            request naming this node, this sender and a readable public key.
    """
    try:
        payload = wire["payload"]
        if wire["kind"] != ENROL_REQUEST_KIND or not isinstance(payload, dict):
            raise KeyError("kind")
        # Both identities in the payload must be the envelope's own, or the key inside it could
        # be claimed for a node and a device it does not belong to.
        if payload["node_id"] != node_id or payload["device_id"] != wire["sender"]:
            raise KeyError("identity")
        key = public_key_from_hex(str(payload["public_key_hex"]))
        return KeyBinding(key=key, senders=frozenset({str(wire["sender"])}))
    except (KeyError, TypeError, ValueError) as exc:
        raise UnknownSignerError(
            f"Node {node_id} is not enrolled, and its frame is not a self-consistent "
            f"{ENROL_REQUEST_KIND}, the only frame an unknown node may send."
        ) from exc
