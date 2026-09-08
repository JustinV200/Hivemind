"""Define the waggle error tree: WaggleError at the root, one subclass per protocol failure.

Waggle (the Hive's shared bee-to-bee wire protocol and primitive package) cannot import
``hivemind.common.errors.HiveMindError`` because ``hivemind`` sits above ``waggle`` in the layer
table (codingrules section 4): waggle is usable by pollen, the lightweight device connector, which
may depend on nothing beyond waggle itself. Section 10 of the coding rules calls this out by name:
waggle gets its own error root instead of inheriting from the hivemind one. Every class below
carries a stable, dotted, lowercase ``code`` class attribute in the same shape hivemind's errors
use. For the protocol families (codec, signature, transport, outbox) those codes are exactly the
strings an ``ErrorMessage`` (the control-family Waggle message that reports a failure to a peer)
carries in its ``code`` field on the wire, so a receiver can identify the failure by code alone
once the Python type is lost to serialisation; subsystem errors that cross the wire use their own
``hive.*`` codes and never appear here. The whole tree is defined once, in this file, so the
codec, signing, transport and outbox modules raise, and the protocol spec documents, one set of
names.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Raised by waggle.ids (ids family), waggle.codec and waggle.messages.registry (codec family),
    waggle.signing (signature family), waggle.transport (transport family) and waggle.outbox
    (outbox family); caught by whichever caller asked for the parse, decode, verify, send or
    replay.

Key invariants:
    - WaggleError never inherits from hivemind.common.errors.HiveMindError, directly or
      indirectly, so waggle keeps zero import dependency on hivemind.
    - Every class sets its own ``code``; no two classes share one, and every code is lowercase,
      dotted and starts with ``waggle.`` (tests/test_errors.py checks all three).
    - A code never changes once it has been sent on the wire: adding a failure means adding a
      class with a new code, never renaming an existing one.

See Also:
    - .claude/codingrules.md section 10 for the "waggle has its own root" rule and the stable
      ``code`` field ErrorMessage carries.
    - hivemind.common.errors for the equivalent root on the hivemind side.
    - docs/waggle/ for the protocol spec whose error-code table (phase 1) lists these strings.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "CodecError",
    "ConnectFailedError",
    "ConnectionLostError",
    "FrameTooLargeError",
    "InvalidIdError",
    "InvalidPayloadError",
    "InvalidSignatureError",
    "MalformedFrameError",
    "MissingSignatureError",
    "OutboxCorruptError",
    "OutboxError",
    "SignatureError",
    "TransportClosedError",
    "TransportError",
    "UnknownKindError",
    "UnknownSignerError",
    "UnsupportedVersionError",
    "WaggleError",
]


class WaggleError(Exception):
    """Root of every error the waggle package raises.

    Never raised directly by waggle itself: every failure below names a specific cause. Catching
    WaggleError catches anything waggle can raise on purpose.
    """

    #: Dotted, lowercase, stable identifier for this error, independent of its Python class name.
    code: ClassVar[str] = "waggle.error"


# ──────────────────────────────────────────────────────────────────────────────
# Ids
# ──────────────────────────────────────────────────────────────────────────────


class InvalidIdError(WaggleError):
    """Raised when a candidate id string is not a well-formed id of the expected IdKind.

    Raised by waggle.ids.parse_id and waggle.ids.timestamp_of when the prefix, length, or
    character set of a candidate id does not match what a real id produced by waggle.ids.new_id
    would look like.
    """

    code: ClassVar[str] = "waggle.ids.invalid"


# ──────────────────────────────────────────────────────────────────────────────
# Codec
# ──────────────────────────────────────────────────────────────────────────────


class CodecError(WaggleError):
    """Root of the frame encode/decode family.

    Raised by waggle.codec only through one of the subclasses below; catch CodecError itself to
    treat every frame-level failure (shape, size, version, kind) the same way, which is what a
    transport does before closing the connection on a bad frame.
    """

    code: ClassVar[str] = "waggle.codec.error"


class MalformedFrameError(CodecError):
    """Raised when a frame is not the JSON object an Envelope serialises to.

    Raised by waggle.codec.Codec.decode when the bytes are not valid UTF-8 JSON, the top level is
    not an object, or an envelope field is missing, extra or of the wrong shape; and by the
    WebSocket transport when a peer sends a text frame where only binary frames are allowed.
    """

    code: ClassVar[str] = "waggle.codec.malformed"


class FrameTooLargeError(CodecError):
    """Raised when a frame exceeds the codec's byte limit in either direction.

    Raised by waggle.codec.Codec.encode when the encoded frame would exceed max_frame_bytes (the
    sender must chunk instead) and by Codec.decode when an incoming frame already does.
    """

    code: ClassVar[str] = "waggle.codec.too_large"


class UnsupportedVersionError(CodecError):
    """Raised when an envelope's protocol version has a major number this node does not speak.

    Raised by waggle.codec.Codec.decode. Only the major number matters: a breaking change bumps
    it and is rejected, while any minor number is accepted because minor changes are additive.
    """

    code: ClassVar[str] = "waggle.version.unsupported_major"


class UnknownKindError(CodecError):
    """Raised when a message kind string or message class is not in the registry.

    Raised by waggle.messages.registry (spec_for, model_for, kind_for) on a miss, and surfaced by
    waggle.codec.Codec.decode when a frame names a kind this node has no model for.
    """

    code: ClassVar[str] = "waggle.codec.unknown_kind"


class InvalidPayloadError(CodecError):
    """Raised when a frame parsed with a readable id but a field, id or shape rule fails.

    Raised by waggle.codec.Codec.decode when the envelope's own validators (a bee address of the
    wrong kind, a naive datetime, a reply without a correlation id) or the registered payload
    model reject the parsed content. Unlike MalformedFrameError the frame is well-formed JSON with
    a readable ``id``, so the receiver answers with a control.error correlated to it and keeps the
    connection open (spec section 7). The keyword attributes carry the raw ``id``, ``sender``,
    ``kind`` and ``correlation_id`` that reply needs, so a caller never parses the message.
    """

    code: ClassVar[str] = "waggle.codec.invalid_payload"

    def __init__(
        self,
        message: str,
        *,
        message_id: str | None = None,
        sender: str | None = None,
        kind: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record the failure sentence and the raw ids a control.error reply is built from.

        Args:
            message: The full-sentence failure, naming locations and never content.
            message_id: The frame's ``id`` as received, or None when it was not text.
            sender: The frame's ``sender`` as received, or None when it was not text.
            kind: The frame's registered ``kind``, or None when unknown.
            correlation_id: The frame's ``correlation_id`` as received, or None.
        """
        super().__init__(message)
        self.message_id = message_id  # What the control.error reply's correlation_id points to.
        self.sender = sender  # Who the reply is addressed to.
        self.kind = kind  # What the reply reports as failed_kind.
        self.correlation_id = correlation_id  # What the failed frame itself pointed to.


