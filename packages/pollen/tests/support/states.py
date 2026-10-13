"""Build values the unit tests need without a running packet: a state, frames, a recording courier.

Most of pollen's modules are pure or take their collaborators as arguments, so their tests need a
valid ``PacketState``, envelopes addressed the way the Queen addresses them, and a courier that
records what it is asked to send instead of writing to a socket. ``sample_state`` builds the state
an enrolment would leave, with fresh ids and keys and any field overridden; ``frame_from`` wraps a
payload as a frame from the Queen or the Warden to the device in that state; ``RecordingCourier``
is the real ``Courier`` with its two send paths replaced by lists. None of it speaks to a network.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen unit tests; depends on pollen's
    public values and on waggle, and on nothing else in support/ but frame_overrides.py.

Key invariants:
    - A state built here passes the same validation as one a real enrolment writes.
    - ``RecordingCourier`` records exactly what the executors and the dispatcher ask it to send,
      in order, and reports every send as delivered.

See Also:
    - queen_double.py for the tests that need a real connection instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from frame_overrides import Overrides

from pollen.agent import Courier, Identity
from pollen.state import PacketState
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    MessageId,
    NodeId,
    new_cell_id,
    new_device_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import DeviceHeartbeat, NodeKey, RuntimeLevel
from waggle.outbox import Outbox
from waggle.signing import Ed25519Signer, public_key_hex

__all__ = ["RecordingCourier", "Sent", "frame_from", "heartbeat_for", "sample_state"]


def sample_state(clock: Clock, **overrides: object) -> PacketState:
    """Build the state an enrolment would leave, with fresh ids and keys.

    Args:
        clock: Mints the ids and stamps ``enrolled_at``.
        **overrides: Any ``PacketState`` field to set instead of the default.

    Returns:
        A valid state for a ``ws://`` loopback Queen, access FULL, tier MEADOW.
    """
    hive_node = new_node_id(clock)
    hive_key = public_key_hex(Ed25519Signer.generate().public_key_bytes)
    values: dict[str, object] = {
        "device_id": new_device_id(clock),
        "node_id": new_node_id(clock),
        "queen_url": "ws://127.0.0.1:8720",
        "tls_pin_sha256": None,
        "hive_id": new_hive_id(clock),
        "hive_node_id": hive_node,
        "hive_key_hex": hive_key,
        "trusted_nodes": (NodeKey(node_id=hive_node, public_key_hex=hive_key),),
        "cell_id": new_cell_id(clock),
        "warden_id": new_warden_id(clock),
        "access_level": AccessLevel.FULL,
        "comb_shield": CombShieldLevel.MEADOW,
        "granted_capabilities": ("fs:read:**",),
        "offline_limit_s": 120.0,
        "heartbeat_interval_s": 15.0,
        "enrolled_at": clock.now(),
    }
    values.update(overrides)
    return PacketState(**values)


def frame_from(
    state: PacketState,
    payload: WaggleMessage,
    clock: Clock,
    sender: str | None = None,
    overrides: Overrides | None = None,
) -> Envelope:
    """Wrap ``payload`` as a frame the Queen (by default) sends to the device in ``state``.

    Args:
        state: The device's state, which names the Queen, the Warden and the device.
        payload: The message.
        clock: Mints the id and stamps the time.
        sender: The sender's address; the Queen's by default.
        overrides: Anything else that should differ from a plain frame from the Queen.

    Returns:
        An unsigned envelope.
    """
    changes = overrides or Overrides()
    hop = Hop(
        sender=sender or state.hive_id,
        recipient=changes.recipient or state.device_id,
        node_id=NodeId(changes.node_id) if changes.node_id else state.hive_node_id,
    )
    link = None if changes.correlation_id is None else MessageId(changes.correlation_id)
    envelope = wrap(payload, hop, clock=clock, correlation_id=link)
    fixed = {
        key: value
        for key, value in (("sent_at", changes.sent_at), ("id", changes.message_id))
        if value is not None
    }
    return envelope.model_copy(update=fixed)


def heartbeat_for(state: PacketState) -> DeviceHeartbeat:
    """Build the heartbeat a packet sends first, which is what opens the Queen's side of a link."""
    return DeviceHeartbeat(
        device_id=state.device_id,
        level=RuntimeLevel.GATEWAY,
        lease_ids=(),
        hive_started_processes=0,
        outbox_pending=0,
        uptime_s=0.0,
    )


@dataclass(frozen=True, slots=True)
class Sent:
    """One frame the courier was asked to send."""

    payload: WaggleMessage  # What.
    to: str  # To whom.
    correlation_id: str | None  # What it answers.
    is_durable: bool  # Whether it went through the outbox path.


class RecordingCourier(Courier):
    """The real courier whose send paths record instead of writing to a link."""

    def __init__(self, state: PacketState, clock: Clock, outbox_path: Path) -> None:
        """Build the courier for ``state``'s identity with an outbox file at ``outbox_path``."""
        identity = Identity(state.device_id, state.node_id, state.hive_id)
        super().__init__(identity, clock, Outbox(outbox_path))
        self.sent: list[Sent] = []

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> bool:
        """Record a live frame."""
        self.sent.append(Sent(payload, to, correlation_id, is_durable=False))
        return True

    async def send_durable(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> None:
        """Record a durable frame."""
        self.sent.append(Sent(payload, to, correlation_id, is_durable=True))
