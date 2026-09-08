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
``Ed25519Verifier`` (or a fake). The key-free decode steps are defined here too and composed by
``Codec.decode`` in the spec's order: ``parse_frame`` (a well-formed object with the envelope
keys), ``check_version`` (a major this node speaks), ``check_kind`` (a registered kind) and
``build_envelope`` (the payload validated with the registered class and the envelope around it),
each raising the ``CodecError`` subclass whose stable code a transport reports, never a ``json``
or pydantic error. Parsing is stricter than Python's ``json`` default: ``NaN`` and ``Infinity``
are not JSON (RFC 8259) and a frame carrying them is malformed, and a frame nested deeply enough
to exhaust the parser's recursion is malformed too, not a crash.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by every waggle.transport on send and receive
    and by the outbox on append and replay; calls into waggle.envelope, waggle.messages.registry
    and the injected Signer/Verifier. Latency class: microseconds, pure CPU, no I/O.

Key invariants:
    - A Verifier present means a valid signature is REQUIRED on every decoded frame; absent means
      signatures are ignored (the in-process case only). There is no third mode.
    - decode() checks in exactly this order and stops at the first failure: size, UTF-8 JSON
      object with the envelope keys, version major, registered kind, signature (when a Verifier
      is configured), payload and envelope validation. Every failure is a CodecError or
      SignatureError subclass; never a pydantic or json error.
    - encode() never produces a frame over max_frame_bytes: it raises rather than sends.
    - decode(encode(envelope)) == envelope apart from the signature the encode added.
    - parse_frame returns a dict whose key set is exactly ENVELOPE_KEYS, or raises
      MalformedFrameError; nothing else inspects the frame's bytes.
    - check_version accepts any minor of PROTOCOL_MAJOR and rejects every other major with
      UnsupportedVersionError; a version that is not a "<major>.<minor>" string is malformed.
    - build_envelope never lets a pydantic ValidationError escape: both the payload and the
      envelope failures become InvalidPayloadError, whose message names locations, never values.

See Also:
    - docs/waggle/spec.md sections 4 (versioning), 5 (wire format), 6 (signing) and 7 (error
      codes).
    - waggle.signing for the Ed25519Signer and Ed25519Verifier that satisfy the protocols here.
    - waggle.errors for the CodecError and SignatureError families decode() raises.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from waggle.envelope import PROTOCOL_MAJOR, VERSION_PATTERN, Envelope
from waggle.errors import (
    FrameTooLargeError,
    InvalidPayloadError,
    MalformedFrameError,
    MissingSignatureError,
    UnsupportedVersionError,
)
from waggle.messages.registry import model_for, spec_for

