"""Encode an Envelope to one signed JSON frame and decode a frame back, checking in a fixed order.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A frame is
one JSON object, UTF-8, whose keys are exactly the Envelope's fields (spec section 5); this module
is the one place that turns an Envelope (the outer wrapper every message travels in) into those
bytes and back, and the one place the wire policy lives: the size limit, whether frames are
signed, and whether a signature is required. Transports are policy-free carriers of the bytes it
produces. Signing is Ed25519 over ``canonical_bytes`` (the raw wire dict minus ``signature``,
serialised with sorted keys, no whitespace and ASCII escapes), always computed from what was
actually sent or received, never from a re-validated model, so a default that validation fills
in can never break a signature; the wire itself is written with ``ensure_ascii=False`` and may
differ byte-for-byte from the canonical form. The ``Signer`` and ``Verifier`` protocols are
defined here, structurally, so this module never imports ``waggle.signing`` and the cryptography
behind it; a node's composition root hands the codec an ``Ed25519Signer`` and an
``Ed25519Verifier`` (or a fake). The key-free decode steps (parse, version, kind, build) live in
``waggle.frame`` so this file stays under the codingrules 5.1 size limit.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by every waggle.transport on send and receive
    and by the outbox on append and replay; calls into waggle.envelope, waggle.frame and the
    injected Signer/Verifier. Latency class: microseconds, pure CPU, no I/O.

Key invariants:
    - A Verifier present means a valid signature is REQUIRED on every decoded frame; absent means
      signatures are ignored (the in-process case only). There is no third mode.
    - decode() checks in exactly this order and stops at the first failure: size, UTF-8 JSON
      object with the envelope keys, version major, registered kind, signature (when a Verifier
      is configured), payload and envelope validation. Every failure is a CodecError or
      SignatureError subclass; never a pydantic or json error.
    - encode() never produces a frame over max_frame_bytes: it raises rather than sends.
    - decode(encode(envelope)) == envelope apart from the signature the encode added.

See Also:
    - docs/waggle/spec.md sections 5 (wire format), 6 (signing) and 7 (error codes).
    - waggle.frame for the decode steps this module composes.
    - waggle.signing for the Ed25519Signer and Ed25519Verifier that satisfy the protocols here.
    - waggle.errors for the CodecError and SignatureError families decode() raises.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from waggle.envelope import Envelope
from waggle.errors import FrameTooLargeError, MalformedFrameError, MissingSignatureError
from waggle.frame import build_envelope, check_kind, check_version, parse_frame

MAX_FRAME_BYTES = 1_048_576  # 1 MiB: websockets' default max_size; a 256 KiB chunk in base64 fits.
_SIGNATURE_BYTES = 64  # RFC 8032 Ed25519 signature; restated so codec never imports signing.
_SIGNATURE_B64_CHARS = -(-_SIGNATURE_BYTES // 3) * 4  # 88: padded base64, 4 chars per 3 bytes.
# What signing adds to a frame at most: the JSON member `,"signature":"<88 chars>"`, that is a
# comma (1), the quoted key (11), a colon (1), two quotes (2) and the base64 text (88) = 103. An
# unsigned frame already spends `"signature":null`, so the true delta is smaller; the upper bound
# keeps the outbox's "will still fit once signed" refusal conservative (spec section 10).
SIGNATURE_OVERHEAD_BYTES = len(',"signature":""') + _SIGNATURE_B64_CHARS

__all__ = [
    "MAX_FRAME_BYTES",
    "SIGNATURE_OVERHEAD_BYTES",
    "Codec",
    "Signer",
    "Verifier",
    "canonical_bytes",
]


class Signer(Protocol):
    """What a Codec needs to sign a frame: one node's private key behind one method.

    Satisfied structurally by waggle.signing.Ed25519Signer; a fake for tests returns any text.
    """

    def sign(self, canonical: bytes) -> str:
        """Sign canonical envelope bytes.

        Args:
            canonical: The output of ``canonical_bytes`` for the frame being sent.

        Returns:
            The signature as text that fits in a JSON string (standard padded base64).
        """
        ...


class Verifier(Protocol):
    """What a Codec needs to verify a frame: the public keys of the nodes this receiver trusts.

    Satisfied structurally by waggle.signing.Ed25519Verifier.
    """

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Check that ``signature`` is ``node_id``'s signature over ``canonical``.

        Args:
            node_id: The envelope's node_id: which registered key to check against.
            canonical: The output of ``canonical_bytes`` for the frame as received.
            signature: The envelope's signature field.

        Returns:
            None when the signature verifies; every failure is an exception.

        Raises:
            SignatureError: A subclass: UnknownSignerError when no key is registered for
                ``node_id``, InvalidSignatureError when the signature does not verify.
        """
        ...