# ──────────────────────────────────────────────────────────────────────────────
# Signature
# ──────────────────────────────────────────────────────────────────────────────


class SignatureError(WaggleError):
    """Root of the envelope-signature family.

    Raised by waggle.codec.Codec.decode, when a Verifier is configured, only through one of the
    subclasses below; a Verifier present means every frame must carry a valid signature.
    """

    code: ClassVar[str] = "waggle.signature.error"


class MissingSignatureError(SignatureError):
    """Raised when a verifier is configured but the frame carries no signature at all.

    Raised by waggle.codec.Codec.decode before any key lookup, since an unsigned frame can never
    be attributed to a node.
    """

    code: ClassVar[str] = "waggle.signature.missing"


class UnknownSignerError(SignatureError):
    """Raised when the envelope's node_id has no public key registered with the verifier.

    Raised by waggle.signing.Ed25519Verifier.verify; the frame may be perfectly well signed, but
    by a node this receiver has not been told to trust.
    """

    code: ClassVar[str] = "waggle.signature.unknown_node"


class InvalidSignatureError(SignatureError):
    """Raised when the signature does not verify against the frame's canonical bytes.

    Raised by waggle.signing.Ed25519Verifier.verify when the node's key is known but the
    signature does not match: the frame was tampered with in transit or signed with another key.
    """

    code: ClassVar[str] = "waggle.signature.invalid"


# ──────────────────────────────────────────────────────────────────────────────
# Transport
# ──────────────────────────────────────────────────────────────────────────────


class TransportError(WaggleError):
    """Root of the connection-level family.

    Raised by any waggle.transport implementation only through one of the subclasses below;
    a transport reports link state here and leaves retries and durability to the outbox.
    """

    code: ClassVar[str] = "waggle.transport.error"


class TransportClosedError(TransportError):
    """Raised when a caller sends on a transport it (or its peer) has already closed cleanly.

    Raised by Transport.send after Transport.close; a clean close is final, so the caller must
    open a new transport rather than retry on this one.
    """

    code: ClassVar[str] = "waggle.transport.closed"


class ConnectionLostError(TransportError):
    """Raised when the underlying link drops without a clean close.

    Raised by Transport.send while disconnected and by Transport.receive when the link dies
    mid-stream; the caller reconnects and replays its outbox.
    """

    code: ClassVar[str] = "waggle.transport.connection_lost"


class ConnectFailedError(TransportError):
    """Raised when a client transport gives up dialling the peer.

    Raised by WebSocketClientTransport.connect after its capped exponential backoff has used up
    max_attempts without the peer accepting a connection.
    """

    code: ClassVar[str] = "waggle.transport.connect_failed"


# ──────────────────────────────────────────────────────────────────────────────
# Outbox
# ──────────────────────────────────────────────────────────────────────────────


class OutboxError(WaggleError):
    """Root of the durable-outbox family.

    Raised by waggle.outbox only through one of the subclasses below; the outbox is the node's
    own durable memory of unsent envelopes, so a failure here is local, never a peer's fault.
    """

    code: ClassVar[str] = "waggle.outbox.error"


class OutboxCorruptError(OutboxError):
    """Raised when the outbox file holds a record that cannot be read back.

    Raised by waggle.outbox.Outbox on open when a complete line is not a valid record. A torn
    trailing line (an append cut short by a crash) is expected and ignored, not an error.
    """

    code: ClassVar[str] = "waggle.outbox.corrupt"