MAX_FRAME_BYTES = 1_048_576  # 1 MiB: websockets' default max_size; a 256 KiB chunk in base64 fits.
_SIGNATURE_BYTES = 64  # RFC 8032 Ed25519 signature; restated so codec never imports signing.
_SIGNATURE_B64_CHARS = -(-_SIGNATURE_BYTES // 3) * 4  # 88: padded base64, 4 chars per 3 bytes.
# What signing adds to a frame at most: the JSON member `,"signature":"<88 chars>"`, that is a
# comma (1), the quoted key (11), a colon (1), two quotes (2) and the base64 text (88) = 103. An
# unsigned frame already spends `"signature":null`, so the true delta is smaller; the upper bound
# keeps the outbox's "will still fit once signed" refusal conservative (spec section 10).
SIGNATURE_OVERHEAD_BYTES = len(',"signature":""') + _SIGNATURE_B64_CHARS

# The keys a frame must carry, exactly: the Envelope's fields (spec section 5).
ENVELOPE_KEYS = frozenset(Envelope.model_fields)
_VERSION_RE = re.compile(VERSION_PATTERN)  # Compiled once; every frame is matched against it.

__all__ = [
    "ENVELOPE_KEYS",
    "MAX_FRAME_BYTES",
    "SIGNATURE_OVERHEAD_BYTES",
    "Codec",
    "Signer",
    "Verifier",
    "build_envelope",
    "canonical_bytes",
    "check_kind",
    "check_version",
    "parse_frame",
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


def parse_frame(frame: bytes) -> dict[str, object]:
    """Decode UTF-8 JSON to the one object shape a frame may have: exactly the envelope keys.

    Args:
        frame: The bytes exactly as received.

    Returns:
        The raw wire dict, keyed by exactly ENVELOPE_KEYS; values are still untyped JSON.

    Raises:
        MalformedFrameError: Not UTF-8, not JSON, a non-JSON constant (NaN, Infinity), nested
            past the parser's limit, not an object, or not exactly the envelope keys.
    """
    # UnicodeDecodeError and JSONDecodeError are both ValueErrors; RecursionError is what a
    # frame of a million nested brackets produces, and it is malformed, not a crash.
    try:
        parsed: object = json.loads(frame.decode("utf-8"), parse_constant=_reject_non_finite)
    except (ValueError, RecursionError) as exc:
        raise MalformedFrameError("The frame is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise MalformedFrameError(
            f"The frame's top level is a JSON {type(parsed).__name__}, not the object an "
            "Envelope serialises to."
        )
    # Exactly the envelope keys: a missing one cannot be defaulted (a default is not what the
    # peer signed) and an extra one is a newer minor this node does not speak, or tampering.
    keys = frozenset(parsed)
    if keys != ENVELOPE_KEYS:
        missing = sorted(ENVELOPE_KEYS - keys)
        extra = sorted(str(key) for key in keys - ENVELOPE_KEYS)
        raise MalformedFrameError(
            f"The frame's keys are not the envelope's: missing {missing}, unknown {extra}."
        )
    return parsed


def check_version(raw: Mapping[str, object]) -> None:
    """Reject a version that is not a "<major>.<minor>" string, or whose major is unknown.

    Args:
        raw: The dict parse_frame returned.

    Raises:
        MalformedFrameError: ``version`` is not a string of the right shape.
        UnsupportedVersionError: The major is not PROTOCOL_MAJOR.
    """
    version = raw["version"]
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise MalformedFrameError(
            f'The frame\'s version {version!r} is not a "<major>.<minor>" string.'
        )
    # Only the major matters: a breaking change bumps it and is rejected; any minor is accepted
    # because minor changes are additive (spec section 4).
    major = int(version.partition(".")[0])
    if major != PROTOCOL_MAJOR:
        raise UnsupportedVersionError(
            f"The frame speaks protocol version {version}, major {major}; this node speaks "
            f"major {PROTOCOL_MAJOR} only."
        )


def check_kind(raw: Mapping[str, object]) -> str:
    """Return the frame's kind once it is known to be a registered kind string.

    Args:
        raw: The dict parse_frame returned.

    Returns:
        The kind string, registered.

    Raises:
        MalformedFrameError: ``kind`` is not a string.
        UnknownKindError: ``kind`` is a string but not registered.
    """
    kind = raw["kind"]
    if not isinstance(kind, str):
        raise MalformedFrameError(f"The frame's kind {kind!r} is not a string.")
    # spec_for raises UnknownKindError with the kind named; that is exactly the codec's message.
    spec_for(kind)
    return kind


def build_envelope(kind: str, raw: Mapping[str, object]) -> Envelope:
    """Validate the payload with the registered class, then the envelope around it.

    Args:
        kind: The registered kind check_kind returned.
        raw: The dict parse_frame returned, signature included.

    Returns:
        The Envelope the frame carried, with a typed payload and its signature field set to
        whatever the frame carried.

    Raises:
        InvalidPayloadError: The payload or the envelope's own rules fail validation.
    """
    # The payload is validated with its registered class first because Envelope.payload is
    # typed as the base WaggleMessage, which would accept nothing; the typed instance then
    # replaces the raw dict for the envelope's own validation. Both failures are one error class:
    # the frame had a readable id, so the receiver answers with a control.error (spec section 7).
    try:
        payload = model_for(kind).model_validate(raw["payload"])
        return Envelope.model_validate({**raw, "payload": payload})
    except ValidationError as exc:
        # The raw ids ride on the error so the receiver can build the control.error reply (spec
        # section 7) without parsing the sentence; a value that is not text is left out.
        raise InvalidPayloadError(
            f"The frame with id {raw['id']!r} of kind {kind!r} failed validation: "
            f"{_describe(exc)}.",
            message_id=_text_or_none(raw["id"]),
            sender=_text_or_none(raw["sender"]),
            kind=kind,
            correlation_id=_text_or_none(raw["correlation_id"]),
        ) from exc


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


def _reject_non_finite(constant: str) -> object:
    """Refuse NaN and Infinity, which Python's json accepts but JSON (RFC 8259) does not have."""
    raise ValueError(f"The JSON constant {constant} is not part of JSON.")


def _text_or_none(value: object) -> str | None:
    """Return ``value`` when it is a string, else None: a raw wire id is only usable as text."""
    return value if isinstance(value, str) else None


def _describe(exc: ValidationError) -> str:
    """Summarise a ValidationError by field location and message, never by input value."""
    # pydantic's own str(exc) prints each input value, which on a Real Cell may be C2 content;
    # locations and messages are enough to debug and safe to log.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()
    )