def canonical_bytes(wire: Mapping[str, object]) -> bytes:
    """Return the bytes a signature covers: the wire dict minus ``signature``, canonically encoded.

    Args:
        wire: The raw wire dict, either the one about to be sent or the one just parsed; never
            a re-validated model, so a default filled in on receipt cannot change the bytes.

    Returns:
        UTF-8 of ``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`` over
        every key but ``signature``; identical for any key order of ``wire``.
    """
    # Sorted keys and fixed separators make the bytes a pure function of the content; ASCII
    # escapes make them independent of how the wire happened to encode non-ASCII text.
    unsigned = {key: value for key, value in wire.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


class Codec:
    """Turn Envelopes into frames and frames into Envelopes, enforcing every wire rule.

    Policy: a Verifier present means signatures are REQUIRED on every frame decoded; absent means
    they are ignored, which is the in-process case only. A Signer present means every frame
    encoded is signed with it, replacing any signature the envelope carried. Transports are
    policy-free: they carry what this class produces and hand back what they receive. Latency
    class: microseconds, pure CPU. Safe to share between tasks; it holds no mutable state.
    """

    def __init__(
        self,
        *,
        signer: Signer | None = None,
        verifier: Verifier | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        """Configure the codec's signing policy and size limit.

        Args:
            signer: Signs every encoded frame when given; None sends unsigned frames.
            verifier: Requires and checks a signature on every decoded frame when given; None
                ignores signatures (in-process only).
            max_frame_bytes: The largest frame encode() will produce or decode() will accept;
                MAX_FRAME_BYTES unless a test or a constrained link says otherwise.
        """
        self._signer = signer
        self._verifier = verifier
        self._max_frame_bytes = max_frame_bytes

    @property
    def max_frame_bytes(self) -> int:
        """The largest frame this codec produces or accepts, in bytes.

        A transport reads it so the link refuses an oversized frame at the same limit the codec
        would (the WebSocket transport passes it as websockets' ``max_size``).

        Returns:
            The limit given at construction; MAX_FRAME_BYTES by default.
        """
        return self._max_frame_bytes

    def encode(self, envelope: Envelope) -> bytes:
        """Serialise ``envelope`` to one frame, signing it when a Signer is configured.

        Args:
            envelope: A consistent Envelope; any signature it carries is discarded and, with a
                Signer, replaced by a fresh one over these exact bytes.

        Returns:
            The frame: compact, sorted-key, UTF-8 JSON with exactly the envelope keys.

        Raises:
            FrameTooLargeError: The frame would exceed max_frame_bytes; it is never sent, the
                sender must split its content into smaller messages (spec section 5).
        """
        # The signature is computed over the wire dict as it will be parsed on the other side,
        # so the JSON-mode dump (datetimes as strings, bytes as base64) is what gets signed.
        wire = envelope.model_dump(mode="json", exclude={"signature"})
        wire["signature"] = (
            self._signer.sign(canonical_bytes(wire)) if self._signer is not None else None
        )
        # ensure_ascii=False keeps non-ASCII text as UTF-8 rather than \u escapes, a third the
        # size for prose; the canonical bytes are recomputed on receipt, so this is allowed to
        # differ from them (spec section 5).
        frame = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        if len(frame) > self._max_frame_bytes:
            raise FrameTooLargeError(
                f"The frame for envelope {envelope.id} is {len(frame)} bytes, over the limit of "
                f"{self._max_frame_bytes}; split the content into smaller messages."
            )
        return frame

    def decode(self, frame: bytes) -> Envelope:
        """Parse and check one frame, in the fixed order, and return its Envelope.

        Args:
            frame: The bytes exactly as received from the peer.

        Returns:
            The Envelope the frame carried, its payload validated with the registered class and
            its ``signature`` field set to whatever the frame carried.

        Raises:
            FrameTooLargeError: Over max_frame_bytes; checked before anything is parsed.
            MalformedFrameError: Not UTF-8 JSON, not an object, wrong keys, or a version, kind,
                node_id or signature of the wrong JSON type.
            UnsupportedVersionError: The version's major is not PROTOCOL_MAJOR.
            UnknownKindError: The kind is not registered.
            SignatureError: With a Verifier only: MissingSignatureError when unsigned, else what
                the Verifier raises (UnknownSignerError, InvalidSignatureError).
            InvalidPayloadError: The payload or the envelope's own rules fail validation; the
                frame had a readable id, so the receiver answers with a control.error.
        """
        # Size first, before any parsing, so an oversized frame costs nothing to reject.
        if len(frame) > self._max_frame_bytes:
            raise FrameTooLargeError(
                f"Received a frame of {len(frame)} bytes, over the limit of "
                f"{self._max_frame_bytes}."
            )
        raw = parse_frame(frame)
        check_version(raw)
        kind = check_kind(raw)
        # Signature before payload validation: a tampered frame must fail as tampering, never as
        # an invalid payload, and no untrusted bytes reach the model until the signer is known.
        if self._verifier is not None:
            _verify(self._verifier, raw)
        return build_envelope(kind, raw)


def _verify(verifier: Verifier, raw: Mapping[str, object]) -> None:
    """Require a signature and have ``verifier`` check it over the raw frame's canonical bytes."""
    signature = raw["signature"]
    # An unsigned frame can never be attributed to a node; distinct from a bad signature so a
    # transport can tell an unsigned peer from a tampered link.
    if signature is None:
        raise MissingSignatureError(
            "The frame carries no signature, but this codec requires one on every frame."
        )
    if not isinstance(signature, str):
        raise MalformedFrameError(
            f"The frame's signature is a {type(signature).__name__}, not text."
        )
    node_id = raw["node_id"]
    if not isinstance(node_id, str):
        raise MalformedFrameError(f"The frame's node_id {node_id!r} is not a string.")
    # Over the RAW dict as parsed, never a validated model (spec section 6).
    verifier.verify(node_id, canonical_bytes(raw), signature)
