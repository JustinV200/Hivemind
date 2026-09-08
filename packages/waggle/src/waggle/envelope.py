"""Define the Envelope every Waggle message travels in, and wrap(), the factory that fills it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A frame on
the wire is one Envelope: the addressing (``sender`` and ``recipient``, each a bee address: the
id of the Queen, the central orchestrator, of a Warden, the always-on supervisor of one Cell, of a
Worker, a subagent a Warden spawns, or of a device carrying a Pollen Packet, the thin gateway on
an enrolled machine), the identity (``id`` and the ``correlation_id`` a reply or event points
back with), the ``kind`` that names the payload's class, the protocol ``version``, the time it
was sent, the ``node_id`` of the process that sent it (signing keys are per node) and the typed
``payload``. The model validates each field's shape, checks that ``kind`` is registered and
matches the payload's class, and applies the correlation rule of the kind's shape (spec section
3): a request carries no correlation_id, a reply must, an event may. ``wrap`` is how a bee builds
one: it mints the id from the injected Clock, stamps ``sent_at`` and looks the kind up, so no
caller ever spells a kind string. The signature is the codec's to fill in; the envelope never
signs. The bee-address check is deliberately built here from ``IdKind`` and the ULID decoder
rather than shared, because the spec makes this module the one place an address pattern exists.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Built by every bee through wrap() and by waggle.codec.Codec.decode from a parsed frame;
    encoded by waggle.codec and carried by every waggle.transport; calls into
    waggle.messages.registry, waggle.messages.base, waggle.ids and waggle.ulid.

Key invariants:
    - An Envelope that exists is consistent: its kind is registered, equals the kind registered
      for type(payload), and its correlation_id obeys the shape rule of that kind.
    - sender and recipient are well-formed ids of a kind in BEE_ADDRESS_KINDS; nothing else on
      the wire is ever a bee address, and no other module checks one.
    - sent_at is timezone-aware UTC; wrap() never produces anything else, and validation
      normalises anything aware it is given.
    - This module imports the registry; the registry never imports this module.

See Also:
    - docs/waggle/spec.md section 2 (envelope), section 3 (shapes) and section 4 (versioning).
    - waggle.codec for the wire form, the signature and the decode order.
    - waggle.messages.registry for the kind list and the shape fixed per kind.
    - waggle.messages.base for UtcDatetime and the id field aliases used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

from waggle.clock import Clock
from waggle.errors import UnknownKindError
from waggle.ids import IdKind, MessageId, NodeId, new_message_id
from waggle.messages.base import (
    MessageIdField,
    MessageShape,
    NodeIdField,
    UtcDatetime,
    WaggleMessage,
)
from waggle.messages.registry import kind_for, spec_for
from waggle.ulid import ULID_LENGTH, decode_ulid

PROTOCOL_VERSION = "1.0"  # What wrap() stamps: PROTOCOL_MAJOR.PROTOCOL_MINOR as the wire string.
PROTOCOL_MAJOR = 1  # A receiver rejects any other major; breaking changes bump it.
PROTOCOL_MINOR = 0  # Additive changes bump it; any minor of a known major is accepted.
VERSION_PATTERN = r"^\d+\.\d+$"  # "<major>.<minor>", both plain decimal integers.
# The id kinds that may address a bee: the Queen (hive), a Warden, a Worker, or a device carrying
# a Pollen Packet. Every other kind names a thing, not something that can send or receive.
BEE_ADDRESS_KINDS = frozenset({IdKind.HIVE, IdKind.WARDEN, IdKind.WORKER, IdKind.DEVICE})

__all__ = [
    "BEE_ADDRESS_KINDS",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "PROTOCOL_VERSION",
    "VERSION_PATTERN",
    "Envelope",
    "Hop",
    "wrap",
]

# Address prefix -> kind, so the check below is one lookup rather than four parse attempts and
# its message can name the whole accepted set.
_ADDRESS_KIND_BY_PREFIX = {kind.value: kind for kind in BEE_ADDRESS_KINDS}
_ACCEPTED_PREFIXES = ", ".join(f"{kind.value}_" for kind in sorted(BEE_ADDRESS_KINDS, key=str))


def _parse_bee_address(value: str) -> str:
    """Return ``value`` when it is a well-formed id of a kind in BEE_ADDRESS_KINDS."""
    # Built from IdKind and decode_ulid directly rather than on parse_id, so the failure names
    # the accepted set (a bee address) instead of one kind's prefix; spec section 2 makes this
    # the only address check in the codebase.
    prefix, _, ulid_part = value.partition("_")
    if _ADDRESS_KIND_BY_PREFIX.get(prefix) is None:
        raise ValueError(
            f"A bee address must be an id with one of the prefixes {_ACCEPTED_PREFIXES}, "
            f"got {value!r}."
        )
    if len(ulid_part) != ULID_LENGTH:
        raise ValueError(
            f"Bee address {value!r} has a {len(ulid_part)}-character ulid part, expected "
            f"{ULID_LENGTH}."
        )
    try:
        decode_ulid(ulid_part)
    except ValueError as exc:
        raise ValueError(f"Bee address {value!r} is not a valid ulid: {exc}.") from exc
    return value


# Evaluated at import, so it must follow the helper it is built from.
_BeeAddress = Annotated[str, AfterValidator(_parse_bee_address)]


@dataclass(frozen=True, slots=True)
class Hop:
    """The addressing of one hop: who sends, to whom, from which node.

    A message that crosses more than one hop (Queen -> Warden -> Worker) is re-wrapped at each
    with a fresh Hop, because each hop is a fresh envelope with its own id, sender, recipient
    and node_id (spec section 3). Grouped as one value so wrap() stays within the codingrules
    5.1 parameter limit.
    """

    sender: str  # The sending bee's address: a hive_, warden_, worker_ or device_ id.
    recipient: str  # The receiving bee's address, same kinds.
    node_id: NodeId  # The process the envelope leaves from; its key signs the frame.


class Envelope(BaseModel):
    """One frame's worth of Waggle: addressing, identity, kind, time and the typed payload.

    Frozen and extras-forbidding like every boundary model (codingrules 8.5). Crosses every
    transport as the JSON object the codec produces from it; the ``signature`` field is empty
    until the codec signs it at send time and is set on what the codec decodes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MessageIdField = Field(
        description="msg_<ULID>, unique per message and minted by wrap(); a reply's "
        "correlation_id points here."
    )
    correlation_id: MessageIdField | None = Field(
        default=None,
        description="The request this answers (required for a reply), the message this event "
        "follows from (optional), or None (required for a request).",
    )
    sender: _BeeAddress = Field(
        description="The sending bee's address: a hive_, warden_, worker_ or device_ id."
    )
    recipient: _BeeAddress = Field(description="The receiving bee's address, same kinds as sender.")
    kind: str = Field(
        description="<family>.<snake_name>: a registered kind that equals the one registered "
        "for the payload's class."
    )
    version: str = Field(
        default=PROTOCOL_VERSION,
        pattern=VERSION_PATTERN,
        description='"<major>.<minor>" of the protocol the sender speaks.',
    )
    sent_at: UtcDatetime = Field(
        description="When the sender's clock stamped it; timezone-aware UTC."
    )
    node_id: NodeIdField = Field(
        description="node_<ULID> of the process that sent it; signing keys are per node."
    )
    payload: SerializeAsAny[WaggleMessage] = Field(
        description="The typed message; the subclass is serialised in full."
    )
    signature: str | None = Field(
        default=None,
        description="Padded base64 Ed25519 signature over the canonical bytes, or None when "
        "unsigned.",
    )

    @model_validator(mode="after")
    def _kind_matches_payload_and_shape(self) -> Envelope:
        """Require a registered kind that matches the payload, and the shape's correlation rule."""
        # The registry is the only place kinds live; an unregistered kind (a newer minor, or a
        # confused peer) or an unregistered payload class makes the envelope unusable. The
        # registry's own error is a WaggleError pydantic would not report, hence the ValueError.
        try:
            shape = spec_for(self.kind).shape
            expected_kind = kind_for(type(self.payload))
        except UnknownKindError as exc:
            raise ValueError(str(exc)) from exc
        # A kind that names one class with a payload of another would validate as something it
        # is not on the receiving side.
        if self.kind != expected_kind:
            raise ValueError(
                f"Envelope kind {self.kind!r} does not match its payload class "
                f"{type(self.payload).__name__}, which is registered as {expected_kind!r}."
            )
        _check_correlation(self.kind, shape, self.correlation_id)
        return self


def wrap(
    payload: WaggleMessage,
    hop: Hop,
    *,
    clock: Clock,
    correlation_id: MessageId | None = None,
) -> Envelope:
    """Build an unsigned Envelope around ``payload``: mint the id, stamp the time, look up the kind.

    Args:
        payload: The typed message to send; its class must be registered.
        hop: The sender and recipient addresses and the sending node.
        clock: The injected Clock; mints the id's timestamp and stamps ``sent_at``.
        correlation_id: The id the payload's shape requires or allows (spec section 3); the
            request's id for a reply, the antecedent for an event that has one, None for a
            request.

    Returns:
        A consistent, unsigned Envelope at PROTOCOL_VERSION; the codec signs it at send time.

    Raises:
        UnknownKindError: ``type(payload)`` is not registered.
        pydantic.ValidationError: An address is not a bee address, or ``correlation_id`` breaks
            the shape rule for the payload's kind.
    """
    return Envelope(
        id=new_message_id(clock),
        correlation_id=correlation_id,
        sender=hop.sender,
        recipient=hop.recipient,
        kind=kind_for(type(payload)),
        sent_at=clock.now(),
        node_id=hop.node_id,
        payload=payload,
    )


def _check_correlation(kind: str, shape: MessageShape, correlation_id: MessageId | None) -> None:
    """Apply spec section 3's rule: a request has no correlation, a reply must, an event may."""
    # A request that carries one would look like an answer to something; a reply without one
    # cannot be matched to the request it answers and would be dropped by the receiver.
    if shape is MessageShape.REQUEST and correlation_id is not None:
        raise ValueError(
            f"Kind {kind!r} is a request and must not carry a correlation_id, got "
            f"{correlation_id!r}."
        )
    if shape is MessageShape.REPLY and correlation_id is None:
        raise ValueError(
            f"Kind {kind!r} is a reply and must carry the correlation_id of the request it answers."
        )
